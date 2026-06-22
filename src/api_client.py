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
from dataclasses import dataclass

import requests

from src.config import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 결과 컨테이너
# ---------------------------------------------------------------------------
@dataclass
class UploadResult:
    doc_id: str          # AnythingLLM 문서 UUID
    location: str        # 서버 내 파일 경로 (embed에 사용)
    title: str = ""
    word_count: int = 0


class ApiError(Exception):
    """API 호출 실패 시 발생"""
    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


# ---------------------------------------------------------------------------
# ApiClient
# ---------------------------------------------------------------------------
class ApiClient:
    def __init__(self):
        self._base_url  = config.allm_base_url.rstrip("/")
        self._api_key   = config.allm_api_key
        self._workspace = config.allm_workspace
        self._timeout   = config.allm_upload_timeout

    @property
    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

    # ------------------------------------------------------------------
    # 1. 텍스트 업로드
    # ------------------------------------------------------------------
    def upload_text(self, filename: str, text: str) -> UploadResult:
        """텍스트를 AnythingLLM에 업로드하고 UploadResult 반환.

        Raises:
            ApiError: HTTP 오류 또는 응답 이상
        """
        url = f"{self._base_url}/api/v1/document/raw-text"
        payload = {
            "textContent": text,
            "metadata": {
                "title": filename,
                "docSource": "rag-project",
                "description": f"Auto-uploaded: {filename}",
            },
        }
        logger.info("업로드 요청: %s (%d자)", filename, len(text))

        try:
            resp = requests.post(url, headers=self._headers, json=payload, timeout=self._timeout)
        except requests.exceptions.Timeout:
            raise ApiError(f"업로드 타임아웃 ({self._timeout}초): {filename}")
        except requests.exceptions.ConnectionError as e:
            raise ApiError(f"연결 실패: {e}")

        if resp.status_code == 403:
            raise ApiError("API 인증 실패 (403) — api_key 확인", 403)
        if not resp.ok:
            raise ApiError(f"업로드 실패 HTTP {resp.status_code}: {resp.text[:200]}", resp.status_code)

        data = resp.json()
        if not data.get("success"):
            raise ApiError(f"업로드 응답 오류: {data.get('error', 'unknown')}")

        docs = data.get("documents", [])
        if not docs:
            raise ApiError("업로드 응답에 documents 없음")

        doc = docs[0]
        result = UploadResult(
            doc_id    = doc["id"],
            location  = doc["location"],
            title     = doc.get("title", filename),
            word_count= doc.get("wordCount", 0),
        )
        logger.info("업로드 완료: doc_id=%s (%d words)", result.doc_id, result.word_count)
        return result

    # ------------------------------------------------------------------
    # 2. 워크스페이스 임베딩
    # ------------------------------------------------------------------
    def embed_document(self, location: str) -> None:
        """문서를 워크스페이스에 임베딩 (동기 처리 — 완료 = INDEXED).

        Raises:
            ApiError: 워크스페이스 미설정 또는 HTTP 오류
        """
        if not self._workspace:
            raise ApiError("workspace slug가 config.yaml에 설정되지 않았습니다")

        url = f"{self._base_url}/api/v1/workspace/{self._workspace}/update-embeddings"
        payload = {"adds": [location], "deletes": []}

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
        """업로드된 문서가 AnythingLLM에 존재하는지 확인."""
        url = f"{self._base_url}/api/v1/documents"
        try:
            resp = requests.get(url, headers=self._headers, timeout=10)
            if not resp.ok:
                return False
            data = resp.json()
            return self._find_doc_id(data, doc_id)
        except Exception as e:
            logger.warning("문서 존재 확인 실패: %s", e)
            return False

    def _find_doc_id(self, data: dict, doc_id: str) -> bool:
        """localFiles 트리에서 doc_id 검색."""
        def _search(node):
            if isinstance(node, dict):
                if node.get("id") == doc_id:
                    return True
                for v in node.values():
                    if _search(v):
                        return True
            elif isinstance(node, list):
                for item in node:
                    if _search(item):
                        return True
            return False
        return _search(data.get("localFiles", {}))

    # ------------------------------------------------------------------
    # 4. 연결 상태 확인
    # ------------------------------------------------------------------
    def ping(self) -> bool:
        try:
            resp = requests.get(f"{self._base_url}/api/ping",
                                headers=self._headers, timeout=5)
            return resp.ok
        except Exception:
            return False


# 싱글턴
api_client = ApiClient()
