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

# 이 모듈 전용 로거를 생성한다. 로그 메시지에 파일명이 자동으로 붙어 디버깅이 편리해진다.
logger = logging.getLogger(__name__)


@dataclass
class PipelineStats:
    """파이프라인 한 사이클의 처리 결과를 집계하는 통계 데이터 클래스.

    각 필드는 해당 결과 유형의 문서 건수를 나타낸다.

    Attributes:
        scanned:   파일 탐지 단계에서 새로 발견되어 DB에 등록된 파일 수.
        processed: 이번 사이클에서 처리를 시도한 문서 수.
        indexed:   최종적으로 INDEXED(임베딩 완료) 상태가 된 문서 수.
        review:    품질 기준 미달로 REVIEW_REQUIRED 상태가 된 문서 수.
        failed:    오류가 발생해 FAILED 상태가 된 문서 수.
        skipped:   처리 대상에서 제외된 문서 수 (현재 파이프라인에서 직접 증가시키지 않음).
        errors:    오류가 발생한 문서의 파일명과 오류 메시지를 담은 문자열 목록.
    """
    scanned:       int = 0
    processed:     int = 0
    indexed:       int = 0
    review:        int = 0
    failed:        int = 0
    skipped:       int = 0
    # errors는 빈 리스트로 초기화된다. field(default_factory=list)를 사용하는 이유는
    # 파이썬에서 dataclass의 기본값으로 가변(mutable) 객체(리스트 등)를 직접 쓰면
    # 모든 인스턴스가 동일한 객체를 공유하는 버그가 생기기 때문이다.
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        """처리 결과를 한 줄 문자열로 요약하여 반환한다.

        반환값:
            처리 건수, INDEXED/REVIEW/FAILED 수, 스킵 수, 신규 탐지 수를 담은 문자열.
        """
        return (
            f"처리: {self.processed} | INDEXED: {self.indexed} | "
            f"REVIEW: {self.review} | FAILED: {self.failed} | "
            f"스킵: {self.skipped} | 신규 탐지: {self.scanned}"
        )


