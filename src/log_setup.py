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

# 프로세스 내에서 이미 로그 초기화가 완료되었는지 추적하는 플래그.
# Streamlit처럼 코드가 여러 번 재실행되는 환경에서 핸들러 중복 등록을 막는다.
_initialized = False

# 현재 활성화된 파일 핸들러를 보관하는 변수.
# 나중에 현재 로그 파일 경로를 조회할 때 사용한다.
_file_handler: "logging.FileHandler | None" = None


class _DateRotatingFileHandler(logging.FileHandler):
    """자정에 날짜가 바뀌면 새 파일로 자동 전환하는 파일 핸들러.

    파일명 규칙: log_yyyymmdd_hhmmss.log
      - 세션 시작 시각을 파일명에 포함 → 같은 날 재실행 시 구분 가능
      - 자정 이후 첫 로그 기록 시 새 날짜_hhmmss 파일로 전환

    파이썬 표준 라이브러리의 logging.FileHandler를 상속(extend)해서 만든 클래스다.
    날짜 변경 감지 기능만 추가하고 나머지는 부모 클래스의 동작을 그대로 사용한다.
    """

    def __init__(self, log_dir: Path, session_start: datetime):
        """핸들러를 초기화하고 첫 번째 로그 파일을 생성한다.

        매개변수
        --------
        log_dir : Path
            로그 파일을 저장할 디렉토리 경로
        session_start : datetime
            세션 시작 시각 (파일명에 포함되어 실행 시점을 구분하는 데 사용)
        """
        # 로그 디렉토리와 오늘 날짜(yyyymmdd 형식)를 인스턴스 변수로 저장.
        # 나중에 emit()에서 날짜 변경 여부를 비교할 때 사용한다.
        self._log_dir = log_dir
        self._current_date = session_start.strftime("%Y%m%d")

        # 파일명에 시작 날짜와 시각을 모두 포함해 같은 날 여러 번 실행해도 덮어쓰지 않는다.
        log_path = log_dir / f"log_{session_start.strftime('%Y%m%d_%H%M%S')}.log"

        # 부모 클래스(FileHandler)의 초기화 호출:
        #   mode="a"       → 기존 파일이 있으면 이어쓰기 (append)
        #   encoding="utf-8" → 한글 등 유니코드 문자가 깨지지 않도록 UTF-8 인코딩
        #   delay=False    → 즉시 파일을 열어 둠 (첫 로그 메시지 전에 파일을 확보)
        super().__init__(str(log_path), mode="a", encoding="utf-8", delay=False)

    def emit(self, record: logging.LogRecord) -> None:
        """로그 레코드 한 건을 파일에 기록한다. 날짜가 바뀌면 새 파일로 전환한다.

        매개변수
        --------
        record : logging.LogRecord
            기록할 로그 이벤트 정보 (레벨, 메시지, 타임스탬프 등을 담은 객체)
        """
        # 현재 시각의 날짜를 가져와 마지막으로 기록한 날짜와 비교한다.
        today = datetime.now().strftime("%Y%m%d")
        if today != self._current_date:
            # 날짜 변경 → 기존 스트림 닫고 새 파일 열기.
            # 멀티스레드 환경에서 파일 전환 도중 다른 스레드가 끼어들지 못하도록
            # acquire()/release()로 잠금(lock)을 걸어 안전하게 처리한다.
            self.acquire()
            try:
                self.close()  # 현재 파일 스트림을 닫아 자원을 반환한다.
                self._current_date = today  # 추적 날짜를 오늘로 갱신한다.
                # 새 날짜·시각이 포함된 파일 경로를 만들고 파일을 열어 기록을 이어간다.
                new_path = self._log_dir / f"log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
                self.baseFilename = str(new_path)  # 부모 클래스가 참조하는 파일 경로를 교체한다.
                self.stream = self._open()  # 새 파일 스트림을 열어 핸들러에 연결한다.
            finally:
                # 예외가 발생하더라도 반드시 잠금을 해제해야 데드락을 방지할 수 있다.
                self.release()
        # 날짜 전환 처리 후(또는 날짜 변경이 없는 경우) 부모 클래스의 emit()으로 실제 기록한다.
        super().emit(record)

    @property
    def current_path(self) -> Path:
        """현재 기록 중인 로그 파일의 경로를 Path 객체로 반환한다.

        반환값
        ------
        Path
            현재 열려 있는 로그 파일의 절대 경로
        """
        # baseFilename은 부모 클래스(FileHandler)가 관리하는 현재 파일 경로 문자열이다.
        return Path(self.baseFilename)


