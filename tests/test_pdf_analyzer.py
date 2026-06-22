# -*- coding: utf-8 -*-
"""Phase 3 — PDF Analyzer / Strategy Selector 단위 테스트"""

from unittest.mock import MagicMock, patch, mock_open
import pytest
import numpy as np

from src.pdf_analyzer import PDFAnalyzer, PDFProfile, LANG_THRESHOLD, FORMULA_CHAR_THRESHOLD
from src.strategy_selector import StrategySelector, get_tesseract_config
from src.database import SourceType, LayoutType, OcrStrategy


@pytest.fixture
def analyzer():
    return PDFAnalyzer()


@pytest.fixture
def selector():
    return StrategySelector()


# ===========================================================================
# 언어 감지 테스트
# ===========================================================================

class TestLanguageDetection:

    def test_korean_text(self, analyzer):
        text = "안녕하세요. 이것은 한국어 텍스트입니다. " * 20
        langs = analyzer._detect_languages(text)
        assert "kor" in langs

    def test_english_text(self, analyzer):
        text = "This is an English document with many words. " * 20
        langs = analyzer._detect_languages(text)
        assert "eng" in langs

    def test_japanese_text(self, analyzer):
        text = "これは日本語のテキストです。ひらがなとカタカナが含まれています。" * 20
        langs = analyzer._detect_languages(text)
        assert "jpn" in langs

    def test_korean_english_mixed(self, analyzer):
        text = ("안녕하세요. Hello. 이것은 mixed 텍스트입니다. "
                "This contains both Korean and English. ") * 15
        langs = analyzer._detect_languages(text)
        assert "kor" in langs
        assert "eng" in langs

    def test_empty_text_returns_eng_default(self, analyzer):
        langs = analyzer._detect_languages("")
        assert langs == ["eng"]

    def test_short_text_returns_default(self, analyzer):
        langs = analyzer._detect_languages("hi")
        assert langs == ["eng"]


# ===========================================================================
# 수식 감지 테스트
# ===========================================================================

class TestFormulaDetection:

    def test_no_formula_plain_text(self, analyzer):
        text = "This is a plain text document without any mathematical formulas." * 20
        assert analyzer._detect_formula_digital(text) is False

    def test_formula_with_math_symbols(self, analyzer):
        # ∑ ∫ ∂ ∇ α β γ 등 수학 기호 포함
        math_text = "∑∫∂∇αβγδεζ" * 10
        padding = "normal text " * 50
        text = padding + math_text
        assert analyzer._detect_formula_digital(text) is True

    def test_formula_with_greek_letters(self, analyzer):
        text = "The equation α + β = γ is fundamental. " \
               "We have ∑ᵢ xᵢ = ∫f(x)dx. αβγδεζηθ" * 5
        padding = "regular words " * 30
        assert analyzer._detect_formula_digital(text + padding) is True

    def test_empty_text_no_formula(self, analyzer):
        assert analyzer._detect_formula_digital("") is False


# ===========================================================================
# 소스 유형 판별 테스트
# ===========================================================================

class TestSourceTypeDetection:

    def test_digital_pdf_sufficient_text(self, analyzer):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "충분한 텍스트가 있는 디지털 PDF 페이지입니다. " * 5

        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_page] * 3

        with patch("src.pdf_analyzer.pdfplumber.open", return_value=mock_pdf):
            source_type, text, avg, count = analyzer._detect_source_type("fake.pdf")

        assert source_type == SourceType.DIGITAL
        assert avg >= 50

    def test_scanned_pdf_no_text(self, analyzer):
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "짧음"  # 50자 미만

        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_page] * 2

        with patch("src.pdf_analyzer.pdfplumber.open", return_value=mock_pdf):
            source_type, text, avg, count = analyzer._detect_source_type("fake.pdf")

        assert source_type == SourceType.SCANNED

    def test_pdfplumber_failure_returns_scanned(self, analyzer):
        with patch("src.pdf_analyzer.pdfplumber.open", side_effect=Exception("corrupt")):
            source_type, text, avg, count = analyzer._detect_source_type("fake.pdf")
        assert source_type == SourceType.SCANNED


# ===========================================================================
# 레이아웃 분석 테스트
# ===========================================================================

