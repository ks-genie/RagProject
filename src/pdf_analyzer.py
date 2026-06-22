# -*- coding: utf-8 -*-
"""
PDF Analyzer — PDF 문서를 분류 분석하여 PDFProfile 반환

분석 항목:
  1. source_type  : DIGITAL (텍스트 레이어 존재) / SCANNED (이미지 기반)
  2. layout_type  : SINGLE_COLUMN / MULTI_COLUMN (2단 이상 논문 형식)
  3. languages    : kor / eng / jpn 포함 여부
  4. has_formula  : 수식 포함 여부
"""

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np
import pdfplumber
import pypdfium2 as pdfium
from langdetect import detect_langs, LangDetectException

from src.config import config
from src.database import SourceType, LayoutType

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 언어 감지 임계값 (해당 문자 비율 기준)
# ---------------------------------------------------------------------------
LANG_THRESHOLD = 0.03          # 전체 텍스트 대비 3% 이상이면 해당 언어 포함으로 판정
FORMULA_CHAR_THRESHOLD = 0.008 # 수학 기호 밀도 0.8% 이상이면 수식 포함으로 판정

# Unicode 범위
_KOR_RE  = re.compile(r'[\uAC00-\uD7A3\u1100-\u11FF\u3130-\u318F]')  # 한글
_JPN_RE  = re.compile(r'[\u3040-\u309F\u30A0-\u30FF]')               # 히라가나/가타카나
_ENG_RE  = re.compile(r'[A-Za-z]')                                    # 영문
_MATH_RE = re.compile(                                                 # 수학 기호
    r'[\u2200-\u22FF'   # Mathematical Operators
    r'\u2A00-\u2AFF'    # Supplemental Math Operators
    r'\u0370-\u03FF'    # Greek (α β γ 등)
    r'\u222B\u221E\u2211\u220F\u2202\u2207\u2260\u2264\u2265]'
)


# ---------------------------------------------------------------------------
# PDFProfile — 분석 결과 컨테이너
# ---------------------------------------------------------------------------
@dataclass
class PDFProfile:
    source_type: str
    layout_type: str
    detected_languages: list[str] = field(default_factory=list)
    has_formula: bool = False
    page_count: int = 0
    avg_chars_per_page: float = 0.0

    def __str__(self) -> str:
        langs = ",".join(self.detected_languages) or "unknown"
        formula = "수식 있음" if self.has_formula else "수식 없음"
        return (
            f"[{self.source_type}] [{self.layout_type}] "
            f"언어:{langs} | {formula} | {self.page_count}페이지"
        )


