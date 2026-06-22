# -*- coding: utf-8 -*-
"""LLM OCR 텍스트 후처리 교정 — Claude · Gemini 공통 인터페이스.

EasyOCR 인식 후 남은 오인식 오류를 LLM으로 교정한다.
API 키 미설정·패키지 미설치·호출 실패 시 원본 텍스트를 그대로 반환하므로
파이프라인이 중단되지 않는다.

지원 provider:
  "claude"  — Anthropic Claude (anthropic 패키지 필요)
  "gemini"  — Google Gemini   (google-generativeai 패키지 필요)
"""
import logging

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
한국어 OCR 텍스트에서 스캔 과정의 오인식 오류만 교정합니다.

교정 대상:
• 받침(종성) 오인식: 을→올, 를→름·률, 된→되 등
• 자모 혼용: 학신→혁신, 용용→응용, 확기→획기, 끈→폰
• 외래어 오표기: 세그만트→세그먼트, 스마트포→스마트폰, 옛지→엣지, 헬스웨어→헬스케어
• OCR 특수문자 삽입: A[→AI, 이미.→이며, 소비률→소비를

불변 원칙:
• 원문 내용·의미를 추가하거나 삭제하지 않습니다
• 문단 구분·줄바꿈 구조를 그대로 유지합니다
• 맞춤법·문법 교정은 OCR 오류가 명백한 경우만 합니다
• 교정된 텍스트만 출력합니다 (설명·주석 없음)\
"""

_USER_TMPL = "다음 OCR 인식 텍스트의 오류를 교정하세요:\n\n{text}"

# 이 글자 수 미만 페이지는 LLM 교정 생략 (도표·빈 페이지 등)
_MIN_CHARS = 80


# ---------------------------------------------------------------------------
# Provider 구현
# ---------------------------------------------------------------------------
def _correct_claude(
    text: str,
    api_key: str,
    model: str,
    max_tokens: int,
    timeout: int,
) -> str:
    from anthropic import Anthropic
    client = Anthropic(api_key=api_key, timeout=timeout)
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _USER_TMPL.format(text=text)}],
    )
    return msg.content[0].text


def _correct_gemini(
    text: str,
    api_key: str,
    model: str,
    timeout: int,
) -> str:
    import google.generativeai as genai
    genai.configure(api_key=api_key)
    model_obj = genai.GenerativeModel(
        model_name=model,
        system_instruction=_SYSTEM_PROMPT,
    )
    response = model_obj.generate_content(
        _USER_TMPL.format(text=text),
        request_options={"timeout": timeout},
    )
    return response.text


# ---------------------------------------------------------------------------
# 공개 API
# ---------------------------------------------------------------------------
def correct_ocr_page(
    text: str,
    provider: str,
    api_key: str,
    model: str,
    max_tokens: int = 8192,
    timeout: int = 60,
) -> str:
    """단일 페이지 OCR 텍스트를 LLM으로 교정하여 반환.

    Args:
        text:       EasyOCR 인식 결과 (단일 페이지)
        provider:   "claude" | "gemini"
        api_key:    해당 provider 의 API 키
        model:      사용할 모델 ID
        max_tokens: 출력 최대 토큰 수 (Claude 전용; Gemini 는 무시)
        timeout:    API 타임아웃 (초)

    Returns:
        교정된 텍스트. 실패 시 원본 text 반환.
    """
    if not text or len(text.strip()) < _MIN_CHARS:
        return text

    try:
        if provider == "gemini":
            return _correct_gemini(text, api_key, model, timeout)
        else:
            return _correct_claude(text, api_key, model, max_tokens, timeout)
    except ImportError:
        pkg = "google-generativeai" if provider == "gemini" else "anthropic"
        logger.error("패키지 미설치 — `pip install %s`", pkg)
        return text
    except Exception as e:
        logger.warning("LLM(%s) 교정 실패 (원본 사용): %s", provider, e)
        return text
