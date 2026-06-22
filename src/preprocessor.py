# -*- coding: utf-8 -*-
"""
Preprocessor for OCR / extracted text cleanup and readiness checks.
"""

import logging
import re
from dataclasses import dataclass

from src.config import config
from src.database import Status

logger = logging.getLogger(__name__)

_CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]")
_HYPHEN_RE = re.compile(r"(\w)-\n(\w)")
_SPACES_RE = re.compile(r" {2,}")
_NEWLINES_RE = re.compile(r"\n{4,}")
_NOISE_LINE_RE = re.compile(r"^[^\w\uAC00-\uD7A3\u3040-\u30FF]{1,3}$")
_LIST_ITEM_RE = re.compile(
    r"^(?:[-*–—•·]|\(?\d{1,3}[.)]\)?|[A-Za-z][.)]|[ivxlcdm]+[.)])\s+\S",
    re.IGNORECASE,
)
# TOC/목차 항목: "1 Scope 1", "3.1 Terms 2", "Annex A (normative) ... 44"
_TOC_ENTRY_RE = re.compile(r"^(?:\d[\d.]*|[Aa]nnex\s+[A-Z])\s+\S")
_CAPTION_RE = re.compile(
    r"^(?:figure|fig\.?|table|appendix|algorithm|chart|equation)\s*\d+(?:[-\.]\d+)*(?![.-]\d)\s*[.:–—]",
    re.IGNORECASE,
)


def _looks_like_list_item(line: str) -> bool:
    return bool(_LIST_ITEM_RE.match(line.strip()))


def _looks_like_caption(line: str) -> bool:
    return bool(_CAPTION_RE.match(line.strip()))


def _looks_like_short_heading(line: str, next_line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if _looks_like_list_item(stripped) or _looks_like_caption(stripped):
        return False
    if len(stripped) > 60 or len(stripped.split()) > 10:
        return False
    if stripped[-1] in ".?!,;":
        return False

    next_stripped = next_line.strip()
    return len(next_stripped) >= max(len(stripped) + 10, 32)


def _should_keep_linebreak(previous_line: str, next_line: str) -> bool:
    prev = previous_line.strip()
    curr = next_line.strip()
    if not prev or not curr:
        return True
    if _looks_like_list_item(prev) or _looks_like_list_item(curr):
        return True
    if _looks_like_caption(prev) or _looks_like_caption(curr):
        return True
    if _looks_like_short_heading(prev, curr):
        return True
    if prev.endswith(":") and (_looks_like_list_item(curr) or len(curr) <= 32):
        return True
    if _TOC_ENTRY_RE.match(prev) and _TOC_ENTRY_RE.match(curr):
        return True
    return False


def _should_merge_soft_wrap(previous_line: str, next_line: str) -> bool:
    prev = previous_line.strip()
    curr = next_line.strip()
    if not prev or not curr:
        return False
    if prev.endswith("-"):
        return True
    if prev.endswith((".", "!", "?", '."', '!"', '?"', ".'", "!'","?'")):
        if curr[:1].isupper() or curr[:1].isdigit():
            return False
    if curr[:1].islower():
        return True
    if curr.startswith(("(", "[", '"', "'", "%", "/", "±")):
        return True
    if len(prev) >= 45:
        return True
    if len(prev.split()) >= 6 and len(curr.split()) >= 3:
        return True
    return False


def normalize_hard_linebreaks(text: str) -> str:
    """Keep explicit line breaks and merge display-only wraps."""
    if not text:
        return ""

    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    parts = re.split(r"(\n{2,})", text)
    normalized_parts: list[str] = []

    for part in parts:
        if not part:
            continue
        if set(part) == {"\n"}:
            normalized_parts.append("\n\n\n" if len(part) >= 3 else "\n\n")
            continue

        logical_lines: list[str] = []
        current = ""

        for raw_line in part.split("\n"):
            line = _SPACES_RE.sub(" ", raw_line).strip()
            if not line or _NOISE_LINE_RE.match(line):
                continue

            if not current:
                current = line
                continue

            if _should_keep_linebreak(current, line):
                logical_lines.append(current)
                current = line
                continue

            if not _should_merge_soft_wrap(current, line):
                logical_lines.append(current)
                current = line
                continue

            if current.endswith("-"):
                current = current[:-1] + line
            else:
                current = f"{current} {line}"

        if current:
            logical_lines.append(current)

        if logical_lines:
            normalized_parts.append("\n".join(logical_lines))

    return "".join(normalized_parts)


@dataclass
class PreprocessResult:
    text: str
    status: str
    reason: str = ""
    char_count: int = 0
    line_count: int = 0

    def is_ready(self) -> bool:
        return self.status == Status.TEXT_READY


class Preprocessor:
    def process(self, text: str, ocr_quality_score: float = 1.0) -> PreprocessResult:
        """Clean text and determine readiness status."""
        cleaned = self._clean(text)

        char_count = len(re.sub(r"\s", "", cleaned))
        line_count = sum(1 for ln in cleaned.splitlines() if ln.strip())

        if char_count < config.ocr_min_text_length:
            reason = (
                f"텍스트 길이 부족: {char_count}자 "
                f"(최소 {config.ocr_min_text_length}자 필요)"
            )
            logger.warning("REVIEW_REQUIRED - %s", reason)
            return PreprocessResult(
                text=cleaned,
                status=Status.REVIEW_REQUIRED,
                reason=reason,
                char_count=char_count,
                line_count=line_count,
            )

        if ocr_quality_score < config.ocr_quality_threshold:
            reason = (
                f"OCR 품질 점수 미달: {ocr_quality_score:.3f} "
                f"(임계값 {config.ocr_quality_threshold})"
            )
            logger.warning("REVIEW_REQUIRED - %s", reason)
            return PreprocessResult(
                text=cleaned,
                status=Status.REVIEW_REQUIRED,
                reason=reason,
                char_count=char_count,
                line_count=line_count,
            )

        logger.debug("TEXT_READY - %d chars, %d logical lines", char_count, line_count)
        return PreprocessResult(
            text=cleaned,
            status=Status.TEXT_READY,
            char_count=char_count,
            line_count=line_count,
        )

    def _clean(self, text: str) -> str:
        if not text:
            return ""

        text = _CTRL_RE.sub("", text)
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = (
            text.replace("\u3000", " ")
            .replace("\u00A0", " ")
            .replace("\uFEFF", "")
        )
        text = text.replace("\t", " ")
        text = _HYPHEN_RE.sub(r"\1\2", text)
        text = normalize_hard_linebreaks(text)
        text = _NEWLINES_RE.sub("\n\n\n", text)
        return text.strip()


preprocessor = Preprocessor()
