# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, ".")
from src.database import db, Status

with db._connect() as conn:
    rows = conn.execute(
        "SELECT id, file_name, status FROM documents WHERE status = ?",
        (Status.FAILED,)
    ).fetchall()
    print(f"FAILED 문서 {len(rows)}건:")
    for r in rows:
        print(f"  [{r[0]}] {r[1]}")

    conn.execute(
        "UPDATE documents SET status=?, updated_at=datetime('now') WHERE status=?",
        (Status.NEW, Status.FAILED)
    )
    conn.commit()
    print(f"-> {len(rows)}건 NEW 초기화 완료")
