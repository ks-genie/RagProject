# -*- coding: utf-8 -*-
"""
API Client — AnythingLLM raw-text 업로드 및 워크스페이스 임베딩

흐름:
  1. upload_text()     → POST /api/v1/document/raw-text
                        → UploadResult(doc_id, location) 반환, DB에 UPLOADED 저장
  2. embed_document()  → POST /api/v1/workspace/{slug}/update-embeddings
                        → 동기 처리 완료 = INDEXED

참고: AnythingLLM의 update-embeddings는 Parsing/Chunking/Embedding을
      동기적으로 한 번에 처리하므로 Phase 7 폴링 대신 embed 완료 즉시 INDEXED.
"""

import logging
from dataclasses import dataclass  # 데이터를 담는 간단한 클래스를 만들어주는 표준 라이브러리

import requests  # HTTP 요청(GET/POST 등)을 보내기 위한 외부 라이브러리

from src.config import config  # 프로젝트 설정값(URL, API 키 등)을 가져옴

# 이 모듈 전용 로거 생성 — 로그 메시지에 파일 이름이 자동으로 표시됨
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 결과 컨테이너
# ---------------------------------------------------------------------------
@dataclass
class UploadResult:
    """AnythingLLM에 텍스트를 업로드한 결과를 담는 데이터 클래스.

    업로드 성공 시 서버에서 반환된 문서 정보를 구조화하여 보관합니다.
    이후 임베딩 단계에서 location 값을 사용합니다.

    속성:
        doc_id (str): AnythingLLM이 부여한 문서 고유 UUID
        location (str): 서버 내 파일 경로 — embed_document() 호출 시 필요
        title (str): 문서 제목 (기본값: 빈 문자열)
        word_count (int): 문서 단어 수 (기본값: 0)
    """
    doc_id: str          # AnythingLLM 문서 UUID
    location: str        # 서버 내 파일 경로 (embed에 사용)
    title: str = ""
    word_count: int = 0


class ApiError(Exception):
    """API 호출 실패 시 발생하는 커스텀 예외 클래스.

    기본 Exception에 HTTP 상태 코드 정보를 추가하여,
    오류 원인을 더 명확하게 파악할 수 있도록 합니다.

    매개변수:
        message (str): 오류를 설명하는 메시지
        status_code (int): HTTP 응답 상태 코드 (기본값: 0, 네트워크 오류 등)
    """
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)       # 부모 클래스(Exception)에 메시지 전달
        self.status_code = status_code  # HTTP 상태 코드를 별도 속성으로 저장


