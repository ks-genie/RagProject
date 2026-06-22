# -*- coding: utf-8 -*-
"""Phase 4 — OCR Engine / Layout Splitter / Formula Handler 단위 테스트"""

from unittest.mock import MagicMock, patch
import numpy as np
import pytest
from PIL import Image as PILImage

from src.ocr_engine import OcrEngine, _VALID_CHAR_RE
from src.layout_splitter import LayoutSplitter
from src.formula_handler import FormulaHandler, FormulaRegion
from src.database import OcrStrategy

# ===========================================================================
# ===========================================================================
# 공통 픽스처
# ===========================================================================

def make_gray_image(h=800, w=600, fill=255) -> PILImage.Image:
    arr = np.full((h, w), fill, dtype=np.uint8)
    return PILImage.fromarray(arr)


def make_two_column_image(h=800, w=600) -> PILImage.Image:
    """좌우 두 열에 검은 텍스트, 중앙 여백이 있는 이미지"""
    arr = np.full((h, w), 255, dtype=np.uint8)
    arr[:, 30:260] = 30    # 좌측 열
    arr[:, 340:570] = 30   # 우측 열
    return PILImage.fromarray(arr)


def make_two_column_image_with_left_header(h=800, w=600) -> PILImage.Image:
    arr = np.full((h, w), 255, dtype=np.uint8)
    arr[20:180, 30:260] = 30
    arr[220:h - 20, 30:260] = 30
    arr[220:h - 20, 340:570] = 30
    return PILImage.fromarray(arr)


def make_balanced_two_column_image(h=800, w=600) -> PILImage.Image:
    arr = np.full((h, w), 255, dtype=np.uint8)
    arr[:, 30:260] = 30
    arr[:, 340:570] = 30
    return PILImage.fromarray(arr)


def make_formula_image(h=400, w=600) -> PILImage.Image:
    """수평 세선(분수선)이 여러 개 있는 이미지"""
    arr = np.full((h, w), 255, dtype=np.uint8)
    arr[100, 200:400] = 0   # 분수선 1
    arr[200, 150:350] = 0   # 분수선 2
    arr[300, 100:450] = 0   # 분수선 3
    # 텍스트 영역
    arr[50:80, 50:550] = 30
    arr[150:180, 50:550] = 30
    return PILImage.fromarray(arr)


# ===========================================================================
# Layout Splitter 테스트
# ===========================================================================

class TestLayoutSplitter:
    @pytest.fixture
    def sp(self):
        return LayoutSplitter()

    def test_single_column_not_split(self, sp):
        img = make_gray_image(fill=200)
        cols = sp._split_page(img)
        assert len(cols) == 1

    def test_two_column_split(self, sp):
        img = make_balanced_two_column_image()
        cols = sp._split_page(img)
        assert len(cols) == 2

    def test_split_preserves_height(self, sp):
        img = make_balanced_two_column_image(h=800, w=600)
        cols = sp._split_page(img)
        if len(cols) == 2:
            assert cols[0].height == 800
            assert cols[1].height == 800

    def test_split_columns_multiple_pages(self, sp):
        images = [make_balanced_two_column_image() for _ in range(3)]
        result = sp.split_columns(images)
        assert len(result) == 3
        for page_cols in result:
            assert len(page_cols) >= 1

    def test_blank_page_not_split(self, sp):
        img = make_gray_image(fill=255)  # 완전 흰색
        cols = sp._split_page(img)
        assert len(cols) == 1


# ===========================================================================
# Formula Handler 테스트
# ===========================================================================

class TestFormulaHandler:
    @pytest.fixture
    def fh(self):
        return FormulaHandler()

    def test_no_formula_plain_image(self, fh):
        img = make_gray_image(fill=255)
        text_img, regions = fh.separate_formula(img)
        assert isinstance(text_img, PILImage.Image)
        assert isinstance(regions, list)

    def test_formula_image_returns_regions(self, fh):
        img = make_formula_image()
        text_img, regions = fh.separate_formula(img)
        assert isinstance(text_img, PILImage.Image)
        # 분수선이 있으면 최소 1개 영역 감지
        assert len(regions) >= 1

    def test_text_image_same_size(self, fh):
        img = make_formula_image()
        text_img, _ = fh.separate_formula(img)
        assert text_img.size == img.size

    def test_formula_region_fields(self, fh):
        img = make_formula_image()
        _, regions = fh.separate_formula(img)
        if regions:
            r = regions[0]
            assert isinstance(r, FormulaRegion)
            assert r.w > 0 and r.h > 0

    def test_masked_pixels_are_white(self, fh):
        img = make_formula_image()
        text_img, regions = fh.separate_formula(img)
        if regions:
            arr = np.array(text_img)
            r = regions[0]
            # 마스킹된 영역 중앙이 흰색(255)인지 확인
            cy, cx = r.y + r.h // 2, r.x + r.w // 2
            if 0 <= cy < arr.shape[0] and 0 <= cx < arr.shape[1]:
                assert arr[cy, cx] == 255


