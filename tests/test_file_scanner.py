# -*- coding: utf-8 -*-
"""Phase 2 — File Scanner 단위 테스트"""

import pytest
from pathlib import Path

from src.database import DatabaseManager, Status
from src.file_scanner import FileScanner


@pytest.fixture
def db(tmp_path):
    manager = DatabaseManager(tmp_path / "test.db")
    manager.initialize()
    return manager


@pytest.fixture
def watch_dir(tmp_path):
    d = tmp_path / "pdf_watch"
    d.mkdir()
    return d


@pytest.fixture
def scanner(watch_dir, db):
    return FileScanner(watch_dir=watch_dir, db=db)


def make_pdf(path: Path, content: bytes = b"%PDF-1.4 fake content") -> Path:
    path.write_bytes(content)
    return path


# ---------------------------------------------------------------------------

def test_scan_empty_dir(scanner):
    result = scanner.scan()
    assert result.total_found == 0


def test_scan_registers_new_pdf(scanner, watch_dir, db):
    make_pdf(watch_dir / "doc1.pdf")
    result = scanner.scan()

    assert len(result.registered) == 1
    assert len(result.errors) == 0

    doc = db.get_document_by_path(str((watch_dir / "doc1.pdf").resolve()))
    assert doc is not None
    assert doc["status"] == Status.NEW
    assert doc["file_name"] == "doc1.pdf"


def test_scan_multiple_pdfs(scanner, watch_dir):
    for i in range(3):
        make_pdf(watch_dir / f"doc{i}.pdf", content=f"%PDF fake {i}".encode())
    result = scanner.scan()

    assert len(result.registered) == 3
    assert result.total_found == 3


def test_scan_skips_nonpdf(scanner, watch_dir):
    (watch_dir / "readme.txt").write_text("not a pdf")
    (watch_dir / "image.png").write_bytes(b"\x89PNG")
    make_pdf(watch_dir / "real.pdf")

    result = scanner.scan()
    assert len(result.registered) == 1


def test_scan_recursive_subdirectory(scanner, watch_dir, db):
    subdir = watch_dir / "2024" / "papers"
    subdir.mkdir(parents=True)
    make_pdf(subdir / "paper.pdf")

    result = scanner.scan()
    assert len(result.registered) == 1
    doc = db.get_all_documents()[0]
    assert "paper.pdf" in doc["file_path"]


def test_second_scan_skips_unchanged(scanner, watch_dir):
    make_pdf(watch_dir / "doc.pdf")
    result1 = scanner.scan()
    result2 = scanner.scan()

    assert len(result1.registered) == 1
    assert len(result2.registered) == 0
    assert len(result2.skipped_duplicate) == 1


def test_scan_detects_file_change(scanner, watch_dir, db):
    pdf = make_pdf(watch_dir / "doc.pdf", content=b"%PDF original")
    scanner.scan()

    # 파일 내용 변경
    pdf.write_bytes(b"%PDF modified content")
    result = scanner.scan()

    assert len(result.requeued) == 1
    doc = db.get_document_by_path(str(pdf.resolve()))
    assert doc["status"] == Status.NEW


def test_scan_skips_duplicate_hash(scanner, watch_dir):
    content = b"%PDF identical content"
    make_pdf(watch_dir / "original.pdf", content)
    scanner.scan()

    # 동일한 내용의 다른 파일명
    make_pdf(watch_dir / "copy.pdf", content)
    result = scanner.scan()

    assert len(result.registered) == 0
    assert len(result.skipped_duplicate) == 2  # original(기존) + copy(중복)


def test_scan_logs_step(scanner, watch_dir, db):
    make_pdf(watch_dir / "doc.pdf")
    result = scanner.scan()

    doc_id = result.registered[0]
    logs = db.get_logs(doc_id)
    assert len(logs) == 1
    assert logs[0]["step"] == "SCAN"
    assert logs[0]["result"] == "SUCCESS"


def test_scan_result_summary(scanner, watch_dir):
    make_pdf(watch_dir / "a.pdf", b"%PDF a")
    make_pdf(watch_dir / "b.pdf", b"%PDF b")
    result = scanner.scan()

    summary = result.summary()
    assert "신규 등록: 2건" in summary
