# -*- coding: utf-8 -*-
"""
Status Tracker — 색인 상태 전이 및 검증

AnythingLLM의 update-embeddings는 동기 처리이므로,
embed 완료 즉시 INDEXED로 전이한다.
(계획서의 PARSING_DONE → CHUNKING_DONE → EMBEDDING_DONE 중간 상태는
 단일 embed 호출로 통합 처리)
"""

import logging

from src.api_client import ApiClient, UploadResult, api_client
from src.database import DatabaseManager, Status, db

logger = logging.getLogger(__name__)


class StatusTracker:
    def __init__(self, database: DatabaseManager = None, client: ApiClient = None):
        self._db = database or db
        self._api = client or api_client

    # ------------------------------------------------------------------
    # 업로드 완료 → UPLOADED
    # ------------------------------------------------------------------
    def mark_uploaded(self, doc_id: int, result: UploadResult) -> None:
        self._db.update_status(
            doc_id,
            Status.UPLOADED,
            anythingllm_doc_id=result.doc_id,
        )
        self._db.log_step(
            doc_id, "UPLOAD", "SUCCESS",
            f"doc_id={result.doc_id} words={result.word_count}",
        )
        logger.info("[doc %d] UPLOADED — anythingllm_doc_id=%s", doc_id, result.doc_id)

    # ------------------------------------------------------------------
    # 임베딩 완료 → INDEXED
    # (AnythingLLM이 동기 처리하므로 embed 반환 = 완전 색인 완료)
    # ------------------------------------------------------------------
    def mark_indexed(self, doc_id: int) -> None:
        self._db.update_status(doc_id, Status.PARSING_DONE)
        self._db.update_status(doc_id, Status.CHUNKING_DONE)
        self._db.update_status(doc_id, Status.EMBEDDING_DONE)
        self._db.update_status(doc_id, Status.INDEXED)
        self._db.log_step(doc_id, "INDEXING", "SUCCESS", "embed 동기 완료 → INDEXED")
        logger.info("[doc %d] INDEXED", doc_id)

    # ------------------------------------------------------------------
    # 임베딩 후 실제 존재 검증 (선택적)
    # ------------------------------------------------------------------
    def verify_indexed(self, anythingllm_doc_id: str) -> bool:
        """AnythingLLM에 문서가 실제로 존재하는지 확인."""
        exists = self._api.document_exists(anythingllm_doc_id)
        if not exists:
            logger.warning("문서 검증 실패: %s 가 AnythingLLM에 없음", anythingllm_doc_id)
        return exists

    # ------------------------------------------------------------------
    # 실패 기록
    # ------------------------------------------------------------------
    def mark_failed(self, doc_id: int, step: str, error: str) -> None:
        self._db.update_status(
            doc_id, Status.FAILED,
            error_message=error,
            failed_step=step,
        )
        self._db.log_step(doc_id, step, "FAILURE", error)
        logger.error("[doc %d] FAILED at %s — %s", doc_id, step, error)

    # ------------------------------------------------------------------
    # REVIEW_REQUIRED 기록
    # ------------------------------------------------------------------
    def mark_review_required(self, doc_id: int, reason: str) -> None:
        self._db.update_status(
            doc_id, Status.REVIEW_REQUIRED,
            error_message=reason,
            failed_step="PREPROCESS",
        )
        self._db.log_step(doc_id, "PREPROCESS", "REVIEW_REQUIRED", reason)
        logger.warning("[doc %d] REVIEW_REQUIRED — %s", doc_id, reason)


# 싱글턴
status_tracker = StatusTracker()
