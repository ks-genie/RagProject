# -*- coding: utf-8 -*-
"""Phase 5 — Preprocessor 단위 테스트"""

import pytest
from src.preprocessor import Preprocessor, PreprocessResult
from src.database import Status


@pytest.fixture
def pp():
    return Preprocessor()


# ===========================================================================
# 정제 (_clean) 테스트
# ===========================================================================

class TestClean:

    def test_removes_control_characters(self, pp):
        text = "정상 텍스트\x00\x01\x07garbage"
        cleaned = pp._clean(text)
        assert "\x00" not in cleaned
        assert "\x07" not in cleaned
        assert "정상 텍스트" in cleaned

    def test_preserves_newline_and_tab_converted(self, pp):
        text = "line1\tindented\nline2"
        cleaned = pp._clean(text)
        assert "\t" not in cleaned
        assert "line1" in cleaned
        assert "line2" in cleaned

    def test_fullwidth_space_normalized(self, pp):
        text = "한국어\u3000텍스트"   # 전각 공백
        cleaned = pp._clean(text)
        assert "\u3000" not in cleaned
        assert "한국어" in cleaned
        assert "텍스트" in cleaned

    def test_non_breaking_space_normalized(self, pp):
        text = "word1\u00A0word2"
        cleaned = pp._clean(text)
        assert "\u00A0" not in cleaned

    def test_bom_removed(self, pp):
        text = "\uFEFF본문 시작"
        cleaned = pp._clean(text)
        assert "\uFEFF" not in cleaned
        assert "본문 시작" in cleaned

    def test_consecutive_spaces_collapsed(self, pp):
        text = "단어1    단어2     단어3"
        cleaned = pp._clean(text)
        assert "  " not in cleaned
        assert "단어1 단어2 단어3" == cleaned

    def test_excessive_newlines_reduced(self, pp):
        text = "단락1\n\n\n\n\n단락2"
        cleaned = pp._clean(text)
        assert "\n\n\n" not in cleaned
        assert "단락1" in cleaned
        assert "단락2" in cleaned

    def test_hyphenated_word_restored(self, pp):
        text = "연-\n결된단어 그리고 다른 텍스트"
        cleaned = pp._clean(text)
        assert "연결된단어" in cleaned

    def test_english_hyphen_restored(self, pp):
        text = "multi-\nline word"
        cleaned = pp._clean(text)
        assert "multiline" in cleaned

    def test_noise_only_line_removed(self, pp):
        text = "정상 줄\n@#\n다음 줄"
        cleaned = pp._clean(text)
        assert "@#" not in cleaned
        assert "정상 줄" in cleaned
        assert "다음 줄" in cleaned

    def test_empty_string(self, pp):
        assert pp._clean("") == ""

    def test_whitespace_only(self, pp):
        assert pp._clean("   \n\t  ") == ""

    def test_leading_trailing_stripped(self, pp):
        text = "\n\n  본문 내용입니다.  \n\n"
        cleaned = pp._clean(text)
        assert cleaned == "본문 내용입니다."


# ===========================================================================
# 상태 분기 (process) 테스트
# ===========================================================================