# ---------------------------------------------------------------------------
# PDFAnalyzer
# ---------------------------------------------------------------------------
class PDFAnalyzer:

    def analyze(self, file_path: str) -> PDFProfile:
        """파일을 분석하여 PDFProfile 반환 (PDF / DOCX / PPTX)"""
        suffix = Path(file_path).suffix.lower()
        if suffix == ".docx":
            return self._analyze_docx(file_path)
        if suffix == ".pptx":
            return self._analyze_pptx(file_path)
        return self._analyze_pdf(file_path)

    def _analyze_pdf(self, pdf_path: str) -> PDFProfile:
        """PDF 파일을 분석하여 PDFProfile 반환"""
        logger.info("PDF 분석 시작: %s", pdf_path)

        # ── 1. 소스 유형 및 텍스트 추출 ─────────────────────────────────────
        source_type, full_text, avg_chars, page_count = self._detect_source_type(pdf_path)
        logger.debug("소스 유형: %s (평균 %.1f자/페이지)", source_type, avg_chars)

        # ── 2. 언어 감지 ────────────────────────────────────────────────────
        sample_text = full_text[:3000] if full_text else ""
        languages = self._detect_languages(sample_text, fallback_path=pdf_path)
        logger.debug("감지된 언어: %s", languages)

        # ── 3. 수식 감지 ────────────────────────────────────────────────────
        has_formula = self._detect_formula_digital(full_text)
        if source_type == SourceType.SCANNED and not has_formula:
            # 스캔본은 이미지 기반으로 추가 검사
            images = self._get_page_images(pdf_path, max_pages=3)
            has_formula = self._detect_formula_scanned(images)
        logger.debug("수식 포함: %s", has_formula)

        # ── 4. 레이아웃 분석 ─────────────────────────────────────────────────
        if source_type == SourceType.DIGITAL:
            layout_type = self._detect_layout_digital(pdf_path)
        else:
            images = self._get_page_images(pdf_path, max_pages=3)
            layout_type = self._detect_layout_scanned(images)
        logger.debug("레이아웃: %s", layout_type)

        profile = PDFProfile(
            source_type=source_type,
            layout_type=layout_type,
            detected_languages=languages,
            has_formula=has_formula,
            page_count=page_count,
            avg_chars_per_page=avg_chars,
        )
        logger.info("PDF 분석 완료: %s", profile)
        return profile

    def _analyze_docx(self, file_path: str) -> PDFProfile:
        """Word 문서(.docx)를 분석하여 PDFProfile 반환."""
        logger.info("DOCX 분석 시작: %s", file_path)
        try:
            from docx import Document
            doc = Document(file_path)
            full_text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())
            page_count = max(1, len(doc.sections))
        except Exception as e:
            logger.warning("DOCX 열기 실패 [%s]: %s", file_path, e)
            full_text = ""
            page_count = 1

        languages   = self._detect_languages(full_text[:3000])
        has_formula = self._detect_formula_digital(full_text)
        profile = PDFProfile(
            source_type=SourceType.DOCX,
            layout_type=LayoutType.SINGLE_COLUMN,
            detected_languages=languages,
            has_formula=has_formula,
            page_count=page_count,
            avg_chars_per_page=len(full_text) / page_count,
        )
        logger.info("DOCX 분석 완료: %s", profile)
        return profile

    def _analyze_pptx(self, file_path: str) -> PDFProfile:
        """PowerPoint 파일(.pptx)을 분석하여 PDFProfile 반환."""
        logger.info("PPTX 분석 시작: %s", file_path)
        try:
            from pptx import Presentation
            prs = Presentation(file_path)
            texts = []
            for slide in prs.slides:
                for shape in slide.shapes:
                    if shape.has_text_frame:
                        t = shape.text_frame.text.strip()
                        if t:
                            texts.append(t)
            full_text  = "\n".join(texts)
            page_count = max(1, len(prs.slides))
        except Exception as e:
            logger.warning("PPTX 열기 실패 [%s]: %s", file_path, e)
            full_text  = ""
            page_count = 1

        languages   = self._detect_languages(full_text[:3000])
        has_formula = self._detect_formula_digital(full_text)
        profile = PDFProfile(
            source_type=SourceType.PPTX,
            layout_type=LayoutType.SINGLE_COLUMN,
            detected_languages=languages,
            has_formula=has_formula,
            page_count=page_count,
            avg_chars_per_page=len(full_text) / page_count,
        )
        logger.info("PPTX 분석 완료: %s", profile)
        return profile

    # ------------------------------------------------------------------
    # 1. 소스 유형 판별
    # ------------------------------------------------------------------
    def _detect_source_type(
        self, pdf_path: str
    ) -> tuple[str, str, float, int]:
        """pdfplumber로 텍스트 추출 → DIGITAL / SCANNED 판별.
        반환: (source_type, full_text, avg_chars_per_page, page_count)
        """
        texts: list[str] = []
        try:
            with pdfplumber.open(pdf_path) as pdf:
                page_count = len(pdf.pages)
                for page in pdf.pages:
                    t = page.extract_text() or ""
                    texts.append(t)
        except Exception as e:
            logger.warning("pdfplumber 열기 실패 [%s]: %s", pdf_path, e)
            return SourceType.SCANNED, "", 0.0, 0

        full_text = "\n".join(texts)
        total_chars = sum(len(t) for t in texts)
        avg_chars = total_chars / page_count if page_count else 0.0

        source_type = (
            SourceType.DIGITAL
            if avg_chars >= config.ocr_min_text_length
            else SourceType.SCANNED
        )
        return source_type, full_text, avg_chars, page_count

    # ------------------------------------------------------------------
    # 2. 언어 감지
    # ------------------------------------------------------------------
    def _detect_languages(self, text: str, fallback_path: str = "") -> list[str]:
        """Unicode 비율 분석 + langdetect 보정으로 언어 목록 반환"""
        if not text or len(text) < 20:
            # SCANNED 문서처럼 텍스트가 없을 때: 파일명에 한글이 있으면 kor 포함
            if fallback_path and _KOR_RE.search(Path(fallback_path).name):
                return ["kor", "eng"]
            return ["eng"]  # 기본값

        total = max(len(text), 1)
        languages: list[str] = []

        # Unicode 범위 기반 1차 판정
        if len(_KOR_RE.findall(text)) / total >= LANG_THRESHOLD:
            languages.append("kor")
        if len(_JPN_RE.findall(text)) / total >= LANG_THRESHOLD:
            languages.append("jpn")
        if len(_ENG_RE.findall(text)) / total >= LANG_THRESHOLD:
            languages.append("eng")

        # langdetect 보정 (누락 언어 보완)
        try:
            detected = detect_langs(text[:1000])
            for lang_prob in detected:
                code = lang_prob.lang
                prob = lang_prob.prob
                if code == "ko" and prob > 0.3 and "kor" not in languages:
                    languages.append("kor")
                elif code == "ja" and prob > 0.3 and "jpn" not in languages:
                    languages.append("jpn")
                elif code == "en" and prob > 0.3 and "eng" not in languages:
                    languages.append("eng")
        except LangDetectException:
            pass

        return languages if languages else ["eng"]

    # ------------------------------------------------------------------
    # 3. 수식 감지
    # ------------------------------------------------------------------
    def _detect_formula_digital(self, text: str) -> bool:
        """Unicode 수학 기호 밀도로 수식 포함 여부 판별"""
        if not text:
            return False
        math_chars = len(_MATH_RE.findall(text))
        density = math_chars / max(len(text), 1)
        return density >= FORMULA_CHAR_THRESHOLD

    def _detect_formula_scanned(self, images: list) -> bool:
        """이미지에서 수식 특징(수평 세선, 고밀도 소형 윤곽) 탐지"""
        formula_page_count = 0
        for img_pil in images:
            img = np.array(img_pil.convert("L"))  # 그레이스케일
            _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

            # 수평 세선(분수선 등) 감지: 폭이 넓고 높이가 매우 낮은 윤곽
            contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            h_img, w_img = binary.shape
            formula_hints = 0
            for cnt in contours:
                x, y, w, h = cv2.boundingRect(cnt)
                aspect_ratio = w / max(h, 1)
                relative_w = w / w_img
                # 분수선: 가로로 길고 높이 1~3px, 페이지 너비의 2% 이상
                # (한글 자모는 분수선 모양이 되지 않으므로 이것만 사용)
                if h <= 3 and aspect_ratio > 10 and relative_w > 0.02:
                    formula_hints += 1

            if formula_hints >= 3:
                formula_page_count += 1

        return formula_page_count >= 1

    # ------------------------------------------------------------------
    # 4. 레이아웃 분석
    # ------------------------------------------------------------------
    def _detect_layout_digital(self, pdf_path: str) -> str:
        """pdfplumber로 다단 레이아웃 판별 — 줄(line) 단위 x-커버리지 방식.

        단어 위치를 줄 단위로 집계하여 '각 x 위치를 덮는 줄의 비율'을 계산한다.
        제목·저자 등 전폭(full-width) 줄이 일부 포함돼도 다수의 2단 본문 줄에
        희석되어 중앙 갭이 명확히 드러난다.

        BINS=200 (0.5% 해상도)로 좁은 갭(~15pt on 612pt ≈ 2.45%)도 탐지 가능.
        """
        multi_count = 0
        analyzed_pages = 0
        BINS    = 200   # x축 0.5% 단위 — 좁은 갭(~1.5%) 탐지에 충분한 해상도
        LINE_H  = 3     # 같은 줄로 묶는 y 허용오차 (pt)
        MIN_GAP = 3     # 컬럼 갭 최소 너비: 3 bin = 1.5% of page (~9pt on letter)

        try:
            with pdfplumber.open(pdf_path) as pdf:
                for page in pdf.pages[:5]:
                    words = page.extract_words()
                    if len(words) < 6:
                        continue
                    page_width = float(page.width)

                    # ── 줄별 x 범위 수집 ─────────────────────────────────
                    y_ranges: dict = defaultdict(list)
                    for w in words:
                        yk = round(float(w["top"]) / LINE_H) * LINE_H
                        x0b = max(0, int(float(w["x0"]) / page_width * BINS))
                        x1b = min(BINS - 1, int(float(w["x1"]) / page_width * BINS))
                        y_ranges[yk].append((x0b, x1b))

                    n_lines = len(y_ranges)
                    # 텍스트가 충분한 페이지만 분석 (그림·참고문헌 페이지 제외)
                    if n_lines < 10:
                        continue
                    analyzed_pages += 1

                    # ── 줄 단위 커버리지: 각 bin을 덮는 줄의 비율 ──────────
                    line_cov = np.zeros(BINS)
                    for ranges in y_ranges.values():
                        covered = np.zeros(BINS, dtype=bool)
                        for x0b, x1b in ranges:
                            covered[x0b: x1b + 1] = True
                        line_cov += covered

                    line_cov /= n_lines  # 0~1 정규화

                    # ── 중앙 35~65% 구간에서 커버리지가 낮은 공간 탐색 ────
                    s = int(BINS * 0.35)
                    e = int(BINS * 0.65)
                    center_cov = line_cov[s:e]
                    side_mean  = float(
                        (line_cov[:s].mean() + line_cov[e:].mean()) / 2
                    )

                    if side_mean <= 0.10:
                        continue

                    # 빈 공간 판정: 양쪽 밀도의 30% 이하이거나 25% 이하인 bin
                    gap_thresh = min(0.25, side_mean * 0.30)
                    max_gap = curr = 0
                    for v in (center_cov < gap_thresh):
                        curr = int(curr + 1) if v else 0
                        if curr > max_gap:
                            max_gap = curr

                    logger.debug(
                        "레이아웃 디지털 p%d: n_lines=%d, side_mean=%.2f, "
                        "gap_thresh=%.2f, max_gap=%d (min=%d)",
                        analyzed_pages, n_lines, side_mean,
                        gap_thresh, max_gap, MIN_GAP,
                    )

                    if max_gap >= MIN_GAP:
                        multi_count += 1

        except Exception as e:
            logger.warning("레이아웃 분석 실패: %s", e)

        # 분석된 페이지의 2/3 이상이 다단이면 MULTI_COLUMN
        threshold = max(1, analyzed_pages * 2 // 3)
        return LayoutType.MULTI_COLUMN if multi_count >= threshold else LayoutType.SINGLE_COLUMN

    def _detect_layout_scanned(self, images: list) -> str:
        """OpenCV 수직 projection으로 다단 레이아웃 판별.

        수직 투영(column-sum)에 스무딩을 적용하여 글자 사이의 짧은 공백 노이즈를
        제거한 뒤, 컬럼 사이의 넓은 골(valley)만 탐지한다.
        """
        multi_count = 0
        analyzed = 0
        for img_pil in images:
            img = np.array(img_pil.convert("L"))
            _, binary = cv2.threshold(img, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

            # 수직 projection: 각 x열의 픽셀 합
            col_sums = binary.sum(axis=0).astype(float)
            if col_sums.max() == 0:
                continue

            analyzed += 1
            col_sums /= col_sums.max()  # 정규화

            w = len(col_sums)
            # 글자 간 노이즈 제거 — 페이지 폭의 4% 커널로 스무딩
            k = max(1, int(w * 0.04))
            smoothed = np.convolve(col_sums, np.ones(k) / k, mode="same")
            smoothed /= max(smoothed.max(), 1e-9)

            # 중앙 35~65% 구간에서 골(valley) 탐색
            s = int(w * 0.35)
            e = int(w * 0.65)
            valley_depth = float(smoothed[s:e].min())
            side_mean = float((smoothed[:s].mean() + smoothed[e:].mean()) / 2)

            # 스무딩 후 골이 양쪽 텍스트 영역 평균의 50% 미만이면 다단
            # (좁은 컬럼 간격에도 민감하게 반응하도록 임계값 완화)
            if side_mean > 0.01 and valley_depth < side_mean * 0.50:
                multi_count += 1

        # 분석된 페이지의 2/3 이상이 다단이면 MULTI_COLUMN
        threshold = max(1, analyzed * 2 // 3)
        return LayoutType.MULTI_COLUMN if multi_count >= threshold else LayoutType.SINGLE_COLUMN

    # ------------------------------------------------------------------
    # 유틸 — 페이지 이미지 변환 (pypdfium2 사용, poppler 불필요)
    # ------------------------------------------------------------------
    def _get_page_images(self, pdf_path: str, max_pages: int = 3) -> list:
        images = []
        try:
            pdf = pdfium.PdfDocument(pdf_path)
            n = min(max_pages, len(pdf))
        except Exception as e:
            logger.warning("PDF 열기 실패: %s", e)
            return images
        for i in range(n):
            try:
                page = pdf[i]
                bitmap = page.render(scale=1.5)  # 약 108 DPI
                images.append(bitmap.to_pil())
            except Exception as e:
                logger.warning("페이지 %d 이미지 변환 실패 (건너뜀): %s", i, e)
        return images


# 싱글턴
analyzer = PDFAnalyzer()
