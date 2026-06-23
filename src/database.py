# -*- coding: utf-8 -*-
"""
Database Manager — SQLite 기반 documents / process_logs 테이블 관리

이 모듈은 RAG 파이프라인에서 처리되는 문서들의 상태를 추적하고 관리하는
SQLite 데이터베이스 인터페이스를 제공합니다.
주요 역할:
  - documents 테이블: 파일 경로, 처리 상태, OCR 분석 결과 등을 저장
  - process_logs 테이블: 각 처리 단계별 성공/실패 이력을 기록
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
    """
    문서 처리 상태를 나타내는 상수 모음.

    파이프라인의 각 단계를 문자열 상수로 정의하여,
    코드 어디서든 일관된 상태 값을 사용할 수 있게 합니다.
    오탈자로 인한 버그를 방지하기 위해 문자열 리터럴 대신 이 상수를 사용합니다.
    """

    NEW               = "NEW"               # 신규 등록, 아직 처리 시작 전
    ANALYZING         = "ANALYZING"         # PDF 분류 분석 중 (디지털/스캔 판별 등)
    TEXT_EXTRACTED    = "TEXT_EXTRACTED"    # 디지털 PDF에서 텍스트 직접 추출 완료
    OCR_DONE          = "OCR_DONE"          # OCR(광학 문자 인식) 처리 완료
    REVIEW_REQUIRED   = "REVIEW_REQUIRED"   # OCR 품질 낮음 — 사람이 검토 필요
    TEXT_READY        = "TEXT_READY"        # 텍스트 최종 준비 완료 (업로드 대기)
    UPLOAD_REQUESTED  = "UPLOAD_REQUESTED"  # AnythingLLM 업로드 요청됨
    UPLOADED          = "UPLOADED"          # AnythingLLM 서버에 업로드 완료
    PARSING_DONE      = "PARSING_DONE"      # 서버측 문서 파싱 완료
    CHUNKING_DONE     = "CHUNKING_DONE"     # 청킹(텍스트 분할) 완료
    EMBEDDING_DONE    = "EMBEDDING_DONE"    # 임베딩(벡터 변환) 완료
    INDEXED           = "INDEXED"           # 벡터 인덱스에 등록 완료 — 최종 성공 상태
    FAILED            = "FAILED"            # 처리 실패 — 오류 원인은 error_message 참조

    # 자동으로 재처리를 시도할 수 있는 상태 집합
    # FAILED는 오류가 수정되면 재시도 가능, REVIEW_REQUIRED는 사람 검토 후 재시도 가능
    RETRYABLE = {FAILED, REVIEW_REQUIRED}

    # 업로드 이후 색인 완료까지의 정상 처리 순서
    # 이 순서대로 상태가 진행되어야 정상적으로 색인이 완료됩니다
    INDEXING_SEQUENCE = [UPLOADED, PARSING_DONE, CHUNKING_DONE, EMBEDDING_DONE, INDEXED]


class SourceType:
    """
    PDF 파일의 원본 유형을 나타내는 상수 모음.

    문서가 디지털로 생성되었는지(텍스트 레이어 있음),
    스캔된 이미지인지, 혹은 Office 문서인지를 구분합니다.
    """

    DIGITAL = "DIGITAL"  # 디지털 PDF — 텍스트 레이어가 존재하여 직접 추출 가능
    SCANNED = "SCANNED"  # 스캔 PDF — 이미지이므로 OCR이 필요
    DOCX    = "DOCX"     # Microsoft Word 문서
    PPTX    = "PPTX"     # Microsoft PowerPoint 문서


class LayoutType:
    """
    문서의 레이아웃(지면 구성) 유형을 나타내는 상수 모음.

    단일 컬럼(일반 세로 문서)과 다중 컬럼(신문·학술지 등)을 구분하여
    적절한 텍스트 추출 전략을 선택하는 데 사용합니다.
    """

    SINGLE_COLUMN = "SINGLE_COLUMN"  # 한 줄짜리 레이아웃 (일반 문서)
    MULTI_COLUMN  = "MULTI_COLUMN"   # 두 단 이상의 다중 컬럼 레이아웃 (논문, 신문 등)


class OcrStrategy:
    """
    문서 유형과 레이아웃에 따라 선택되는 OCR/텍스트 추출 전략 상수 모음.

    각 전략은 문서의 특성(디지털/스캔, 단/다단, 언어, 수식 포함 여부)에 맞게
    최적화된 처리 방법을 지정합니다.
    """

    DIGITAL_EXTRACT       = "DIGITAL_EXTRACT"        # 디지털 PDF 단일 컬럼 — 텍스트 직접 추출
    DIGITAL_EXTRACT_MULTI = "DIGITAL_EXTRACT_MULTI"  # 디지털 PDF 2단 컬럼 인식 추출
    DOCX_EXTRACT          = "DOCX_EXTRACT"           # Word 문서 직접 추출
    PPTX_EXTRACT          = "PPTX_EXTRACT"           # PowerPoint 슬라이드 직접 추출
    OCR_ENG               = "OCR_ENG"                # 영어 전용 OCR
    OCR_KOR_ENG           = "OCR_KOR_ENG"            # 한국어+영어 혼합 OCR
    OCR_JPN               = "OCR_JPN"                # 일본어 OCR
    OCR_MULTI_COL         = "OCR_MULTI_COL"          # 다단 레이아웃 스캔 OCR
    OCR_FORMULA           = "OCR_FORMULA"            # 수식 포함 단일 컬럼 OCR
    OCR_MULTI_FORMULA     = "OCR_MULTI_FORMULA"      # 수식 포함 다단 OCR


# ---------------------------------------------------------------------------
# DDL (Data Definition Language) — 테이블 및 인덱스 생성 SQL 문
# ---------------------------------------------------------------------------

# documents 테이블 생성 SQL
# 이 테이블은 처리 대상 파일 하나당 레코드 하나를 가지며,
# 파일의 현재 처리 상태와 분석 결과를 저장합니다.
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

# process_logs 테이블 생성 SQL
# 각 처리 단계(step)마다 성공/실패 결과를 시간순으로 기록합니다.
# 문제 발생 시 어느 단계에서 실패했는지 추적하는 데 사용됩니다.
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

# 자주 조회되는 컬럼에 인덱스를 생성하여 검색 속도를 높입니다.
# IF NOT EXISTS를 사용하므로 이미 존재해도 오류가 발생하지 않습니다.
DDL_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_documents_status    ON documents(status);",
    "CREATE INDEX IF NOT EXISTS idx_documents_file_hash ON documents(file_hash);",
    "CREATE INDEX IF NOT EXISTS idx_process_logs_doc_id ON process_logs(document_id);",
]


# ---------------------------------------------------------------------------
# DatabaseManager
# ---------------------------------------------------------------------------
class DatabaseManager:
    """
    SQLite 데이터베이스를 통해 문서 처리 상태를 관리하는 클래스.

    documents 테이블과 process_logs 테이블에 대한 CRUD(생성·조회·수정·삭제) 기능과
    파이프라인 상태 전이(status transition)를 담당합니다.
    매 메서드 호출마다 새로운 DB 연결을 열고 닫아 멀티스레드 환경에서도 안전합니다.
    """

    def __init__(self, db_path: Optional[Path] = None):
        """
        DatabaseManager 초기화.

        매개변수:
            db_path: 사용할 SQLite 파일의 경로(Path 객체).
                     None이면 config.db_path(프로젝트 기본 경로)를 사용합니다.
        """
        # db_path가 주어지지 않으면 프로젝트 설정의 기본 경로를 사용
        self.db_path = db_path or config.db_path
        # DB 파일이 위치할 디렉터리가 없으면 자동으로 생성 (parents=True: 중간 디렉터리도 함께 생성)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _connect(self) -> sqlite3.Connection:
        """
        SQLite 데이터베이스에 연결하고 Connection 객체를 반환하는 내부 헬퍼 메서드.

        반환값:
            설정이 완료된 sqlite3.Connection 객체.

        참고:
            - row_factory=sqlite3.Row 설정으로 컬럼명으로 결과를 접근할 수 있습니다.
              예: row["file_name"] 처럼 딕셔너리 방식으로 조회 가능.
            - WAL(Write-Ahead Logging) 모드: 읽기와 쓰기를 동시에 허용하여 성능을 향상시킵니다.
            - foreign_keys=ON: 외래 키 제약(documents.id ↔ process_logs.document_id)을 강제합니다.
        """
        conn = sqlite3.connect(self.db_path)
        # 결과 행을 딕셔너리처럼 컬럼명으로 접근할 수 있게 설정
        conn.row_factory = sqlite3.Row
        # WAL 모드 활성화 — 동시 읽기/쓰기 성능 향상
        conn.execute("PRAGMA journal_mode=WAL;")
        # 외래 키 제약 조건 활성화 (SQLite는 기본적으로 비활성화됨)
        conn.execute("PRAGMA foreign_keys=ON;")
        return conn

    # ------------------------------------------------------------------
    # 초기화 / 마이그레이션
    # ------------------------------------------------------------------
    def initialize(self):
        """
        데이터베이스 테이블과 인덱스를 생성합니다.

        애플리케이션 최초 실행 시 한 번 호출합니다.
        IF NOT EXISTS 구문을 사용하므로 이미 테이블이 있어도 안전하게 재호출 가능합니다.
        page_db 모듈의 DDL도 함께 실행하여 document_metadata / page_contents 테이블을 생성합니다.
        """
        # page_db 모듈의 DDL을 지역 임포트하여 순환 임포트 문제를 방지
        from src.page_db import _DDL as _PAGE_DDL
        with self._connect() as conn:
            conn.execute(DDL_DOCUMENTS)
            conn.execute(DDL_PROCESS_LOGS)
            # 각 인덱스 DDL을 순서대로 실행
            for ddl in DDL_INDEXES:
                conn.execute(ddl)
            # executescript는 세미콜론으로 구분된 여러 SQL문을 한 번에 실행합니다
            conn.executescript(_PAGE_DDL)   # document_metadata / page_contents

    # ------------------------------------------------------------------
    # documents CRUD
    # ------------------------------------------------------------------
    def register_document(self, file_path: str, file_name: str, file_hash: str) -> Optional[int]:
        """
        새 문서를 documents 테이블에 등록합니다.

        매개변수:
            file_path: 파일의 절대 경로 문자열 (UNIQUE 제약이 걸려 있음)
            file_name: 파일명 (표시용)
            file_hash: 파일 내용의 MD5 해시값 (변경 감지에 사용)

        반환값:
            새로 삽입된 레코드의 ID(정수). 이미 같은 경로로 등록된 파일이면 None 반환.
        """
        try:
            with self._connect() as conn:
                cur = conn.execute(
                    """
                    INSERT INTO documents (file_path, file_name, file_hash, status)
                    VALUES (?, ?, ?, ?)
                    """,
                    (file_path, file_name, file_hash, Status.NEW),
                )
                # lastrowid: 방금 삽입된 행의 자동 증가 ID
                return cur.lastrowid
        except sqlite3.IntegrityError:
            # file_path UNIQUE 제약 위반 — 동일한 경로의 파일이 이미 등록되어 있음
            return None  # UNIQUE 제약 — 이미 등록된 파일

    def get_document_by_path(self, file_path: str) -> Optional[sqlite3.Row]:
        """
        파일 경로로 문서 레코드를 조회합니다.

        매개변수:
            file_path: 조회할 파일의 절대 경로 문자열

        반환값:
            해당 경로의 documents 레코드(Row 객체). 없으면 None.
        """
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM documents WHERE file_path = ?", (file_path,)
            ).fetchone()

    def get_document_by_hash(self, file_hash: str) -> Optional[sqlite3.Row]:
        """
        파일 해시값으로 문서 레코드를 조회합니다.
        동일한 내용의 파일이 다른 경로에 있는지 확인할 때 사용합니다.

        매개변수:
            file_hash: 조회할 파일의 MD5 해시 문자열

        반환값:
            해당 해시값의 documents 레코드(Row 객체). 없으면 None.
        """
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM documents WHERE file_hash = ?", (file_hash,)
            ).fetchone()

    def get_document_by_id(self, doc_id: int) -> Optional[sqlite3.Row]:
        """
        문서 ID(기본 키)로 문서 레코드를 조회합니다.

        매개변수:
            doc_id: 조회할 문서의 정수형 ID

        반환값:
            해당 ID의 documents 레코드(Row 객체). 없으면 None.
        """
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM documents WHERE id = ?", (doc_id,)
            ).fetchone()

    def get_documents_by_status(self, status: str) -> list[sqlite3.Row]:
        """
        특정 처리 상태의 문서 목록을 등록 시간 순으로 반환합니다.
        파이프라인이 다음에 처리해야 할 문서를 찾을 때 사용합니다.

        매개변수:
            status: 조회할 상태 문자열 (Status 클래스의 상수 사용 권장)

        반환값:
            해당 상태의 documents 레코드 목록 (등록 시간 오름차순).
        """
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM documents WHERE status = ? ORDER BY created_at",
                (status,),
            ).fetchall()

    def get_all_documents(self) -> list[sqlite3.Row]:
        """
        모든 문서 레코드를 최신 등록순(내림차순)으로 반환합니다.

        반환값:
            documents 테이블의 전체 레코드 목록 (등록 시간 내림차순).
        """
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
        """
        문서의 처리 상태를 변경하고, 선택적으로 추가 정보도 함께 업데이트합니다.

        매개변수:
            doc_id: 상태를 변경할 문서의 ID
            status: 새로운 상태 문자열 (Status 클래스의 상수 사용 권장)
            anythingllm_doc_id: AnythingLLM 서버에서 발급된 문서 식별자 (업로드 완료 시 저장)
            ocr_quality_score: OCR 품질 점수 (0.0 ~ 1.0 범위의 실수)
            error_message: 실패 시 오류 메시지
            failed_step: 실패가 발생한 처리 단계 이름

        참고:
            키워드 전용 인수(*) 뒤의 파라미터는 반드시 이름을 명시하여 호출해야 합니다.
            None인 파라미터는 SQL UPDATE에 포함되지 않아 기존 값이 유지됩니다.
        """
        # 항상 업데이트하는 필드: status와 updated_at
        fields = ["status = ?", "updated_at = ?"]
        values: list = [status, datetime.now(timezone.utc).isoformat()]

        # None이 아닌 선택적 필드만 동적으로 UPDATE 구문에 추가
        # 이 방식으로 필요한 컬럼만 선택적으로 업데이트할 수 있습니다
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

        # WHERE id = ? 조건을 위해 doc_id를 values 맨 뒤에 추가
        values.append(doc_id)
        with self._connect() as conn:
            # f-string으로 동적으로 구성된 UPDATE 문을 실행
            # 예: "UPDATE documents SET status = ?, updated_at = ?, error_message = ? WHERE id = ?"
            conn.execute(
                f"UPDATE documents SET {', '.join(fields)} WHERE id = ?",
                values,
            )

    def increment_retry(self, doc_id: int):
        """
        문서의 재시도 횟수(retry_count)를 1 증가시킵니다.

        처리 실패 후 자동 재시도 시 호출하여 재시도 횟수를 추적합니다.
        무한 재시도를 방지하기 위해 retry_count 상한을 외부에서 체크해야 합니다.

        매개변수:
            doc_id: 재시도 횟수를 증가시킬 문서의 ID
        """
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
        """
        PDF 분류 분석 결과를 documents 테이블에 저장합니다.

        ANALYZING 단계 완료 후 호출되며, 이후 단계에서 적절한 OCR 전략을
        선택하는 데 사용되는 메타데이터를 저장합니다.

        매개변수:
            doc_id: 분석 결과를 저장할 문서의 ID
            source_type: 문서 원본 유형 (SourceType 상수 사용)
            layout_type: 문서 레이아웃 유형 (LayoutType 상수 사용)
            detected_languages: 감지된 언어 코드 목록 (예: ["kor", "eng"])
            has_formula: 수식 포함 여부 (True/False)
            ocr_strategy: 선택된 OCR 전략 (OcrStrategy 상수 사용)
        """
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
                    # 언어 목록을 콤마로 연결된 문자열로 변환하여 저장 (예: "kor,eng")
                    ",".join(detected_languages),
                    # SQLite는 Boolean을 지원하지 않으므로 0/1 정수로 변환
                    1 if has_formula else 0,
                    ocr_strategy,
                    datetime.now(timezone.utc).isoformat(),
                    doc_id,
                ),
            )

    def update_file_hash(self, doc_id: int, file_hash: str):
        """
        파일 내용이 변경된 경우 해시값을 갱신하고 상태를 NEW로 되돌립니다.

        파일이 수정되어 재처리가 필요한 경우 호출됩니다.
        상태를 NEW로 초기화하여 파이프라인이 처음부터 다시 실행되도록 합니다.

        매개변수:
            doc_id: 해시를 갱신할 문서의 ID
            file_hash: 새로 계산된 MD5 해시 문자열
        """
        with self._connect() as conn:
            conn.execute(
                "UPDATE documents SET file_hash = ?, status = ?, updated_at = ? WHERE id = ?",
                (file_hash, Status.NEW, datetime.now(timezone.utc).isoformat(), doc_id),
            )

    def reset_for_retry(self, doc_id: int):
        """
        실패하거나 검토가 필요한 문서를 NEW 상태로 초기화하여 재처리를 허용합니다.

        Status.RETRYABLE에 포함된 상태(FAILED, REVIEW_REQUIRED)인 문서에 대해서만
        초기화를 수행합니다. 다른 상태의 문서는 변경하지 않습니다.

        매개변수:
            doc_id: 재처리할 문서의 ID
        """
        # 먼저 현재 상태를 확인하여 재처리 가능한 상태인지 검증
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
        """
        문서를 상태에 관계없이 강제로 NEW 상태로 초기화합니다.

        INDEXED(최종 완료) 상태의 문서도 포함하여 모든 처리 결과를 초기화합니다.
        재처리가 필요한 특수한 경우(예: 모델 업그레이드, 설정 변경)에 사용합니다.

        매개변수:
            doc_id: 강제 초기화할 문서의 ID
        """
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
        """
        NEW·ANALYZING 이외 모든 문서를 NEW로 초기화합니다 (전체 재OCR 용).

        OCR 결과·업로드 정보를 모두 지워 파이프라인이 처음부터 재실행되도록 합니다.
        시스템 전체의 OCR 설정이 변경되었거나 대규모 재처리가 필요할 때 사용합니다.
        NEW와 ANALYZING 상태의 문서는 건드리지 않아 현재 진행 중인 작업을 보호합니다.

        반환값:
            실제로 초기화된 문서 수 (정수)
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
                    source_type       = NULL,
                    layout_type       = NULL,
                    ocr_strategy      = NULL,
                    has_formula       = 0,
                    language          = NULL,
                    updated_at        = ?
                WHERE status NOT IN (?, ?)
                """,
                (
                    Status.NEW,
                    datetime.now(timezone.utc).isoformat(),
                    Status.NEW,       # 이미 NEW인 문서는 제외
                    Status.ANALYZING, # 현재 분석 중인 문서는 제외
                ),
            )
            # rowcount: UPDATE 문에 의해 실제로 변경된 행(레코드)의 수
            return cur.rowcount

    # ------------------------------------------------------------------
    # process_logs
    # ------------------------------------------------------------------
    def log_step(self, doc_id: int, step: str, result: str, message: str = ""):
        """
        문서 처리 단계의 결과를 process_logs 테이블에 기록합니다.

        각 처리 단계의 성공/실패 이력을 남겨 디버깅과 감사(audit)에 활용합니다.

        매개변수:
            doc_id: 로그를 남길 문서의 ID
            step: 처리 단계 이름 (예: "ANALYZING", "OCR", "UPLOAD")
            result: 처리 결과 (예: "SUCCESS", "FAILED")
            message: 결과에 대한 추가 설명 또는 오류 메시지 (기본값: 빈 문자열)
        """
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO process_logs (document_id, step, result, message) VALUES (?, ?, ?, ?)",
                (doc_id, step, result, message),
            )

    def get_logs(self, doc_id: int) -> list[sqlite3.Row]:
        """
        특정 문서의 모든 처리 로그를 시간순으로 반환합니다.

        매개변수:
            doc_id: 로그를 조회할 문서의 ID

        반환값:
            해당 문서의 process_logs 레코드 목록 (기록 시간 오름차순).
        """
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM process_logs WHERE document_id = ? ORDER BY logged_at",
                (doc_id,),
            ).fetchall()

    def get_all_logs_grouped(self) -> dict:
        """
        전체 process_logs를 문서 ID별로 그룹화하여 단일 쿼리로 반환합니다.

        문서별로 로그를 개별 조회하면 N+1 쿼리 문제가 발생하므로,
        한 번의 쿼리로 전체를 가져와 파이썬에서 그룹화합니다.

        반환값:
            딕셔너리 형태: {doc_id: [{"step":…, "result":…, "message":…, "logged_at":…}, …]}
            각 값은 해당 문서의 로그 항목 목록이며 시간순으로 정렬되어 있습니다.
        """
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT document_id, step, result, message, logged_at "
                "FROM process_logs ORDER BY document_id, logged_at"
            ).fetchall()
        # 결과를 문서 ID 기준으로 그룹화하여 딕셔너리로 변환
        result: dict = {}
        for r in rows:
            doc_id = r["document_id"]
            # 해당 doc_id의 키가 없으면 빈 리스트로 초기화
            if doc_id not in result:
                result[doc_id] = []
            # sqlite3.Row는 dict()로 변환하여 일반 딕셔너리로 사용 가능하게 함
            result[doc_id].append(dict(r))
        return result


# ---------------------------------------------------------------------------
# 파일 해시 유틸
# ---------------------------------------------------------------------------
def compute_file_hash(file_path: str) -> str:
    """
    파일 내용의 MD5 해시값을 계산하여 반환합니다.

    파일이 변경되었는지 감지하거나 중복 파일을 찾는 데 사용됩니다.
    대용량 파일도 메모리에 한 번에 올리지 않고 청크 단위로 읽어 처리합니다.

    매개변수:
        file_path: 해시를 계산할 파일의 경로 문자열

    반환값:
        32자리 16진수 MD5 해시 문자열 (예: "d41d8cd98f00b204e9800998ecf8427e")
    """
    h = hashlib.md5()
    with open(file_path, "rb") as f:
        # iter(callable, sentinel): callable이 sentinel을 반환할 때까지 반복 호출
        # 파일을 8192바이트(8KB) 단위로 읽어 메모리를 절약하며 해시를 누적 계산
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    # hexdigest(): 최종 해시값을 16진수 문자열로 반환
    return h.hexdigest()


# 싱글턴 — 모듈 임포트 시 단 하나의 DatabaseManager 인스턴스를 생성합니다.
# 다른 모듈에서 `from src.database import db` 로 가져와 공유하여 사용합니다.
db = DatabaseManager()
