# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, ".")
from src.database import db, Status

with db._connect() as conn:
    rows = conn.execute(
        "SELECT DISTINCT document_id FROM process_logs WHERE message LIKE ?",
        ("%UNIQUE%",)
    ).fetchall()
    ids = [r[0] for r in rows]
    placeholders = ",".join("?" * len(ids))
    conn.execute(
        f"UPDATE documents SET status=?, updated_at=datetime('now') WHERE id IN ({placeholders})",
        [Status.NEW] + ids
    )
    conn.commit()
    print(f"{len(ids)}건 NEW 초기화 완료")
    print(f"대상 doc_id: {ids}")
