# -*- coding: utf-8 -*-
"""
설정 로더 — config.yaml + .env 통합 관리
"""

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

# 프로젝트 루트 기준
ROOT_DIR = Path(__file__).parent.parent
CONFIG_PATH = ROOT_DIR / "config.yaml"

load_dotenv(ROOT_DIR / ".env")


def _load_yaml() -> dict:
    with open(CONFIG_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


class Config:
    def __init__(self):
        raw = _load_yaml()

        # --- 감시 디렉토리 ---
        self.watch_dir: Path = ROOT_DIR / raw.get("watch_dir", "data/pdf_watch")

        # --- OCR ---
        ocr = raw.get("ocr", {})
        self.ocr_language: str = ocr.get("language", "kor+eng")
        self.ocr_tesseract_cmd: str | None = ocr.get("tesseract_cmd")
        self.ocr_tessdata_dir: str | None = ocr.get("tessdata_dir")
        self.ocr_min_text_length: int = ocr.get("min_text_length", 50)
        self.ocr_quality_threshold: float = ocr.get("quality_threshold", 0.5)

        # --- 데이터베이스 ---
        db = raw.get("database", {})
        self.db_path: Path = ROOT_DIR / db.get("path", "data/rag_project.db")

        # --- AnythingLLM (.env 우선) ---
        allm = raw.get("anythingllm", {})
        self.allm_base_url: str = (
            os.getenv("ANYTHINGLLM_BASE_URL") or allm.get("base_url", "http://localhost:3001")
        )
        self.allm_api_key: str = (
            os.getenv("ANYTHINGLLM_API_KEY") or allm.get("api_key", "")
        )
        self.allm_workspace: str = (
            os.getenv("ANYTHINGLLM_WORKSPACE") or allm.get("workspace", "")
        )
        self.allm_upload_timeout: int = allm.get("upload_timeout", 30)

        # --- LLM OCR 후처리 교정 (Claude · Gemini) ---
        # config.yaml: llm_correction.enabled / provider / claude{} / gemini{}
        llm = raw.get("llm_correction", raw.get("claude", {}))  # 구 섹션명 호환
        self.llm_correction_enabled: bool = llm.get("enabled", False)
        self.llm_provider: str = llm.get("provider", "claude")  # "claude" | "gemini"

        claude_cfg = llm.get("claude", llm)   # 구 claude: 단일 섹션도 허용
        self.claude_api_key: str = os.getenv("ANTHROPIC_API_KEY") or claude_cfg.get("api_key", "")
        self.claude_model: str = claude_cfg.get("model", "claude-haiku-4-5-20251001")
        self.claude_max_tokens: int = claude_cfg.get("max_tokens", 8192)
        self.claude_timeout: int = claude_cfg.get("timeout", 60)

        gemini_cfg = llm.get("gemini", {})
        self.gemini_api_key: str = os.getenv("GOOGLE_API_KEY") or gemini_cfg.get("api_key", "")
        self.gemini_model: str = gemini_cfg.get("model", "gemini-2.0-flash")
        self.gemini_timeout: int = gemini_cfg.get("timeout", 60)

        # --- 색인 추적 ---
        idx = raw.get("indexing", {})
        self.indexing_poll_interval: int = idx.get("poll_interval", 10)
        self.indexing_timeout: int = idx.get("timeout", 300)

        # --- 재시도 ---
        retry = raw.get("retry", {})
        self.retry_max_attempts: int = retry.get("max_attempts", 3)
        self.retry_backoff_seconds: list[int] = retry.get("backoff_seconds", [60, 120, 240])

        # --- 로그 ---
        log = raw.get("logging", {})
        self.log_level: str = log.get("level", "INFO")
        self.log_file: Path = ROOT_DIR / log.get("file", "logs/rag_project.log")

    def validate(self) -> list[str]:
        """설정 유효성 검사 — 문제 항목 목록 반환 (빈 리스트 = 정상)"""
        issues = []
        if not self.allm_api_key:
            issues.append("anythingllm.api_key 가 비어 있습니다 (.env 또는 config.yaml 확인)")
        if not self.allm_workspace:
            issues.append("anythingllm.workspace 가 비어 있습니다")
        return issues


# 싱글턴
config = Config()
