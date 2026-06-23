# -*- coding: utf-8 -*-
"""
Page DB — document_metadata / page_contents 테이블 관리

document_metadata : 문서 레벨 메타데이터 (제목·저자·소속 등)
page_contents     : 페이지 레벨 구조화 콘텐츠 (헤더·푸터·본문)

이 모듈은 RAG 파이프라인에서 PDF 문서를 파싱한 뒤,
문서 단위 정보와 페이지 단위 정보를 SQLite DB에 저장·조회·삭제하는
모든 기능을 담당합니다.
"""

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from src.config import config

# 이 모듈 전용 로거 생성 — 로그 메시지에 모듈 이름이 함께 출력됩니다.
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# DDL(Data Definition Language): DB 테이블 및 인덱스 생성 SQL 정의
# ------------------------------------------------------------------
# 아래 SQL은 모듈이 처음 로드될 때 테이블이 없으면 자동으로 생성합니다.
# "CREATE TABLE IF NOT EXISTS" 덕분에 이미 존재하면 오류 없이 건너뜁니다.
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
    """
    SQLite 기반 페이지 DB 관리 클래스.

    두 개의 테이블을 통합 관리합니다:
    - document_metadata : 문서 전체에 대한 메타데이터 (제목, 저자, 초록 등)
    - page_contents     : 각 페이지별 헤더·푸터·본문 텍스트

    사용 예시:
        db = PageDatabase()
        db.save_document_metadata(doc_id, title="논문 제목", authors=["홍길동"])
        meta = db.get_document_metadata(doc_id)
    """

    def __init__(self, db_path: str = None):
        """
        PageDatabase 인스턴스를 초기화합니다.

        매개변수:
            db_path (str, 선택): SQLite DB 파일 경로.
                                 지정하지 않으면 config에서 설정된 기본 경로를 사용합니다.
        """
        # db_path가 없으면 프로젝트 전역 설정(config.db_path)을 사용합니다.
        self.db_path = db_path or str(config.db_path)

        # DB 파일이 저장될 디렉터리가 없으면 자동으로 생성합니다.
        # parents=True : 중간 경로도 함께 생성, exist_ok=True : 이미 있어도 오류 없음
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)

        # 테이블 및 인덱스 초기화
        self._init()

    @contextmanager
    def _conn(self):
        """
        SQLite 연결을 열고 트랜잭션을 관리하는 컨텍스트 매니저.

        'with self._conn() as conn:' 형태로 사용합니다.
        - 정상 종료 시: 변경사항을 커밋(저장)합니다.
        - 예외 발생 시: 변경사항을 롤백(취소)하고 예외를 다시 던집니다.
        - 항상 연결을 닫아 리소스 누수를 방지합니다.
        """
        conn = sqlite3.connect(self.db_path)

        # row_factory를 설정하면 쿼리 결과를 dict처럼 컬럼명으로 접근할 수 있습니다.
        # 예: row["title"] 형태로 접근 가능
        conn.row_factory = sqlite3.Row

        # WAL(Write-Ahead Logging) 모드: 읽기와 쓰기를 동시에 허용해 성능을 향상시킵니다.
        conn.execute("PRAGMA journal_mode=WAL;")

        # 외래 키 제약 조건 활성화: SQLite는 기본으로 비활성 상태이므로 명시적으로 켜야 합니다.
        conn.execute("PRAGMA foreign_keys=ON;")

        try:
            yield conn          # 호출자에게 연결 객체를 전달합니다.
            conn.commit()       # 예외 없이 with 블록이 끝나면 변경사항을 확정합니다.
        except Exception:
            conn.rollback()     # 오류 발생 시 모든 변경사항을 취소합니다.
            raise               # 예외를 다시 던져 호출자가 처리하도록 합니다.
        finally:
            conn.close()        # 성공/실패 여부와 관계없이 반드시 연결을 닫습니다.

    def _init(self):
        """
        DB 테이블과 인덱스를 초기화합니다.

        모듈 최초 실행 시 또는 DB 파일이 새로 생성될 때 호출됩니다.
        이미 테이블이 존재하면 아무 작업도 하지 않습니다.
        """
        with self._conn() as conn:
            # executescript는 여러 SQL 문을 한 번에 실행합니다.
            conn.executescript(_DDL)

    # ------------------------------------------------------------------
    # document_metadata 관련 메서드
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
        """
        문서 메타데이터를 저장하거나 업데이트합니다(Upsert).

        동일한 document_id가 이미 존재하면 모든 필드를 새 값으로 갱신합니다.
        존재하지 않으면 새 레코드를 삽입합니다.

        매개변수:
            document_id  (int)  : 대상 문서의 고유 ID (documents 테이블의 외래 키)
            title        (str)  : 문서 제목
            authors      (list) : 저자 목록. 예: ["홍길동", "김철수"]
            affiliations (list) : 저자 소속 기관 목록. 예: ["서울대학교", "KAIST"]
            abstract     (str)  : 논문 초록(요약문)
            keywords     (str)  : 키워드 (쉼표 구분 등 자유 형식)

        반환값:
            없음 (None)
        """
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
                    # 리스트를 JSON 문자열로 변환해 TEXT 컬럼에 저장합니다.
                    # ensure_ascii=False : 한글 등 비ASCII 문자를 그대로 저장합니다.
                    json.dumps(list(authors), ensure_ascii=False),
                    json.dumps(list(affiliations), ensure_ascii=False),
                    abstract or "",
                    keywords or "",
                ),
            )

    def get_document_metadata(self, document_id: int) -> dict | None:
        """
        특정 문서의 메타데이터를 조회합니다.

        매개변수:
            document_id (int): 조회할 문서의 고유 ID

        반환값:
            dict  : 메타데이터 딕셔너리. authors와 affiliations는 리스트로 변환됩니다.
            None  : 해당 document_id의 레코드가 없을 경우
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM document_metadata WHERE document_id = ?",
                (document_id,),
            ).fetchone()

        if row is None:
            return None

        # sqlite3.Row 객체를 일반 dict로 변환합니다.
        d = dict(row)

        # DB에는 JSON 문자열로 저장된 authors/affiliations를 리스트로 복원합니다.
        d["authors"]      = _parse_json_list(d.get("authors"))
        d["affiliations"] = _parse_json_list(d.get("affiliations"))
        return d

    def get_all_metadata(self) -> dict:
        """
        모든 문서의 메타데이터를 한 번에 조회합니다 (UI 일괄 조회용).

        반환값:
            dict: { document_id(int) → metadata(dict) } 형태의 딕셔너리.
                  예: { 1: {"title": "...", "authors": [...], ...}, 2: {...} }
        """
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM document_metadata").fetchall()

        result = {}
        for row in rows:
            d = dict(row)
            # JSON 문자열로 저장된 리스트 필드를 다시 파이썬 리스트로 변환합니다.
            d["authors"]      = _parse_json_list(d.get("authors"))
            d["affiliations"] = _parse_json_list(d.get("affiliations"))
            # document_id를 키로 사용해 빠른 조회가 가능한 딕셔너리를 구성합니다.
            result[d["document_id"]] = d
        return result

    def delete_document_metadata(self, document_id: int) -> None:
        """
        특정 문서의 메타데이터 레코드를 삭제합니다.

        매개변수:
            document_id (int): 삭제할 문서의 고유 ID

        반환값:
            없음 (None)
        """
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM document_metadata WHERE document_id = ?", (document_id,)
            )

    # ------------------------------------------------------------------
    # page_contents 관련 메서드
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
        """
        특정 페이지의 구조화 텍스트를 저장하거나 업데이트합니다(Upsert).

        동일한 (document_id, page_num) 조합이 이미 존재하면 텍스트 필드를 갱신합니다.

        매개변수:
            document_id (int): 대상 문서의 고유 ID
            page_num    (int): 페이지 번호 (1부터 시작)
            header_text (str): 페이지 상단 헤더 텍스트
            footer_text (str): 페이지 하단 푸터 텍스트
            body_text   (str): 페이지 본문 텍스트

        반환값:
            없음 (None)
        """
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
        """
        특정 문서의 특정 페이지 내용을 조회합니다.

        매개변수:
            document_id (int): 조회할 문서의 고유 ID
            page_num    (int): 조회할 페이지 번호

        반환값:
            dict : 페이지 내용 딕셔너리 (header_text, footer_text, body_text 등 포함)
            None : 해당 페이지 레코드가 없을 경우
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM page_contents WHERE document_id = ? AND page_num = ?",
                (document_id, page_num),
            ).fetchone()
        # row가 존재하면 dict로 변환해 반환, 없으면 None 반환
        return dict(row) if row else None

    def delete_page_contents(self, document_id: int) -> None:
        """
        특정 문서에 속한 모든 페이지 내용을 삭제합니다.

        매개변수:
            document_id (int): 삭제할 문서의 고유 ID

        반환값:
            없음 (None)
        """
        with self._conn() as conn:
            conn.execute(
                "DELETE FROM page_contents WHERE document_id = ?", (document_id,)
            )

    # ------------------------------------------------------------------
    # 일괄 삭제 (재처리 시)
    # ------------------------------------------------------------------

    def delete_all(self, document_id: int) -> None:
        """
        문서 재처리 시 해당 문서의 모든 구조화 데이터를 삭제합니다.

        page_contents와 document_metadata를 순서대로 삭제합니다.
        문서를 다시 파싱하기 전에 기존 데이터를 완전히 지울 때 사용합니다.

        매개변수:
            document_id (int): 삭제 대상 문서의 고유 ID

        반환값:
            없음 (None)
        """
        # 페이지 내용을 먼저 삭제한 뒤 문서 메타데이터를 삭제합니다.
        # 순서가 중요하지는 않지만, 논리적으로 하위 데이터를 먼저 정리합니다.
        self.delete_page_contents(document_id)
        self.delete_document_metadata(document_id)


