# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, ".")
from src.database import db

with db._connect() as conn:
    total = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    for s in ["INDEXED", "REVIEW_REQUIRED", "FAILED", "NEW", "ANALYZING"]:
        n = conn.execute("SELECT COUNT(*) FROM documents WHERE status=?", (s,)).fetchone()[0]
        if n:
            print(f"  {s:20s}: {n}건")
    print(f"  {'TOTAL':20s}: {total}건")

    # 한국어 문서 확인
    kor = conn.execute(
        "SELECT id, file_name, status FROM documents WHERE file_name LIKE '%주요국%'"
    ).fetchone()
    if kor:
        print(f"\n한국어 문서: [{kor[0]}] {kor[1]} -> {kor[2]}")