# ---------------------------------------------------------------------------
# ApiClient
# ---------------------------------------------------------------------------
class ApiClient:
    """AnythingLLM REST API와 통신하는 클라이언트 클래스.

    텍스트 업로드, 워크스페이스 임베딩, 문서 존재 확인, 연결 상태 확인 등
    AnythingLLM과 관련된 모든 HTTP 통신을 담당합니다.

    사용 예:
        from src.api_client import api_client
        result = api_client.upload_text("파일명.txt", "문서 내용...")
        api_client.embed_document(result.location)
    """

    def __init__(self):
        """설정 파일(config.yaml)에서 AnythingLLM 접속 정보를 읽어 초기화합니다."""
        self._base_url  = config.allm_base_url.rstrip("/")  # URL 끝의 슬래시 제거 (중복 방지)
        self._api_key   = config.allm_api_key               # 인증에 사용할 API 키
        self._workspace = config.allm_workspace             # 임베딩 대상 워크스페이스 슬러그
        self._timeout   = config.allm_upload_timeout        # 요청 제한 시간(초)

    @property
    def _headers(self) -> dict:
        """모든 API 요청에 공통으로 사용되는 HTTP 헤더를 반환합니다.

        Bearer 토큰 방식으로 인증하고, 요청 본문이 JSON임을 명시합니다.

        반환값:
            dict: Authorization 및 Content-Type 헤더가 담긴 딕셔너리
        """
        return {
            "Authorization": f"Bearer {self._api_key}",  # API 키를 Bearer 토큰 형식으로 전달
            "Content-Type": "application/json",           # 요청 본문이 JSON 형식임을 알림
        }

    # ------------------------------------------------------------------
    # 1. 텍스트 업로드
    # ------------------------------------------------------------------
    def upload_text(self, filename: str, text: str) -> UploadResult:
        """텍스트를 AnythingLLM에 업로드하고 UploadResult 반환.

        AnythingLLM의 raw-text 업로드 엔드포인트로 텍스트 내용을 전송합니다.
        성공하면 이후 임베딩에 필요한 문서 ID와 경로 정보를 반환합니다.

        매개변수:
            filename (str): 문서 제목으로 사용될 파일명
            text (str): 업로드할 텍스트 내용

        반환값:
            UploadResult: 서버에서 반환된 문서 ID, 경로, 제목, 단어 수

        예외:
            ApiError: HTTP 오류 또는 응답 이상
        """
        url = f"{self._base_url}/api/v1/document/raw-text"  # 업로드 API 엔드포인트 URL 구성

        # AnythingLLM이 요구하는 요청 본문 형식으로 데이터 구성
        payload = {
            "textContent": text,       # 업로드할 실제 텍스트 내용
            "metadata": {
                "title": filename,                           # 문서 제목
                "docSource": "rag-project",                  # 문서 출처 식별자
                "description": f"Auto-uploaded: {filename}", # 자동 생성 설명
            },
        }
        logger.info("업로드 요청: %s (%d자)", filename, len(text))

        # HTTP POST 요청 시도 — 네트워크 오류는 별도로 처리
        try:
            resp = requests.post(url, headers=self._headers, json=payload, timeout=self._timeout)
        except requests.exceptions.Timeout:
            # 설정한 제한 시간 내에 서버가 응답하지 않은 경우
            raise ApiError(f"업로드 타임아웃 ({self._timeout}초): {filename}")
        except requests.exceptions.ConnectionError as e:
            # 서버에 아예 연결할 수 없는 경우 (URL 오류, 네트워크 단절 등)
            raise ApiError(f"연결 실패: {e}")

        # HTTP 403은 인증 실패를 의미하므로 별도 메시지로 안내
        if resp.status_code == 403:
            raise ApiError("API 인증 실패 (403) — api_key 확인", 403)
        # 그 외 4xx, 5xx 오류 처리 (resp.ok는 상태 코드가 200~299일 때만 True)
        if not resp.ok:
            raise ApiError(f"업로드 실패 HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code)

        # JSON 응답 파싱 — AnythingLLM은 성공 여부를 success 필드로 알려줌
        data = resp.json()
        if not data.get("success"):
            raise ApiError(f"업로드 응답 오류: {data.get('error', 'unknown')}")

        # 업로드된 문서 목록 추출 — 빈 리스트이면 오류
        docs = data.get("documents", [])
        if not docs:
            raise ApiError("업로드 응답에 documents 없음")

        # 첫 번째(유일한) 문서 정보로 결과 객체 생성
        doc = docs[0]
        result = UploadResult(
            doc_id    = doc["id"],                    # 서버가 부여한 문서 UUID
            location  = doc["location"],              # 임베딩 시 필요한 서버 내 경로
            title     = doc.get("title", filename),   # 제목이 없으면 파일명으로 대체
            word_count= doc.get("wordCount", 0),      # 단어 수 (없으면 0으로 기본값)
        )
        logger.info("업로드 완료: doc_id=%s (%d words)", result.doc_id, result.word_count)
        return result

    # ------------------------------------------------------------------
    # 2. 워크스페이스 임베딩
    # ------------------------------------------------------------------
    def embed_document(self, location: str) -> None:
        """문서를 워크스페이스에 임베딩 (동기 처리 — 완료 = INDEXED).

        AnythingLLM의 update-embeddings 엔드포인트를 호출하여
        업로드된 문서를 지정된 워크스페이스에 벡터 임베딩합니다.
        이 API는 파싱 → 청킹 → 임베딩을 동기적으로 한 번에 처리합니다.

        매개변수:
            location (str): upload_text()에서 반환된 UploadResult.location 값

        반환값:
            None: 성공 시 반환값 없음

        예외:
            ApiError: 워크스페이스 미설정 또는 HTTP 오류
        """
        # 워크스페이스가 설정되지 않으면 임베딩 불가 — 사전 검사
        if not self._workspace:
            raise ApiError("workspace slug가 config.yaml에 설정되지 않았습니다")

        url = f"{self._base_url}/api/v1/workspace/{self._workspace}/update-embeddings"

        # adds: 임베딩할 문서 경로 목록 / deletes: 제거할 문서 (여기서는 없음)
        payload = {"adds": [location], "deletes": []}

        # 경로에서 파일명만 추출하여 로그에 기록 (Windows/Unix 경로 모두 처리)
        logger.info("임베딩 요청: %s", location.split("\\")[-1].split("/")[-1])

        try:
            resp = requests.post(url, headers=self._headers, json=payload, timeout=self._timeout)
        except requests.exceptions.Timeout:
            raise ApiError(f"임베딩 타임아웃 ({self._timeout}초)")
        except requests.exceptions.ConnectionError as e:
            raise ApiError(f"연결 실패: {e}")

        if not resp.ok:
            raise ApiError(f"임베딩 실패 HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code)

        logger.info("임베딩 완료: %s", location.split("\\")[-1].split("/")[-1])

    # ------------------------------------------------------------------
    # 3. 문서 존재 확인 (업로드 검증)
    # ------------------------------------------------------------------
    def document_exists(self, doc_id: str) -> bool:
        """업로드된 문서가 AnythingLLM에 존재하는지 확인합니다.

        /api/v1/documents 엔드포인트로 전체 문서 목록을 가져온 뒤,
        해당 doc_id가 트리 구조 어딘가에 있는지 재귀적으로 탐색합니다.

        매개변수:
            doc_id (str): 확인할 문서의 UUID (UploadResult.doc_id)

        반환값:
            bool: 문서가 존재하면 True, 없거나 오류 발생 시 False
        """
        url = f"{self._base_url}/api/v1/documents"
        try:
            resp = requests.get(url, headers=self._headers, timeout=10)
            if not resp.ok:
                return False  # HTTP 오류 시 존재하지 않는 것으로 간주
            data = resp.json()
            return self._find_doc_id(data, doc_id)  # 트리 탐색으로 ID 검색
        except Exception as e:
            # 예기치 못한 오류(JSON 파싱 실패 등)는 경고만 남기고 False 반환
            logger.warning("문서 존재 확인 실패: %s", e)
            return False

    def _find_doc_id(self, data: dict, doc_id: str) -> bool:
        """localFiles 트리에서 doc_id를 재귀적으로 검색합니다.

        AnythingLLM의 문서 목록은 폴더 구조(트리)로 반환됩니다.
        딕셔너리와 리스트가 중첩된 구조를 깊이 우선으로 탐색합니다.

        매개변수:
            data (dict): /api/v1/documents 응답 JSON 전체
            doc_id (str): 찾고자 하는 문서 UUID

        반환값:
            bool: 트리 어딘가에 해당 doc_id가 있으면 True, 없으면 False
        """
        def _search(node):
            """노드를 재귀적으로 탐색하여 doc_id를 찾는 내부 함수."""
            if isinstance(node, dict):
                # 현재 딕셔너리 노드의 id가 일치하면 즉시 True 반환
                if node.get("id") == doc_id:
                    return True
                # 일치하지 않으면 모든 값(value)에 대해 재귀 탐색
                for v in node.values():
                    if _search(v):
                        return True
            elif isinstance(node, list):
                # 리스트라면 각 요소에 대해 재귀 탐색
                for item in node:
                    if _search(item):
                        return True
            return False  # 현재 노드에서 찾지 못한 경우

        # localFiles 키 아래의 트리 구조에서만 탐색 시작
        return _search(data.get("localFiles", {}))

    # ------------------------------------------------------------------
    # 4. 연결 상태 확인
    # ------------------------------------------------------------------
    def ping(self) -> bool:
        """AnythingLLM 서버에 연결할 수 있는지 확인합니다.

        /api/ping 엔드포인트에 GET 요청을 보내 서버 응답 여부를 확인합니다.
        타임아웃은 5초로 짧게 설정하여 빠른 상태 확인에 적합합니다.

        반환값:
            bool: 서버가 정상 응답하면 True, 연결 실패 또는 오류 시 False
        """
        try:
            resp = requests.get(f"{self._base_url}/api/ping",
                                headers=self._headers, timeout=5)
            return resp.ok  # 200~299 응답이면 True
        except Exception:
            # 타임아웃, 연결 오류 등 모든 예외를 False로 처리
            return False


# 모듈 전체에서 공유하는 싱글턴 인스턴스 — 매번 새로 생성하지 않고 이 객체를 재사용
api_client = ApiClient()
