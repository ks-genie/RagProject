# -*- coding: utf-8 -*-
import sys, re
sys.path.insert(0, ".")
from src.database import db

DOC_ID = 38

with db._connect() as conn:
    doc = conn.execute(
        "SELECT id, file_name, status, ocr_strategy, ocr_quality_score, anythingllm_doc_id "
        "FROM documents WHERE id=?", (DOC_ID,)
    ).fetchone()

    logs = conn.execute(
        "SELECT step, message, logged_at FROM process_logs WHERE document_id=? ORDER BY logged_at",
        (DOC_ID,)
    ).fetchall()

print(f"문서   : {doc[1]}")
print(f"상태   : {doc[2]}")
print(f"전략   : {doc[3]}")
print(f"품질   : {doc[4]}")
print(f"ALLM ID: {doc[5]}")
print()
print("="*60)
print("[ 처리 로그 ]")
print("="*60)
for log in logs:
    print(f"  [{log[0]:12s}] {log[1]}")