# ---------------------------------------------------------------------------
# 헬퍼 함수
# ---------------------------------------------------------------------------

def _parse_json_list(value: str | None) -> list:
    """
    JSON 문자열을 파이썬 리스트로 안전하게 변환합니다.

    DB에서 읽어온 JSON 문자열을 리스트로 복원할 때 사용합니다.
    값이 None이거나 JSON 파싱에 실패하면 빈 리스트를 반환합니다.

    매개변수:
        value (str | None): JSON 배열 형식의 문자열. 예: '["홍길동", "김철수"]'

    반환값:
        list: 파싱된 리스트. 실패 시 빈 리스트([])를 반환합니다.
    """
    try:
        # value가 None이면 "[]"를 파싱해 빈 리스트를 반환합니다.
        result = json.loads(value or "[]")
        # 파싱 결과가 리스트인지 확인 — 예상치 못한 타입(dict, int 등)이면 빈 리스트 반환
        return result if isinstance(result, list) else []
    except Exception:
        # JSON 파싱 오류(잘못된 형식 등) 시 빈 리스트로 안전하게 처리합니다.
        return []


# 모듈 레벨 싱글턴 — 앱 전역에서 이 인스턴스를 공유해 DB 연결 오버헤드를 줄입니다.
# 다른 모듈에서는 "from src.page_db import page_db"로 가져다 씁니다.
page_db = PageDatabase()
