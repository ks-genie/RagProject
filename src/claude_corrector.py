# -*- coding: utf-8 -*-
"""
하위 호환성 shim — 실제 구현은 llm_corrector.py 로 이전됨.

이 모듈은 과거 코드와의 호환성을 유지하기 위해 존재합니다.
예전에 claude_corrector.py를 직접 import하던 코드가 여전히 동작하도록,
실제 구현이 있는 llm_corrector.py의 함수를 그대로 감싸서 제공합니다.
"""

# 실제 OCR 교정 로직이 구현된 llm_corrector 모듈에서 함수를 가져옵니다.
# 언더스코어(_)로 시작하는 이름은 "내부용"임을 나타내는 파이썬 관례입니다.
from src.llm_corrector import correct_ocr_page as _correct


def correct_ocr_page(
    text: str,
    api_key: str,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 8192,
    timeout: int = 60,
) -> str:
    """OCR로 인식된 텍스트를 Claude AI를 사용해 교정합니다.

    이 함수는 하위 호환성을 위해 남겨진 래퍼(wrapper)입니다.
    내부적으로 llm_corrector.py의 correct_ocr_page 함수를 호출하며,
    provider를 "claude"로 고정해서 전달합니다.

    매개변수:
        text (str): OCR로 추출된 원본 텍스트. 오타나 인식 오류가 포함될 수 있습니다.
        api_key (str): Claude API 인증에 사용할 API 키.
        model (str): 사용할 Claude 모델 이름.
                     기본값은 "claude-haiku-4-5-20251001"입니다.
        max_tokens (int): 응답으로 생성할 최대 토큰(단어 단위) 수.
                          기본값은 8192입니다.
        timeout (int): API 요청 대기 시간 제한(초 단위).
                       이 시간이 지나도 응답이 없으면 오류를 발생시킵니다.
                       기본값은 60초입니다.

    반환값:
        str: AI가 교정한 텍스트 문자열.
    """
    # provider를 "claude"로 지정하여 실제 구현 함수를 호출합니다.
    # 나머지 인자들은 그대로 전달합니다.
    return _correct(
        text,
        provider="claude",
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        timeout=timeout,
    )
