# -*- coding: utf-8 -*-
"""
Asset Database — 표·그림 이미지 저장소 (data/assets.db)

documents 테이블의 PDF별 표/그림을 PNG 이미지로 저장하고
텍스트 내 참조 태그([TABLE_001] 등)와 연결한다.
"""

import logging
import sqlite3
from contextlib import contextmanager  # 'with' 구문을 지원하는 컨텍스트 매니저를 쉽게 만들기 위해 사용
from pathlib import Path               # 파일/디렉토리 경로를 다루는 객체 지향 방식의 모듈

# 이 모듈 전용 로거 생성 — 모듈 이름(asset_db)으로 로그를 구분할 수 있음
logger = logging.getLogger(__name__)

# DDL(Data Definition Language): 데이터베이스 테이블과 인덱스를 생성하는 SQL 문
# IF NOT EXISTS를 사용해 이미 존재하면 무시하므로 중복 실행해도 안전함
_DDL = """
CREATE TABLE IF NOT EXISTS document_assets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,  -- 자동 증가 고유 ID
    document_id  INTEGER NOT NULL,                   -- 부모 문서의 ID (documents 테이블 참조)
    file_path    TEXT    NOT NULL,                   -- 원본 PDF 파일 경로
    page_num     INTEGER NOT NULL,                   -- 자산이 위치한 페이지 번호
    asset_type   TEXT    NOT NULL CHECK(asset_type IN ('TABLE','FIGURE')),  -- 자산 종류: 표 또는 그림만 허용
    seq_in_doc   INTEGER NOT NULL,                   -- 문서 내 순서 (예: 3번째 표이면 3)
    ref_tag      TEXT    NOT NULL,                   -- 텍스트에서 참조할 태그 (예: [TABLE_001])
    bbox_x0      REAL,                               -- 바운딩 박스 좌상단 x 좌표 (없으면 NULL)
    bbox_y0      REAL,                               -- 바운딩 박스 좌상단 y 좌표
    bbox_x1      REAL,                               -- 바운딩 박스 우하단 x 좌표
    bbox_y1      REAL,                               -- 바운딩 박스 우하단 y 좌표
    image_data   BLOB    NOT NULL,                   -- PNG 이미지 바이너리 데이터
    ocr_text     TEXT,                               -- 이미지에서 OCR로 추출한 텍스트 (없으면 NULL)
    caption      TEXT,                               -- 표/그림 캡션 텍스트 (없으면 NULL)
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP  -- 레코드 생성 시각 (자동 기록)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_da_reftag   ON document_assets(document_id, ref_tag);  -- 같은 문서 내에서 ref_tag 중복 방지
CREATE INDEX        IF NOT EXISTS idx_da_document ON document_assets(document_id);            -- 문서 ID 기준 검색 속도 향상
CREATE INDEX        IF NOT EXISTS idx_da_doctype  ON document_assets(document_id, asset_type);  -- 문서+유형 조합 검색 속도 향상
"""

# ref_tag UNIQUE INDEX를 (document_id, ref_tag) 복합 인덱스로 변경하는 마이그레이션
# 기존에 ref_tag 단독 인덱스로 만든 경우, 다른 문서에서 동일한 태그명 사용이 불가능했던 문제를 해결
_MIGRATE_REFTAG_INDEX = """
DROP INDEX IF EXISTS idx_da_reftag;
CREATE UNIQUE INDEX IF NOT EXISTS idx_da_reftag ON document_assets(document_id, ref_tag);
"""

# 이전 버전에서 ocr_text 컬럼이 없을 때 추가하는 마이그레이션 SQL
_MIGRATE_ADD_OCR_TEXT = """
ALTER TABLE document_assets ADD COLUMN ocr_text TEXT;
"""


