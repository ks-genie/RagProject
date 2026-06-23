# -*- coding: utf-8 -*-
"""
Status Tracker — 색인 상태 전이 및 검증

AnythingLLM의 update-embeddings는 동기 처리이므로,
embed 완료 즉시 INDEXED로 전이한다.
(계획서의 PARSING_DONE → CHUNKING_DONE → EMBEDDING_DONE 중간 상태는
 단일 embed 호출로 통합 처리)
"""

import logging

from src.api_client import ApiClient, UploadResult, api_client
from src.database import DatabaseManager, Status, db

# 이 모듈 전용 로거를 생성한다. 로그 출력 시 모듈 이름이 함께 표시된다.
logger = logging.getLogger(__name__)


class StatusTracker:
    """
    문서의 색인(처리) 상태를 추적하고 기록하는 클래스.

    문서가 업로드되고, 임베딩(벡터화)되고, 색인 완료되거나
    오류가 발생할 때마다 DB에 상태를 기록하는 역할을 한다.
    """

    def __init__(self, database: DatabaseManager = None, client: ApiClient = None):
        """
        StatusTracker 초기화.

        매개변수:
            database (DatabaseManager, optional): 사용할 DB 관리자 객체.
                None이면 전역 싱글턴 db를 사용한다.
            client (ApiClient, optional): AnythingLLM API 클라이언트 객체.
                None이면 전역 싱글턴 api_client를 사용한다.
        """
        # 외부에서 DB 객체를 주입받지 않으면 전역 싱글턴을 사용한다.
        # 이렇게 하면 테스트 시 가짜(mock) DB를 주입하기 쉽다.
        self._db = database or db
        self._api = client or api_client

    # ------------------------------------------------------------------
    # 업로드 완료 → UPLOADED
    # ------------------------------------------------------------------
    def mark_uploaded(self, doc_id: int, result: UploadResult) -> None:
        """
        문서 업로드가 완료되었을 때 상태를 UPLOADED로 기록한다.

        파일이 AnythingLLM 서버에 성공적으로 업로드된 직후 호출된다.
        DB에 상태와 AnythingLLM 내부 문서 ID를 저장하고, 처리 로그도 남긴다.

        매개변수:
            doc_id (int): 로컬 DB에서 관리하는 문서 고유 번호.
            result (UploadResult): 업로드 결과 객체.
                내부에 AnythingLLM이 반환한 doc_id와 단어 수(word_count)가 담겨 있다.
        """
        # DB에 상태를 UPLOADED로 변경하고, AnythingLLM이 부여한 문서 ID를 함께 저장한다.
        self._db.update_status(
            doc_id,
            Status.UPLOADED,
            anythingllm_doc_id=result.doc_id,
        )
        # 처리 단계별 로그 테이블에 성공 기록을 남긴다.
        self._db.log_step(
            doc_id, "UPLOAD", "SUCCESS",
            f"doc_id={result.doc_id} words={result.word_count}",
        )
        logger.info("[doc %d] UPLOADED — anythingllm_doc_id=%s", doc_id, result.doc_id)

    # ------------------------------------------------------------------
    # 임베딩 완료 → INDEXED
    # (AnythingLLM이 동기 처리하므로 embed 반환 = 완전 색인 완료)
    # ------------------------------------------------------------------
    def mark_indexed(self, doc_id: int) -> None:
        """
        문서 임베딩(벡터화 및 색인)이 완료되었을 때 상태를 INDEXED로 기록한다.

        AnythingLLM은 embed 호출 한 번에 파싱 → 청킹 → 임베딩을 모두 동기로 처리한다.
        따라서 중간 단계 상태(PARSING_DONE, CHUNKING_DONE, EMBEDDING_DONE)를
        순서대로 모두 기록한 뒤 최종 INDEXED 상태로 전이한다.

        매개변수:
            doc_id (int): 로컬 DB에서 관리하는 문서 고유 번호.
        """
        # AnythingLLM의 단일 embed 호출이 아래 세 단계를 한꺼번에 처리하므로
        # 각 중간 상태를 순서대로 기록하여 이력을 남긴다.
        self._db.update_status(doc_id, Status.PARSING_DONE)    # 텍스트 파싱 완료
        self._db.update_status(doc_id, Status.CHUNKING_DONE)   # 청크(조각) 분리 완료
        self._db.update_status(doc_id, Status.EMBEDDING_DONE)  # 벡터 임베딩 완료
        self._db.update_status(doc_id, Status.INDEXED)         # 최종 색인 완료
        self._db.log_step(doc_id, "INDEXING", "SUCCESS", "embed 동기 완료 → INDEXED")
        logger.info("[doc %d] INDEXED", doc_id)

    # ------------------------------------------------------------------
    # 임베딩 후 실제 존재 검증 (선택적)
    # ------------------------------------------------------------------
    def verify_indexed(self, anythingllm_doc_id: str) -> bool:
        """
        AnythingLLM에 문서가 실제로 존재하는지 확인한다.

        임베딩 완료 후 선택적으로 호출하여, 서버 측에 문서가 정상 등록되었는지 검증한다.
        존재하지 않으면 경고 로그를 남긴다.

        매개변수:
            anythingllm_doc_id (str): AnythingLLM이 부여한 문서 ID.

        반환값:
            bool: 문서가 AnythingLLM에 존재하면 True, 없으면 False.
        """
        # API를 호출하여 해당 문서가 AnythingLLM 서버에 실제로 있는지 확인한다.
        exists = self._api.document_exists(anythingllm_doc_id)
        if not exists:
            # 문서가 없으면 색인 과정에서 오류가 발생했을 가능성이 있으므로 경고를 남긴다.
            logger.warning("문서 검증 실패: %s 가 AnythingLLM에 없음", anythingllm_doc_id)
        return exists

    # ------------------------------------------------------------------
    # 실패 기록
    # ------------------------------------------------------------------
    def mark_failed(self, doc_id: int, step: str, error: str) -> None:
        """
        처리 중 오류가 발생했을 때 상태를 FAILED로 기록한다.

        어느 단계(업로드, 임베딩 등)에서 예외가 발생하면 이 메서드를 호출하여
        DB에 실패 상태, 오류 메시지, 실패한 단계를 저장한다.

        매개변수:
            doc_id (int): 로컬 DB에서 관리하는 문서 고유 번호.
            step (str): 실패가 발생한 처리 단계 이름 (예: "UPLOAD", "INDEXING").
            error (str): 오류 내용을 설명하는 문자열.
        """
        # DB 상태를 FAILED로 변경하고, 어느 단계에서 어떤 오류가 났는지 기록한다.
        self._db.update_status(
            doc_id, Status.FAILED,
            error_message=error,
            failed_step=step,
        )
        # 단계별 로그 테이블에도 실패 내역을 별도로 남긴다.
        self._db.log_step(doc_id, step, "FAILURE", error)
        logger.error("[doc %d] FAILED at %s — %s", doc_id, step, error)

    # ------------------------------------------------------------------
    # REVIEW_REQUIRED 기록
    # ------------------------------------------------------------------
    def mark_review_required(self, doc_id: int, reason: str) -> None:
        """
        문서가 자동 처리 불가로 판단되어 사람이 검토해야 할 때 상태를 REVIEW_REQUIRED로 기록한다.

        전처리(PREPROCESS) 단계에서 파일 형식 문제, 내용 이상 등의 이유로
        자동 처리를 진행할 수 없을 때 호출된다.

        매개변수:
            doc_id (int): 로컬 DB에서 관리하는 문서 고유 번호.
            reason (str): 검토가 필요한 이유를 설명하는 문자열.
        """
        # DB 상태를 REVIEW_REQUIRED로 변경하고, 검토가 필요한 이유와 실패 단계를 저장한다.
        self._db.update_status(
            doc_id, Status.REVIEW_REQUIRED,
            error_message=reason,
            failed_step="PREPROCESS",  # 검토 필요는 항상 전처리 단계에서 발생한다.
        )
        # 단계별 로그에도 검토 필요 상태를 기록한다.
        self._db.log_step(doc_id, "PREPROCESS", "REVIEW_REQUIRED", reason)
        logger.warning("[doc %d] REVIEW_REQUIRED — %s", doc_id, reason)


# 모듈 전체에서 공유하는 싱글턴 인스턴스.
# 별도 설정 없이 `from src.status_tracker import status_tracker`로 바로 사용할 수 있다.
status_tracker = StatusTracker()
