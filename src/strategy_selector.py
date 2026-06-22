# -*- coding: utf-8 -*-
"""
Strategy Selector — PDFProfile을 바탕으로 최적 OCR 전략 결정

전략 우선순위:
  1. DIGITAL → DIGITAL_EXTRACT (OCR 불필요)
  2. SCANNED + 다단 + 수식 → OCR_MULTI_FORMULA
  3. SCANNED + 수식         → OCR_FORMULA
  4. SCANNED + 다단         → OCR_MULTI_COL
  5. SCANNED + jpn 포함     → OCR_JPN       (kor+eng+jpn)
  6. SCANNED + kor 포함     → OCR_KOR_ENG   (kor+eng)
  7. SCANNED + eng only     → OCR_ENG
"""

import logging
from src.database import SourceType, LayoutType, OcrStrategy
from src.pdf_analyzer import PDFProfile

logger = logging.getLogger(__name__)


class StrategySelector:

    def select(self, profile: PDFProfile) -> str:
        strategy = self._decide(profile)
        logger.info(
            "OCR 전략 결정: %s → %s",
            profile,
            strategy,
        )
        return strategy

    def _decide(self, profile: PDFProfile) -> str:
        # Office 문서는 OCR 불필요 — 직접 텍스트 추출
        if profile.source_type == SourceType.DOCX:
            return OcrStrategy.DOCX_EXTRACT
        if profile.source_type == SourceType.PPTX:
            return OcrStrategy.PPTX_EXTRACT

        # Digital PDF는 OCR 불필요 — 다단이면 컬럼 인식 추출 사용
        if profile.source_type == SourceType.DIGITAL:
            if profile.layout_type == LayoutType.MULTI_COLUMN:
                return OcrStrategy.DIGITAL_EXTRACT_MULTI
            return OcrStrategy.DIGITAL_EXTRACT

        is_multi   = profile.layout_type == LayoutType.MULTI_COLUMN
        has_formula = profile.has_formula
        has_jpn    = "jpn" in profile.detected_languages
        has_kor    = "kor" in profile.detected_languages

        # 다단 + 수식 복합 (한국어 문서는 수식 마스킹 오탐지 방지를 위해 제외)
        if is_multi and has_formula and not has_kor:
            return OcrStrategy.OCR_MULTI_FORMULA

        # 수식 전용 (한국어 문서는 수식 마스킹 오탐지 방지를 위해 제외)
        if has_formula and not has_kor:
            return OcrStrategy.OCR_FORMULA

        # 다단 레이아웃
        if is_multi:
            return OcrStrategy.OCR_MULTI_COL

        # 단일 컬럼 — 언어별 분기
        if has_jpn:
            return OcrStrategy.OCR_JPN

        if has_kor:
            return OcrStrategy.OCR_KOR_ENG

        return OcrStrategy.OCR_ENG


# 각 전략에 대응하는 Tesseract 파라미터
STRATEGY_TESSERACT_CONFIG: dict[str, dict] = {
    OcrStrategy.DIGITAL_EXTRACT: {
        "lang": None,
        "psm": None,
        "note": "OCR 불필요 — pdfplumber 직접 추출",
    },
    OcrStrategy.DIGITAL_EXTRACT_MULTI: {
        "lang": None,
        "psm": None,
        "note": "OCR 불필요 — 다단 컬럼 인식 pdfplumber 추출",
    },
    OcrStrategy.DOCX_EXTRACT: {
        "lang": None,
        "psm": None,
        "note": "OCR 불필요 — python-docx 텍스트 직접 추출",
    },
    OcrStrategy.PPTX_EXTRACT: {
        "lang": None,
        "psm": None,
        "note": "OCR 불필요 — python-pptx 슬라이드 텍스트 직접 추출",
    },
    OcrStrategy.OCR_ENG: {
        "lang": "eng",
        "psm": 6,
        "oem": 1,
        "note": "영문 단일 컬럼",
    },
    OcrStrategy.OCR_KOR_ENG: {
        "lang": "kor+eng",
        "psm": 6,
        "oem": 1,
        "note": "한영 혼합 단일 컬럼",
    },
    OcrStrategy.OCR_JPN: {
        "lang": "kor+eng+jpn",
        "psm": 6,
        "oem": 1,
        "note": "일본어 포함 단일 컬럼",
    },
    OcrStrategy.OCR_MULTI_COL: {
        "lang": "kor+eng",
        "psm": 6,
        "note": "다단 레이아웃 — OpenCV 컬럼 분할 후 처리",
    },
    OcrStrategy.OCR_FORMULA: {
        "lang": "kor+eng",
        "psm": 6,
        "oem": 1,
        "note": "수식 포함 — 수식 영역 마스킹 후 텍스트/수식 분리",
    },
    OcrStrategy.OCR_MULTI_FORMULA: {
        "lang": "kor+eng",
        "psm": 6,
        "oem": 1,
        "note": "다단 + 수식 복합 처리",
    },
}


def get_tesseract_config(strategy: str) -> dict:
    return STRATEGY_TESSERACT_CONFIG.get(strategy, STRATEGY_TESSERACT_CONFIG[OcrStrategy.OCR_KOR_ENG])


# 싱글턴
selector = StrategySelector()
