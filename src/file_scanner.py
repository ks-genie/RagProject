# -*- coding: utf-8 -*-
"""
File Scanner — 감시 디렉토리에서 PDF 파일을 탐지하고 DB에 등록

이 모듈은 지정된 폴더를 재귀적으로 탐색하여 PDF/DOCX/PPTX 파일을 찾고,
각 파일의 상태(신규/변경/중복)를 판별한 뒤 데이터베이스에 등록합니다.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path

from src.config import config
from src.database import DatabaseManager, Status, compute_file_hash

# 이 모듈 전용 로거를 생성합니다.
# __name__을 사용하면 로그 메시지에 모듈 경로가 자동으로 표시됩니다.
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 스캔 결과
# ---------------------------------------------------------------------------
@dataclass
class ScanResult:
    """
    파일 스캔 작업의 결과를 담는 데이터 클래스.

    스캔이 완료된 후 각 파일이 어떤 처리를 받았는지
    (신규 등록, 중복 스킵, 재처리 대상, 오류) 를 분류하여 보관합니다.
    """

    # field(default_factory=list)를 사용하는 이유:
    # 데이터클래스에서 리스트를 기본값으로 쓰면 모든 인스턴스가 같은 리스트를 공유하는
    # 버그가 발생합니다. default_factory는 인스턴스마다 새 리스트를 만들어 이를 방지합니다.
    registered: list[int] = field(default_factory=list)          # 신규 등록된 doc_id 목록
    skipped_duplicate: list[str] = field(default_factory=list)   # 동일 hash 중복 파일 경로 목록
    requeued: list[int] = field(default_factory=list)             # 파일 변경 감지 → NEW 재전환된 doc_id 목록
    errors: list[str] = field(default_factory=list)               # 처리 중 오류가 발생한 파일 경로 목록

    @property
    def total_found(self) -> int:
        """
        이번 스캔에서 탐지된 파일의 총 개수를 반환합니다.

        반환값:
            int: 신규 등록 + 중복 스킵 + 재처리 대상 + 오류 파일의 합계
        """
        # 모든 분류의 개수를 합산하여 전체 탐지 파일 수를 계산합니다.
        return len(self.registered) + len(self.skipped_duplicate) + len(self.requeued) + len(self.errors)

    def summary(self) -> str:
        """
        스캔 결과를 사람이 읽기 좋은 한 줄 요약 문자열로 반환합니다.

        반환값:
            str: 탐지 건수, 신규 등록, 재처리 대상, 중복 스킵, 오류 건수를 포함한 요약 문자열
        """
        return (
            f"탐지: {self.total_found}건 | "
            f"신규 등록: {len(self.registered)}건 | "
            f"재처리 대상: {len(self.requeued)}건 | "
            f"중복 스킵: {len(self.skipped_duplicate)}건 | "
            f"오류: {len(self.errors)}건"
        )


# ---------------------------------------------------------------------------
# FileScanner
# ---------------------------------------------------------------------------
class FileScanner:
    """
    감시 디렉토리를 주기적으로 탐색하여 문서 파일을 DB에 등록하는 스캐너 클래스.

    지정된 디렉토리 내의 PDF, DOCX, PPTX 파일을 재귀적으로 탐색하고,
    각 파일의 해시값을 기준으로 신규/변경/중복 여부를 판별하여 DB에 반영합니다.
    """

    def __init__(self, watch_dir: Path | None = None, db: DatabaseManager | None = None):
        """
        FileScanner 인스턴스를 초기화합니다.

        매개변수:
            watch_dir (Path | None): 탐색할 디렉토리 경로.
                                     None이면 설정 파일(config)의 기본 경로를 사용합니다.
            db (DatabaseManager | None): 데이터베이스 관리 객체.
                                         None이면 새로운 DatabaseManager를 생성합니다.
        """
        # watch_dir가 주어지지 않으면 config에서 정의된 기본 감시 경로를 사용합니다.
        self.watch_dir = watch_dir or config.watch_dir
        # db가 주어지지 않으면 기본 설정으로 DatabaseManager를 새로 생성합니다.
        self.db = db or DatabaseManager()

    def scan(self) -> ScanResult:
        """
        감시 디렉토리를 재귀 탐색하여 PDF/DOCX/PPTX 파일을 처리합니다.

        디렉토리 내 모든 지원 파일을 찾아 각각 _process_file()로 처리하고,
        최종 결과를 ScanResult 객체에 담아 반환합니다.

        반환값:
            ScanResult: 신규 등록, 재처리, 중복 스킵, 오류 파일 정보를 담은 결과 객체
        """
        result = ScanResult()

        # 감시 디렉토리가 실제로 존재하지 않으면 경고를 남기고 빈 결과를 반환합니다.
        if not self.watch_dir.exists():
            logger.warning("감시 디렉토리가 존재하지 않습니다: %s", self.watch_dir)
            return result

        # 지원하는 파일 확장자를 순서대로 재귀 탐색하여 하나의 리스트에 모읍니다.
        # rglob()은 하위 폴더까지 모두 탐색하는 재귀(glob) 함수입니다.
        all_files: list[Path] = []
        for ext in ("*.pdf", "*.docx", "*.pptx"):
            all_files.extend(self.watch_dir.rglob(ext))

        # 파일 목록을 정렬하여 처리 순서를 일관되게 유지합니다.
        all_files.sort()
        logger.info("파일 탐지: %d건 (%s)", len(all_files), self.watch_dir)

        # 탐지된 파일을 하나씩 처리합니다.
        for file_path in all_files:
            self._process_file(file_path, result)

        logger.info(result.summary())
        return result

    def _process_file(self, pdf_path: Path, result: ScanResult) -> None:
        """
        단일 파일을 분석하여 신규 등록, 재처리, 중복 스킵 중 적절한 처리를 수행합니다.

        파일의 절대 경로와 해시값을 기준으로 아래 세 가지 케이스를 처리합니다:
          - 케이스 1: 같은 경로로 이미 등록된 파일 (변경 없음 또는 내용 변경)
          - 케이스 2: 다른 경로에 동일 해시로 이미 등록된 파일 (중복)
          - 케이스 3: 완전히 새로운 파일 (신규 등록)

        매개변수:
            pdf_path (Path): 처리할 파일의 Path 객체
            result (ScanResult): 처리 결과를 누적할 ScanResult 객체 (참조로 수정됨)
        """
        # 파일의 절대 경로 문자열과 파일명을 미리 추출합니다.
        file_path_str = str(pdf_path.resolve())
        file_name = pdf_path.name

        # 파일의 SHA-256 해시값을 계산합니다.
        # 해시는 파일 내용의 고유한 지문으로, 내용이 변경되면 해시값도 달라집니다.
        try:
            file_hash = compute_file_hash(file_path_str)
        except OSError as e:
            # 파일을 읽을 수 없는 경우(권한 문제, 파일 잠금 등) 오류로 분류하고 건너뜁니다.
            logger.error("해시 계산 실패 [%s]: %s", file_name, e)
            result.errors.append(file_path_str)
            return

        # DB에서 동일한 파일 경로로 이미 등록된 문서가 있는지 조회합니다.
        existing_by_path = self.db.get_document_by_path(file_path_str)

        # ── 케이스 1: 경로로 이미 등록된 파일 ──────────────────────────────
        if existing_by_path:
            if existing_by_path["file_hash"] == file_hash:
                # DB에 저장된 해시와 현재 해시가 같으면 파일 내용이 변경되지 않은 것입니다.
                # 이미 처리 중이거나 완료된 파일이므로 별도 처리 없이 스킵합니다.
                logger.debug("변경 없음, 스킵: %s", file_name)
                result.skipped_duplicate.append(file_path_str)
            else:
                # 해시가 다르면 파일 내용이 바뀐 것입니다.
                # DB의 해시를 새 해시로 갱신하고, 상태를 NEW로 되돌려 재처리 대상으로 만듭니다.
                doc_id = existing_by_path["id"]
                self.db.update_file_hash(doc_id, file_hash)
                self.db.log_step(doc_id, "SCAN", "SUCCESS", f"파일 변경 감지 → 재처리 대상 (hash: {file_hash[:8]})")
                logger.info("파일 변경 감지, 재처리 등록: %s", file_name)
                result.requeued.append(doc_id)
            return

        # ── 케이스 2: 동일 hash가 다른 경로로 이미 존재 ────────────────────
        # 경로는 다르지만 내용이 완전히 동일한 파일(복사본 등)은 중복으로 처리합니다.
        existing_by_hash = self.db.get_document_by_hash(file_hash)
        if existing_by_hash:
            logger.warning(
                "중복 파일 스킵 [%s] — 동일 hash가 이미 등록됨: %s",
                file_name,
                existing_by_hash["file_path"],
            )
            result.skipped_duplicate.append(file_path_str)
            return

        # ── 케이스 3: 신규 파일 ─────────────────────────────────────────────
        # DB에 경로/해시 기준으로 기존 레코드가 없으므로 새 문서로 등록합니다.
        doc_id = self.db.register_document(file_path_str, file_name, file_hash)
        if doc_id is not None:
            # 등록 성공 시 처리 이력 로그를 남기고 결과에 추가합니다.
            self.db.log_step(doc_id, "SCAN", "SUCCESS", f"신규 등록 (hash: {file_hash[:8]})")
            logger.info("신규 등록: %s (id=%d)", file_name, doc_id)
            result.registered.append(doc_id)
        else:
            # register_document가 None을 반환하는 경우는 두 프로세스가 동시에 같은 파일을
            # 등록하려 할 때(경합 조건) 발생할 수 있습니다. 오류로 분류합니다.
            logger.warning("등록 실패 (경합 추정): %s", file_name)
            result.errors.append(file_path_str)


# 모듈 레벨 싱글턴 인스턴스입니다.
# 외부에서 별도로 인스턴스를 생성하지 않고 이 객체를 바로 import하여 사용할 수 있습니다.
file_scanner = FileScanner()