class TestProcess:

    def _long_text(self, n=200):
        return "한국어 영어 혼합 텍스트입니다. English text included. " * (n // 25 + 1)

    def test_text_ready_good_quality(self, pp):
        text = self._long_text()
        result = pp.process(text, ocr_quality_score=0.9)
        assert result.status == Status.TEXT_READY
        assert result.is_ready() is True
        assert result.reason == ""

    def test_review_required_too_short(self, pp):
        text = "짧음"   # 2자 — min_text_length(50) 미만
        result = pp.process(text, ocr_quality_score=0.9)
        assert result.status == Status.REVIEW_REQUIRED
        assert result.is_ready() is False
        assert "길이 부족" in result.reason

    def test_review_required_low_quality(self, pp):
        text = self._long_text()
        result = pp.process(text, ocr_quality_score=0.1)  # threshold=0.5 미만
        assert result.status == Status.REVIEW_REQUIRED
        assert "품질 점수" in result.reason

    def test_text_ready_exact_threshold_quality(self, pp):
        text = self._long_text()
        # config.ocr_quality_threshold = 0.5
        result = pp.process(text, ocr_quality_score=0.5)
        assert result.status == Status.TEXT_READY

    def test_review_required_just_below_threshold(self, pp):
        text = self._long_text()
        result = pp.process(text, ocr_quality_score=0.49)
        assert result.status == Status.REVIEW_REQUIRED

    def test_char_count_excludes_whitespace(self, pp):
        text = "안 녕 하 세 요" * 20  # 공백 포함
        result = pp.process(text, ocr_quality_score=0.9)
        assert result.char_count < len(text)

    def test_line_count_correct(self, pp):
        text = "줄1\n줄2\n\n줄3\n"
        result = pp.process(text + "x" * 200, ocr_quality_score=0.9)
        # 비어 있지 않은 줄만 카운트
        assert result.line_count >= 3

    def test_cleaned_text_in_result(self, pp):
        raw = "텍스트\x00내용\t with\ttabs  and spaces. " * 10
        result = pp.process(raw, ocr_quality_score=0.9)
        assert "\x00" not in result.text
        assert "\t" not in result.text

    def test_default_quality_score_is_perfect(self, pp):
        text = self._long_text()
        result = pp.process(text)   # 기본값 1.0
        assert result.status == Status.TEXT_READY

    def test_empty_text_review_required(self, pp):
        result = pp.process("", ocr_quality_score=1.0)
        assert result.status == Status.REVIEW_REQUIRED

    def test_result_char_count(self, pp):
        text = "안녕하세요 " * 20  # 공백 제외 100자
        result = pp.process(text, ocr_quality_score=0.9)
        assert result.char_count == 100


class TestLayoutAwareLinebreaks:

    def test_soft_wrapped_lines_are_joined(self, pp):
        text = (
            "This is a long paragraph line that wraps in the source view\n"
            "and should continue without a hard line break.\n\n"
            "Next paragraph stays separate."
        )
        cleaned = pp._clean(text)
        assert (
            "This is a long paragraph line that wraps in the source view "
            "and should continue without a hard line break."
        ) in cleaned
        assert "\n\nNext paragraph stays separate." in cleaned

    def test_list_items_keep_linebreaks(self, pp):
        text = "- first item\n- second item\n- third item"
        cleaned = pp._clean(text)
        assert cleaned == text

    def test_short_heading_keeps_following_linebreak(self, pp):
        text = (
            "HBM Overview\n"
            "This paragraph starts below the heading and should not be merged "
            "into the heading line."
        )
        cleaned = pp._clean(text)
        assert cleaned.startswith("HBM Overview\nThis paragraph starts below the heading")

    def test_triple_newline_between_wide_paragraphs_is_preserved(self, pp):
        text = "First paragraph.\n\n\nSecond paragraph."
        cleaned = pp._clean(text)
        assert cleaned == text

    def test_sentence_end_then_uppercase_next_line_is_not_merged(self, pp):
        text = (
            "considering temperature distribution (ART) scheme as a solution.\n"
            "HBM is composed of stacked DRAM dies over a buffer die."
        )
        cleaned = pp._clean(text)
        assert cleaned == text

    def test_em_dash_bullets_keep_linebreaks(self, pp):
        """em-dash(—) 글머리 항목은 개행이 유지되어야 함 (Features 섹션)."""
        text = (
            "— Up to 1 TB/s aggregate bandwidth for 8-Hi HBM3 interface\n"
            "— Per stack, up to 16 channels, 64-bit width per channel\n"
            "— Supports ECC mode for enhanced data integrity"
        )
        cleaned = pp._clean(text)
        lines = [ln for ln in cleaned.splitlines() if ln.strip()]
        assert len(lines) == 3, f"em-dash 항목이 합쳐짐: {repr(cleaned)}"

    def test_toc_entries_keep_linebreaks(self, pp):
        """목차(TOC) 항목은 개행이 유지되어야 함."""
        text = (
            "3 Terms, definitions, and abbreviated terms 2\n"
            "3.1 Terms and definitions 2\n"
            "3.2 Abbreviated terms 4\n"
            "4 Symbols 4"
        )
        cleaned = pp._clean(text)
        lines = [ln for ln in cleaned.splitlines() if ln.strip()]
        assert len(lines) == 4, f"TOC 항목이 합쳐짐: {repr(cleaned)}"

    def test_bullet_variants_keep_linebreaks(self, pp):
        """•, –, — 등 다양한 글머리 기호가 리스트 항목으로 인식되어야 함."""
        for bullet in ("•", "–", "—"):
            text = f"{bullet} First feature item here\n{bullet} Second feature item here"
            cleaned = pp._clean(text)
            lines = [ln for ln in cleaned.splitlines() if ln.strip()]
            assert len(lines) == 2, f"'{bullet}' 글머리 항목이 합쳐짐: {repr(cleaned)}"

    def test_figure_toc_emdash_keep_linebreaks(self, pp):
        """Figure/Table TOC 항목(em-dash 형식)은 개행이 유지되어야 함."""
        text = (
            "Figure 1—HBM3 Device Block Diagram 6\n"
            "Figure 2—HBM3 I/O Cluster 7\n"
            "Figure 3—Die Stack Assembly 8"
        )
        cleaned = pp._clean(text)
        lines = [ln for ln in cleaned.splitlines() if ln.strip()]
        assert len(lines) == 3, f"Figure TOC 항목이 합쳐짐: {repr(cleaned)}"

    def test_figure_toc_emdash_spaced_keep_linebreaks(self, pp):
        """Figure/Table TOC 항목(공백+em-dash 형식)은 개행이 유지되어야 함."""
        text = (
            "Figure 1 — HBM3 Device Block Diagram 6\n"
            "Figure 2 — HBM3 I/O Cluster 7"
        )
        cleaned = pp._clean(text)
        lines = [ln for ln in cleaned.splitlines() if ln.strip()]
        assert len(lines) == 2, f"Figure TOC 항목(공백+em-dash)이 합쳐짐: {repr(cleaned)}"

    def test_annex_toc_keep_linebreaks(self, pp):
        """Annex TOC 항목은 개행이 유지되어야 함."""
        text = (
            "Annex A (normative) Compliance Testing 44\n"
            "Annex B (informative) Timing Diagrams 52"
        )
        cleaned = pp._clean(text)
        lines = [ln for ln in cleaned.splitlines() if ln.strip()]
        assert len(lines) == 2, f"Annex TOC 항목이 합쳐짐: {repr(cleaned)}"
