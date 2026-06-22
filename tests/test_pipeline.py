# -*- coding: utf-8 -*-
"""Phase 7·8 — StatusTracker / RetryHandler / Pipeline 단위 테스트"""

import pytest
from unittest.mock import MagicMock, patch, call
from pathlib import Path

from src.api_client import UploadResult, ApiError
from src.database import DatabaseManager, Status
from src.pdf_analyzer import PDFProfile
from src.database import SourceType, LayoutType, OcrStrategy
from src.preprocessor import PreprocessResult
from src.retry_handler import RetryHandler, RetryExhausted
from src.status_tracker import StatusTracker
from src.pipeline import Pipeline, PipelineStats


# ===========================================================================
# 공통 픽스처
# ===========================================================================

@pytest.fixture
def db(tmp_path):
    m = DatabaseManager(tmp_path / "test.db")
    m.initialize()
    return m


@pytest.fixture
def doc_id(db):
    return db.register_document("/path/doc.pdf", "doc.pdf", "abc123")


def _upload_result(doc_id="uid-1", location="custom-documents/raw-doc.json"):
    return UploadResult(doc_id=doc_id, location=location, title="doc.pdf", word_count=50)


# ===========================================================================
# StatusTracker 테스트
# ===========================================================================

class TestStatusTracker:

    @pytest.fixture
    def tracker(self, db):
        mock_api = MagicMock()
        return StatusTracker(database=db, client=mock_api)

    def test_mark_uploaded(self, tracker, db, doc_id):
        tracker.mark_uploaded(doc_id, _upload_result("uid-1"))
        doc = db.get_document_by_id(doc_id)
        assert doc["status"] == Status.UPLOADED
        assert doc["anythingllm_doc_id"] == "uid-1"

    def test_mark_uploaded_logs_step(self, tracker, db, doc_id):
        tracker.mark_uploaded(doc_id, _upload_result())
        logs = db.get_logs(doc_id)
        assert any(l["step"] == "UPLOAD" and l["result"] == "SUCCESS" for l in logs)

    def test_mark_indexed_transitions(self, tracker, db, doc_id):
        tracker.mark_indexed(doc_id)
        doc = db.get_document_by_id(doc_id)
        assert doc["status"] == Status.INDEXED

    def test_mark_indexed_logs_step(self, tracker, db, doc_id):
        tracker.mark_indexed(doc_id)
        logs = db.get_logs(doc_id)
        assert any(l["step"] == "INDEXING" and l["result"] == "SUCCESS" for l in logs)

    def test_mark_failed(self, tracker, db, doc_id):
        tracker.mark_failed(doc_id, "UPLOAD", "connection refused")
        doc = db.get_document_by_id(doc_id)
        assert doc["status"] == Status.FAILED
        assert doc["failed_step"] == "UPLOAD"
        assert "connection refused" in doc["error_message"]

    def test_mark_review_required(self, tracker, db, doc_id):
        tracker.mark_review_required(doc_id, "텍스트 길이 부족: 10자")
        doc = db.get_document_by_id(doc_id)
        assert doc["status"] == Status.REVIEW_REQUIRED

    def test_verify_indexed_true(self, tracker):
        tracker._api.document_exists.return_value = True
        assert tracker.verify_indexed("uid-1") is True

    def test_verify_indexed_false(self, tracker):
        tracker._api.document_exists.return_value = False
        assert tracker.verify_indexed("missing-id") is False


# ===========================================================================
# RetryHandler 테스트
# ===========================================================================

