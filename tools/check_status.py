# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, ".")
from src.database import db

with db._connect() as conn:
    rows = conn.execute(
        "SELECT id, file_name, status, updated_at FROM documents "
        "WHERE status NOT IN ('INDEXED') ORDER BY updated_at DESC"
    ).fetchall()
    for r in rows:
        print(f"[{r[0]}] {r[2]:20s} | {r[3]} | {r[1]}")
