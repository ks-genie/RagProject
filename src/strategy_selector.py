# -*- coding: utf-8 -*-
"""
Strategy Selector — PDFProfile을 바탕으로 최적 OCR 전략 결정

문서의 특성(출처 유형, 레이아웃, 언어, 수식 포함 여부)을 분석한 결과인
PDFProfile을 입력받아, 가장 적합한 OCR 처리 전략을 자동으로 선택합니다.

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

# 이 모듈 전용 로거 생성 — 로그 메시지에 파일명이 자동으로 붙어서 디버깅이 쉬워집니다
logger = logging.getLogger(__name__)


class StrategySelector:
    """PDF 프로파일을 분석하여 최적의 OCR 전략을 결정하는 클래스.

    문서마다 특성이 다르기 때문에 (스캔본 vs 디지털, 단일 컬럼 vs 다단,
    수식 포함 여부, 언어 종류 등) 각 상황에 맞는 처리 전략을 자동으로
    선택해서 OCR 품질을 최대화합니다.
    """

    def select(self, profile: PDFProfile) -> str:
        """프로파일을 받아 최적 전략을 결정하고, 결과를 로그에 기록한 뒤 반환합니다.

        내부적으로 _decide()를 호출해 전략을 결정하며,
        어떤 프로파일에서 어떤 전략이 선택되었는지 로그로 추적할 수 있습니다.

        매개변수:
            profile (PDFProfile): pdf_analyzer가 생성한 문서 분석 결과 객체.
                                  출처 유형, 레이아웃, 언어, 수식 포함 여부 등이 담겨 있습니다.

        반환값:
            str: 선택된 OCR 전략 이름 (OcrStrategy 상수 중 하나).
        """
        strategy = self._decide(profile)
        logger.info(
            "OCR 전략 결정: %s → %s",
            profile,
            strategy,
        )
        return strategy

    def _decide(self, profile: PDFProfile) -> str:
        """실제 전략 결정 로직을 담당하는 내부 메서드.

        문서 특성을 단계별로 검사하여 적합한 전략을 반환합니다.
        우선순위가 높은 조건부터 순서대로 확인하므로, 먼저 매칭된 전략이 선택됩니다.

        매개변수:
            profile (PDFProfile): 문서 분석 결과 객체.

        반환값:
            str: 선택된 OCR 전략 이름.
        """
        # Office 문서는 OCR 불필요 — 직접 텍스트 추출
        # DOCX/PPTX는 이미 텍스트 레이어가 있으므로 OCR 없이 바로 읽을 수 있습니다
        if profile.source_type == SourceType.DOCX:
            return OcrStrategy.DOCX_EXTRACT
        if profile.source_type == SourceType.PPTX:
            return OcrStrategy.PPTX_EXTRACT

        # Digital PDF는 OCR 불필요 — 다단이면 컬럼 인식 추출 사용
        # 디지털 PDF는 내부에 텍스트 데이터가 이미 포함되어 있어 OCR이 필요 없습니다.
        # 단, 다단(두 컬럼 이상) 레이아웃이면 컬럼 순서를 올바르게 인식하는 별도 추출 방식을 씁니다.
        if profile.source_type == SourceType.DIGITAL:
            if profile.layout_type == LayoutType.MULTI_COLUMN:
                return OcrStrategy.DIGITAL_EXTRACT_MULTI
            return OcrStrategy.DIGITAL_EXTRACT

        # 아래부터는 스캔 문서(이미지 기반)에 대한 OCR 전략 결정입니다

        # 자주 참조할 특성들을 변수에 미리 저장해 코드 가독성을 높입니다
        is_multi   = profile.layout_type == LayoutType.MULTI_COLUMN  # 다단 레이아웃 여부
        has_formula = profile.has_formula                              # 수식 포함 여부
        has_jpn    = "jpn" in profile.detected_languages              # 일본어 감지 여부
        has_kor    = "kor" in profile.detected_languages              # 한국어 감지 여부

        # 다단 + 수식 복합 (한국어 문서는 수식 마스킹 오탐지 방지를 위해 제외)
        # 한국어 문서에서는 수식 마스킹 알고리즘이 한글을 수식으로 잘못 인식하는
        # 오탐지 문제가 있어, 한국어가 감지된 경우에는 이 전략을 적용하지 않습니다.
        if is_multi and has_formula and not has_kor:
            return OcrStrategy.OCR_MULTI_FORMULA

        # 수식 전용 (한국어 문서는 수식 마스킹 오탐지 방지를 위해 제외)
        # 위와 같은 이유로 한국어가 포함된 경우 수식 전용 전략도 건너뜁니다.
        if has_formula and not has_kor:
            return OcrStrategy.OCR_FORMULA

        # 다단 레이아웃 — 컬럼을 분리해서 각각 OCR 처리
        if is_multi:
            return OcrStrategy.OCR_MULTI_COL

        # 단일 컬럼 — 언어별 분기
        # 일본어가 있으면 일본어 언어팩까지 포함해서 OCR을 수행합니다
        if has_jpn:
            return OcrStrategy.OCR_JPN

        # 한국어가 있으면 한영 혼합 모드로 OCR을 수행합니다
        if has_kor:
            return OcrStrategy.OCR_KOR_ENG

        # 위 조건에 모두 해당하지 않으면 영문 전용 OCR을 사용합니다
        return OcrStrategy.OCR_ENG


# 각 전략에 대응하는 Tesseract 파라미터 매핑 테이블.
# Tesseract는 오픈소스 OCR 엔진으로, 언어(lang)와 페이지 분할 모드(psm),
# OCR 엔진 모드(oem) 설정에 따라 인식 결과가 크게 달라집니다.
# psm=6: 균일한 텍스트 블록 단위로 인식 (일반적인 문서에 적합)
# oem=1: LSTM 신경망 기반 엔진 사용 (가장 정확한 현대적 방식)
STRATEGY_TESSERACT_CONFIG: dict[str, dict] = {
    OcrStrategy.DIGITAL_EXTRACT: {
        "lang": None,   # OCR을 사용하지 않으므로 언어 설정 불필요
        "psm": None,    # OCR을 사용하지 않으므로 페이지 분할 모드 불필요
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
        "lang": "eng",  # 영어 언어팩만 사용
        "psm": 6,       # 단일 균일 텍스트 블록으로 가정하고 인식
        "oem": 1,       # LSTM 신경망 기반 OCR 엔진 사용
        "note": "영문 단일 컬럼",
    },
    OcrStrategy.OCR_KOR_ENG: {
        "lang": "kor+eng",  # 한국어 + 영어 언어팩 동시 사용
        "psm": 6,
        "oem": 1,
        "note": "한영 혼합 단일 컬럼",
    },
    OcrStrategy.OCR_JPN: {
        "lang": "kor+eng+jpn",  # 한국어 + 영어 + 일본어 언어팩 동시 사용
        "psm": 6,
        "oem": 1,
        "note": "일본어 포함 단일 컬럼",
    },
    OcrStrategy.OCR_MULTI_COL: {
        "lang": "kor+eng",
        "psm": 6,
        # oem 미지정 — 다단 처리는 OpenCV로 컬럼을 분리한 후 각각 Tesseract를 호출하므로
        # 호출 시점에 별도로 oem을 지정합니다
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
    """주어진 전략 이름에 해당하는 Tesseract 설정 딕셔너리를 반환합니다.

    전략에 맞는 설정이 없으면 기본값으로 한영 혼합(OCR_KOR_ENG) 설정을 반환합니다.
    이는 예상치 못한 전략 이름이 들어왔을 때 OCR이 완전히 실패하는 것을 방지하기 위한
    안전장치(fallback)입니다.

    매개변수:
        strategy (str): 조회할 OCR 전략 이름 (OcrStrategy 상수 중 하나).

    반환값:
        dict: 해당 전략의 Tesseract 파라미터 설정 딕셔너리.
              lang, psm, oem, note 등의 키를 포함합니다.
    """
    return STRATEGY_TESSERACT_CONFIG.get(strategy, STRATEGY_TESSERACT_CONFIG[OcrStrategy.OCR_KOR_ENG])


# 싱글턴 — 모듈 로드 시 StrategySelector 인스턴스를 하나만 생성해 재사용합니다.
# 외부에서 `from src.strategy_selector import selector` 로 바로 가져다 쓸 수 있습니다.
selector = StrategySelector()
