#!/usr/bin/env python3
"""
CHANGELOG 동기화 + 소스코드 자동 commit/push 훅 — Claude Code Stop 이벤트에서 자동 실행.

app.py의 APP_VERSION이 CHANGELOG.md 두 테이블 모두에 기록되어 있는지 확인:
  1. 개정이력표      (| 날짜 | 시간 | 버전 | 변경 내용 | 요청자 | 작성자 |)
  2. 버전별 주요 변경 요약 (| 버전 | 날짜 | 시간(KST) | 주요 내용 |)

누락된 경우 플레이스홀더 행을 삽입한다.
작업 종료 시 변경된 tracked 파일 전체를 자동 commit·push한다.
"""
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path


# ── 프로젝트 루트: 이 스크립트는 <root>/scripts/ 에 위치 ─────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_PY       = PROJECT_ROOT / "app.py"
CHANGELOG    = PROJECT_ROOT / "CHANGELOG.md"


def get_app_version() -> str | None:
    """app.py에서 APP_VERSION 추출."""
    try:
        m = re.search(
            r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']',
            APP_PY.read_text(encoding="utf-8"),
            re.MULTILINE,
        )
        return m.group(1) if m else None
    except Exception:
        return None


def find_last_table_row(lines: list[str], header_marker: str) -> tuple[int, int]:
    """
    header_marker 문자열이 포함된 헤더 행 위치와
    그 테이블의 마지막 데이터 행 위치를 반환.
    헤더를 찾지 못하면 (-1, -1).
    """
    header_idx = -1
    for i, line in enumerate(lines):
        if header_marker in line:
            header_idx = i
            break
    if header_idx == -1:
        return -1, -1

    last_row_idx = header_idx
    for i in range(header_idx + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("|"):
            last_row_idx = i
        elif stripped == "" or stripped.startswith("#") or stripped == "---":
            break
    return header_idx, last_row_idx


def version_in_table(lines: list[str], header_idx: int, ver_tag: str) -> bool:
    """header_idx 이후 테이블 행에 ver_tag 가 포함되어 있는지 확인."""
    if header_idx == -1:
        return False
    for i in range(header_idx + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith("|"):
            if ver_tag in lines[i]:
                return True
        elif stripped and not stripped.startswith("|"):
            break
    return False


def main() -> None:
    # stdin JSON 읽기 (Claude Code 훅 페이로드 — 없어도 무방)
    try:
        json.loads(sys.stdin.read())
    except Exception:
        pass

    if not APP_PY.exists() or not CHANGELOG.exists():
        sys.exit(0)

    version = get_app_version()
    if not version:
        sys.exit(0)

    ver_tag = f"v{version}"
    today   = datetime.now().strftime("%Y%m%d")

    lines    = CHANGELOG.read_text(encoding="utf-8").splitlines(keepends=True)
    modified = False
    warnings = []

    # ── 1. 개정이력표 확인 ─────────────────────────────────────────────────
    REV_HEADER = "| 날짜 | 시간 | 버전 | 변경 내용 | 요청자 | 작성자 |"
    h_idx, last_idx = find_last_table_row(lines, REV_HEADER)

    if h_idx != -1 and not version_in_table(lines, h_idx, ver_tag):
        placeholder = (
            f"| {today} |       | {ver_tag} | "
            f"⚠️ [CHANGELOG 미작성 — Claude가 다음 응답에서 채워야 함] | kslee | claude |\n"
        )
        lines.insert(last_idx + 1, placeholder)
        warnings.append(f"⚠️  개정이력표에 {ver_tag} 항목 누락 → 플레이스홀더 추가")
        modified = True

    # ── 2. 버전별 주요 변경 요약 확인 ──────────────────────────────────────
    SUM_HEADER = "| 버전 | 날짜 | 시간(KST) | 주요 내용 |"
    h_idx2, last_idx2 = find_last_table_row(lines, SUM_HEADER)

    if h_idx2 != -1 and not version_in_table(lines, h_idx2, ver_tag):
        placeholder2 = (
            f"| {ver_tag} | {today} |               | "
            f"⚠️ [요약 미작성 — Claude가 다음 응답에서 채워야 함] |\n"
        )
        lines.insert(last_idx2 + 1, placeholder2)
        warnings.append(f"⚠️  버전별 요약에 {ver_tag} 항목 누락 → 플레이스홀더 추가")
        modified = True

    if modified:
        CHANGELOG.write_text("".join(lines), encoding="utf-8")
        for w in warnings:
            print(w, file=sys.stderr)

    _auto_commit_and_push(ver_tag, changelog_updated=modified)
    sys.exit(0)


def _auto_commit_and_push(ver_tag: str, changelog_updated: bool) -> None:
    """변경된 tracked 파일 전체를 자동 커밋·푸시."""
    try:
        # 수정된 tracked 파일만 스테이징 (untracked 새 파일은 제외 — 민감 파일 보호)
        subprocess.run(
            ["git", "add", "-u"],
            cwd=PROJECT_ROOT, check=True, capture_output=True,
        )

        # 스테이징된 파일 목록 확인
        result = subprocess.run(
            ["git", "diff", "--cached", "--name-only"],
            cwd=PROJECT_ROOT, check=True, capture_output=True, text=True,
        )
        staged_files = [f.strip() for f in result.stdout.splitlines() if f.strip()]

        if not staged_files:
            return  # 변경 없음

        # 커밋 메시지 생성
        only_changelog = staged_files == ["CHANGELOG.md"]
        if only_changelog:
            msg = f"chore: auto-update CHANGELOG for {ver_tag}"
        else:
            non_changelog = [f for f in staged_files if f != "CHANGELOG.md"]
            file_summary = ", ".join(non_changelog[:3])
            if len(non_changelog) > 3:
                file_summary += f" 외 {len(non_changelog) - 3}개"
            suffix = " + CHANGELOG" if changelog_updated else ""
            msg = f"chore: auto-sync {ver_tag} — {file_summary}{suffix}"

        subprocess.run(
            ["git", "commit", "-m", msg],
            cwd=PROJECT_ROOT, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "push"],
            cwd=PROJECT_ROOT, check=True, capture_output=True,
        )
        print(f"✅ auto-push 완료: {msg}", file=sys.stderr)

    except subprocess.CalledProcessError as e:
        print(f"⚠️  git push 실패: {e.stderr.decode(errors='replace').strip()}", file=sys.stderr)


if __name__ == "__main__":
    main()