class AssetDatabase:
    """
    표(TABLE)와 그림(FIGURE) 이미지를 SQLite 데이터베이스에 저장하고 조회하는 클래스.

    PDF 문서에서 추출한 표·그림 이미지를 data/assets.db에 보관하며,
    각 자산에는 텍스트에서 참조할 수 있는 고유 태그(예: [TABLE_001])가 부여된다.
    """

    def __init__(self, db_path: str = "data/assets.db"):
        """
        AssetDatabase 인스턴스를 초기화하고 데이터베이스를 준비한다.

        매개변수:
            db_path: SQLite 데이터베이스 파일 경로. 기본값은 'data/assets.db'.
        """
        self.db_path = db_path
        # 데이터베이스 파일이 위치할 디렉토리가 없으면 자동으로 생성
        # parents=True: 중간 경로도 모두 생성, exist_ok=True: 이미 있어도 오류 없이 통과
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        # 테이블 생성 및 스키마 마이그레이션 실행
        self._init()

    def _init(self):
        """
        데이터베이스 테이블을 생성하고 스키마 마이그레이션을 실행한다.

        처음 실행 시 테이블과 인덱스를 생성하며,
        이전 버전 DB라면 누락된 컬럼이나 인덱스를 자동으로 보완한다.
        """
        with self._conn() as conn:
            # DDL 실행 — 테이블과 인덱스를 생성 (이미 있으면 건너뜀)
            conn.executescript(_DDL)

            # 마이그레이션: ocr_text 컬럼이 없으면 추가
            # PRAGMA table_info()는 테이블의 컬럼 정보를 반환하는 SQLite 내장 명령
            cols = {row[1] for row in conn.execute("PRAGMA table_info(document_assets)")}
            if "ocr_text" not in cols:
                try:
                    conn.execute(_MIGRATE_ADD_OCR_TEXT)
                    logger.info("document_assets: ocr_text 컬럼 추가 완료")
                except Exception as e:
                    # ALTER TABLE이 실패해도 치명적이지 않으므로 경고만 남김
                    logger.warning("ocr_text 컬럼 추가 실패 (이미 존재할 수 있음): %s", e)

            # 마이그레이션: ref_tag UNIQUE INDEX → (document_id, ref_tag) 복합 인덱스
            # sqlite_master는 SQLite의 스키마 정보를 담는 시스템 테이블
            idx_row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_da_reftag'"
            ).fetchone()
            # 기존 인덱스가 존재하고 document_id를 포함하지 않는 구버전 형식이면 마이그레이션 수행
            if idx_row and "document_id" not in idx_row[0]:
                try:
                    conn.executescript(_MIGRATE_REFTAG_INDEX)
                    logger.info("idx_da_reftag: (ref_tag) → (document_id, ref_tag) 복합 인덱스로 변경 완료")
                except Exception as e:
                    logger.warning("idx_da_reftag 마이그레이션 실패: %s", e)

    @contextmanager
    def _conn(self):
        """
        SQLite 데이터베이스 연결을 'with' 구문으로 관리하는 컨텍스트 매니저.

        정상 완료 시 자동으로 커밋하고, 예외 발생 시 롤백 후 예외를 다시 발생시킨다.
        사용 후 연결은 항상 닫힌다.
        """
        conn = sqlite3.connect(self.db_path)
        # row_factory 설정: 쿼리 결과를 튜플 대신 컬럼 이름으로 접근 가능한 Row 객체로 반환
        # 예: row[0] 대신 row["id"]처럼 사용할 수 있음
        conn.row_factory = sqlite3.Row
        try:
            yield conn          # 'with' 블록 안에서 conn 객체를 사용할 수 있게 전달
            conn.commit()       # 블록이 정상 종료되면 변경사항을 DB에 확정
        except Exception:
            conn.rollback()     # 예외 발생 시 이번 트랜잭션의 모든 변경사항 취소
            raise               # 예외를 호출자에게 다시 전달
        finally:
            conn.close()        # 성공/실패 여부와 상관없이 연결 반드시 닫기

    # ------------------------------------------------------------------
    # 쓰기
    # ------------------------------------------------------------------
    def save_asset(
        self,
        *,                       # 이하 모든 인자는 키워드 인자로만 전달 가능 (순서 오류 방지)
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
        """
        자산(표 또는 그림) 1건을 데이터베이스에 저장한다.

        같은 document_id + ref_tag 조합이 이미 존재하면 덮어쓴다(INSERT OR REPLACE).

        매개변수:
            document_id: 이 자산이 속한 문서의 ID
            file_path:   원본 PDF 파일 경로
            page_num:    자산이 있는 페이지 번호
            asset_type:  자산 종류 — 'TABLE' 또는 'FIGURE'
            seq_in_doc:  문서 내 해당 유형의 순서 번호 (예: 3번째 표이면 3)
            bbox:        바운딩 박스 좌표 (x0, y0, x1, y1) 튜플 또는 None
            image_data:  PNG 이미지 바이너리 데이터
            ocr_text:    OCR로 추출한 텍스트 (선택사항)
            caption:     자산의 캡션 텍스트 (선택사항)

        반환값:
            생성된 참조 태그 문자열 (예: '[TABLE_001]', '[FIGURE_003]')
        """
        # seq_in_doc을 3자리 숫자로 zero-padding하여 태그 생성 (예: 1 → '[TABLE_001]')
        ref_tag = f"[{asset_type}_{seq_in_doc:03d}]"

        # bbox가 None이면 각 좌표도 None으로 설정, 있으면 float으로 변환하여 저장
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
        """
        특정 문서에 속한 모든 자산을 삭제한다.

        문서를 재처리(재파싱)할 때 기존 자산을 모두 지우고 새로 저장할 때 사용한다.

        매개변수:
            document_id: 자산을 삭제할 문서의 ID

        반환값:
            실제로 삭제된 레코드 수
        """
        with self._conn() as conn:
            cur = conn.execute(
                "DELETE FROM document_assets WHERE document_id=?", (document_id,)
            )
            n = cur.rowcount  # 삭제된 행의 수
        if n:
            logger.debug("자산 삭제: doc_id=%d (%d건)", document_id, n)
        return n

    def delete_all_assets(self) -> int:
        """
        데이터베이스의 모든 자산을 삭제한다.

        전체 초기화가 필요할 때 사용한다.

        반환값:
            실제로 삭제된 레코드 수
        """
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM document_assets")
            return cur.rowcount

    # ------------------------------------------------------------------
    # 읽기
    # ------------------------------------------------------------------
    def get_assets_for_document(self, document_id: int) -> list:
        """
        특정 문서에 속한 모든 자산의 메타데이터를 페이지 순서대로 반환한다.

        이미지 바이너리(image_data)는 포함하지 않으므로 목록 조회에 적합하다.

        매개변수:
            document_id: 조회할 문서의 ID

        반환값:
            자산 메타데이터 Row 객체의 리스트 (페이지 번호, 순서 기준 정렬)
        """
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
        """
        자산 ID로 해당 자산의 이미지 바이너리 데이터를 반환한다.

        매개변수:
            asset_id: 조회할 자산의 고유 ID (document_assets.id)

        반환값:
            이미지 bytes 데이터, 해당 ID가 없으면 None
        """
        with self._conn() as conn:
            row = conn.execute(
                "SELECT image_data FROM document_assets WHERE id=?", (asset_id,)
            ).fetchone()
            # row가 존재하면 BLOB 데이터를 bytes로 변환하여 반환, 없으면 None
            return bytes(row["image_data"]) if row else None

    def get_page_assets(self, document_id: int, page_num: int) -> list:
        """
        특정 문서의 특정 페이지에 있는 모든 자산을 반환한다.

        PDF 페이지 렌더링 시 해당 페이지의 표·그림을 불러올 때 사용한다.
        이미지 바이너리(image_data)도 포함된다.

        매개변수:
            document_id: 조회할 문서의 ID
            page_num:    조회할 페이지 번호

        반환값:
            해당 페이지의 자산 Row 객체 리스트 (seq_in_doc 기준 정렬)
        """
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
        """
        특정 문서의 자산 유형별 개수를 반환한다.

        매개변수:
            document_id: 개수를 셀 문서의 ID

        반환값:
            자산 유형을 키로 하는 딕셔너리 (예: {'TABLE': 5, 'FIGURE': 3})
        """
        with self._conn() as conn:
            rows = conn.execute(
                """
                SELECT asset_type, COUNT(*) as cnt
                FROM document_assets WHERE document_id=?
                GROUP BY asset_type
                """,
                (document_id,),
            ).fetchall()
            # Row 객체 리스트를 {유형: 개수} 딕셔너리로 변환
            return {r["asset_type"]: r["cnt"] for r in rows}

    def count_all_assets(self) -> dict:
        """
        전체 자산 건수를 문서 ID별로 단일 쿼리로 반환.

        모든 문서의 통계를 한 번의 DB 접근으로 가져오므로 효율적이다.

        반환값:
            중첩 딕셔너리 형태 — {document_id: {"TABLE": n, "FIGURE": n}}
            예: {1: {"TABLE": 5, "FIGURE": 2}, 2: {"TABLE": 0, "FIGURE": 8}}
        """
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT document_id, asset_type, COUNT(*) as cnt "
                "FROM document_assets GROUP BY document_id, asset_type"
            ).fetchall()

        result: dict = {}
        for r in rows:
            doc_id = r["document_id"]
            # 해당 document_id 키가 없으면 빈 딕셔너리로 초기화
            if doc_id not in result:
                result[doc_id] = {}
            result[doc_id][r["asset_type"]] = r["cnt"]
        return result


# 모듈 로드 시 기본 경로(data/assets.db)로 전역 인스턴스를 생성
# 애플리케이션 전반에서 이 인스턴스를 공유하여 사용함 (싱글턴 패턴)
asset_db = AssetDatabase()
