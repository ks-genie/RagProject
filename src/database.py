# -*- coding: utf-8 -*-
"""
Database Manager — SQLite 기반 documents / process_logs 테이블 관리
"""

import sqlite3
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.config import config


# ---------------------------------------------------------------------------
# 상태 상수
# ---------------------------------------------------------------------------
class Status:
    NEW               = "NEW"
    ANALYZING         = "ANALYZING"
    TEXT_EXTRACTED    = "TEXT_EXTRACTED"
    OCR_DONE          = "OCR_DONE"
    REVIEW_REQUIRED   = "REVIEW_REQUIRED"
    TEXT_READY        = "TEXT_READY"
    UPLOAD_REQUESTED  = "UPLOAD_REQUESTED"
    UPLOADED          = "UPLOADED"
    PARSING_DONE      = "PARSING_DONE"
    CHUNKING_DONE     = "CHUNKING_DONE"
    EMBEDDING_DONE    = "EMBEDDING_DONE"
    INDEXED           = "INDEXED"
    FAILED            = "FAILED"

    # 재처리 가능한 상태
    RETRYABLE = {FAILED, REVIEW_REQUIRED}

    # 정상 완료 순서 (색인 추적용)
    INDEXING_SEQUENCE = [UPLOADED, PARSING_DONE, CHUNKING_DONE, EMBEDDING_DONE, INDEXED]


class SourceType:
    DIGITAL = "DIGITAL"
    SCANNED = "SCANNED"
    DOCX    = "DOCX"
    PPTX    = "PPTX"


class LayoutType:
    SINGLE_COLUMN = "SINGLE_COLUMN"
    MULTI_COLUMN  = "MULTI_COLUMN"


class OcrStrategy:
    DIGITAL_EXTRACT       = "DIGITAL_EXTRACT"
    DIGITAL_EXTRACT_MULTI = "DIGITAL_EXTRACT_MULTI"   # 디지털 2단 컬럼 인식 추출
    DOCX_EXTRACT          = "DOCX_EXTRACT"            # Word 문서 직접 추출
    PPTX_EXTRACT          = "PPTX_EXTRACT"            # PowerPoint 슬라이드 직접 추출
    OCR_ENG               = "OCR_ENG"
    OCR_KOR_ENG           = "OCR_KOR_ENG"
    OCR_JPN               = "OCR_JPN"
    OCR_MULTI_COL         = "OCR_MULTI_COL"
    OCR_FORMULA           = "OCR_FORMULA"
    OCR_MULTI_FORMULA     = "OCR_MULTI_FORMULA"


# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------
DDL_DOCUMENTS = """
CREATE TABLE IF NOT EXISTS documents (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path           TEXT    NOT NULL UNIQUE,
    file_name           TEXT    NOT NULL,
    file_hash           TEXT,
    status              TEXT    NOT NULL DEFAULT 'NEW',

    -- PDF 분류 분석 결과 (Phase 3)
    source_type         TEXT,                      -- DIGITAL / SCANNED
    layout_type         TEXT,                      -- SINGLE_COLUMN / MULTI_COLUMN
    detected_languages  TEXT,                      -- 콤마 구분: "kor,eng" / "kor,eng,jpn"
    has_formula         INTEGER NOT NULL DEFAULT 0, -- 0: 없음, 1: 있음
    ocr_strategy        TEXT,                      -- 선택된 OCR 전략 ID

    -- OCR / 업로드 결과
    anythingllm_doc_id  TEXT,
    ocr_quality_score   REAL,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    error_message       TEXT,
    failed_step         TEXT,

    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME
);
"""

DDL_PROCESS_LOGS = """
CREATE TABLE IF NOT EXISTS process_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER  NOT NULL,
    step         TEXT     NOT NULL,
    result       TEXT     NOT NULL,
    message      TEXT,
    logged_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);
"""

DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_documents_status    ON documents(status);",
    "CREATE INDEX IF NOT EXISTS idx_documents_file_hash ON documents(file_hash);",
    "CREATE INDEX IF NOT EXISTS idx_process_logs_doc_id ON process_logs(document_id);",
]


