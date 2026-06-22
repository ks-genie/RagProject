# -*- coding: utf-8 -*-
"""
Retry Handler — 재시도 로직 (Exponential Backoff)

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

logger = logging.getLogger(__name__)
T = TypeVar("T")


class RetryExhausted(Exception):
    """재시도 횟수 초과 시 발생"""
    def __init__(self, step: str, last_error: Exception):
        super().__init__(f"[{step}] 재시도 {config.retry_max_attempts}회 초과: {last_error}")
        self.step = step
        self.last_error = last_error


class RetryHandler:
    def __init__(self, database: DatabaseManager = None):
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

        Args:
            func:     실행할 함수 (인자 없음)
            doc_id:   처리 중인 문서 ID (retry_count 갱신용)
            step:     단계명 (로그용)
            sleep_fn: 대기 함수 (테스트 시 mock으로 교체 가능)

        Returns:
            func()의 반환값

        Raises:
            RetryExhausted: 최대 재시도 횟수 초과 시
        """
        if sleep_fn is None:
            sleep_fn = time.sleep
        max_attempts = config.retry_max_attempts
        backoff = config.retry_backoff_seconds
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            try:
                result = func()
                if attempt > 0:
                    logger.info("[doc %d] %s 재시도 성공 (attempt %d)", doc_id, step, attempt + 1)
                return result

            except Exception as e:
                last_error = e
                is_last = attempt == max_attempts - 1

                if is_last:
                    logger.error(
                        "[doc %d] %s 실패 — 최대 재시도 초과 (%d회): %s",
                        doc_id, step, max_attempts, e,
                    )
                    break

                wait = backoff[attempt] if attempt < len(backoff) else backoff[-1]
                logger.warning(
                    "[doc %d] %s 실패 (attempt %d/%d) — %ds 후 재시도: %s",
                    doc_id, step, attempt + 1, max_attempts, wait, e,
                )
                self._db.increment_retry(doc_id)
                sleep_fn(wait)

        raise RetryExhausted(step, last_error)


# 싱글턴
retry_handler = RetryHandler()
