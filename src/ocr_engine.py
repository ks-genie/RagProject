# -*- coding: utf-8 -*-
"""
OCR Engine — 전략별 Tesseract 실행 및 품질 점수 산출

전략에 따라 Layout Splitter / Formula Handler와 연동하여
최적의 OCR 파이프라인을 실행.
"""

import logging
import os
import re
from collections import defaultdict

import cv2
import numpy as np
import pdfplumber
import pypdfium2 as pdfium
import pytesseract
from PIL import Image as PILImage

from src.config import config
from src.database import OcrStrategy, Status
from src.formula_handler import FormulaHandler
from src.layout_splitter import LayoutSplitter
from src.ocr_postprocess import fix_easyocr_korean
from src.strategy_selector import get_tesseract_config

logger = logging.getLogger(__name__)

# 컬럼 병합 시 우 컬럼 앞에서 건너뛸 그림/표 캡션 패턴
_COL_CAP_RE = re.compile(
    r"^(?:그림|표|Figure|Fig\.?|Table)\s*\d+(?:[-\.]\d+)*(?![.-]\d)\s*[\.:]",
    re.IGNORECASE,
)
# 좌 컬럼 끝 바로 다음에 오는 약어 정의 패턴: (HBM3), (DQS), (CLK) 등 대문자+숫자 약어
_COL_ABBREV_RE = re.compile(r"^\([A-Z][A-Z0-9]{1,10}\)")

# 유효 문자 패턴 (한글·영문·일문·숫자·공백·기본 구두점)
_VALID_CHAR_RE = re.compile(
    r'[\uAC00-\uD7A3'    # 한글
    r'\u3040-\u30FF'     # 히라가나·가타카나
    r'A-Za-z0-9'
    r'\s.,!?;:()\-\'"·]'
)


