# -*- coding: utf-8 -*-
"""
Pipeline — 문서 처리 파이프라인 오케스트레이터

Phase 2 → 3 → 4 → 5 → 6 → 7·8 를 연결하여
NEW 상태 문서를 INDEXED 까지 처리한다.

처리 흐름:
  NEW
   → ANALYZING     (PDF 분류 분석)
   → TEXT_EXTRACTED / OCR_DONE  (텍스트 추출)
   → TEXT_READY    (전처리 통과)  또는  REVIEW_REQUIRED
   → UPLOAD_REQUESTED → UPLOADED (API 업로드, 재시도 포함)
   → INDEXED       (임베딩 완료)
   → FAILED        (복구 불가 오류)
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.api_client import ApiClient, ApiError, api_client
from src.asset_pipeline import AssetPipeline, asset_pipeline
from src.config import config
from src.database import DatabaseManager, Status, SourceType, db
from src.file_scanner import FileScanner, file_scanner as _default_scanner
from src.ocr_engine import OcrEngine, ocr_engine
from src.pdf_analyzer import PDFAnalyzer, analyzer
from src.preprocessor import Preprocessor, preprocessor
from src.retry_handler import RetryExhausted, RetryHandler, retry_handler
from src.status_tracker import StatusTracker, status_tracker
from src.strategy_selector import StrategySelector, selector

logger = logging.getLogger(__name__)


@dataclass
class PipelineStats:
    scanned:       int = 0
    processed:     int = 0
    indexed:       int = 0
    review:        int = 0
    failed:        int = 0
    skipped:       int = 0
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"처리: {self.processed} | INDEXED: {self.indexed} | "
            f"REVIEW: {self.review} | FAILED: {self.failed} | "
            f"스킵: {self.skipped} | 신규 탐지: {self.scanned}"
        )


class Pipeline:
    def __init__(
        self,
        database:      DatabaseManager  = None,
        scanner:       FileScanner      = None,
        pdf_analyzer:  PDFAnalyzer      = None,
        strat_sel:     StrategySelector = None,
        ocr:           OcrEngine        = None,
        asset_proc:    AssetPipeline    = None,
        preproc:       Preprocessor     = None,
        client:        ApiClient        = None,
        tracker:       StatusTracker    = None,
        retrier:       RetryHandler     = None,
    ):
        self._db        = database    or db
        self._scanner   = scanner     or _default_scanner
        self._analyzer  = pdf_analyzer or analyzer
        self._selector  = strat_sel   or selector
        self._ocr       = ocr         or ocr_engine
        self._assets    = asset_proc  or asset_pipeline
        self._preproc   = preproc     or preprocessor
        self._api       = client      or api_client
        self._tracker   = tracker     or status_tracker
        self._retry     = retrier     or retry_handler

    # ------------------------------------------------------------------
    # 공개 인터페이스
    # ------------------------------------------------------------------
    def run_once(self) -> PipelineStats:
        """한 번의 처리 사이클:
        1. 감시 디렉토리 탐지 → NEW 등록
        2. NEW 문서 전체 처리
        """
        stats = PipelineStats()

        # ── 파일 탐지 ────────────────────────────────────────────────────
        scan = self._scanner.scan()
        stats.scanned = len(scan.registered)

        # ── NEW 상태 문서 처리 ────────────────────────────────────────────
        pending = self._db.get_documents_by_status(Status.NEW)
        logger.info("처리 대기: %d건", len(pending))

        for doc in pending:
            self._process_one(doc, stats)

        logger.info("사이클 완료 — %s", stats.summary())
        return stats

    def process_document(self, doc_id: int) -> bool:
        """특정 문서 ID 한 건 처리 (수동 재처리용). 성공 시 True."""
        doc = self._db.get_document_by_id(doc_id)
        if not doc:
            logger.error("문서 없음: id=%d", doc_id)
            return False
        stats = PipelineStats()
        self._process_one(doc, stats)
        return stats.indexed == 1

    # ------------------------------------------------------------------
    # 단일 문서 처리
    # ------------------------------------------------------------------
    def _process_one(self, doc, stats: PipelineStats) -> None:
        doc_id    = doc["id"]
        file_path = doc["file_path"]
        file_name = doc["file_name"]
        logger.info("처리 시작: [%d] %s", doc_id, file_name)
        stats.processed += 1

        try:
            # ── Step 1: PDF 분류 분석 ─────────────────────────────────────
            profile = self._step_analyze(doc_id, file_path)
            if profile is None:
                stats.failed += 1
                return

            # ── Step 2: OCR / 텍스트 추출 ────────────────────────────────
            strategy = self._selector.select(profile)
            text, quality = self._step_ocr(doc_id, file_path, file_name, strategy)
            if text is None:
                stats.failed += 1
                return

            # ── Step 2.5: 표·그림 자산 추출 및 참조 태그 삽입 ────────────
            text = self._step_assets(doc_id, file_path, text, profile.source_type)

            # ── Step 3: 전처리 ────────────────────────────────────────────
            prep = self._preproc.process(text, quality)
            _digital_types = (SourceType.DIGITAL, SourceType.DOCX, SourceType.PPTX)
            self._db.update_status(
                doc_id,
                Status.TEXT_EXTRACTED if profile.source_type in _digital_types
                else Status.OCR_DONE,
                ocr_quality_score=quality,
            )

            if not prep.is_ready():
                self._tracker.mark_review_required(doc_id, prep.reason)
                stats.review += 1
                return

            self._db.update_status(doc_id, Status.TEXT_READY)
            self._db.log_step(doc_id, "PREPROCESS", "SUCCESS",
                              f"{prep.char_count}자, {prep.line_count}줄")

            # ── Step 4: 업로드 (재시도 포함) ──────────────────────────────
            self._db.update_status(doc_id, Status.UPLOAD_REQUESTED)
            upload_result = self._step_upload(doc_id, file_name, prep.text)
            if upload_result is None:
                stats.failed += 1
                return
            self._tracker.mark_uploaded(doc_id, upload_result)

            # ── Step 5: 임베딩 (재시도 포함) ─────────────────────────────
            ok = self._step_embed(doc_id, upload_result.location)
            if not ok:
                stats.failed += 1
                return
            self._tracker.mark_indexed(doc_id)
            stats.indexed += 1
            logger.info("처리 완료: [%d] %s → INDEXED", doc_id, file_name)

        except Exception as e:
            logger.exception("예상치 못한 오류 [doc %d]: %s", doc_id, e)
            self._tracker.mark_failed(doc_id, "PIPELINE", str(e))
            stats.failed += 1
            stats.errors.append(f"{file_name}: {e}")

    # ------------------------------------------------------------------
    # Step 구현
    # ------------------------------------------------------------------
    def _step_analyze(self, doc_id: int, file_path: str):
        self._db.update_status(doc_id, Status.ANALYZING)
        try:
            profile = self._analyzer.analyze(file_path)
            self._db.update_analysis(
                doc_id,
                source_type=profile.source_type,
                layout_type=profile.layout_type,
                detected_languages=profile.detected_languages,
                has_formula=profile.has_formula,
                ocr_strategy=self._selector.select(profile),
            )
            self._db.log_step(doc_id, "ANALYZE", "SUCCESS", str(profile))
            return profile
        except Exception as e:
            self._tracker.mark_failed(doc_id, "ANALYZE", str(e))
            return None

    def _step_ocr(self, doc_id: int, file_path: str, file_name: str, strategy: str):
        try:
            text, quality = self._ocr.run(file_path, strategy)
            self._db.log_step(doc_id, "OCR", "SUCCESS",
                              f"strategy={strategy} quality={quality:.3f} chars={len(text)}")
            return text, quality
        except Exception as e:
            self._tracker.mark_failed(doc_id, "OCR", str(e))
            return None, None

    def _step_assets(
        self,
        doc_id:      int,
        file_path:   str,
        text:        str,
        source_type: str,
    ) -> str:
        """표·그림 자산 추출, OCR, DB 저장, 텍스트 참조 태그 삽입.

        실패해도 파이프라인을 멈추지 않고 원본 텍스트를 그대로 반환.
        """
        try:
            _digital_types = (SourceType.DIGITAL, SourceType.DOCX, SourceType.PPTX)
            if source_type in _digital_types:
                new_text, n = self._assets.process_digital(file_path, doc_id)
            else:
                page_texts = self._ocr.last_page_texts or [text]
                new_text, n = self._assets.process_scanned(file_path, doc_id, page_texts)

            if n > 0:
                self._db.log_step(doc_id, "ASSETS", "SUCCESS",
                                  f"자산 {n}건 추출 및 참조 태그 삽입")
                return new_text
            return text
        except Exception as e:
            logger.warning("자산 처리 실패 (doc_id=%d), 원본 텍스트 유지: %s", doc_id, e)
            self._db.log_step(doc_id, "ASSETS", "WARN", str(e))
            return text

    def _step_upload(self, doc_id: int, file_name: str, text: str):
        try:
            return self._retry.run(
                lambda: self._api.upload_text(file_name, text),
                doc_id=doc_id,
                step="UPLOAD",
            )
        except RetryExhausted as e:
            self._tracker.mark_failed(doc_id, "UPLOAD", str(e))
            return None

    def _step_embed(self, doc_id: int, location: str) -> bool:
        try:
            self._retry.run(
                lambda: self._api.embed_document(location),
                doc_id=doc_id,
                step="EMBED",
            )
            return True
        except RetryExhausted as e:
            self._tracker.mark_failed(doc_id, "EMBED", str(e))
            return False


# 싱글턴
pipeline = Pipeline()