class Pipeline:
    """문서 처리 파이프라인의 핵심 오케스트레이터 클래스.

    파일 탐지부터 최종 임베딩(INDEXED)까지 모든 처리 단계를 순서대로
    조율(orchestrate)한다. 각 단계는 별도 모듈(analyzer, ocr, api 등)이 담당하며,
    이 클래스는 단계들을 연결하고 오류를 처리하는 역할만 한다.

    의존성 주입(Dependency Injection) 패턴을 사용하므로, 테스트 시 각 컴포넌트를
    가짜(mock) 객체로 쉽게 교체할 수 있다.

    Args:
        database:   DB 입출력을 담당하는 DatabaseManager 인스턴스.
        scanner:    감시 디렉토리에서 신규 파일을 탐지하는 FileScanner 인스턴스.
        pdf_analyzer: PDF를 분석해 종류·레이아웃·언어 등을 파악하는 PDFAnalyzer 인스턴스.
        strat_sel:  분석 결과를 보고 OCR 전략을 결정하는 StrategySelector 인스턴스.
        ocr:        실제 OCR(광학 문자 인식) 및 텍스트 추출을 수행하는 OcrEngine 인스턴스.
        asset_proc: 표·그림 등의 자산(asset)을 추출하는 AssetPipeline 인스턴스.
        preproc:    추출된 텍스트를 정제·전처리하는 Preprocessor 인스턴스.
        client:     외부 API(업로드·임베딩)와 통신하는 ApiClient 인스턴스.
        tracker:    문서 상태 변경을 기록하는 StatusTracker 인스턴스.
        retrier:    API 호출 실패 시 재시도 로직을 담당하는 RetryHandler 인스턴스.

    인수를 생략하면 각 모듈에서 제공하는 기본 싱글턴 인스턴스가 사용된다.
    """

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
        # 인수가 None이면 각 모듈의 기본 싱글턴을 사용한다 (or 연산자 활용).
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
        """한 번의 처리 사이클을 실행하고 결과 통계를 반환한다.

        내부 동작:
          1. 감시 디렉토리를 스캔하여 신규 파일을 발견하면 DB에 NEW 상태로 등록한다.
          2. DB에서 NEW 상태의 문서 목록을 가져와 각각 전체 처리 파이프라인을 실행한다.

        반환값:
            처리 결과 통계를 담은 PipelineStats 객체.
        """
        stats = PipelineStats()

        # ── 파일 탐지 ────────────────────────────────────────────────────
        # 감시 디렉토리를 순회하여 새 파일을 발견하고 DB에 등록한다.
        scan = self._scanner.scan()
        stats.scanned = len(scan.registered)  # 이번에 새로 등록된 파일 수를 기록한다.

        # ── NEW 상태 문서 처리 ────────────────────────────────────────────
        # DB에서 아직 처리되지 않은(NEW) 문서 목록을 가져온다.
        pending = self._db.get_documents_by_status(Status.NEW)
        logger.info("처리 대기: %d건", len(pending))

        # 대기 중인 문서를 하나씩 순서대로 처리한다.
        for doc in pending:
            self._process_one(doc, stats)

        logger.info("사이클 완료 — %s", stats.summary())
        return stats

    def process_document(self, doc_id: int) -> bool:
        """특정 문서 ID 한 건을 수동으로 처리한다. 주로 실패 문서 재처리에 사용한다.

        Args:
            doc_id: 처리할 문서의 DB ID.

        반환값:
            처리에 성공해 INDEXED 상태가 되면 True, 그렇지 않으면 False.
        """
        doc = self._db.get_document_by_id(doc_id)
        if not doc:
            # DB에서 해당 ID를 찾지 못하면 즉시 실패를 반환한다.
            logger.error("문서 없음: id=%d", doc_id)
            return False
        stats = PipelineStats()
        self._process_one(doc, stats)
        # indexed가 1이면 성공적으로 처리된 것이다.
        return stats.indexed == 1

    # ------------------------------------------------------------------
    # 단일 문서 처리
    # ------------------------------------------------------------------
    def _process_one(self, doc, stats: PipelineStats) -> None:
        """문서 한 건을 Step 1~5까지 순서대로 처리한다.

        각 Step이 실패하면 즉시 중단하고 FAILED로 기록한다.
        예상치 못한 예외도 최상위에서 잡아 FAILED 처리한다.

        Args:
            doc:   DB에서 가져온 문서 정보 딕셔너리 (id, file_path, file_name 등 포함).
            stats: 이번 사이클의 통계 객체. 처리 결과에 따라 카운터를 증가시킨다.
        """
        doc_id    = doc["id"]
        file_path = doc["file_path"]
        file_name = doc["file_name"]
        logger.info("처리 시작: [%d] %s", doc_id, file_name)
        stats.processed += 1  # 시도 건수를 증가시킨다.

        try:
            # ── Step 1: PDF 분류 분석 ─────────────────────────────────────
            # PDF의 종류(디지털/스캔), 레이아웃, 언어 등을 파악한다.
            profile = self._step_analyze(doc_id, file_path)
            if profile is None:
                # 분석 실패 시 이 문서의 처리를 중단한다.
                stats.failed += 1
                return

            # ── Step 2: OCR / 텍스트 추출 ────────────────────────────────
            # Step 1의 분석 결과를 바탕으로 가장 적합한 OCR 전략을 선택한다.
            strategy = self._selector.select(profile)
            text, quality = self._step_ocr(doc_id, file_path, file_name, strategy)
            if text is None:
                # 텍스트 추출 실패 시 처리를 중단한다.
                stats.failed += 1
                return

            # ── Step 2.5: 표·그림 자산 추출 및 참조 태그 삽입 ────────────
            # 추출된 텍스트에서 표나 그림의 위치를 파악하고 참조 태그를 삽입한다.
            # 이 단계가 실패해도 원본 텍스트를 유지하며 파이프라인을 계속 진행한다.
            text = self._step_assets(doc_id, file_path, text, profile.source_type)

            # ── Step 3: 전처리 ────────────────────────────────────────────
            # 추출된 텍스트를 정제하고 품질 기준을 확인한다.
            prep = self._preproc.process(text, quality)

            # 문서 종류에 따라 상태를 구분한다.
            # 디지털 PDF/Word/PPT는 TEXT_EXTRACTED, 스캔 문서는 OCR_DONE으로 기록한다.
            _digital_types = (SourceType.DIGITAL, SourceType.DOCX, SourceType.PPTX)
            self._db.update_status(
                doc_id,
                Status.TEXT_EXTRACTED if profile.source_type in _digital_types
                else Status.OCR_DONE,
                ocr_quality_score=quality,
            )

            if not prep.is_ready():
                # 전처리 결과가 품질 기준을 통과하지 못하면 사람이 검토해야 한다.
                self._tracker.mark_review_required(doc_id, prep.reason)
                stats.review += 1
                return  # 이 문서는 더 이상 자동 처리하지 않는다.

            # 전처리를 통과했으므로 TEXT_READY 상태로 변경하고 결과를 기록한다.
            self._db.update_status(doc_id, Status.TEXT_READY)
            self._db.log_step(doc_id, "PREPROCESS", "SUCCESS",
                              f"{prep.char_count}자, {prep.line_count}줄")

            # ── Step 4: 업로드 (재시도 포함) ──────────────────────────────
            # 전처리된 텍스트를 외부 API 서버에 업로드한다. 실패 시 자동으로 재시도한다.
            self._db.update_status(doc_id, Status.UPLOAD_REQUESTED)
            upload_result = self._step_upload(doc_id, file_name, prep.text)
            if upload_result is None:
                # 재시도를 모두 소진하고도 실패하면 처리를 중단한다.
                stats.failed += 1
                return
            # 업로드 성공 - 서버에서 반환된 위치(location) 등의 결과를 DB에 기록한다.
            self._tracker.mark_uploaded(doc_id, upload_result)

            # ── Step 5: 임베딩 (재시도 포함) ─────────────────────────────
            # 업로드된 텍스트로 벡터 임베딩을 생성한다. 실패 시 자동으로 재시도한다.
            ok = self._step_embed(doc_id, upload_result.location)
            if not ok:
                stats.failed += 1
                return
            # 모든 단계 성공 - 최종 INDEXED 상태로 기록한다.
            self._tracker.mark_indexed(doc_id)
            stats.indexed += 1
            logger.info("처리 완료: [%d] %s → INDEXED", doc_id, file_name)

        except Exception as e:
            # 위에서 처리하지 못한 예상치 못한 오류를 최상위에서 잡아 기록한다.
            # 이렇게 하면 하나의 문서 처리 실패가 전체 사이클을 멈추지 않는다.
            logger.exception("예상치 못한 오류 [doc %d]: %s", doc_id, e)
            self._tracker.mark_failed(doc_id, "PIPELINE", str(e))
            stats.failed += 1
            stats.errors.append(f"{file_name}: {e}")

    # ------------------------------------------------------------------
    # Step 구현
    # ------------------------------------------------------------------
    def _step_analyze(self, doc_id: int, file_path: str):
        """Step 1: PDF 파일을 분석하여 문서 프로파일을 생성하고 DB에 저장한다.

        문서 상태를 ANALYZING으로 변경한 뒤, PDF 분석기를 통해 문서의 종류,
        레이아웃, 언어, 수식 포함 여부 등을 파악한다.

        Args:
            doc_id:    처리 중인 문서의 DB ID.
            file_path: 분석할 PDF 파일의 경로.

        반환값:
            분석에 성공하면 문서 프로파일 객체, 실패하면 None.
        """
        # 처리 시작을 알리기 위해 상태를 ANALYZING으로 변경한다.
        self._db.update_status(doc_id, Status.ANALYZING)
        try:
            # PDF를 실제로 분석하여 프로파일(종류, 레이아웃, 언어 등)을 생성한다.
            profile = self._analyzer.analyze(file_path)
            # 분석 결과를 DB의 문서 레코드에 저장한다.
            self._db.update_analysis(
                doc_id,
                source_type=profile.source_type,          # 문서 종류 (디지털/스캔 등)
                layout_type=profile.layout_type,          # 레이아웃 종류 (단단/다단 등)
                detected_languages=profile.detected_languages,  # 감지된 언어 목록
                has_formula=profile.has_formula,          # 수식 포함 여부
                ocr_strategy=self._selector.select(profile),   # 선택된 OCR 전략
            )
            self._db.log_step(doc_id, "ANALYZE", "SUCCESS", str(profile))
            return profile
        except Exception as e:
            # 분석 실패 시 FAILED로 기록하고 None을 반환하여 상위 호출자가 중단할 수 있게 한다.
            self._tracker.mark_failed(doc_id, "ANALYZE", str(e))
            return None

    def _step_ocr(self, doc_id: int, file_path: str, file_name: str, strategy: str):
        """Step 2: 선택된 전략으로 파일에서 텍스트를 추출하고 품질 점수를 반환한다.

        Args:
            doc_id:    처리 중인 문서의 DB ID.
            file_path: 텍스트를 추출할 파일 경로.
            file_name: 로그 기록용 파일 이름.
            strategy:  Step 1에서 선택된 OCR 전략 이름 (예: "digital", "tesseract" 등).

        반환값:
            성공 시 (추출된 텍스트 문자열, 품질 점수 float) 튜플.
            실패 시 (None, None) 튜플.
        """
        try:
            # OCR 엔진을 실행하여 텍스트와 품질 점수를 함께 받는다.
            text, quality = self._ocr.run(file_path, strategy)
            self._db.log_step(doc_id, "OCR", "SUCCESS",
                              f"strategy={strategy} quality={quality:.3f} chars={len(text)}")
            return text, quality
        except Exception as e:
            self._tracker.mark_failed(doc_id, "OCR", str(e))
            # 실패를 표현하기 위해 두 값 모두 None으로 반환한다.
            return None, None

    def _step_assets(
        self,
        doc_id:      int,
        file_path:   str,
        text:        str,
        source_type: str,
    ) -> str:
        """Step 2.5: 문서에서 표·그림 자산을 추출하고, 텍스트에 참조 태그를 삽입한다.

        실패해도 파이프라인을 멈추지 않고 원본 텍스트를 그대로 반환한다.
        (이 단계는 선택적 보강 단계이므로 실패가 치명적이지 않다.)

        Args:
            doc_id:      처리 중인 문서의 DB ID.
            file_path:   원본 파일 경로.
            text:        OCR로 추출된 텍스트.
            source_type: 문서 종류 (SourceType 열거형 값).

        반환값:
            자산 추출에 성공하면 참조 태그가 삽입된 새 텍스트,
            실패하거나 추출된 자산이 없으면 원본 텍스트.
        """
        try:
            # 디지털 문서(PDF/Word/PPT)와 스캔 문서는 자산 추출 방식이 다르다.
            _digital_types = (SourceType.DIGITAL, SourceType.DOCX, SourceType.PPTX)
            if source_type in _digital_types:
                # 디지털 문서: 파일 자체에서 표·그림을 직접 추출한다.
                new_text, n = self._assets.process_digital(file_path, doc_id)
            else:
                # 스캔 문서: OCR 엔진이 저장해 둔 페이지별 텍스트를 활용해 자산을 추출한다.
                # last_page_texts가 없으면 전체 텍스트를 하나의 페이지로 간주한다.
                page_texts = self._ocr.last_page_texts or [text]
                new_text, n = self._assets.process_scanned(file_path, doc_id, page_texts)

            if n > 0:
                # 자산이 한 건 이상 추출되었으면 참조 태그가 포함된 새 텍스트를 사용한다.
                self._db.log_step(doc_id, "ASSETS", "SUCCESS",
                                  f"자산 {n}건 추출 및 참조 태그 삽입")
                return new_text
            # 추출된 자산이 없으면 원본 텍스트를 그대로 반환한다.
            return text
        except Exception as e:
            # 자산 처리 실패는 치명적 오류가 아니므로 경고만 기록하고 원본 텍스트를 유지한다.
            logger.warning("자산 처리 실패 (doc_id=%d), 원본 텍스트 유지: %s", doc_id, e)
            self._db.log_step(doc_id, "ASSETS", "WARN", str(e))
            return text

    def _step_upload(self, doc_id: int, file_name: str, text: str):
        """Step 4: 전처리된 텍스트를 외부 API에 업로드한다. 실패 시 자동으로 재시도한다.

        Args:
            doc_id:    처리 중인 문서의 DB ID.
            file_name: API 업로드 시 사용할 파일 이름.
            text:      업로드할 전처리 완료 텍스트.

        반환값:
            업로드에 성공하면 API가 반환한 결과 객체(location 등 포함),
            재시도를 모두 소진하면 None.
        """
        try:
            # retry_handler가 실패 시 자동으로 재시도한다.
            # lambda를 사용하여 실제 API 호출을 지연 평가(lazy evaluation)한다.
            return self._retry.run(
                lambda: self._api.upload_text(file_name, text),
                doc_id=doc_id,
                step="UPLOAD",
            )
        except RetryExhausted as e:
            # 모든 재시도 횟수를 소진한 경우 발생하는 예외를 처리한다.
            self._tracker.mark_failed(doc_id, "UPLOAD", str(e))
            return None

    def _step_embed(self, doc_id: int, location: str) -> bool:
        """Step 5: 업로드된 문서로 벡터 임베딩을 생성한다. 실패 시 자동으로 재시도한다.

        임베딩(embedding)이란 텍스트를 벡터(숫자 배열)로 변환하는 과정으로,
        이후 유사도 검색에 사용된다.

        Args:
            doc_id:   처리 중인 문서의 DB ID.
            location: Step 4 업로드 결과로 받은 서버 내 문서 위치(경로 또는 URL).

        반환값:
            임베딩에 성공하면 True, 재시도를 모두 소진하면 False.
        """
        try:
            # 업로드된 문서의 위치를 지정하여 임베딩 생성을 요청한다.
            self._retry.run(
                lambda: self._api.embed_document(location),
                doc_id=doc_id,
                step="EMBED",
            )
            return True
        except RetryExhausted as e:
            # 모든 재시도 횟수를 소진한 경우 실패로 처리한다.
            self._tracker.mark_failed(doc_id, "EMBED", str(e))
            return False


# 싱글턴 — 모듈 임포트 시 기본 설정으로 Pipeline 인스턴스를 하나만 생성해 공유한다.
# 다른 모듈에서 `from src.pipeline import pipeline` 으로 바로 사용할 수 있다.
pipeline = Pipeline()