class TestLayoutDetection:

    def test_single_column_digital(self, analyzer):
        # 단어가 페이지 전체에 고르게 분포 (중앙에도 단어 존재) — 10줄
        words = [
            {"x0": x, "x1": x + 50, "top": line * 20, "bottom": line * 20 + 14}
            for line in range(10)
            for x in range(50, 500, 55)  # 중앙 포함 고르게 분포
        ]
        mock_page = MagicMock()
        mock_page.extract_words.return_value = words
        mock_page.width = 600

        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_page] * 3

        with patch("src.pdf_analyzer.pdfplumber.open", return_value=mock_pdf):
            layout = analyzer._detect_layout_digital("fake.pdf")

        assert layout == LayoutType.SINGLE_COLUMN

    def test_multi_column_digital(self, analyzer):
        # 단어가 좌우 두 열에만 분포 (중앙에 단어 없음) — 10줄
        words = []
        for line in range(10):
            top = line * 20
            bottom = top + 14
            words += [
                {"x0": x, "x1": x + 40, "top": top, "bottom": bottom}
                for x in range(20, 220, 45)   # 좌측 열
            ]
            words += [
                {"x0": x, "x1": x + 40, "top": top, "bottom": bottom}
                for x in range(380, 580, 45)  # 우측 열 (중앙 240~360 비어 있음)
            ]

        mock_page = MagicMock()
        mock_page.extract_words.return_value = words
        mock_page.width = 600

        mock_pdf = MagicMock()
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)
        mock_pdf.pages = [mock_page] * 3

        with patch("src.pdf_analyzer.pdfplumber.open", return_value=mock_pdf):
            layout = analyzer._detect_layout_digital("fake.pdf")

        assert layout == LayoutType.MULTI_COLUMN

    def test_multi_column_scanned(self, analyzer):
        # 실제 스캔 문서 형태: 흰 배경(255) + 검은 텍스트(30)
        # 좌우에만 텍스트 존재, 중앙(250~350)은 빈 여백
        img_array = np.full((800, 600), 255, dtype=np.uint8)  # 흰 배경
        img_array[:, :250] = 30    # 좌측 열 텍스트
        img_array[:, 350:] = 30    # 우측 열 텍스트

        from PIL import Image as PILImage
        mock_images = [PILImage.fromarray(img_array)] * 3

        layout = analyzer._detect_layout_scanned(mock_images)
        assert layout == LayoutType.MULTI_COLUMN

    def test_single_column_scanned(self, analyzer):
        # 흰 배경 + 중앙 전체 폭에 텍스트 (단일 컬럼)
        img_array = np.full((800, 600), 255, dtype=np.uint8)  # 흰 배경
        img_array[:, 50:550] = 30  # 텍스트가 전체 너비에 걸쳐 존재

        from PIL import Image as PILImage
        mock_images = [PILImage.fromarray(img_array)] * 3

        layout = analyzer._detect_layout_scanned(mock_images)
        assert layout == LayoutType.SINGLE_COLUMN


# ===========================================================================
# Strategy Selector 테스트
# ===========================================================================

class TestStrategySelector:

    def _profile(self, source, layout, langs, formula):
        return PDFProfile(
            source_type=source,
            layout_type=layout,
            detected_languages=langs,
            has_formula=formula,
        )

    def test_digital_always_extract(self, selector):
        p = self._profile(SourceType.DIGITAL, LayoutType.SINGLE_COLUMN, ["kor", "eng"], False)
        assert selector.select(p) == OcrStrategy.DIGITAL_EXTRACT

    def test_digital_multi_uses_multicol_extract(self, selector):
        # 디지털 2단 문서는 컬럼 인식 추출 전략을 사용해야 함
        p = self._profile(SourceType.DIGITAL, LayoutType.MULTI_COLUMN, ["eng"], True)
        assert selector.select(p) == OcrStrategy.DIGITAL_EXTRACT_MULTI

    def test_scanned_single_eng(self, selector):
        p = self._profile(SourceType.SCANNED, LayoutType.SINGLE_COLUMN, ["eng"], False)
        assert selector.select(p) == OcrStrategy.OCR_ENG

    def test_scanned_single_kor_eng(self, selector):
        p = self._profile(SourceType.SCANNED, LayoutType.SINGLE_COLUMN, ["kor", "eng"], False)
        assert selector.select(p) == OcrStrategy.OCR_KOR_ENG

    def test_scanned_single_jpn(self, selector):
        p = self._profile(SourceType.SCANNED, LayoutType.SINGLE_COLUMN, ["kor", "eng", "jpn"], False)
        assert selector.select(p) == OcrStrategy.OCR_JPN

    def test_scanned_multi_col(self, selector):
        p = self._profile(SourceType.SCANNED, LayoutType.MULTI_COLUMN, ["kor", "eng"], False)
        assert selector.select(p) == OcrStrategy.OCR_MULTI_COL

    def test_scanned_formula_single(self, selector):
        p = self._profile(SourceType.SCANNED, LayoutType.SINGLE_COLUMN, ["eng"], True)
        assert selector.select(p) == OcrStrategy.OCR_FORMULA

    def test_scanned_formula_multi(self, selector):
        p = self._profile(SourceType.SCANNED, LayoutType.MULTI_COLUMN, ["eng"], True)
        assert selector.select(p) == OcrStrategy.OCR_MULTI_FORMULA

    def test_get_tesseract_config(self):
        cfg = get_tesseract_config(OcrStrategy.OCR_KOR_ENG)
        assert cfg["lang"] == "kor+eng"
        assert cfg["psm"] == 3

    def test_get_tesseract_config_formula(self):
        cfg = get_tesseract_config(OcrStrategy.OCR_FORMULA)
        assert cfg["oem"] == 1
        assert cfg["psm"] == 6