class TestRetryHandler:

    @pytest.fixture
    def handler(self, db):
        return RetryHandler(database=db)

    def _no_sleep(self, _):
        pass  # 테스트에서 실제 대기 없음

    def test_success_first_try(self, handler, db, doc_id):
        result = handler.run(lambda: 42, doc_id=doc_id, step="TEST",
                             sleep_fn=self._no_sleep)
        assert result == 42

    def test_success_second_try(self, handler, db, doc_id):
        calls = [0]
        def flaky():
            calls[0] += 1
            if calls[0] < 2:
                raise ApiError("임시 오류")
            return "ok"

        result = handler.run(flaky, doc_id=doc_id, step="TEST",
                             sleep_fn=self._no_sleep)
        assert result == "ok"
        assert calls[0] == 2

    def test_retry_increments_count(self, handler, db, doc_id):
        calls = [0]
        def flaky():
            calls[0] += 1
            if calls[0] < 3:
                raise ApiError("오류")
            return "done"

        handler.run(flaky, doc_id=doc_id, step="TEST", sleep_fn=self._no_sleep)
        doc = db.get_document_by_id(doc_id)
        assert doc["retry_count"] == 2  # 2번 실패 후 3번째 성공

    def test_exhausted_raises(self, handler, db, doc_id):
        with pytest.raises(RetryExhausted) as exc:
            handler.run(lambda: (_ for _ in ()).throw(ApiError("항상 실패")),
                       doc_id=doc_id, step="UPLOAD",
                       sleep_fn=self._no_sleep)
        assert "UPLOAD" in str(exc.value)

    def test_exhausted_after_max_attempts(self, handler, db, doc_id):
        attempt_count = [0]
        def always_fail():
            attempt_count[0] += 1
            raise ApiError("실패")

        with pytest.raises(RetryExhausted):
            handler.run(always_fail, doc_id=doc_id, step="TEST",
                       sleep_fn=self._no_sleep)

        # max_attempts=3 이므로 3번 시도
        assert attempt_count[0] == 3

    def test_sleep_called_between_retries(self, handler, db, doc_id):
        sleep_calls = []
        calls = [0]
        def flaky():
            calls[0] += 1
            if calls[0] < 3:
                raise ApiError("오류")
            return "ok"

        handler.run(flaky, doc_id=doc_id, step="TEST",
                    sleep_fn=lambda s: sleep_calls.append(s))
        assert len(sleep_calls) == 2
        assert sleep_calls[0] == 60    # 1분
        assert sleep_calls[1] == 120   # 2분

    def test_no_sleep_on_success(self, handler, db, doc_id):
        sleep_calls = []
        handler.run(lambda: "ok", doc_id=doc_id, step="TEST",
                    sleep_fn=lambda s: sleep_calls.append(s))
        assert sleep_calls == []


# ===========================================================================
# Pipeline 테스트
# ===========================================================================

def _make_pipeline(db, **overrides):
    """테스트용 Pipeline — 모든 외부 의존성 mock"""
    mock_scanner  = MagicMock()
    mock_analyzer = MagicMock()
    mock_selector = MagicMock()
    mock_ocr      = MagicMock()
    mock_preproc  = MagicMock()
    mock_api      = MagicMock()
    mock_tracker  = StatusTracker(database=db, client=mock_api)
    mock_retry    = RetryHandler(database=db)

    # 기본 동작 설정
    mock_scanner.scan.return_value = MagicMock(registered=[], skipped_duplicate=[],
                                               requeued=[], errors=[])
    mock_analyzer.analyze.return_value = PDFProfile(
        source_type=SourceType.DIGITAL,
        layout_type=LayoutType.SINGLE_COLUMN,
        detected_languages=["kor", "eng"],
        has_formula=False,
        page_count=5,
    )
    mock_selector.select.return_value = OcrStrategy.DIGITAL_EXTRACT
    mock_ocr.run.return_value = ("충분한 텍스트 내용입니다. " * 20, 0.92)
    mock_preproc.process.return_value = PreprocessResult(
        text="정제된 텍스트. " * 20,
        status=Status.TEXT_READY,
        char_count=200,
        line_count=5,
    )
    mock_api.upload_text.return_value = _upload_result()
    mock_api.embed_document.return_value = None  # 성공 (None 반환)

    kwargs = dict(
        database=db,
        scanner=mock_scanner,
        pdf_analyzer=mock_analyzer,
        strat_sel=mock_selector,
        ocr=mock_ocr,
        preproc=mock_preproc,
        client=mock_api,
        tracker=mock_tracker,
        retrier=RetryHandler(database=db),
    )
    kwargs.update(overrides)
    return Pipeline(**kwargs), {
        "scanner": mock_scanner, "analyzer": mock_analyzer,
        "selector": mock_selector, "ocr": mock_ocr,
        "preproc": mock_preproc, "api": mock_api,
    }


