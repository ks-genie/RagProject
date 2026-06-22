# -*- coding: utf-8 -*-
"""Claude Code UserPromptSubmit hook: append prompts to prompt_history.md."""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


HIST_FILENAME = "prompt_history.md"
PROJECT_NAME = "RagProject"
KST = timezone(timedelta(hours=9))

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FALLBACK_HIST_PATH = PROJECT_ROOT / HIST_FILENAME

ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*(\d{8})\s*\|\s*(\d{2}:\d{2})\s*\|")
COUNT_RE = re.compile(r"^\*총\s+\d+개\s+항목\*")
SEPARATOR_RE = re.compile(r"^\|\s*-+")


def configure_stdio() -> None:
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name)
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")


def find_hist_path(cwd: Any) -> Path | None:
    candidates: list[Path] = []
    if isinstance(cwd, str) and cwd.strip():
        candidates.append(Path(cwd) / HIST_FILENAME)
    candidates.append(FALLBACK_HIST_PATH)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def find_last_row(lines: list[str]) -> tuple[int, int, datetime] | None:
    last: tuple[int, int, datetime] | None = None
    for idx, line in enumerate(lines):
        match = ROW_RE.match(line)
        if not match:
            continue

        no = int(match.group(1))
        started_at = datetime.strptime(
            f"{match.group(2)} {match.group(3)}", "%Y%m%d %H:%M"
        ).replace(tzinfo=KST)
        last = (idx, no, started_at)
    return last


def find_insert_index(lines: list[str], last_row_idx: int | None) -> int:
    if last_row_idx is not None:
        return last_row_idx + 1

    for idx, line in enumerate(lines):
        if SEPARATOR_RE.match(line):
            return idx + 1

    return len(lines)


def format_duration(delta: timedelta) -> str:
    total_seconds = max(0, int(delta.total_seconds()))
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60

    if hours:
        return f"{hours}시간 {minutes}분"
    return f"{minutes}분 {seconds:02d}초"


def update_duration_column(line: str, duration: str) -> str:
    parts = line.split("|")
    if len(parts) < 7:
        return line

    parts[4] = f" {duration} "
    return "|".join(parts)


def sanitize_prompt(prompt: str) -> str:
    return (
        prompt.strip()
        .replace("|", "｜")
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .replace("\n", " ↵ ")
    )


def update_count(lines: list[str], count: int) -> None:
    count_line = f"*총 {count}개 항목*"
    for idx, line in enumerate(lines):
        if COUNT_RE.match(line):
            lines[idx] = count_line
            return

    if lines and lines[-1].strip():
        lines.append("")
    lines.append(count_line)


def run() -> None:
    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    prompt = sanitize_prompt(str(data.get("prompt", "")))
    if not prompt:
        return

    hist_path = find_hist_path(data.get("cwd"))
    if hist_path is None:
        return

    now = datetime.now(tz=KST)
    date_str = now.strftime("%Y%m%d")
    time_str = now.strftime("%H:%M")

    lines = hist_path.read_text(encoding="utf-8").splitlines()
    last_row = find_last_row(lines)

    if last_row is None:
        no = 1
        insert_idx = find_insert_index(lines, None)
    else:
        last_row_idx, last_no, started_at = last_row
        no = last_no + 1
        insert_idx = find_insert_index(lines, last_row_idx)
        lines[last_row_idx] = update_duration_column(
            lines[last_row_idx], format_duration(now - started_at)
        )

    new_row = f"| {no} | {date_str} | {time_str} | — | {PROJECT_NAME} | {prompt} |"
    lines.insert(insert_idx, new_row)
    update_count(lines, no)

    hist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    try:
        configure_stdio()
        run()
    except Exception:
        # Hooks must never block prompt submission because history logging failed.
        return


if __name__ == "__main__":
    main()
