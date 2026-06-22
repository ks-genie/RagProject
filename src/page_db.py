# -*- coding: utf-8 -*-
"""
Page DB — document_metadata / page_contents 테이블 관리

document_metadata : 문서 레벨 메타데이터 (제목·저자·소속 등)
page_contents     : 페이지 레벨 구조화 콘텐츠 (헤더·푸터·본문)
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from src.config import config

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS document_metadata (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   INTEGER NOT NULL UNIQUE,
    title         TEXT,
    authors       TEXT,
    affiliations  TEXT,
    abstract      TEXT,
    keywords      TEXT,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS page_contents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   INTEGER NOT NULL,
    page_num      INTEGER NOT NULL,
    header_text   TEXT,
    footer_text   TEXT,
    body_text     TEXT,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (document_id, page_num),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dm_document ON document_metadata(document_id);
CREATE INDEX IF NOT EXISTS idx_pc_document ON page_contents(document_id);
"""


class PageDatabase:
    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(config.db_path)
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init(self):
        with self._conn() as conn:
            conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # document_metadata
    # ------------------------------------------------------------------

    def save_document_metadata(
        self,
        document_id: int,
        *,
        title: str = "",
        authors: list = (),
        affiliations: list = (),
        abstract: str = "",
        keywords: str = "",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO document_metadata
                    (document_id, title, authors, affiliations, abstract, keywords)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    title        = excluded.title,
                    authors      = excluded.authors,
                    affiliations = excluded.affiliations,
                    abstract     = excluded.abstract,
                    keywords     = excluded.keywords
                """,
                (
                    document_id,
                    title or "",
                    json.dumps(list(authors), ensure_ascii=False),
                    json.dumps(list(affiliations), ensure_ascii=False),
                    abstract or "",
                    keywords or "",
                ),
            )

    def get_document_metadata(self, document_id: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM document_metadata WHERE document_id = ?",
                (document_id,),
            ).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["authors"]      = _parse_json_list(d.get("authors"))
        d["affiliations"] = _parse_json_list(d.get("affiliations"))
        return d

    def get_all_metadata(self) -> dict:
        """document_id → metadata dict 매핑 반환 (UI 일괄 조회용)."""
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM document_metadata").fetchall()
        result = {}
        for row in rows:
            d = dict(row)
            d["authors"]      = _parse_json_list(d.get("authors"))
            d["affiliations"] = _parse_json_list(d.get("affiliations"))
            result[d["document_id"]] = d
        return result

    def delete_document_metadata(self, document_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM document_metadata WHERE document_id = ?", (document_id,)
            )

    # ------------------------------------------------------------------
    # page_contents
    # ------------------------------------------------------------------

    def save_page_content(
        self,
        document_id: int,
        page_num: int,
        *,
        header_text: str = "",
        footer_text: str = "",
        body_text: str = "",
    ) -> None:
        with self._conn() as conn:
            conn.execute(
                """
                INSERT INTO page_contents
                    (document_id, page_num, header_text, footer_text, body_text)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(document_id, page_num) DO UPDATE SET
                    header_text = excluded.header_text,
                    footer_text = excluded.footer_text,
                    body_text   = excluded.body_text
                """,
                (document_id, page_num, header_text or "", footer_text or "", body_text or ""),
            )

    def get_page_content(self, document_id: int, page_num: int) -> dict | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM page_contents WHERE document_id = ? AND page_num = ?",
                (document_id, page_num),
            ).fetchone()
        return dict(row) if row else None

    def delete_page_contents(self, document_id: int) -> None:
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM page_contents WHERE document_id = ?", (document_id,)
            )

    # ------------------------------------------------------------------
    # 일괄 삭제 (재처리 시)
    # ------------------------------------------------------------------

    def delete_all(self, document_id: int) -> None:
        """문서 재처리 시 해당 문서의 모든 구조화 데이터 삭제."""
        self.delete_page_contents(document_id)
        self.delete_document_metadata(document_id)


# ---------------------------------------------------------------------------
# 헬퍼
# ---------------------------------------------------------------------------

def _parse_json_list(value: str | None) -> list:
    try:
        result = json.loads(value or "[]")
        return result if isinstance(result, list) else []
    except Exception:
        return []


# 모듈 레벨 싱글턴
page_db = PageDatabase()