# ---------------------------------------------------------------------------
# DatabaseManager
# ---------------------------------------------------------------------------
class DatabaseManager:
    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or config.db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    # ------------------------------------------------------------------
    # 초기화 / 마이그레이션
    # ------------------------------------------------------------------
    def initialize(self):
        """테이블 및 인덱스 생성"""
        from src.page_db import _DDL as _PAGE_DDL
        with self._connect() as conn:
            conn.execute(DDL_DOCUMENTS)
            conn.execute(DDL_PROCESS_LOGS)
            for ddl in DDL_INDEXES:
                conn.execute(ddl)
            conn.executescript(_PAGE_DDL)   # document_metadata / page_contents

    # ------------------------------------------------------------------
    # documents CRUD
    # ------------------------------------------------------------------
    def register_document(self, file_path: str, file_name: str, file_hash: str) -> Optional[int]:
        """신규 문서 등록. 이미 존재하면 None 반환."""
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO documents (file_path, file_name, file_hash, status)
                    VALUES (?, ?, ?, ?)
                    """,
                    (file_path, file_name, file_hash, Status.NEW),
                )
                return cur.lastrowid
        except sqlite3.IntegrityError:
            return None  # UNIQUE 제약 — 이미 등록된 파일

    def get_document_by_path(self, file_path: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM documents WHERE file_path = ?", (file_path,)
            ).fetchone()

    def get_document_by_hash(self, file_hash: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM documents WHERE file_hash = ?", (file_hash,)
            ).fetchone()

    def get_document_by_id(self, doc_id: int) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()

    def get_documents_by_status(self, status: str) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM documents WHERE status = ? ORDER BY created_at",
                (status,),
            ).fetchall()

    def get_all_documents(self) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM documents ORDER BY created_at DESC"
            ).fetchall()

    def update_status(
        self,
        doc_id: int,
        status: str,
        *,
        anythingllm_doc_id: Optional[str] = None,
        ocr_quality_score: Optional[float] = None,
        error_message: Optional[str] = None,
        failed_step: Optional[str] = None,
    ):
        fields = ["status = ?", "updated_at = ?"]
        values: list = [status, datetime.now(timezone.utc).isoformat()]

        if anythingllm_doc_id is not None:
            fields.append("anythingllm_doc_id = ?")
            values.append(anythingllm_doc_id)
        if ocr_quality_score is not None:
            fields.append("ocr_quality_score = ?")
            values.append(ocr_quality_score)
        if error_message is not None:
            fields.append("error_message = ?")
            values.append(error_message)
        if failed_step is not None:
            fields.append("failed_step = ?")
            values.append(failed_step)

        values.append(doc_id)
        with self._connect() as conn:
            conn.execute(
                f"UPDATE documents SET {', '.join(fields)} WHERE id = ?",
                values,
            )

    def increment_retry(self, doc_id: int):
        with self._connect() as conn:
            conn.execute(
                "UPDATE documents SET retry_count = retry_count + 1, updated_at = ? WHERE id = ?",
                (datetime.now(timezone.utc).isoformat(), doc_id),
            )

    def update_analysis(
        self,
        doc_id: int,
        *,
        source_type: str,
        layout_type: str,
        detected_languages: list[str],
        has_formula: bool,
        ocr_strategy: str,
    ):
        """PDF 분류 분석 결과 저장"""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE documents
                SET source_type = ?, layout_type = ?, detected_languages = ?,
                    has_formula = ?, ocr_strategy = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    source_type,
                    layout_type,
                    ",".join(detected_languages),
                    1 if has_formula else 0,
                    ocr_strategy,
                    datetime.now(timezone.utc).isoformat(),
                    doc_id,
                ),
            )

    def update_file_hash(self, doc_id: int, file_hash: str):
        """파일 변경 시 hash 갱신 (재처리 대상)"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE documents SET file_hash = ?, status = ?, updated_at = ? WHERE id = ?",
                (file_hash, Status.NEW, datetime.now(timezone.utc).isoformat(), doc_id),
            )

    def reset_for_retry(self, doc_id: int):
        """FAILED / REVIEW_REQUIRED 문서를 NEW로 초기화"""
        doc = self.get_document_by_id(doc_id)
        if doc and doc["status"] in Status.RETRYABLE:
            with self._connect() as conn:
                conn.execute(
                    """
                    UPDATE documents
                    SET status = ?, error_message = NULL, failed_step = NULL, updated_at = ?
                    WHERE id = ?
                    """,
                    (Status.NEW, datetime.now(timezone.utc).isoformat(), doc_id),
                )

    def force_reset_for_reprocess(self, doc_id: int):
        """상태에 관계없이 (INDEXED 포함) 문서를 NEW로 강제 초기화"""
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE documents
                SET status = ?, error_message = NULL, failed_step = NULL,
                    retry_count = 0, ocr_quality_score = NULL,
                    anythingllm_doc_id = NULL, updated_at = ?
                WHERE id = ?
                """,
                (Status.NEW, datetime.now(timezone.utc).isoformat(), doc_id),
            )

    def reset_all_for_reocr(self) -> int:
        """NEW·ANALYZING 이외 모든 문서를 NEW로 초기화 (전체 재OCR 용).

        OCR 결과·업로드 정보를 모두 지워 파이프라인이 처음부터 재실행되도록 한다.
        Returns: 초기화된 문서 수
        """
        with self._connect() as conn:
            cur = conn.execute(
                """
                UPDATE documents
                SET status            = ?,
                    error_message     = NULL,
                    failed_step       = NULL,
                    retry_count       = 0,
                    ocr_quality_score = NULL,
                    anythingllm_doc_id = NULL,
                    updated_at        = ?
                WHERE status NOT IN (?, ?)
                """,
                (
                    Status.NEW,
                    datetime.now(timezone.utc).isoformat(),
                    Status.NEW,
                    Status.ANALYZING,
                ),
            )
            return cur.rowcount

    # ------------------------------------------------------------------
    # process_logs
    # ------------------------------------------------------------------
    def log_step(self, doc_id: int, step: str, result: str, message: str = ""):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO process_logs (document_id, step, result, message) VALUES (?, ?, ?, ?)",
                (doc_id, step, result, message),
            )

    def get_logs(self, doc_id: int) -> list[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM process_logs WHERE document_id = ? ORDER BY logged_at",
                (doc_id,),
            ).fetchall()

    def get_all_logs_grouped(self) -> dict:
        """전체 process_logs를 문서 ID별 리스트로 단일 쿼리 반환.

        Returns: {doc_id: [{"step":…, "result":…, "message":…, "logged_at":…}, …]}
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT document_id, step, result, message, logged_at "
                "FROM process_logs ORDER BY document_id, logged_at"
            ).fetchall()
        result: dict = {}
        for r in rows:
            doc_id = r["document_id"]
            if doc_id not in result:
                result[doc_id] = []
            result[doc_id].append(dict(r))
        return result


# ---------------------------------------------------------------------------
# 파일 해시 유틸
# ---------------------------------------------------------------------------
def compute_file_hash(file_path: str) -> str:
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


# 싱글턴
db = DatabaseManager()
