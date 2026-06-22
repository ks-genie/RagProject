# -*- coding: utf-8 -*-
"""Phase 1 — DB 단위 테스트"""

import tempfile
from pathlib import Path

import pytest

from src.database import DatabaseManager, Status, compute_file_hash


@pytest.fixture
def db(tmp_path):
    manager = DatabaseManager(tmp_path / "test.db")
    manager.initialize()
    return manager


def test_register_document(db):
    doc_id = db.register_document("/path/to/test.pdf", "test.pdf", "abc123")
    assert doc_id is not None
    doc = db.get_document_by_id(doc_id)
    assert doc["status"] == Status.NEW
    assert doc["file_name"] == "test.pdf"


def test_duplicate_registration_returns_none(db):
    db.register_document("/path/to/test.pdf", "test.pdf", "abc123")
    result = db.register_document("/path/to/test.pdf", "test.pdf", "abc123")
    assert result is None


def test_update_status(db):
    doc_id = db.register_document("/path/to/a.pdf", "a.pdf", "hash1")
    db.update_status(doc_id, Status.TEXT_EXTRACTED)
    doc = db.get_document_by_id(doc_id)
    assert doc["status"] == Status.TEXT_EXTRACTED


def test_update_status_with_ocr_score(db):
    doc_id = db.register_document("/path/to/b.pdf", "b.pdf", "hash2")
    db.update_status(doc_id, Status.OCR_DONE, ocr_quality_score=0.85)
    doc = db.get_document_by_id(doc_id)
    assert doc["ocr_quality_score"] == pytest.approx(0.85)


def test_failed_status_with_error(db):
    doc_id = db.register_document("/path/to/c.pdf", "c.pdf", "hash3")
    db.update_status(doc_id, Status.FAILED, error_message="timeout", failed_step="OCR")
    doc = db.get_document_by_id(doc_id)
    assert doc["status"] == Status.FAILED
    assert doc["error_message"] == "timeout"
    assert doc["failed_step"] == "OCR"


def test_reset_for_retry(db):
    doc_id = db.register_document("/path/to/d.pdf", "d.pdf", "hash4")
    db.update_status(doc_id, Status.FAILED, error_message="err", failed_step="UPLOAD")
    db.reset_for_retry(doc_id)
    doc = db.get_document_by_id(doc_id)
    assert doc["status"] == Status.NEW
    assert doc["error_message"] is None


def test_log_step(db):
    doc_id = db.register_document("/path/to/e.pdf", "e.pdf", "hash5")
    db.log_step(doc_id, "OCR", "SUCCESS", "글자 수: 320")
    logs = db.get_logs(doc_id)
    assert len(logs) == 1
    assert logs[0]["step"] == "OCR"
    assert logs[0]["result"] == "SUCCESS"


def test_get_documents_by_status(db):
    db.register_document("/a.pdf", "a.pdf", "h1")
    db.register_document("/b.pdf", "b.pdf", "h2")
    db.register_document("/c.pdf", "c.pdf", "h3")
    doc_id = db.get_document_by_path("/c.pdf")["id"]
    db.update_status(doc_id, Status.TEXT_READY)

    new_docs = db.get_documents_by_status(Status.NEW)
    assert len(new_docs) == 2
    ready_docs = db.get_documents_by_status(Status.TEXT_READY)
    assert len(ready_docs) == 1


def test_increment_retry(db):
    doc_id = db.register_document("/r.pdf", "r.pdf", "hr")
    db.increment_retry(doc_id)
    db.increment_retry(doc_id)
    doc = db.get_document_by_id(doc_id)
    assert doc["retry_count"] == 2
