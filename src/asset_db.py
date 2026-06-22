# -*- coding: utf-8 -*-
"""
Asset Database — 표·그림 이미지 저장소 (data/assets.db)

documents 테이블의 PDF별 표/그림을 PNG 이미지로 저장하고
텍스트 내 참조 태그([TABLE_001] 등)와 연결한다.
"""

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

_DDL = """
CREATE TABLE IF NOT EXISTS document_assets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL,
    file_path    TEXT    NOT NULL,
    page_num     INTEGER NOT NULL,
    asset_type   TEXT    NOT NULL CHECK(asset_type IN ('TABLE','FIGURE')),
    seq_in_doc   INTEGER NOT NULL,
    ref_tag      TEXT    NOT NULL,
    bbox_x0      REAL,
    bbox_y0      REAL,
    bbox_x1      REAL,
    bbox_y1      REAL,
    image_data   BLOB    NOT NULL,
    ocr_text     TEXT,
    caption      TEXT,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_da_reftag   ON document_assets(document_id, ref_tag);
CREATE INDEX        IF NOT EXISTS idx_da_document ON document_assets(document_id);
CREATE INDEX        IF NOT EXISTS idx_da_doctype  ON document_assets(document_id, asset_type);
"""

# ref_tag UNIQUE INDEX를 (document_id, ref_tag) 복합 인덱스로 변경하는 마이그레이션
_MIGRATE_REFTAG_INDEX = """
DROP INDEX IF EXISTS idx_da_reftag;
CREATE UNIQUE INDEX IF NOT EXISTS idx_da_reftag ON document_assets(document_id, ref_tag);
"""

_MIGRATE_ADD_OCR_TEXT = """
ALTER TABLE document_assets ADD COLUMN ocr_text TEXT;
"""


class AssetDatabase:
    def __init__(self, db_path: str = "data/assets.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init()

    def _init(self):
        with self._conn() as conn:
            conn.executescript(_DDL)
            # 마이그레이션: ocr_text 컬럼이 없으면 추가
            cols = {row[1] for row in conn.execute("PRAGMA table_info(document_assets)")}
            if "ocr_text" not in cols:
                try:
                    conn.execute(_MIGRATE_ADD_OCR_TEXT)
                    logger.info("document_assets: ocr_text 컬럼 추가 완료")
                except Exception as e:
                    logger.warning("ocr_text 컬럼 추가 실패 (이미 존재할 수 있음): %s", e)
            # 마이그레이션: ref_tag UNIQUE INDEX → (document_id, ref_tag) 복합 인덱스
            idx_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_da_reftag'"
            ).fetchone()
            if idx_row and "document_id" not in idx_row[0]:
                try:
                    conn.executescript(_MIGRATE_REFTAG_INDEX)
                    logger.info("idx_da_reftag: (ref_tag) → (document_id, ref_tag) 복합 인덱스로 변경 완료")
                except Exception as e:
                    logger.warning("idx_da_reftag 마이그레이션 실패: %s", e)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # 쓰기
    # ------------------------------------------------------------------
    def save_asset(
        self,
        *,
        document_id: int,
        file_path:   str,
        page_num:    int,
        asset_type:  str,
        seq_in_doc:  int,
        bbox:        tuple | None,
        image_data:  bytes,
        ocr_text:    str | None = None,
        caption:     str | None = None,
    ) -> str:
        """자산 1건 저장. 반환값: 참조 태그 (예: '[TABLE_001]')"""
        ref_tag = f"[{asset_type}_{seq_in_doc:03d}]"
        bx0 = float(bbox[0]) if bbox is not None else None
        by0 = float(bbox[1]) if bbox is not None else None
        bx1 = float(bbox[2]) if bbox is not None else None
        by1 = float(bbox[3]) if bbox is not None else None
        with self._conn() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO document_assets
                  (document_id, file_path, page_num, asset_type, seq_in_doc,
                   ref_tag, bbox_x0, bbox_y0, bbox_x1, bbox_y1,
                   image_data, ocr_text, caption)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    document_id, file_path, page_num, asset_type, seq_in_doc,
                    ref_tag, bx0, by0, bx1, by1,
                    image_data, ocr_text, caption,
                ),
            )
        logger.debug("자산 저장: %s (p%d, doc_id=%d)", ref_tag, page_num, document_id)
        return ref_tag

    def delete_assets_for_document(self, document_id: int) -> int:
        """문서의 모든 자산 삭제. 반환값: 삭제 건수"""
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM document_assets WHERE document_id=?", (document_id,)
            )
            n = cur.rowcount
        if n:
            logger.debug("자산 삭제: doc_id=%d (%d건)", document_id, n)
        return n

    def delete_all_assets(self) -> int:
        """전체 자산 삭제. 반환값: 삭제 건수"""
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM document_assets")
            return cur.rowcount

    # ------------------------------------------------------------------
    # 읽기
    # ------------------------------------------------------------------
    def get_assets_for_document(self, document_id: int) -> list:
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT id, page_num, asset_type, seq_in_doc, ref_tag,
                       bbox_x0, bbox_y0, bbox_x1, bbox_y1, ocr_text, caption
                FROM document_assets
                WHERE document_id=?
                ORDER BY page_num, seq_in_doc
                """,
                (document_id,),
            ).fetchall()

    def get_asset_image(self, asset_id: int) -> bytes | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT image_data FROM document_assets WHERE id=?", (asset_id,)
            ).fetchone()
            return bytes(row["image_data"]) if row else None

    def get_page_assets(self, document_id: int, page_num: int) -> list:
        with self._conn() as conn:
            return conn.execute(
                """
                SELECT id, asset_type, seq_in_doc, ref_tag,
                       bbox_x0, bbox_y0, bbox_x1, bbox_y1, image_data, caption
                FROM document_assets
                WHERE document_id=? AND page_num=?
                ORDER BY seq_in_doc
                """,
                (document_id, page_num),
            ).fetchall()

    def count_assets(self, document_id: int) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT asset_type, COUNT(*) as cnt
                FROM document_assets WHERE document_id=?
                GROUP BY asset_type
                """,
                (document_id,),
            ).fetchall()
            return {r["asset_type"]: r["cnt"] for r in rows}

    def count_all_assets(self) -> dict:
        """전체 자산 건수를 문서 ID별로 단일 쿼리로 반환.

        Returns: {document_id: {"TABLE": n, "FIGURE": n}}
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT document_id, asset_type, COUNT(*) as cnt "
                "FROM document_assets GROUP BY document_id, asset_type"
            ).fetchall()
        result: dict = {}
        for r in rows:
            doc_id = r["document_id"]
            if doc_id not in result:
                result[doc_id] = {}
            result[doc_id][r["asset_type"]] = r["cnt"]
        return result


asset_db = AssetDatabase()
