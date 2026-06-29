# -*- coding: utf-8 -*-
"""Phase 6 — API Client 단위 테스트 (requests mocking)"""

import pytest
from unittest.mock import MagicMock, patch

from src.api_client import ApiClient, UploadResult, ApiError


@pytest.fixture
def client():
    return ApiClient()


def _mock_response(status_code=200, json_data=None, raise_timeout=False, raise_conn=False):
    import requests as req
    if raise_timeout:
        mock = MagicMock(side_effect=req.exceptions.Timeout)
        return mock
    if raise_conn:
        mock = MagicMock(side_effect=req.exceptions.ConnectionError("refused"))
        return mock
    resp = MagicMock()
    resp.status_code = status_code
    resp.ok = (200 <= status_code < 300)
    resp.json.return_value = json_data or {}
    resp.text = str(json_data or {})
    return resp


UPLOAD_SUCCESS = {
    "success": True,
    "error": None,
    "documents": [{
        "id": "test-doc-uuid-1234",
        "location": "custom-documents/raw-test-test-doc-uuid-1234.json",
        "title": "test.txt",
        "wordCount": 42,
    }]
}


# ===========================================================================
# upload_text 테스트
# ===========================================================================

class TestUploadText:

    def test_upload_success(self, client):
        with patch("src.api_client.requests.post",
                   return_value=_mock_response(200, UPLOAD_SUCCESS)):
            result = client.upload_text("test.txt", "샘플 텍스트입니다.")

        assert isinstance(result, UploadResult)
        assert result.doc_id == "test-doc-uuid-1234"
        assert result.word_count == 42
        assert "custom-documents" in result.location

    def test_upload_sends_correct_payload(self, client):
        with patch("src.api_client.requests.post",
                   return_value=_mock_response(200, UPLOAD_SUCCESS)) as mock_post:
            client.upload_text("doc.pdf", "텍스트 내용")

        _, kwargs = mock_post.call_args
        payload = kwargs["json"]
        assert payload["textContent"] == "텍스트 내용"
        assert payload["metadata"]["title"] == "doc.pdf"

    def test_upload_http_error(self, client):
        with patch("src.api_client.requests.post",
                   return_value=_mock_response(500, {"error": "server error"})):
            with pytest.raises(ApiError) as exc:
                client.upload_text("test.txt", "내용")
        assert "500" in str(exc.value)

    def test_upload_auth_error(self, client):
        with patch("src.api_client.requests.post",
                   return_value=_mock_response(403, {})):
            with pytest.raises(ApiError) as exc:
                client.upload_text("test.txt", "내용")
        assert "403" in str(exc.value)

    def test_upload_timeout(self, client):
        with patch("src.api_client.requests.post",
                   _mock_response(raise_timeout=True)):
            with pytest.raises(ApiError) as exc:
                client.upload_text("test.txt", "내용")
        assert "타임아웃" in str(exc.value)

    def test_upload_connection_error(self, client):
        with patch("src.api_client.requests.post",
                   _mock_response(raise_conn=True)):
            with pytest.raises(ApiError) as exc:
                client.upload_text("test.txt", "내용")
        assert "연결 실패" in str(exc.value)

    def test_upload_success_false(self, client):
        data = {"success": False, "error": "문서 처리 실패", "documents": []}
        with patch("src.api_client.requests.post",
                   return_value=_mock_response(200, data)):
            with pytest.raises(ApiError) as exc:
                client.upload_text("test.txt", "내용")
        assert "문서 처리 실패" in str(exc.value)

    def test_upload_empty_documents(self, client):
        data = {"success": True, "error": None, "documents": []}
        with patch("src.api_client.requests.post",
                   return_value=_mock_response(200, data)):
            with pytest.raises(ApiError) as exc:
                client.upload_text("test.txt", "내용")
        assert "documents 없음" in str(exc.value)


# ===========================================================================
# embed_document 테스트
# ===========================================================================