class OcrEngine:
    def __init__(self):
        self._setup_tesseract()
        self.splitter = LayoutSplitter()
        self.formula_handler = FormulaHandler()
        self.last_page_texts: list[str] = []   # run() 호출 후 페이지별 텍스트
        self._easy_readers: dict[tuple, object] = {}  # lang_key → easyocr.Reader (lazy)

    def _setup_tesseract(self):
        if config.ocr_tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = config.ocr_tesseract_cmd
        if config.ocr_tessdata_dir:
            os.environ["TESSDATA_PREFIX"] = config.ocr_tessdata_dir

    # ------------------------------------------------------------------
    # EasyOCR
    # ------------------------------------------------------------------
    def _get_easy_reader(self, langs: list[str]):
        """EasyOCR Reader를 언어 조합별로 캐싱하여 반환 (모델 재로딩 방지)."""
        import easyocr
        import torch
        key = tuple(sorted(langs))
        if key not in self._easy_readers:
            gpu = torch.cuda.is_available()
            logger.info("EasyOCR Reader 초기화: langs=%s, gpu=%s", langs, gpu)
            self._easy_readers[key] = easyocr.Reader(langs, gpu=gpu, verbose=False)
        return self._easy_readers[key]

    def _easyocr_pages(self, images: list[PILImage.Image], langs: list[str]) -> list[str]:
        """EasyOCR로 페이지별 텍스트 추출. paragraph=True로 줄 병합 후 단락 리스트 반환."""
        reader = self._get_easy_reader(langs)
        pages: list[str] = []
        for page_idx, img in enumerate(images):
            try:
                result = reader.readtext(np.array(img), detail=0, paragraph=True)
                text = "\n\n".join(result) if result else ""
                pages.append(fix_easyocr_korean(text))
            except Exception as e:
                logger.warning("EasyOCR 페이지 %d 실패 (빈 결과): %s", page_idx + 1, e)
                pages.append("")
        return pages

    def _llm_correct_pages(self, pages: list[str]) -> list[str]:
        """LLM(Claude / Gemini)으로 EasyOCR 결과를 페이지별 교정. 실패 시 원본 유지."""
        from src.llm_corrector import correct_ocr_page
        provider = config.llm_provider
        if provider == "gemini":
            api_key = config.gemini_api_key
            model   = config.gemini_model
            timeout = config.gemini_timeout
            max_tokens = 8192
        else:
            api_key = config.claude_api_key
            model   = config.claude_model
            timeout = config.claude_timeout
            max_tokens = config.claude_max_tokens

        if not api_key:
            logger.warning("LLM(%s) API 키 미설정 — 교정 생략", provider)
            return pages

        corrected: list[str] = []
        for i, text in enumerate(pages):
            result = correct_ocr_page(
                text,
                provider=provider,
                api_key=api_key,
                model=model,
                max_tokens=max_tokens,
                timeout=timeout,
            )
            if result != text:
                logger.info(
                    "LLM(%s) 교정 완료: 페이지 %d (%d→%d자)",
                    provider, i + 1, len(text), len(result),
                )
            corrected.append(result)
        return corrected

    # ------------------------------------------------------------------
    # 공개 인터페이스
    # ------------------------------------------------------------------
    def run(self, pdf_path: str, strategy: str) -> tuple[str, float]:
        """OCR 실행. (extracted_text, quality_score) 반환.

        quality_score: 0.0 ~ 1.0 (유효 문자 비율)

        스캔 전략은 모두 _ocr_adaptive 를 통해
        페이지별 레이아웃을 감지하여 다단/단일을 자동 전환한다.
        """
        logger.info("OCR 시작: strategy=%s [%s]", strategy, pdf_path)

        try:
            if strategy == OcrStrategy.DIGITAL_EXTRACT:
                pages = self._extract_digital_pages(pdf_path)
                text = "\n\n".join(pages)
                score = self._compute_quality(text)
            elif strategy == OcrStrategy.DIGITAL_EXTRACT_MULTI:
                pages = self._extract_digital_multicol_pages(pdf_path)
                text = "\n\n".join(pages)
                score = self._compute_quality(text)
            elif strategy == OcrStrategy.DOCX_EXTRACT:
                pages = self._extract_docx_pages(pdf_path)
                text = "\n\n".join(pages)
                score = self._compute_quality(text)
            elif strategy == OcrStrategy.PPTX_EXTRACT:
                pages = self._extract_pptx_pages(pdf_path)
                text = "\n\n".join(pages)
                score = self._compute_quality(text)
            elif strategy == OcrStrategy.OCR_KOR_ENG:
                images = self._get_page_images(pdf_path)
                pages = self._easyocr_pages(images, ["ko", "en"])
                if config.llm_correction_enabled:
                    logger.info("LLM(%s) OCR 교정 시작: %d 페이지", config.llm_provider, len(pages))
                    pages = self._llm_correct_pages(pages)
                text = "\n\n".join(pages)
                score = self._compute_quality(text)
            elif strategy == OcrStrategy.OCR_JPN:
                images = self._get_page_images(pdf_path)
                pages = self._easyocr_pages(images, ["ko", "en", "ja"])
                if config.llm_correction_enabled:
                    logger.info("LLM(%s) OCR 교정 시작: %d 페이지", config.llm_provider, len(pages))
                    pages = self._llm_correct_pages(pages)
                text = "\n\n".join(pages)
                score = self._compute_quality(text)
            else:
                images = self._get_page_images(pdf_path)
                tess_cfg = get_tesseract_config(strategy)
                with_formula = strategy in (
                    OcrStrategy.OCR_FORMULA,
                    OcrStrategy.OCR_MULTI_FORMULA,
                )
                pages = self._ocr_adaptive_pages(images, tess_cfg,
                                                 with_formula=with_formula)
                text = "\n\n".join(pages)
                score = self._compute_quality(text)
            self.last_page_texts = pages
        except Exception as e:
            logger.error("OCR 실패: %s", e)
            raise

        logger.info("OCR 완료: 글자 수=%d, 품질=%.3f", len(text), score)
        return text, score

    def meets_quality(self, text: str, score: float) -> bool:
        """품질 기준 충족 여부 판단"""
        return (
            len(text) >= config.ocr_min_text_length
            and score >= config.ocr_quality_threshold
        )

    # ------------------------------------------------------------------
    # 워터마크 필터 (색상·회전 기반)
    # ------------------------------------------------------------------
    @staticmethod
    def _luminance(color) -> float | None:
        """PDF 색상값 → 밝기(0=검정, 1=흰색). 변환 불가 시 None."""
        if color is None:
            return None
        if isinstance(color, (int, float)):
            return float(color)
        if not isinstance(color, (list, tuple)):
            return None
        if len(color) == 1:
            return float(color[0])
        if len(color) == 3:
            r, g, b = (float(v) for v in color)
            return 0.299 * r + 0.587 * g + 0.114 * b
        if len(color) == 4:          # CMYK
            c, m, y, k = (float(v) for v in color)
            r = (1 - c) * (1 - k)
            g = (1 - m) * (1 - k)
            b = (1 - y) * (1 - k)
            return 0.299 * r + 0.587 * g + 0.114 * b
        return None

    @classmethod
    def filter_watermarks(
        cls,
        page,
        light_threshold: float = 0.80,
        stamp_size_pt: float = 30.0,
        stamp_lum_min: float = 0.15,
    ):
        """워터마크 의심 문자를 제거한 pdfplumber 페이지 반환.

        제거 대상:
          ① upright=False: 텍스트 행렬 회전 스탬프
          ② non_stroking_color 또는 stroking_color 밝기 > light_threshold:
             반투명·연한 배경 워터마크 (기본 0.80)
          ③ 폰트 크기 > stamp_size_pt AND 밝기 > stamp_lum_min:
             CTM 회전 대형 스탬프 낱글자 (T·S 등, 검정이 아닌 큰 글자)
        """
        def _keep(obj):
            if obj.get("object_type") != "char":
                return True
            # ① 텍스트 행렬 회전
            if not obj.get("upright", True):
                return False
            lum_fill   = cls._luminance(obj.get("non_stroking_color"))
            lum_stroke = cls._luminance(obj.get("stroking_color"))
            # ② 연한 색상 (채움 또는 획)
            if lum_fill   is not None and lum_fill   > light_threshold:
                return False
            if lum_stroke is not None and lum_stroke > light_threshold:
                return False
            # ③ 큰 폰트 + 비검정 → CTM 회전 스탬프 낱글자
            size = float(obj.get("size") or 0)
            lum  = lum_fill if lum_fill is not None else lum_stroke
            if size > stamp_size_pt and lum is not None and lum > stamp_lum_min:
                return False
            return True
        return page.filter(_keep)

    # ------------------------------------------------------------------
    # Office 문서 추출 — DOCX / PPTX
    # ------------------------------------------------------------------
    def _extract_docx_pages(self, file_path: str) -> list[str]:
        """python-docx로 Word 문서 텍스트 추출. 문서 전체를 단일 페이지로 반환.

        python-docx가 열지 못하는 경우(HTML/XML로 저장된 .docx 등)는
        HTML 태그 제거 방식으로 폴백 추출한다.
        """
        from docx import Document
        try:
            doc = Document(file_path)
            parts: list[str] = []
            for para in doc.paragraphs:
                t = para.text.strip()
                if t:
                    parts.append(t)
            for table in doc.tables:
                for row in table.rows:
                    cells = [c.text.strip() for c in row.cells if c.text.strip()]
                    if cells:
                        parts.append(" | ".join(cells))
            # 텍스트박스 등 python-docx가 누락하는 요소: XML w:t 직접 순회
            if not parts:
                ns = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
                for elem in doc.element.body.iter(f'{{{ns}}}t'):
                    t = (elem.text or '').strip()
                    if t:
                        parts.append(t)
            return ["\n\n".join(parts)] if parts else [""]
        except Exception as first_err:
            # ZIP(DOCX) 형식이 아닌 경우 — Word HTML export 등
            logger.warning("DOCX ZIP 열기 실패, HTML 폴백 시도 [%s]: %s", file_path, first_err)
            return self._extract_html_as_docx_pages(file_path)

    def _extract_html_as_docx_pages(self, file_path: str) -> list[str]:
        """확장자가 .docx이지만 실제로는 HTML인 파일에서 텍스트 추출.

        Word HTML export(mso- 속성 포함)만 처리하고,
        온라인 변환 사이트 웹페이지는 거부하여 빈 결과를 반환한다.
        """
        import re
        try:
            with open(file_path, encoding='utf-8', errors='replace') as f:
                raw = f.read()

            # Word HTML export 판별 — mso- CSS 속성 또는 Word 메타 존재 여부
            is_word_html = bool(
                re.search(r'mso-', raw, re.IGNORECASE) or
                re.search(r'Microsoft\s+Word', raw, re.IGNORECASE) or
                re.search(r'WordDocument', raw)
            )
            if not is_word_html:
                logger.warning(
                    "웹페이지 HTML로 판단됨 — 문서 내용 추출 불가 (Word HTML export가 아님): [%s]",
                    file_path,
                )
                return [""]

            # <style>, <script> 블록 제거
            raw = re.sub(r'<style[^>]*>.*?</style>', ' ', raw,
                         flags=re.DOTALL | re.IGNORECASE)
            raw = re.sub(r'<script[^>]*>.*?</script>', ' ', raw,
                         flags=re.DOTALL | re.IGNORECASE)
            # HTML 태그 제거
            text = re.sub(r'<[^>]+>', ' ', raw)
            # 기본 HTML 엔티티 변환
            for ent, ch in [('&nbsp;', ' '), ('&lt;', '<'), ('&gt;', '>'),
                            ('&amp;', '&'), ('&quot;', '"')]:
                text = text.replace(ent, ch)
            # 공백·줄바꿈 정규화
            text = re.sub(r'[ \t]+', ' ', text)
            text = re.sub(r'\n{3,}', '\n\n', text).strip()
            if text:
                logger.info("Word HTML 폴백 추출 완료: %d자 [%s]", len(text), file_path)
                return [text]
        except Exception as e:
            logger.error("HTML 폴백 추출 실패 [%s]: %s", file_path, e)
        return [""]

    def _extract_pptx_pages(self, file_path: str) -> list[str]:
        """python-pptx로 PowerPoint 슬라이드 텍스트 추출. 슬라이드별 페이지 반환."""
        from pptx import Presentation
        try:
            prs = Presentation(file_path)
            pages: list[str] = []
            for slide in prs.slides:
                slide_texts: list[str] = []
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        t = shape.text_frame.text.strip()
                        if t:
                            slide_texts.append(t)
                    elif shape.has_table:
                        for row in shape.table.rows:
                            cells = [c.text_frame.text.strip() for c in row.cells
                                     if c.text_frame.text.strip()]
                            if cells:
                                slide_texts.append(" | ".join(cells))
                pages.append("\n\n".join(slide_texts))
            return pages if pages else [""]
        except Exception as e:
            logger.error("PPTX 추출 실패 [%s]: %s", file_path, e)
            return [""]

    # ------------------------------------------------------------------
    # Digital 추출 — 단일 컬럼
    # ------------------------------------------------------------------
    def _extract_digital_pages(self, pdf_path: str) -> list[str]:
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page  = self.filter_watermarks(page)
                words = page.extract_words()
                pages.append(
                    self._words_to_text_with_paragraphs(words) if words
                    else (page.extract_text() or "")
                )
        return pages

    def _extract_digital(self, pdf_path: str) -> tuple[str, float]:
        pages = self._extract_digital_pages(pdf_path)
        full_text = "\n\n".join(pages)
        return full_text, self._compute_quality(full_text)

    # ------------------------------------------------------------------
    # Digital 추출 — 다단 컬럼 인식
    # ------------------------------------------------------------------
    def _extract_digital_multicol_pages(self, pdf_path: str) -> list[str]:
        """2단 디지털 PDF를 컬럼별로 분리하여 순서대로 추출. 페이지 리스트 반환."""
        pages = []
        with pdfplumber.open(pdf_path) as pdf:
            for page in pdf.pages:
                page  = self.filter_watermarks(page)
                words = page.extract_words()
                if not words:
                    pages.append("")
                    continue
                page_width = float(page.width)
                gap_x = self._find_column_gap(words, page_width)
                if gap_x is None:
                    pages.append(self._words_to_text_with_paragraphs(words))
                else:
                    body_start_y = self._find_body_start_y(
                        words, gap_x, page_height=float(page.height)
                    )
                    pages.append(self._compose_multicol_text(words, gap_x, body_start_y))
        return pages

    def _extract_digital_multicol(self, pdf_path: str) -> tuple[str, float]:
        pages = self._extract_digital_multicol_pages(pdf_path)
        full_text = "\n\n".join(pages)
        return full_text, self._compute_quality(full_text)

    @staticmethod
    def _find_column_gap(words: list, page_width: float) -> float | None:
        """줄 단위 x-커버리지로 컬럼 갭 중심 x(pt) 반환. 없으면 None."""
        BINS   = 200
        LINE_H = 3

        lines = OcrEngine._group_words_by_line(words, line_height=LINE_H)
        candidate_lines = [
            line_words
            for _, line_words in lines
            if OcrEngine._line_spans_both_columns(line_words, page_width * 0.5)
        ]
        selected_lines = (
            candidate_lines
            if len(candidate_lines) >= 3
            else [line_words for _, line_words in lines]
        )

        n_lines = len(selected_lines)
        if n_lines < 3:
            return None

        line_cov = np.zeros(BINS)
        for line_words in selected_lines:
            covered = np.zeros(BINS, dtype=bool)
            for w in line_words:
                x0b = max(0, int(float(w["x0"]) / page_width * BINS))
                x1b = min(BINS - 1, int(float(w["x1"]) / page_width * BINS))
                covered[x0b: x1b + 1] = True
            line_cov += covered
        line_cov /= n_lines

        s, e = int(BINS * 0.35), int(BINS * 0.65)
        center_cov = line_cov[s:e]
        side_mean  = float((line_cov[:s].mean() + line_cov[e:].mean()) / 2)
        if side_mean <= 0.10:
            return None

        gap_thresh = min(0.25, side_mean * 0.30)
        max_gap = curr = best_start = best_end = 0
        for i, v in enumerate(center_cov < gap_thresh):
            curr = curr + 1 if v else 0
            if curr > max_gap:
                max_gap   = curr
                best_end   = s + i
                best_start = best_end - max_gap + 1

        if max_gap < 3:
            return None

        gap_center_bin = (best_start + best_end + 1) / 2
        return gap_center_bin / BINS * page_width

    @staticmethod
    def _group_words_by_line(words: list, line_height: int = 3) -> list[tuple[float, list]]:
        grouped: dict[float, list] = defaultdict(list)
        for w in words:
            yk = round(float(w["top"]) / line_height) * line_height
            grouped[yk].append(w)
        groups = sorted(grouped.items())
        if len(groups) < 2:
            return groups

        # Merge sub/superscript groups: small y-gap AND directly x-adjacent to a word above.
        # Use per-word adjacency (not range) to avoid incorrectly merging entire line groups.
        merged: list[list] = [[groups[0][0], list(groups[0][1])]]
        for yk, grp_words in groups[1:]:
            prev_yk, prev_words = merged[-1]
            gap = yk - prev_yk
            if gap < line_height * 3:
                sub: list = []
                rest: list = []
                for cw in grp_words:
                    cx0 = float(cw.get("x0", 0))
                    # Subscript/superscript: candidate's left edge touches a specific prev word
                    is_sub = any(
                        -1.0 <= cx0 - float(pw.get("x1", pw.get("x0", 0))) <= 1.5
                        for pw in prev_words
                    )
                    if is_sub:
                        sub.append(cw)
                    else:
                        rest.append(cw)
                if sub:
                    merged[-1][1].extend(sub)
                if rest:
                    merged.append([yk, rest])
            else:
                merged.append([yk, list(grp_words)])
        return [(yk, ws) for yk, ws in merged]

    @staticmethod
    def _join_line_words(words: list) -> str:
        """줄 내 단어 조합: x가 붙어있는 단어(아래첨자·위첨자)는 공백 없이 연결."""
        sorted_w = sorted(words, key=lambda w: float(w.get("x0", 0)))
        if not sorted_w:
            return ""
        result = sorted_w[0]["text"]
        for i in range(1, len(sorted_w)):
            gap = float(sorted_w[i].get("x0", 0)) - float(sorted_w[i - 1].get("x1", sorted_w[i - 1].get("x0", 0)))
            # Gap < 1.0pt: directly touching (sub/superscript) → no space.
            # Normal inter-word space in this type of PDF is ≥ 1.5pt.
            result += sorted_w[i]["text"] if gap < 1.0 else (" " + sorted_w[i]["text"])
        return result

    @staticmethod
    def _line_spans_both_columns(line_words: list, split_x: float) -> bool:
        has_left = False
        has_right = False
        for w in line_words:
            mid_x = (float(w["x0"]) + float(w["x1"])) / 2
            if mid_x < split_x:
                has_left = True
            else:
                has_right = True
            if has_left and has_right:
                return True
        return False

    @staticmethod
    def _split_header_footer_words(
        words: list,
        page_height: float,
        header_pct: float = 0.08,
        footer_pct: float = 0.93,
    ) -> tuple[list, list, list]:
        """단어 목록을 y 좌표 기준으로 헤더·본문·푸터로 분리.

        Returns:
            (header_words, main_words, footer_words)
        """
        h = page_height
        header = [w for w in words if float(w.get("top", 0)) < h * header_pct]
        footer = [w for w in words if float(w.get("top", 0)) > h * footer_pct]
        main   = [w for w in words
                  if h * header_pct <= float(w.get("top", 0)) <= h * footer_pct]
        return header, main, footer

    @staticmethod
    def _split_words_by_column(words: list, gap_x: float) -> tuple[list, list]:
        left = []
        right = []
        for w in words:
            mid_x = (float(w["x0"]) + float(w["x1"])) / 2
            if mid_x < gap_x:
                left.append(w)
            else:
                right.append(w)
        return left, right

    @classmethod
    def _find_body_start_y(
        cls,
        words: list,
        gap_x: float,
        page_height: float = 0.0,
    ) -> float | None:
        """양쪽 컬럼에 단어가 고르게 분포하는 첫 번째 y 좌표(본문 시작)를 탐지.

        탐지 기준: 각 줄에서 좌·우 양쪽에 모두 단어가 2개 이상인 줄이
        연속 3줄 이상 이어지는 시작점.
        GAP_THRESHOLD: 줄 간격이 이 값을 초과하면 섹션 구분으로 보아 카운터 리셋
        → 제목(2줄)·저자(2줄)는 섹션 구분 후 각각 2줄씩 WINDOW=3 미달로 패스.

        Args:
            page_height: 페이지 높이(pt). 미전달 시 words로부터 추정.
                         상단 10% 이하 영역(런닝헤더)은 탐색 대상에서 제외.
        """
        lines = cls._group_words_by_line(words)
        if not lines:
            return None

        if not page_height and words:
            page_height = max(float(w.get("bottom", w.get("top", 0))) for w in words)
        min_y = page_height * 0.10  # 런닝헤더 영역 제외

        WINDOW          = 3   # 연속 줄 수
        MIN_EACH        = 2   # 각 줄에서 양쪽 최소 단어 수
        GAP_THRESHOLD   = 30  # pt — 줄 간격이 이를 초과하면 섹션 구분으로 리셋

        consecutive: int = 0
        first_y: float | None = None
        prev_yk: float | None = None

        for yk, line_words in lines:
            if yk < min_y:
                prev_yk = yk
                consecutive = 0
                first_y = None
                continue

            # 줄 간격이 GAP_THRESHOLD 초과 → 섹션 구분: 카운터 리셋
            if prev_yk is not None and (yk - prev_yk) > GAP_THRESHOLD:
                consecutive = 0
                first_y = None
            prev_yk = yk

            mid_xs = [(float(w["x0"]) + float(w["x1"])) / 2 for w in line_words]
            left_n  = sum(1 for x in mid_xs if x < gap_x)
            right_n = sum(1 for x in mid_xs if x >= gap_x)

            if left_n >= MIN_EACH and right_n >= MIN_EACH:
                consecutive += 1
                if first_y is None:
                    first_y = yk
                if consecutive >= WINDOW:
                    return first_y
            else:
                consecutive = 0
                first_y = None

        return None

    @staticmethod
    def _join_columns(left_text: str, right_text: str) -> str:
        """좌→우 컬럼 경계에서 이어지는 문장을 공백으로 연결.

        병합 조건:
          A. 우 첫 단락이 소문자로 시작  (원래 동작)
          B. 좌 마지막 단어가 문장 중단 단어이고, 우 컬럼 앞에 그림/표 캡션이 선행
             → 캡션을 건너뛰어 그 다음 단락과 병합
        하이픈으로 끝나는 경우 하이픈 제거 후 이어붙임.
        """
        _SENT_END = frozenset('.?!;')
        _MID_WORD = frozenset({
            "a", "an", "the",
            "and", "or", "nor", "but", "yet", "so",
            "in", "of", "to", "at", "by", "for", "on", "as", "from",
            "with", "into", "onto", "upon", "over", "under",
            "is", "are", "was", "were", "be", "been", "being",
            "that", "which", "who", "whom", "whose", "when", "where",
            "this", "these", "those", "its", "their", "our", "your",
            "not", "also", "both", "either", "neither", "than", "such",
            "using", "shown", "given", "based", "called", "used",
            "mentioned", "described", "defined",
        })

        left_paras  = [p for p in left_text.strip().split('\n\n')  if p.strip()]
        right_paras = [p for p in right_text.strip().split('\n\n') if p.strip()]
        if not left_paras or not right_paras:
            return "\n\n".join(filter(None, [left_text.strip(), right_text.strip()]))

        last_left = left_paras[-1].rstrip()
        if not last_left or last_left[-1] in _SENT_END:
            return "\n\n".join(filter(None, [left_text.strip(), right_text.strip()]))

        # 우 컬럼 앞 캡션 단락들을 분리
        right_caps = []
        right_main = list(right_paras)
        while right_main and _COL_CAP_RE.match(right_main[0].strip()):
            right_caps.append(right_main.pop(0))
        if not right_main:          # 우 컬럼이 캡션만으로 구성된 경우
            right_main = right_caps
            right_caps = []

        first_right = right_main[0].lstrip()
        last_word   = last_left.split()[-1].lower().rstrip(".,;:?!") if last_left.split() else ""

        should_merge = (
            first_right[:1].islower()                   # A: 소문자 시작
            or (right_caps and last_word in _MID_WORD)  # B: 캡션 선행 + 문장 중단 단어
            or bool(_COL_ABBREV_RE.match(first_right))  # C: (HBM3) 등 대문자 약어 정의
        )

        if should_merge:
            connector = "" if last_left.endswith('-') else " "
            ll = last_left[:-1] if last_left.endswith('-') else last_left
            merged      = ll + connector + first_right
            merged_left = '\n\n'.join(left_paras[:-1] + [merged])
            remainder   = '\n\n'.join(right_caps + right_main[1:])
            return "\n\n".join(filter(None, [merged_left, remainder]))

        return "\n\n".join(filter(None, [left_text.strip(), right_text.strip()]))

    @classmethod
    def _compose_multicol_text(
        cls,
        words: list,
        gap_x: float,
        body_start_y: float | None,
        page_height: float = 0.0,
    ) -> str:
        if body_start_y is None:
            left, right = cls._split_words_by_column(words, gap_x)
            return cls._join_columns(
                cls._words_to_text_with_paragraphs(left),
                cls._words_to_text_with_paragraphs(right),
            )

        header = []
        left_body = []
        right_body = []

        pre_body_words = [w for w in words if float(w["top"]) < body_start_y]
        for _, line_words in cls._group_words_by_line(pre_body_words):
            if cls._line_spans_both_columns(line_words, gap_x):
                header.extend(line_words)
                continue
            left_line, right_line = cls._split_words_by_column(line_words, gap_x)
            left_body.extend(left_line)
            right_body.extend(right_line)

        body_words = [w for w in words if float(w["top"]) >= body_start_y]
        left_words, right_words = cls._split_words_by_column(body_words, gap_x)
        left_body.extend(left_words)
        right_body.extend(right_words)

        # ── 좌 컬럼 하단 각주 분리 ──────────────────────────────────────────
        # 페이지 하단 50% 구간에서 줄 간격 35pt 이상인 첫 지점을 각주 시작으로 판단.
        # 대상: 학술 논문의 소속·수신일 각주 등 열 분리가 시작된 후 좌측에만 있는 하단 블록.
        if not page_height and words:
            page_height = max(float(w.get("bottom", w.get("top", 0))) for w in words)

        FOOTNOTE_GAP  = 35   # pt
        FOOTNOTE_ZONE = 0.50

        note_start_y: float | None = None
        if page_height:
            zone_y = page_height * FOOTNOTE_ZONE
            prev_yk: float | None = None
            for yk, _ in cls._group_words_by_line(left_body):
                if yk > zone_y and prev_yk is not None and (yk - prev_yk) > FOOTNOTE_GAP:
                    note_start_y = yk
                    break
                prev_yk = yk

        left_main = left_body
        left_note: list = []
        if note_start_y is not None:
            left_main = [w for w in left_body if float(w.get("top", 0)) < note_start_y]
            left_note  = [w for w in left_body if float(w.get("top", 0)) >= note_start_y]
        # ────────────────────────────────────────────────────────────────────

        header_text = cls._words_to_text_with_paragraphs(header)
        body_joined = cls._join_columns(
            cls._words_to_text_with_paragraphs(left_main),
            cls._words_to_text_with_paragraphs(right_body),
        )

        parts = [s for s in (header_text, body_joined) if s]
        if left_note:
            note_text = cls._words_to_text_with_paragraphs(left_note).strip()
            if note_text:
                parts.append(note_text)

        return "\n\n".join(parts)

    @classmethod
    def _words_to_text(cls, words: list) -> str:
        """단어 목록 → 줄 단위 텍스트 재구성 (y→x 순 정렬)."""
        if not words:
            return ""
        lines = []
        for _, line_words in cls._group_words_by_line(words):
            lines.append(cls._join_line_words(line_words))
        return "\n".join(lines)

    @classmethod
    def _words_to_text_with_paragraphs(
        cls,
        words: list,
        line_height: int = 3,
    ) -> str:
        """Rebuild text with line and paragraph breaks from word coordinates."""
        if not words:
            return ""

        line_data: list[tuple[float, float, str]] = []
        for _, line_words in cls._group_words_by_line(words, line_height=line_height):
            text = cls._join_line_words(line_words).strip()
            if not text:
                continue
            sorted_line = sorted(line_words, key=lambda w: float(w.get("x0", 0)))
            line_data.append(
                (
                    float(sorted_line[0].get("top", 0)),
                    max(float(w.get("bottom", w.get("top", 0))) for w in sorted_line),
                    text,
                )
            )

        if not line_data:
            return ""
        if len(line_data) == 1:
            return line_data[0][2]

        gaps = [
            max(line_data[i + 1][0] - line_data[i][1], 0.0)
            for i in range(len(line_data) - 1)
        ]
        positive_gaps = [gap for gap in gaps if gap > 0]
        if not positive_gaps:
            return "\n".join(text for _, _, text in line_data)

        base_gap = sorted(positive_gaps)[len(positive_gaps) // 2]
        paragraph_gap = max(base_gap * 1.35, base_gap + 1.0)
        wide_paragraph_gap = max(base_gap * 2.1, paragraph_gap + 3.0)

        parts: list[str] = [line_data[0][2]]
        for idx, gap in enumerate(gaps):
            if gap >= wide_paragraph_gap:
                separator = "\n\n\n"
            elif gap >= paragraph_gap:
                separator = "\n\n"
            else:
                separator = "\n"
            parts.append(separator + line_data[idx + 1][2])

        return "".join(parts)

    # ------------------------------------------------------------------
    # 페이지별 적응형 OCR (메인 경로)
    # ------------------------------------------------------------------
    def _ocr_adaptive_pages(
        self,
        images: list,
        tess_cfg: dict,
        with_formula: bool = False,
    ) -> list[str]:
        """모든 페이지에 대해 레이아웃을 개별 감지하여 최적 OCR 적용. 페이지 리스트 반환."""
        cfg_str = self._build_tess_config(tess_cfg)
        lang = tess_cfg["lang"]
        pages: list[str] = []

        for page_idx, img in enumerate(images):
            cols = self.splitter._split_page(img)
            logger.debug(
                "페이지 %d: %s (%d컬럼)",
                page_idx + 1,
                "다단" if len(cols) > 1 else "단일",
                len(cols),
            )
            col_texts: list[str] = []
            for col in cols:
                if with_formula:
                    text_img, regions = self.formula_handler.separate_formula(col)
                    text_img = self._enhance_for_ocr(text_img)
                    t = pytesseract.image_to_string(text_img, lang=lang, config=cfg_str)
                    if regions:
                        t += f"\n[수식 영역 {len(regions)}개]"
                else:
                    t = pytesseract.image_to_string(self._enhance_for_ocr(col), lang=lang, config=cfg_str)
                col_texts.append(t)
            pages.append("\n".join(col_texts))

        return pages

    def _ocr_adaptive(
        self,
        images: list,
        tess_cfg: dict,
        with_formula: bool = False,
    ) -> tuple[str, float]:
        pages = self._ocr_adaptive_pages(images, tess_cfg, with_formula=with_formula)
        full_text = "\n\n".join(pages)
        return full_text, self._compute_quality(full_text)

    # ------------------------------------------------------------------
    # 단순 OCR (단일 컬럼, 언어별) — 하위 호환용
    # ------------------------------------------------------------------
    def _ocr_simple(self, images: list, tess_cfg: dict) -> tuple[str, float]:
        cfg_str = self._build_tess_config(tess_cfg)
        lang = tess_cfg["lang"]
        texts = [
            pytesseract.image_to_string(img, lang=lang, config=cfg_str)
            for img in images
        ]
        full_text = "\n\n".join(texts)
        return full_text, self._compute_quality(full_text)

    # ------------------------------------------------------------------
    # 다단 OCR
    # ------------------------------------------------------------------
    def _ocr_multi_column(self, images: list, tess_cfg: dict) -> tuple[str, float]:
        cfg_str = self._build_tess_config(tess_cfg)
        lang = tess_cfg["lang"]
        all_texts = []
        for page_cols in self.splitter.split_columns(images):
            col_texts = [
                pytesseract.image_to_string(col, lang=lang, config=cfg_str)
                for col in page_cols
            ]
            all_texts.append("\n".join(col_texts))
        full_text = "\n\n".join(all_texts)
        return full_text, self._compute_quality(full_text)

    # ------------------------------------------------------------------
    # 수식 포함 OCR
    # ------------------------------------------------------------------
    def _ocr_formula(self, images: list, tess_cfg: dict) -> tuple[str, float]:
        cfg_str = self._build_tess_config(tess_cfg)
        lang = tess_cfg["lang"]
        all_texts = []
        for img in images:
            text_img, regions = self.formula_handler.separate_formula(img)
            text = pytesseract.image_to_string(text_img, lang=lang, config=cfg_str)
            if regions:
                text += f"\n[수식 영역 {len(regions)}개]"
            all_texts.append(text)
        full_text = "\n\n".join(all_texts)
        return full_text, self._compute_quality(full_text)

    # ------------------------------------------------------------------
    # 다단 + 수식 복합
    # ------------------------------------------------------------------
    def _ocr_multi_formula(self, images: list, tess_cfg: dict) -> tuple[str, float]:
        cfg_str = self._build_tess_config(tess_cfg)
        lang = tess_cfg["lang"]
        all_texts = []
        for page_cols in self.splitter.split_columns(images):
            col_texts = []
            for col in page_cols:
                text_img, regions = self.formula_handler.separate_formula(col)
                text = pytesseract.image_to_string(text_img, lang=lang, config=cfg_str)
                if regions:
                    text += f"\n[수식 영역 {len(regions)}개]"
                col_texts.append(text)
            all_texts.append("\n".join(col_texts))
        full_text = "\n\n".join(all_texts)
        return full_text, self._compute_quality(full_text)

    # ------------------------------------------------------------------
    # 유틸
    # ------------------------------------------------------------------
    def _build_tess_config(self, tess_cfg: dict) -> str:
        parts = []
        if "psm" in tess_cfg:
            parts.append(f"--psm {tess_cfg['psm']}")
        if "oem" in tess_cfg:
            parts.append(f"--oem {tess_cfg['oem']}")
        return " ".join(parts)

    @staticmethod
    def _enhance_for_ocr(img_pil: PILImage.Image) -> PILImage.Image:
        """적응형 이진화: 스캔 문서의 불균일 조명에 강건하여 한글 인식률 향상"""
        g = np.array(img_pil.convert("L"))
        b = cv2.adaptiveThreshold(
            g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10
        )
        return PILImage.fromarray(b)

    def _compute_quality(self, text: str) -> float:
        """유효 문자 비율로 품질 점수 산출 (0.0 ~ 1.0)"""
        if not text.strip():
            return 0.0
        valid = len(_VALID_CHAR_RE.findall(text))
        return round(valid / max(len(text), 1), 4)

    def _get_page_images(self, pdf_path: str, dpi_scale: float = 4.17) -> list[PILImage.Image]:
        """pypdfium2로 페이지 이미지 변환 (poppler 불필요)"""
        images = []
        try:
            pdf = pdfium.PdfDocument(pdf_path)
        except Exception as e:
            logger.error("PDF 열기 실패 [%s]: %s", pdf_path, e)
            return images
        for i in range(len(pdf)):
            try:
                page = pdf[i]
                bitmap = page.render(scale=dpi_scale)
                images.append(bitmap.to_pil())
            except Exception as e:
                logger.warning("페이지 %d 이미지 변환 실패 (빈 페이지 대체): %s", i, e)
                # A4 72dpi×2 기준 빈 흰 페이지
                images.append(PILImage.new("RGB", (1240, 1754), color=(255, 255, 255)))
        return images


# 싱글턴
ocr_engine = OcrEngine()