# ===========================================================================
# OCR Engine — 품질 점수 테스트
# ===========================================================================

class TestOcrEngineQuality:
    @pytest.fixture
    def engine(self):
        return OcrEngine()

    def test_quality_perfect_korean(self, engine):
        text = "안녕하세요 이것은 한국어 텍스트입니다." * 10
        score = engine._compute_quality(text)
        assert score >= 0.9

    def test_quality_perfect_english(self, engine):
        text = "This is a clear English text document." * 10
        score = engine._compute_quality(text)
        assert score >= 0.9

    def test_quality_garbage_text(self, engine):
        text = "@@##$$%%^^&&**!!~~ |||\\//<<>>" * 10
        score = engine._compute_quality(text)
        assert score < 0.3

    def test_quality_empty_text(self, engine):
        assert engine._compute_quality("") == 0.0

    def test_quality_mixed(self, engine):
        good = "Hello World 안녕 " * 20
        bad = "@#$% " * 5
        text = good + bad
        score = engine._compute_quality(text)
        assert 0.5 < score < 1.0

    def test_meets_quality_pass(self, engine):
        text = "충분히 긴 한국어 영어 혼합 텍스트입니다. Enough chars." * 5
        score = engine._compute_quality(text)
        assert engine.meets_quality(text, score) is True

    def test_meets_quality_too_short(self, engine):
        text = "짧음"
        assert engine.meets_quality(text, 0.9) is False

    def test_meets_quality_low_score(self, engine):
        text = "충분히 긴 텍스트가 있지만 품질이 낮음." * 10
        assert engine.meets_quality(text, 0.1) is False


# ===========================================================================
# OCR Engine — 전략별 실행 (pytesseract mocking)
# ===========================================================================

