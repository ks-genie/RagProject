# -*- coding: utf-8 -*-
"""
마이그레이션 스크립트 — DB 초기화 및 스키마 버전 관리
실행: python migrate.py
"""

import sys
import io
import sqlite3
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from src.database import DatabaseManager
from src.config import config

# ---------------------------------------------------------------------------
# 마이그레이션 목록 — 순서대로 실행됨
# (version, description, sql)
# ---------------------------------------------------------------------------
MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "초기 스키마 생성 (documents, process_logs)",
        "",  # DatabaseManager.initialize()가 처리
    ),
    (
        2,
        "PDF 분류 분석 컬럼 추가 (source_type, layout_type, detected_languages, has_formula, ocr_strategy)",
        """
        ALTER TABLE documents ADD COLUMN source_type        TEXT;
        ALTER TABLE documents ADD COLUMN layout_type        TEXT;
        ALTER TABLE documents ADD COLUMN detected_languages TEXT;
        ALTER TABLE documents ADD COLUMN has_formula        INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE documents ADD COLUMN ocr_strategy       TEXT;
        """,
    ),
]

DDL_MIGRATION_LOG = """
CREATE TABLE IF NOT EXISTS migration_log (
    version     INTEGER PRIMARY KEY,
    description TEXT    NOT NULL,
    applied_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def get_applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(DDL_MIGRATION_LOG)
    rows = conn.execute("SELECT version FROM migration_log").fetchall()
    return {r[0] for r in rows}


def mark_applied(conn: sqlite3.Connection, version: int, description: str):
    conn.execute(
        "INSERT OR IGNORE INTO migration_log (version, description) VALUES (?, ?)",
        (version, description),
    )


def run():
    db_path = config.db_path
    print(f"DB 경로: {db_path}")

    manager = DatabaseManager(db_path)
    manager.initialize()

    conn = sqlite3.connect(db_path)
    try:
        applied = get_applied_versions(conn)

        pending = [(v, d, s) for v, d, s in MIGRATIONS if v not in applied]
        if not pending:
            print("적용할 마이그레이션 없음 — 최신 상태입니다.")
        else:
            for version, desc, sql in pending:
                print(f"  [v{version}] {desc} ... ", end="")
                if sql:
                    conn.executescript(sql)
                mark_applied(conn, version, desc)
                conn.commit()
                print("완료")

        # 현재 상태 출력
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
        print("\n생성된 테이블:")
        for (t,) in tables:
            count = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            print(f"  - {t} ({count}건)")

    finally:
        conn.close()

    print("\nDB 마이그레이션 완료")


if __name__ == "__main__":
    run()
