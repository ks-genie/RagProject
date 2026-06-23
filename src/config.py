# -*- coding: utf-8 -*-
"""
설정 로더 — config.yaml + .env 통합 관리

이 모듈은 프로젝트 전체에서 사용하는 설정값을 한 곳에서 관리합니다.
config.yaml 파일에서 기본값을 읽어오고, .env 파일의 환경 변수로
민감한 정보(API 키 등)를 덮어씁니다.
"""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# 이 파일(src/config.py)의 부모 폴더(src)의 부모 폴더가 프로젝트 루트입니다.
# Path(__file__).parent.parent 를 사용하면 파일 위치가 바뀌어도 루트를 정확히 찾습니다.
ROOT_DIR = Path(__file__).parent.parent

# config.yaml 파일의 전체 경로를 미리 계산해 둡니다.
CONFIG_PATH = ROOT_DIR / "config.yaml"

# 프로젝트 루트에 있는 .env 파일을 읽어 환경 변수로 로드합니다.
# 이렇게 하면 os.getenv()로 .env의 값을 가져올 수 있습니다.
load_dotenv(ROOT_DIR / ".env")


def _load_yaml() -> dict:
    """
    config.yaml 파일을 읽어 파이썬 딕셔너리(dict)로 반환합니다.

    yaml.safe_load()는 YAML 파일을 안전하게 파싱합니다.
    'safe'를 사용하는 이유는 임의 파이썬 객체 실행을 방지하기 위해서입니다.

    반환값:
        dict: config.yaml 전체 내용을 담은 딕셔너리
    """
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