def setup_logging(log_dir: Path | None = None) -> Path:
    """로그 파일을 설정하고 root logger에 핸들러를 등록한다.

    최초 호출 시에만 파일·콘솔 핸들러를 등록하며, 이후 재호출은 무시된다.
    날짜가 바뀌면 _DateRotatingFileHandler 가 자동으로 새 파일로 전환한다.

    매개변수
    --------
    log_dir : Path | None
        로그 파일을 저장할 디렉토리. None이면 config에서 설정된 기본 경로를 사용한다.

    반환값
    ------
    Path
        현재 기록 중인 로그 파일의 절대 경로
    """
    # global 키워드: 함수 안에서 모듈 수준의 변수를 수정할 때 반드시 선언해야 한다.
    global _initialized, _file_handler

    # log_dir 인수가 없으면 설정 파일(config)에서 지정된 로그 디렉토리를 사용한다.
    target_dir = log_dir if log_dir else config.log_file.parent

    # 로그 디렉토리가 없으면 자동으로 생성한다.
    # parents=True  → 중간 경로도 함께 생성 (mkdir -p 와 동일)
    # exist_ok=True → 이미 존재해도 오류를 내지 않음
    target_dir.mkdir(parents=True, exist_ok=True)

    if _initialized and _file_handler is not None:
        # 이미 초기화됨 — 현재 파일 경로만 반환 (날짜 전환 후 경로 반영)
        # Streamlit 등에서 스크립트가 재실행될 때 핸들러가 중복 등록되는 것을 막는다.
        return _file_handler.current_path

    # config에 지정된 로그 레벨 문자열(예: "DEBUG", "INFO")을 logging 모듈의 정수 상수로 변환.
    # getattr의 세 번째 인수는 해당 레벨명이 없을 때 사용할 기본값이다.
    level = getattr(logging, config.log_level.upper(), logging.INFO)

    # 로그 메시지 형식을 정의한다.
    #   %(asctime)s    → 로그가 기록된 날짜·시각
    #   %(levelname)-8s → 로그 레벨 (DEBUG, INFO 등), 8자리로 왼쪽 정렬
    #   %(name)s       → 로거 이름 (어느 모듈에서 호출했는지 표시)
    #   %(message)s    → 실제 로그 메시지
    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # 루트(root) 로거를 가져온다.
    # 루트 로거는 모든 로거의 최상위 부모로, 여기에 핸들러를 등록하면
    # 프로그램 어디서 만든 로거든 같은 핸들러를 통해 출력된다.
    root = logging.getLogger()
    root.setLevel(level)

    # 파일 핸들러 — 프로세스당 1회만 등록
    # 날짜 자동 전환 기능이 있는 커스텀 핸들러를 생성해 루트 로거에 붙인다.
    fh = _DateRotatingFileHandler(target_dir, datetime.now())
    fh.setLevel(level)
    fh.setFormatter(fmt)
    root.addHandler(fh)

    # 나중에 current_path를 조회할 수 있도록 모듈 변수에 핸들러를 보관한다.
    _file_handler = fh

    # 콘솔 핸들러 — 프로세스당 1회만 등록
    # sys.stdout으로 출력하면 터미널·Streamlit 콘솔 모두에서 로그를 실시간으로 볼 수 있다.
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(level)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    # 초기화 완료 플래그를 True로 설정해 이후 재호출 시 중복 등록을 막는다.
    _initialized = True

    # 로그 시스템이 준비되었음을 알리는 첫 번째 로그 메시지를 기록한다.
    logging.getLogger(__name__).info(
        "로그 파일 생성: %s  (level=%s)", fh.current_path, config.log_level
    )
    return fh.current_path


def get_log_files(log_dir: Path | None = None) -> list[Path]:
    """logs/ 디렉토리 내의 log_*.log 파일 목록을 최신 순으로 반환한다.

    매개변수
    --------
    log_dir : Path | None
        검색할 디렉토리. None이면 config에서 설정된 기본 로그 디렉토리를 사용한다.

    반환값
    ------
    list[Path]
        log_*.log 패턴에 맞는 파일 경로 목록. 최신 파일이 앞쪽에 온다.
        디렉토리가 존재하지 않으면 빈 리스트를 반환한다.
    """
    target_dir = log_dir if log_dir else config.log_file.parent

    # 디렉토리가 없으면 빈 리스트를 반환해 오류 없이 처리한다.
    if not target_dir.exists():
        return []

    # glob("log_*.log")로 파일명이 "log_"로 시작하고 ".log"로 끝나는 파일만 찾는다.
    # reverse=True로 정렬하면 파일명에 포함된 날짜·시각이 가장 최근인 것이 먼저 온다.
    return sorted(target_dir.glob("log_*.log"), reverse=True)