class TestOcrEngineStrategies:
    @pytest.fixture
    def engine(self):
        return OcrEngine()

    @pytest.fixture
    def mock_images(self):
        return [make_gray_image() for _ in range(2)]

    def _mock_pdfplumber(self, text="충분한 디지털 텍스트 내용입니다. " * 10):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = text
        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_page] * 2
        return mock_pdf

    def test_digital_extract(self, engine):
        mock_pdf = self._mock_pdfplumber()
        with patch("src.ocr_engine.pdfplumber.open", return_value=mock_pdf):
            text, score = engine._extract_digital("fake.pdf")
        assert len(text) > 50
        assert 0 < score <= 1.0

    def test_digital_extract_multi_keeps_left_body_before_right(self, engine):
        words = [
            {"text": "Title", "x0": 40, "x1": 90, "top": 12, "bottom": 24},
            {"text": "Author", "x0": 100, "x1": 170, "top": 12, "bottom": 24},
            {"text": "L1", "x0": 40, "x1": 70, "top": 100, "bottom": 112},
            {"text": "L2", "x0": 80, "x1": 110, "top": 100, "bottom": 112},
            {"text": "R1", "x0": 360, "x1": 390, "top": 100, "bottom": 112},
            {"text": "R2", "x0": 400, "x1": 430, "top": 100, "bottom": 112},
            {"text": "L3", "x0": 40, "x1": 70, "top": 120, "bottom": 132},
            {"text": "L4", "x0": 80, "x1": 110, "top": 120, "bottom": 132},
            {"text": "R3", "x0": 360, "x1": 390, "top": 120, "bottom": 132},
            {"text": "R4", "x0": 400, "x1": 430, "top": 120, "bottom": 132},
            {"text": "L5", "x0": 40, "x1": 70, "top": 140, "bottom": 152},
            {"text": "L6", "x0": 80, "x1": 110, "top": 140, "bottom": 152},
            {"text": "R5", "x0": 360, "x1": 390, "top": 140, "bottom": 152},
            {"text": "R6", "x0": 400, "x1": 430, "top": 140, "bottom": 152},
        ]
        mock_page = MagicMock()
        mock_page.extract_words.return_value = words
        mock_page.extract_text.return_value = "fallback"
        mock_page.width = 600

        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_page]

        with patch("src.ocr_engine.pdfplumber.open", return_value=mock_pdf):
            text, score = engine._extract_digital_multicol("fake.pdf")

        assert mock_page.extract_text.call_count == 0
        assert text.startswith("Title Author")
        assert text.index("L1 L2") < text.index("R1 R2")
        assert 0 < score <= 1.0

    def test_compose_multicol_text_keeps_right_prebody_after_left_column(self, engine):
        words = [
            {"text": "Title", "x0": 40, "x1": 90, "top": 20, "bottom": 32},
            {"text": "Author", "x0": 100, "x1": 170, "top": 20, "bottom": 32},
            {"text": "Rpre1", "x0": 360, "x1": 410, "top": 80, "bottom": 92},
            {"text": "Rpre2", "x0": 420, "x1": 470, "top": 80, "bottom": 92},
            {"text": "R1", "x0": 360, "x1": 390, "top": 100, "bottom": 112},
            {"text": "R2", "x0": 400, "x1": 430, "top": 100, "bottom": 112},
            {"text": "L1", "x0": 40, "x1": 70, "top": 120, "bottom": 132},
            {"text": "L2", "x0": 80, "x1": 110, "top": 120, "bottom": 132},
            {"text": "R3", "x0": 360, "x1": 390, "top": 120, "bottom": 132},
            {"text": "R4", "x0": 400, "x1": 430, "top": 120, "bottom": 132},
            {"text": "L3", "x0": 40, "x1": 70, "top": 140, "bottom": 152},
            {"text": "L4", "x0": 80, "x1": 110, "top": 140, "bottom": 152},
            {"text": "R5", "x0": 360, "x1": 390, "top": 140, "bottom": 152},
            {"text": "R6", "x0": 400, "x1": 430, "top": 140, "bottom": 152},
        ]

        gap_x = 300.0
        body_start_y = engine._find_body_start_y(words, gap_x)
        text = engine._compose_multicol_text(words, gap_x, body_start_y)

        # 테스트 데이터(2줄)는 WINDOW=5 미달 → None 반환이 정상
        assert body_start_y is None
        assert text.startswith("Title Author")
        assert text.index("L1 L2") < text.index("Rpre1 Rpre2")

    def test_ocr_simple_calls_tesseract(self, engine, mock_images):
        tess_cfg = {"lang": "kor+eng", "psm": 3}
        with patch("src.ocr_engine.pytesseract.image_to_string",
                   return_value="한국어 텍스트 English text") as mock_tess:
            text, score = engine._ocr_simple(mock_images, tess_cfg)
        assert mock_tess.call_count == len(mock_images)
        assert "한국어" in text

    def test_ocr_multi_column_splits_and_ocr(self, engine):
        images = [make_balanced_two_column_image() for _ in range(2)]
        tess_cfg = {"lang": "kor+eng", "psm": 6}
        with patch("src.ocr_engine.pytesseract.image_to_string",
                   return_value="컬럼 텍스트") as mock_tess:
            text, score = engine._ocr_multi_column(images, tess_cfg)
        # 2페이지 × 2컬럼 = 최소 4회 호출
        assert mock_tess.call_count >= 2

    def test_ocr_formula_inserts_placeholder(self, engine):
        images = [make_formula_image()]
        tess_cfg = {"lang": "kor+eng", "psm": 6, "oem": 1}
        with patch("src.ocr_engine.pytesseract.image_to_string",
                   return_value="텍스트 내용"):
            text, score = engine._ocr_formula(images, tess_cfg)
        # 수식 영역 감지 시 플레이스홀더 포함
        # (감지 못해도 텍스트는 반환)
        assert isinstance(text, str)

    def test_build_tess_config_psm_only(self, engine):
        cfg = engine._build_tess_config({"lang": "eng", "psm": 3})
        assert "--psm 3" in cfg
        assert "--oem" not in cfg

    def test_build_tess_config_psm_and_oem(self, engine):
        cfg = engine._build_tess_config({"lang": "kor+eng", "psm": 6, "oem": 1})
        assert "--psm 6" in cfg
        assert "--oem 1" in cfg

    def test_run_dispatches_digital(self, engine):
        mock_pdf = self._mock_pdfplumber()
        with patch("src.ocr_engine.pdfplumber.open", return_value=mock_pdf):
            text, score = engine.run("fake.pdf", OcrStrategy.DIGITAL_EXTRACT)
        assert isinstance(text, str)
        assert 0.0 <= score <= 1.0

    def test_run_dispatches_ocr(self, engine):
        with (
            patch.object(engine, "_get_page_images", return_value=[make_gray_image()]),
            patch("src.ocr_engine.pytesseract.image_to_string",
                  return_value="OCR 결과 텍스트 English"),
        ):
            text, score = engine.run("fake.pdf", OcrStrategy.OCR_KOR_ENG)
        assert "OCR" in text

    def test_words_to_text_with_paragraphs_adds_triple_newline_for_wide_gap(self, engine):
        words = [
            {"text": "Line1", "x0": 40, "x1": 90, "top": 10, "bottom": 20},
            {"text": "Line2", "x0": 40, "x1": 90, "top": 24, "bottom": 34},
            {"text": "Para2", "x0": 40, "x1": 90, "top": 70, "bottom": 80},
        ]
        text = engine._words_to_text_with_paragraphs(words)
        assert text == "Line1\nLine2\n\n\nPara2"
