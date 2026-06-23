# -*- coding: utf-8 -*-
"""
Retry Handler — 재시도 로직 (Exponential Backoff)

실패한 작업을 일정 횟수만큼 자동으로 다시 시도하는 모듈입니다.
재시도 간격은 점점 길어지는 방식(지수 백오프)을 사용합니다.

설정 (config.yaml):
  retry.max_attempts:    3
  retry.backoff_seconds: [60, 120, 240]  # 1분 → 2분 → 4분

사용 예:
  result = retry_handler.run(func, doc_id=1, step="UPLOAD")
"""

import logging
import time
from typing import Callable, TypeVar

from src.config import config
from src.database import DatabaseManager, Status, db

# 이 모듈 전용 로거를 생성합니다. 로그 출력 시 모듈명이 함께 표시됩니다.
logger = logging.getLogger(__name__)

# 제네릭 타입 변수: 함수의 반환 타입을 유연하게 표현하기 위해 사용합니다.
# T는 run() 메서드가 func()의 반환값과 동일한 타입을 반환한다는 것을 타입 검사기에 알립니다.
T = TypeVar("T")


class RetryExhausted(Exception):
    """재시도 횟수를 모두 소진했을 때 발생하는 사용자 정의 예외 클래스.

    모든 재시도가 실패로 끝났을 때 이 예외를 발생시켜 호출자에게 알립니다.

    매개변수:
        step (str): 실패한 처리 단계명 (예: "UPLOAD", "PARSE")
        last_error (Exception): 마지막 시도에서 발생한 원본 예외
    """
    def __init__(self, step: str, last_error: Exception):
        # 부모 Exception 클래스의 생성자를 호출하여 오류 메시지를 설정합니다.
        super().__init__(f"[{step}] 재시도 {config.retry_max_attempts}회 초과: {last_error}")
        self.step = step          # 어떤 단계에서 실패했는지 저장 (나중에 참조 가능)
        self.last_error = last_error  # 마지막 예외를 저장 (원인 파악용)


class RetryHandler:
    """재시도 로직을 담당하는 핸들러 클래스.

    지수 백오프(Exponential Backoff) 방식으로 실패한 함수를 재시도합니다.
    재시도 횟수와 대기 시간은 config에서 읽어옵니다.

    매개변수:
        database (DatabaseManager, optional): 사용할 DB 매니저 인스턴스.
            지정하지 않으면 전역 싱글턴 db를 사용합니다.
    """
    def __init__(self, database: DatabaseManager = None):
        # database가 None이면 전역 db 싱글턴을 사용합니다.
        # 테스트 시에는 가짜(mock) DB를 주입할 수 있습니다.
        self._db = database or db

    def run(
        self,
        func: Callable[[], T],
        *,
        doc_id: int,
        step: str,
        sleep_fn: Callable[[float], None] | None = None,  # 테스트에서 mock 가능
    ) -> T:
        """func()을 최대 max_attempts 회 실행. 실패 시 Exponential Backoff 후 재시도.

        주어진 함수를 실행하고, 예외가 발생하면 설정된 대기 시간 후 다시 시도합니다.
        모든 시도가 실패하면 RetryExhausted 예외를 발생시킵니다.

        매개변수:
            func (Callable):  실행할 함수. 인자를 받지 않아야 합니다.
                              예: lambda: upload_file(path)
            doc_id (int):     처리 중인 문서의 ID. DB에 재시도 횟수를 기록할 때 사용합니다.
            step (str):       현재 처리 단계명. 로그 메시지에 포함됩니다.
                              예: "UPLOAD", "PARSE", "EMBED"
            sleep_fn (Callable, optional): 대기에 사용할 함수.
                              기본값은 time.sleep. 테스트 시 mock으로 교체해 실제 대기 없이 테스트 가능합니다.

        반환값:
            func()가 성공했을 때의 반환값 (타입은 func에 따라 다름)

        예외:
            RetryExhausted: 최대 재시도 횟수를 모두 소진했을 때 발생
        """
        # sleep_fn이 지정되지 않으면 표준 라이브러리의 time.sleep을 사용합니다.
        if sleep_fn is None:
            sleep_fn = time.sleep

        # 설정 파일에서 최대 시도 횟수와 백오프 대기 시간 목록을 읽어옵니다.
        max_attempts = config.retry_max_attempts
        backoff = config.retry_backoff_seconds  # 예: [60, 120, 240] (초 단위)

        last_error: Exception | None = None  # 마지막으로 발생한 예외를 저장합니다.

        # attempt: 0부터 시작하는 현재 시도 횟수 (0 = 첫 번째 시도)
        for attempt in range(max_attempts):
            try:
                result = func()  # 전달받은 함수를 실행합니다.

                # 첫 번째 시도(attempt=0)가 아닌 재시도에서 성공한 경우 성공 로그를 남깁니다.
                if attempt > 0:
                    logger.info("[doc %d] %s 재시도 성공 (attempt %d)", doc_id, step, attempt + 1)

                return result  # 성공 시 결과를 즉시 반환하고 루프를 종료합니다.

            except Exception as e:
                last_error = e  # 발생한 예외를 저장해 두었다가 나중에 RetryExhausted에 전달합니다.

                # 현재 시도가 마지막 시도인지 확인합니다.
                is_last = attempt == max_attempts - 1

                if is_last:
                    # 마지막 시도마저 실패했으므로 오류 로그를 남기고 루프를 빠져나갑니다.
                    logger.error(
                        "[doc %d] %s 실패 — 최대 재시도 초과 (%d회): %s",
                        doc_id, step, max_attempts, e,
                    )
                    break  # 루프 종료 후 아래에서 RetryExhausted를 발생시킵니다.

                # backoff 목록의 해당 인덱스 대기 시간을 사용합니다.
                # 시도 횟수가 backoff 목록 길이를 초과하면 마지막 값을 재사용합니다.
                # 예: backoff=[60,120,240], attempt=0 → 60초, attempt=1 → 120초, attempt=5 → 240초
                wait = backoff[attempt] if attempt < len(backoff) else backoff[-1]

                logger.warning(
                    "[doc %d] %s 실패 (attempt %d/%d) — %ds 후 재시도: %s",
                    doc_id, step, attempt + 1, max_attempts, wait, e,
                )

                # DB에 이 문서의 재시도 횟수를 1 증가시켜 기록합니다.
                self._db.increment_retry(doc_id)

                # 설정된 시간만큼 대기한 후 다음 시도를 진행합니다.
                sleep_fn(wait)

        # 모든 시도가 실패했으므로 RetryExhausted 예외를 발생시킵니다.
        # 호출자는 이 예외를 잡아 적절히 처리해야 합니다.
        raise RetryExhausted(step, last_error)


# 모듈 전체에서 공유하는 싱글턴 인스턴스입니다.
# 별도로 인스턴스를 생성하지 않고 이 객체를 직접 import해서 사용합니다.
# 사용 예: from src.retry_handler import retry_handler
retry_handler = RetryHandler()
