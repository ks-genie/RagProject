# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, ".")
from src.database import db, Status

with db._connect() as conn:
    rows = conn.execute(
        "SELECT id, file_name FROM documents WHERE status='ANALYZING'"
    ).fetchall()
    for r in rows:
        print(f"[{r[0]}] {r[1]} -> NEW 초기화")
    conn.execute(
        "UPDATE documents SET status=?, updated_at=datetime('now') WHERE status='ANALYZING'",
        (Status.NEW,)
    )
    conn.commit()
    print(f"{len(rows)}건 초기화 완료")
