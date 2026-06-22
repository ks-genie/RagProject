# -*- coding: utf-8 -*-
"""하위 호환성 shim — 실제 구현은 llm_corrector.py 로 이전됨."""
from src.llm_corrector import correct_ocr_page as _correct


def correct_ocr_page(
    text: str,
    api_key: str,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 8192,
    timeout: int = 60,
) -> str:
    return _correct(
        text,
        provider="claude",
        api_key=api_key,
        model=model,
        max_tokens=max_tokens,
        timeout=timeout,
    )