class Config:
    """
    프로젝트 전체 설정을 한 곳에서 관리하는 클래스입니다.

    config.yaml 파일과 .env 환경 변수를 읽어 각 설정값을
    인스턴스 속성으로 저장합니다. 속성에 직접 접근하는 방식으로
    설정값을 사용합니다 (예: config.watch_dir, config.claude_model).

    .env 파일의 환경 변수가 config.yaml 값보다 우선 적용됩니다.
    API 키처럼 민감한 정보는 반드시 .env 파일에 저장하세요.
    """

    def __init__(self):
        """
        Config 객체를 초기화합니다.

        config.yaml을 읽어 각 섹션별로 설정값을 파싱하고
        인스턴스 속성으로 저장합니다.
        raw.get("키", 기본값) 형태를 사용하므로,
        config.yaml에 해당 키가 없어도 기본값이 자동으로 적용됩니다.
        """
        # config.yaml 전체를 딕셔너리로 읽어옵니다.
        raw = _load_yaml()

        # --- 감시 디렉토리 ---
        # PDF 파일을 자동으로 감시할 폴더 경로입니다.
        # 프로젝트 루트를 기준으로 상대 경로를 절대 경로로 변환합니다.
        self.watch_dir: Path = ROOT_DIR / raw.get("watch_dir", "data/pdf_watch")

        # --- OCR (광학 문자 인식) 설정 ---
        # config.yaml의 'ocr:' 섹션을 가져옵니다. 없으면 빈 딕셔너리를 사용합니다.
        ocr = raw.get("ocr", {})

        # Tesseract OCR이 인식할 언어입니다. 'kor+eng'는 한국어와 영어를 함께 인식합니다.
        self.ocr_language: str = ocr.get("language", "kor+eng")

        # Tesseract 실행 파일의 경로입니다. None이면 시스템 PATH에서 자동으로 찾습니다.
        self.ocr_tesseract_cmd: str | None = ocr.get("tesseract_cmd")

        # Tesseract 언어 데이터 파일(.traineddata)이 있는 폴더 경로입니다.
        self.ocr_tessdata_dir: str | None = ocr.get("tessdata_dir")

        # OCR 결과로 인정하는 최소 텍스트 길이입니다.
        # 이 값보다 짧으면 OCR이 실패한 것으로 간주합니다.
        self.ocr_min_text_length: int = ocr.get("min_text_length", 50)

        # OCR 품질 임계값(0.0 ~ 1.0)입니다.
        # 이 값 미만이면 품질이 낮은 것으로 판단해 후처리를 수행합니다.
        self.ocr_quality_threshold: float = ocr.get("quality_threshold", 0.5)

        # --- 데이터베이스 설정 ---
        # config.yaml의 'database:' 섹션을 가져옵니다.
        db = raw.get("database", {})

        # SQLite 데이터베이스 파일의 경로입니다.
        self.db_path: Path = ROOT_DIR / db.get("path", "data/rag_project.db")

        # --- AnythingLLM 설정 (.env 환경 변수가 config.yaml보다 우선 적용됨) ---
        # config.yaml의 'anythingllm:' 섹션을 가져옵니다.
        allm = raw.get("anythingllm", {})

        # AnythingLLM 서버의 API 주소입니다.
        # os.getenv()가 None을 반환하면(환경 변수 없음) config.yaml 값을 사용합니다.
        self.allm_base_url: str = (
            os.getenv("ANYTHINGLLM_BASE_URL") or allm.get("base_url", "http://localhost:3001")
        )

        # AnythingLLM API 인증 키입니다. .env의 ANYTHINGLLM_API_KEY를 우선 사용합니다.
        self.allm_api_key: str = (
            os.getenv("ANYTHINGLLM_API_KEY") or allm.get("api_key", "")
        )

        # 문서를 업로드할 AnythingLLM 워크스페이스 이름입니다.
        self.allm_workspace: str = (
            os.getenv("ANYTHINGLLM_WORKSPACE") or allm.get("workspace", "")
        )

        # 파일 업로드 요청이 완료될 때까지 기다리는 최대 시간(초)입니다.
        self.allm_upload_timeout: int = allm.get("upload_timeout", 30)

        # --- LLM OCR 후처리 교정 설정 (Claude · Gemini) ---
        # config.yaml의 'llm_correction:' 섹션을 읽습니다.
        # 과거 버전에서는 'claude:'라는 섹션명을 사용했으므로, 하위 호환을 위해
        # 'llm_correction'이 없으면 'claude'를 대신 사용합니다.
        llm = raw.get("llm_correction", raw.get("claude", {}))  # 구 섹션명 호환

        # OCR 결과를 LLM으로 교정하는 기능을 사용할지 여부입니다.
        self.llm_correction_enabled: bool = llm.get("enabled", False)

        # 교정에 사용할 LLM 제공자입니다. "claude" 또는 "gemini" 중 하나입니다.
        self.llm_provider: str = llm.get("provider", "claude")  # "claude" | "gemini"

        # Claude 전용 설정을 읽습니다.
        # 과거 버전처럼 claude: 하위 섹션이 없는 경우 llm 섹션 자체를 사용합니다.
        claude_cfg = llm.get("claude", llm)   # 구 claude: 단일 섹션도 허용

        # Claude API 키입니다. .env의 ANTHROPIC_API_KEY를 우선 사용합니다.
        self.claude_api_key: str = os.getenv("ANTHROPIC_API_KEY") or claude_cfg.get("api_key", "")

        # 사용할 Claude 모델의 이름입니다.
        self.claude_model: str = claude_cfg.get("model", "claude-haiku-4-5-20251001")

        # Claude가 한 번의 응답에서 생성할 수 있는 최대 토큰(단어 조각) 수입니다.
        self.claude_max_tokens: int = claude_cfg.get("max_tokens", 8192)

        # Claude API 요청이 완료될 때까지 기다리는 최대 시간(초)입니다.
        self.claude_timeout: int = claude_cfg.get("timeout", 60)

        # Gemini 전용 설정을 읽습니다.
        gemini_cfg = llm.get("gemini", {})

        # Gemini API 키입니다. .env의 GOOGLE_API_KEY를 우선 사용합니다.
        self.gemini_api_key: str = os.getenv("GOOGLE_API_KEY") or gemini_cfg.get("api_key", "")

        # 사용할 Gemini 모델의 이름입니다.
        self.gemini_model: str = gemini_cfg.get("model", "gemini-2.0-flash")

        # Gemini API 요청이 완료될 때까지 기다리는 최대 시간(초)입니다.
        self.gemini_timeout: int = gemini_cfg.get("timeout", 60)

        # --- 색인 추적 설정 ---
        # AnythingLLM이 문서를 색인(인덱싱)하는 작업을 완료했는지 확인하는 설정입니다.
        idx = raw.get("indexing", {})

        # 색인 완료 여부를 확인하는 주기(초)입니다. 너무 짧으면 서버에 부담이 됩니다.
        self.indexing_poll_interval: int = idx.get("poll_interval", 10)

        # 색인 완료를 기다리는 최대 시간(초)입니다. 이 시간을 초과하면 오류로 처리합니다.
        self.indexing_timeout: int = idx.get("timeout", 300)

        # --- 재시도 설정 ---
        # API 호출 등이 실패했을 때 자동으로 재시도하는 방식을 설정합니다.
        retry = raw.get("retry", {})

        # 실패 시 최대 재시도 횟수입니다.
        self.retry_max_attempts: int = retry.get("max_attempts", 3)

        # 각 재시도 사이에 기다리는 시간(초) 목록입니다.
        # [60, 120, 240]이면 1차 실패 후 60초, 2차 실패 후 120초, 3차 실패 후 240초 대기합니다.
        # 점점 대기 시간을 늘리는 방식을 지수 백오프(Exponential Backoff)라고 합니다.
        self.retry_backoff_seconds: list[int] = retry.get("backoff_seconds", [60, 120, 240])

        # --- 로그 설정 ---
        # 애플리케이션 동작 기록(로그)과 관련된 설정입니다.
        log = raw.get("logging", {})

        # 로그 출력 수준입니다. "DEBUG" < "INFO" < "WARNING" < "ERROR" < "CRITICAL" 순으로
        # 상위 레벨일수록 덜 상세한 로그를 출력합니다.
        self.log_level: str = log.get("level", "INFO")

        # 로그를 저장할 파일의 경로입니다.
        self.log_file: Path = ROOT_DIR / log.get("file", "logs/rag_project.log")

    def validate(self) -> list[str]:
        """
        설정값이 올바른지 검사하고 문제 항목 목록을 반환합니다.

        API 키나 워크스페이스처럼 반드시 설정해야 하는 값이
        비어 있는지 확인합니다. 애플리케이션 시작 시 호출해서
        설정 오류를 미리 발견하는 데 사용합니다.

        반환값:
            list[str]: 문제가 있는 항목의 설명 문자열 목록.
                       빈 리스트([])이면 모든 설정이 정상입니다.
        """
        # 문제 항목을 담을 빈 리스트를 준비합니다.
        issues = []

        # AnythingLLM API 키가 비어 있으면 문서 업로드가 불가능하므로 오류로 기록합니다.
        if not self.allm_api_key:
            issues.append("anythingllm.api_key 가 비어 있습니다 (.env 또는 config.yaml 확인)")

        # AnythingLLM 워크스페이스가 지정되지 않으면 어디에 업로드할지 알 수 없으므로 오류입니다.
        if not self.allm_workspace:
            issues.append("anythingllm.workspace 가 비어 있습니다")

        return issues


# 싱글턴(Singleton) 패턴: 모듈이 처음 임포트될 때 Config 객체를 딱 한 번만 생성합니다.
# 다른 파일에서 'from src.config import config' 로 가져다 쓰면
# 항상 같은 객체를 공유하므로 설정이 일관되게 유지됩니다.
config = Config()
