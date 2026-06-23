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

# 이 모듈 전용 로거 생성 — 로그 메시지에 모듈 이름이 함께 출력된다
logger = logging.getLogger(__name__)

# LLM에게 전달하는 시스템 지시문:
# LLM이 어떤 역할을 맡을지, 무엇을 교정해야 하는지, 무엇을 건드리면 안 되는지를 정의한다
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

# LLM에게 교정을 요청할 때 사용하는 사용자 메시지 템플릿
# {text} 자리에 실제 OCR 인식 텍스트가 삽입된다
_USER_TMPL = "다음 OCR 인식 텍스트의 오류를 교정하세요:\n\n{text}"

# 이 글자 수 미만 페이지는 LLM 교정 생략 (도표·빈 페이지 등)
# 너무 짧은 텍스트는 교정 효과가 없고 API 비용만 낭비되므로 건너뛴다
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
    """Anthropic Claude API를 호출하여 OCR 텍스트를 교정한다.

    매개변수:
        text:       교정할 OCR 인식 텍스트
        api_key:    Anthropic API 키
        model:      사용할 Claude 모델 ID (예: "claude-3-5-sonnet-20241022")
        max_tokens: LLM이 생성할 최대 토큰 수 (응답 길이 상한선)
        timeout:    API 응답 대기 최대 시간 (초 단위)

    반환값:
        교정된 텍스트 문자열
    """
    # anthropic 패키지는 함수 호출 시점에만 임포트한다.
    # 이렇게 하면 패키지가 설치되지 않은 환경에서도 모듈 자체는 정상 로드된다
    from anthropic import Anthropic

    # API 키와 타임아웃을 설정하여 Claude 클라이언트 객체를 생성한다
    client = Anthropic(api_key=api_key, timeout=timeout)

    # Claude Messages API를 호출하여 교정을 요청한다
    # system: LLM의 역할과 행동 지침
    # messages: 실제 교정 요청 메시지
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": _USER_TMPL.format(text=text)}],
    )

    # 응답 메시지의 첫 번째 콘텐츠 블록에서 텍스트를 추출하여 반환한다
    return msg.content[0].text


def _correct_gemini(
    text: str,
    api_key: str,
    model: str,
    timeout: int,
) -> str:
    """Google Gemini API를 호출하여 OCR 텍스트를 교정한다.

    매개변수:
        text:     교정할 OCR 인식 텍스트
        api_key:  Google AI API 키
        model:    사용할 Gemini 모델 ID (예: "gemini-1.5-pro")
        timeout:  API 응답 대기 최대 시간 (초 단위)

    반환값:
        교정된 텍스트 문자열
    """
    # google-generativeai 패키지도 함수 호출 시점에만 임포트한다
    import google.generativeai as genai

    # API 키를 전역으로 설정한다 (Gemini SDK의 인증 방식)
    genai.configure(api_key=api_key)

    # 시스템 지시문을 포함한 Gemini 모델 객체를 생성한다
    # system_instruction이 Claude의 system 파라미터에 해당한다
    model_obj = genai.GenerativeModel(
        model_name=model,
        system_instruction=_SYSTEM_PROMPT,
    )

    # 교정 요청을 전송하고 응답을 받는다
    # request_options로 타임아웃을 지정한다
    response = model_obj.generate_content(
        _USER_TMPL.format(text=text),
        request_options={"timeout": timeout},
    )

    # 응답 객체에서 교정된 텍스트를 반환한다
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

    파이프라인 외부에서 직접 호출하는 공개 함수다.
    provider에 따라 Claude 또는 Gemini API를 선택하여 교정을 수행한다.
    오류가 발생하면 원본 텍스트를 그대로 반환하여 파이프라인이 중단되지 않는다.

    매개변수:
        text:       EasyOCR 인식 결과 (단일 페이지)
        provider:   사용할 LLM 서비스 — "claude" 또는 "gemini"
        api_key:    해당 provider 의 API 키
        model:      사용할 모델 ID
        max_tokens: 출력 최대 토큰 수 (Claude 전용; Gemini 는 무시)
        timeout:    API 타임아웃 (초)

    반환값:
        교정된 텍스트. 실패 시 원본 text 반환.
    """
    # 텍스트가 비어있거나 최소 글자 수(_MIN_CHARS)보다 짧으면 교정 없이 원본 반환
    # 빈 페이지나 도표처럼 텍스트가 거의 없는 경우 불필요한 API 호출을 막는다
    if not text or len(text.strip()) < _MIN_CHARS:
        return text

    try:
        # provider 값에 따라 적절한 교정 함수를 호출한다
        if provider == "gemini":
            return _correct_gemini(text, api_key, model, timeout)
        else:
            # "gemini"가 아닌 경우 기본값으로 Claude를 사용한다
            return _correct_claude(text, api_key, model, max_tokens, timeout)

    except ImportError:
        # 필요한 패키지가 설치되지 않은 경우 안내 메시지를 남기고 원본 반환
        pkg = "google-generativeai" if provider == "gemini" else "anthropic"
        logger.error("패키지 미설치 — `pip install %s`", pkg)
        return text

    except Exception as e:
        # API 호출 실패, 네트워크 오류 등 그 밖의 모든 예외를 처리한다
        # 교정 실패 시에도 파이프라인이 계속 진행될 수 있도록 원본 텍스트를 반환한다
        logger.warning("LLM(%s) 교정 실패 (원본 사용): %s", provider, e)
        return text
