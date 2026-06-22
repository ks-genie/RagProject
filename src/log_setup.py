# -*- coding: utf-8 -*-
"""
log_setup.py — 로그 파일 초기화

동작 규칙:
  - 프로세스당 1회만 파일 핸들러를 등록 (Streamlit rerun 등 중복 호출 무시)
  - 새 파일 생성 조건: ① 프로세스 최초 실행 (새 세션)  ② 자정 이후 날짜 변경
  - 그 외 모든 경우에는 기존 파일에 append

사용법:
    from src.log_setup import setup_logging
    log_path = setup_logging()          # 앱 또는 스크립트 시작 시 1회 호출
"""

import logging
import sys
from datetime import datetime
from pathlib import Path

from src.config import config

_initialized = False   # 프로세스 내 중복 초기화 방지
_file_handler: "logging.FileHandler | None" = None


class _DateRotatingFileHandler(logging.FileHandler):
    """자정에 날짜가 바뀌면 새 파일로 자동 전환하는 파일 핸들러.

    파일명 규칙: log_yyyymmdd_hhmmss.log
      - 세션 시작 시각을 파일명에 포함 → 같은 날 재실행 시 구분 가능
      - 자정 이후 첫 로그 기록 시 새 날짜_hhmmss 파일로 전환
    """

    def __init__(self, log_dir: Path, session_start: datetime):
        self._log_dir = log_dir
        self._current_date = session_start.strftime("%Y%m%d")
        log_path = log_dir / f"log_{session_start.strftime('%Y%m%d_%H%M%S')}.log"
        super().__init__(str(log_path), mode="a", encoding="utf-8", delay=False)

    def emit(self, record: logging.LogRecord) -> None:
        today = datetime.now().strftime("%Y%m%d")
        if today != self._current_date:
            # 날짜 변경 → 기존 스트림 닫고 새 파일 열기
            self.acquire()
            try:
                self.close()
                self._current_date = today
                new_path = self._log_dir / f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
                self.baseFilename = str(new_path)
                self.stream = self._open()
            finally:
                self.release()
        super().emit(record)

    @property
    def current_path(self) -> Path:
        return Path(self.baseFilename)


def setup_logging(log_dir: Path | None = None) -> Path:
    """로그 파일을 설정하고 root logger에 핸들러를 등록한다.

    최초 호출 시에만 파일·콘솔 핸들러를 등록하며, 이후 재호출은 무시된다.
    날짜가 바뀌면 _DateRotatingFileHandler 가 자동으로 새 파일로 전환한다.

    Returns
    -------
    Path
        현재 기록 중인 로그 파일의 절대 경로
    """
    global _initialized, _file_handler

    target_dir = log_dir if log_dir else config.log_file.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    if _initialized and _file_handler is not None:
        # 이미 초기화됨 — 현재 파일 경로만 반환 (날짜 전환 후 경로 반영)
        return _file_handler.current_path

    level = getattr(logging, config.log_level.upper(), logging.INFO)
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root = logging.getLogger()
    root.setLevel(level)

    # 파일 핸들러 — 프로세스당 1회만 등록
    fh = _DateRotatingFileHandler(target_dir, datetime.now())
    fh.setLevel(level)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    _file_handler = fh

    # 콘솔 핸들러 — 프로세스당 1회만 등록
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    _initialized = True

    logging.getLogger(__name__).info(
        "로그 파일 생성: %s  (level=%s)", fh.current_path, config.log_level
    )
    return fh.current_path


def get_log_files(log_dir: Path | None = None) -> list[Path]:
    """logs/ 디렉토리 내의 log_*.log 파일 목록을 최신 순으로 반환한다."""
    target_dir = log_dir if log_dir else config.log_file.parent
    if not target_dir.exists():
        return []
    return sorted(target_dir.glob("log_*.log"), reverse=True)