class TestEmbedDocument:

    def test_embed_success(self, client):
        loc = "custom-documents/raw-test-uuid.json"
        body = {"workspace": {"documents": [{"docpath": loc}]}}
        with patch("src.api_client.requests.post",
                   return_value=_mock_response(200, body)):
            client.embed_document(loc)  # 워크스페이스에 반영 확인되면 예외 없음

    def test_embed_no_workspace(self, client):
        client._workspace = ""
        with pytest.raises(ApiError) as exc:
            client.embed_document("custom-documents/doc.json")
        assert "workspace" in str(exc.value)

    def test_embed_sends_correct_location(self, client):
        location = "custom-documents/raw-test-uuid.json"
        body = {"workspace": {"documents": [{"docpath": location}]}}
        with patch("src.api_client.requests.post",
                   return_value=_mock_response(200, body)) as mock_post:
            client.embed_document(location)

        _, kwargs = mock_post.call_args
        assert kwargs["json"]["adds"] == [location]
        assert kwargs["json"]["deletes"] == []

    def test_embed_http_error(self, client):
        with patch("src.api_client.requests.post",
                   return_value=_mock_response(404, {})):
            with pytest.raises(ApiError) as exc:
                client.embed_document("custom-documents/doc.json")
        assert "404" in str(exc.value)

    def test_embed_timeout(self, client):
        with patch("src.api_client.requests.post",
                   _mock_response(raise_timeout=True)):
            with pytest.raises(ApiError) as exc:
                client.embed_document("custom-documents/doc.json")
        assert "타임아웃" in str(exc.value)

    def test_embed_raises_when_not_reflected(self, client):
        # update-embeddings가 HTTP 200을 주지만 워크스페이스 documents에
        # 해당 location이 없으면(서버가 조용히 무시) 실패로 처리해야 한다.
        body = {"workspace": {"documents": []}}
        with patch("src.api_client.requests.post",
                   return_value=_mock_response(200, body)):
            with pytest.raises(ApiError) as exc:
                client.embed_document("custom-documents/raw-test-uuid.json")
        assert "반영" in str(exc.value)

    def test_embed_success_when_reflected(self, client):
        # 응답 본문의 workspace.documents에 location이 있으면 성공(예외 없음).
        loc = "custom-documents/raw-test-uuid.json"
        body = {"workspace": {"documents": [{"docpath": loc}]}}
        with patch("src.api_client.requests.post",
                   return_value=_mock_response(200, body)):
            client.embed_document(loc)


# ===========================================================================
# document_exists 테스트
# ===========================================================================

class TestDocumentExists:

    def _docs_response(self, doc_id="test-id"):
        return {
            "localFiles": {
                "name": "documents",
                "type": "folder",
                "items": [{
                    "name": "custom-documents",
                    "type": "folder",
                    "items": [{"id": doc_id, "name": "raw-test.json"}]
                }]
            }
        }

    def test_document_found(self, client):
        with patch("src.api_client.requests.get",
                   return_value=_mock_response(200, self._docs_response("abc-123"))):
            assert client.document_exists("abc-123") is True

    def test_document_not_found(self, client):
        with patch("src.api_client.requests.get",
                   return_value=_mock_response(200, self._docs_response("abc-123"))):
            assert client.document_exists("xyz-999") is False

    def test_document_exists_api_error(self, client):
        with patch("src.api_client.requests.get",
                   return_value=_mock_response(500, {})):
            assert client.document_exists("any-id") is False


# ===========================================================================
# ping 테스트
# ===========================================================================

class TestPing:

    def test_ping_success(self, client):
        with patch("src.api_client.requests.get",
                   return_value=_mock_response(200, {"online": True})):
            assert client.ping() is True

    def test_ping_failure(self, client):
        import requests as req
        with patch("src.api_client.requests.get",
                   side_effect=req.exceptions.ConnectionError):
            assert client.ping() is False
