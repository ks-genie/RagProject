# -*- coding: utf-8 -*-
"""
File Scanner — 감시 디렉토리에서 PDF 파일을 탐지하고 DB에 등록
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.config import config
from src.database import DatabaseManager, Status, compute_file_hash

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 스캔 결과
# ---------------------------------------------------------------------------
@dataclass
class ScanResult:
    registered: list[int] = field(default_factory=list)   # 신규 등록된 doc_id 목록
    skipped_duplicate: list[str] = field(default_factory=list)  # 동일 hash 중복 파일
    requeued: list[int] = field(default_factory=list)      # 파일 변경 감지 → NEW 재전환
    errors: list[str] = field(default_factory=list)        # 처리 중 오류 파일

    @property
    def total_found(self) -> int:
        return len(self.registered) + len(self.skipped_duplicate) + len(self.requeued) + len(self.errors)

    def summary(self) -> str:
        return (
            f"탐지: {self.total_found}건 | "
            f"신규 등록: {len(self.registered)}건 | "
            f"재처리 대상: {len(self.requeued)}건 | "
            f"중복 스킵: {len(self.skipped_duplicate)}건 | "
            f"오류: {len(self.errors)}건"
        )


# ---------------------------------------------------------------------------
# FileScanner
# ---------------------------------------------------------------------------
class FileScanner:
    def __init__(self, watch_dir: Path | None = None, db: DatabaseManager | None = None):
        self.watch_dir = watch_dir or config.watch_dir
        self.db = db or DatabaseManager()

    def scan(self) -> ScanResult:
        """감시 디렉토리를 재귀 탐색하여 PDF/DOCX/PPTX 파일을 처리한다."""
        result = ScanResult()

        if not self.watch_dir.exists():
            logger.warning("감시 디렉토리가 존재하지 않습니다: %s", self.watch_dir)
            return result

        all_files: list[Path] = []
        for ext in ("*.pdf", "*.docx", "*.pptx"):
            all_files.extend(self.watch_dir.rglob(ext))
        all_files.sort()
        logger.info("파일 탐지: %d건 (%s)", len(all_files), self.watch_dir)

        for file_path in all_files:
            self._process_file(file_path, result)

        logger.info(result.summary())
        return result

    def _process_file(self, pdf_path: Path, result: ScanResult) -> None:
        file_path_str = str(pdf_path.resolve())
        file_name = pdf_path.name

        try:
            file_hash = compute_file_hash(file_path_str)
        except OSError as e:
            logger.error("해시 계산 실패 [%s]: %s", file_name, e)
            result.errors.append(file_path_str)
            return

        existing_by_path = self.db.get_document_by_path(file_path_str)

        # ── 케이스 1: 경로로 이미 등록된 파일 ──────────────────────────────
        if existing_by_path:
            if existing_by_path["file_hash"] == file_hash:
                # 변경 없음 — 이미 처리 중이거나 완료
                logger.debug("변경 없음, 스킵: %s", file_name)
                result.skipped_duplicate.append(file_path_str)
            else:
                # 파일 내용이 변경됨 → hash 갱신 후 NEW로 재전환
                doc_id = existing_by_path["id"]
                self.db.update_file_hash(doc_id, file_hash)
                self.db.log_step(doc_id, "SCAN", "SUCCESS", f"파일 변경 감지 → 재처리 대상 (hash: {file_hash[:8]})")
                logger.info("파일 변경 감지, 재처리 등록: %s", file_name)
                result.requeued.append(doc_id)
            return

        # ── 케이스 2: 동일 hash가 다른 경로로 이미 존재 ────────────────────
        existing_by_hash = self.db.get_document_by_hash(file_hash)
        if existing_by_hash:
            logger.warning(
                "중복 파일 스킵 [%s] — 동일 hash가 이미 등록됨: %s",
                file_name,
                existing_by_hash["file_path"],
            )
            result.skipped_duplicate.append(file_path_str)
            return

        # ── 케이스 3: 신규 파일 ─────────────────────────────────────────────
        doc_id = self.db.register_document(file_path_str, file_name, file_hash)
        if doc_id is not None:
            self.db.log_step(doc_id, "SCAN", "SUCCESS", f"신규 등록 (hash: {file_hash[:8]})")
            logger.info("신규 등록: %s (id=%d)", file_name, doc_id)
            result.registered.append(doc_id)
        else:
            # register_document가 None → 동시 삽입 경합 등 예외 상황
            logger.warning("등록 실패 (경합 추정): %s", file_name)
            result.errors.append(file_path_str)


# 싱글턴
file_scanner = FileScanner()