class TestPipeline:

    @pytest.fixture(autouse=True)
    def no_sleep(self):
        """Pipeline 내부 RetryHandler의 실제 sleep 제거"""
        with patch("src.retry_handler.time.sleep"):
            yield

    def test_full_pipeline_success(self, db, doc_id):
        p, mocks = _make_pipeline(db)
        stats = p.run_once()
        doc = db.get_document_by_id(doc_id)
        assert doc["status"] == Status.INDEXED
        assert stats.indexed == 1
        assert stats.failed == 0

    def test_pipeline_review_required(self, db, doc_id):
        p, mocks = _make_pipeline(db)
        mocks["preproc"].process.return_value = PreprocessResult(
            text="짧음",
            status=Status.REVIEW_REQUIRED,
            reason="텍스트 길이 부족: 2자",
            char_count=2,
        )
        stats = p.run_once()
        doc = db.get_document_by_id(doc_id)
        assert doc["status"] == Status.REVIEW_REQUIRED
        assert stats.review == 1
        assert stats.indexed == 0

    def test_pipeline_analyze_failure(self, db, doc_id):
        p, mocks = _make_pipeline(db)
        mocks["analyzer"].analyze.side_effect = Exception("PDF 손상")
        stats = p.run_once()
        doc = db.get_document_by_id(doc_id)
        assert doc["status"] == Status.FAILED
        assert stats.failed == 1

    def test_pipeline_ocr_failure(self, db, doc_id):
        p, mocks = _make_pipeline(db)
        mocks["ocr"].run.side_effect = Exception("Tesseract 오류")
        stats = p.run_once()
        doc = db.get_document_by_id(doc_id)
        assert doc["status"] == Status.FAILED

    def test_pipeline_upload_retry_exhausted(self, db, doc_id):
        p, mocks = _make_pipeline(db)
        mocks["api"].upload_text.side_effect = ApiError("서버 오류")
        stats = p.run_once()
        doc = db.get_document_by_id(doc_id)
        assert doc["status"] == Status.FAILED
        assert doc["retry_count"] >= 2  # 최소 2회 재시도

    def test_pipeline_embed_failure(self, db, doc_id):
        p, mocks = _make_pipeline(db)
        mocks["api"].embed_document.side_effect = ApiError("임베딩 실패")
        stats = p.run_once()
        doc = db.get_document_by_id(doc_id)
        assert doc["status"] == Status.FAILED

    def test_pipeline_status_sequence_logged(self, db, doc_id):
        p, mocks = _make_pipeline(db)
        p.run_once()
        logs = db.get_logs(doc_id)
        steps = [l["step"] for l in logs]
        assert "ANALYZE"    in steps
        assert "OCR"        in steps
        assert "PREPROCESS" in steps
        assert "UPLOAD"     in steps
        assert "INDEXING"   in steps

    def test_process_document_by_id(self, db, doc_id):
        p, mocks = _make_pipeline(db)
        result = p.process_document(doc_id)
        assert result is True
        assert db.get_document_by_id(doc_id)["status"] == Status.INDEXED

    def test_process_nonexistent_document(self, db):
        p, _ = _make_pipeline(db)
        result = p.process_document(9999)
        assert result is False

    def test_stats_summary_format(self, db, doc_id):
        p, _ = _make_pipeline(db)
        stats = p.run_once()
        summary = stats.summary()
        assert "INDEXED" in summary
        assert "FAILED"  in summary
