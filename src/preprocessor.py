# -*- coding: utf-8 -*-
"""
OCR 또는 추출된 텍스트를 정리하고 처리 준비 상태를 확인하는 전처리 모듈.
"""

import logging
import re
from dataclasses import dataclass

from src.config import config
from src.database import Status

# 이 모듈의 로그를 기록하는 로거 객체 생성
logger = logging.getLogger(__name__)

# 제어 문자(출력 불가능한 특수 문자)를 감지하는 정규식
# \x00-\x08: NULL 등 초기 제어 문자, \x0B\x0C: 수직 탭·폼피드
# \x0E-\x1F: 기타 제어 문자, \x7F-\x9F: DEL 및 확장 제어 문자
_CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F-\x9F]")

# 줄 끝에서 하이픈으로 단어가 나뉜 경우를 감지하는 정규식
# 예: "exam-\nple" → "example" 로 이어 붙이기 위해 사용
_HYPHEN_RE = re.compile(r"(\w)-\n(\w)")

# 연속된 공백 두 개 이상을 감지하는 정규식 (하나의 공백으로 줄이기 위해 사용)
_SPACES_RE = re.compile(r" {2,}")

# 연속된 빈 줄이 4개 이상인 경우를 감지하는 정규식 (최대 3줄로 제한)
_NEWLINES_RE = re.compile(r"\n{4,}")

# 의미 없는 짧은 줄(소음 줄)을 감지하는 정규식
# 한글(가-힣), 일본어(぀-ヿ), 영숫자(\w)가 전혀 없고 길이가 1~3인 줄은 제거 대상
_NOISE_LINE_RE = re.compile(r"^[^\w가-힣぀-ヿ]{1,3}$")

# 목록 항목을 감지하는 정규식
# 예: "- 항목", "* 항목", "1. 항목", "a) 항목", "iv. 항목" 등 다양한 형식 지원
_LIST_ITEM_RE = re.compile(
    r"^(?:[-*–—•·]|\(?\d{1,3}[.)]\)?|[A-Za-z][.)]|[ivxlcdm]+[.)])\s+\S",
    re.IGNORECASE,
)

# 목차(TOC) 항목을 감지하는 정규식
# 예: "1 Scope 1", "3.1 Terms 2", "Annex A (normative) ... 44"
_TOC_ENTRY_RE = re.compile(r"^(?:\d[\d.]*|[Aa]nnex\s+[A-Z])\s+\S")

# 그림, 표, 부록 등의 캡션(설명문)을 감지하는 정규식
# 예: "Figure 1:", "Table 3.2 —", "Appendix A." 등
_CAPTION_RE = re.compile(
    r"^(?:figure|fig\.?|table|appendix|algorithm|chart|equation)\s*\d+(?:[-\.]\d+)*(?![.-]\d)\s*[.:–—]",
    re.IGNORECASE,
)


def _looks_like_list_item(line: str) -> bool:
    """
    주어진 줄이 목록 항목처럼 보이는지 판단한다.

    매개변수:
        line: 검사할 텍스트 줄

    반환값:
        목록 항목 형식이면 True, 아니면 False
    """
    # 앞뒤 공백을 제거한 뒤 목록 항목 정규식과 일치하는지 확인
    return bool(_LIST_ITEM_RE.match(line.strip()))


def _looks_like_caption(line: str) -> bool:
    """
    주어진 줄이 그림·표 등의 캡션처럼 보이는지 판단한다.

    매개변수:
        line: 검사할 텍스트 줄

    반환값:
        캡션 형식이면 True, 아니면 False
    """
    # 앞뒤 공백을 제거한 뒤 캡션 정규식과 일치하는지 확인
    return bool(_CAPTION_RE.match(line.strip()))


def _looks_like_short_heading(line: str, next_line: str) -> bool:
    """
    현재 줄이 짧은 제목(heading)처럼 보이는지 판단한다.

    제목으로 판단하는 기준:
    - 내용이 비어 있지 않아야 함
    - 목록 항목이나 캡션이 아니어야 함
    - 길이가 60자 이하이고 단어 수가 10개 이하여야 함
    - 문장 부호(.?!,;)로 끝나지 않아야 함
    - 다음 줄이 충분히 길어야 함 (현재 줄 길이 + 10 또는 최소 32자 이상)

    매개변수:
        line: 검사할 현재 줄
        next_line: 현재 줄 다음에 오는 줄

    반환값:
        짧은 제목처럼 보이면 True, 아니면 False
    """
    stripped = line.strip()

    # 빈 줄이면 제목이 아님
    if not stripped:
        return False

    # 목록 항목이나 캡션이면 제목으로 보지 않음
    if _looks_like_list_item(stripped) or _looks_like_caption(stripped):
        return False

    # 너무 길거나 단어 수가 많으면 제목이 아님
    if len(stripped) > 60 or len(stripped.split()) > 10:
        return False

    # 문장 종결 부호로 끝나면 일반 문장으로 간주
    if stripped[-1] in ".?!,;":
        return False

    # 다음 줄이 현재 줄보다 충분히 길어야 제목으로 인정
    # (제목 다음에는 본문이 이어지기 때문에 본문이 더 길어야 함)
    next_stripped = next_line.strip()
    return len(next_stripped) >= max(len(stripped) + 10, 32)


def _should_keep_linebreak(previous_line: str, next_line: str) -> bool:
    """
    두 줄 사이의 줄바꿈을 유지해야 하는지 판단한다.

    다음 중 하나라도 해당하면 줄바꿈을 유지한다:
    - 어느 한 줄이 비어 있을 때
    - 어느 한 줄이 목록 항목일 때
    - 어느 한 줄이 캡션일 때
    - 이전 줄이 짧은 제목처럼 보일 때
    - 이전 줄이 콜론(:)으로 끝나고 다음 줄이 목록이거나 짧을 때
    - 두 줄 모두 목차 항목일 때

    매개변수:
        previous_line: 앞 줄
        next_line: 뒷 줄

    반환값:
        줄바꿈을 유지해야 하면 True, 이어 붙여도 되면 False
    """
    prev = previous_line.strip()
    curr = next_line.strip()

    # 빈 줄이 있으면 줄바꿈 유지
    if not prev or not curr:
        return True

    # 어느 한 줄이라도 목록 항목이면 줄바꿈 유지
    if _looks_like_list_item(prev) or _looks_like_list_item(curr):
        return True

    # 어느 한 줄이라도 캡션이면 줄바꿈 유지
    if _looks_like_caption(prev) or _looks_like_caption(curr):
        return True

    # 앞 줄이 짧은 제목처럼 보이면 줄바꿈 유지 (제목과 본문을 분리)
    if _looks_like_short_heading(prev, curr):
        return True

    # 앞 줄이 콜론으로 끝나고, 뒷 줄이 목록이거나 짧으면 줄바꿈 유지
    if prev.endswith(":") and (_looks_like_list_item(curr) or len(curr) <= 32):
        return True

    # 두 줄 모두 목차 항목이면 줄바꿈 유지 (목차는 줄마다 독립적)
    if _TOC_ENTRY_RE.match(prev) and _TOC_ENTRY_RE.match(curr):
        return True

    return False


def _should_merge_soft_wrap(previous_line: str, next_line: str) -> bool:
    """
    PDF나 OCR에서 생긴 디스플레이용 줄바꿈(소프트 랩)을 하나의 문장으로 이어 붙여야 하는지 판단한다.

    다음 중 하나라도 해당하면 이어 붙인다:
    - 앞 줄이 하이픈(-)으로 끝날 때 (단어가 줄에서 잘린 경우)
    - 뒷 줄이 소문자로 시작할 때 (문장이 이어지는 경우)
    - 뒷 줄이 특정 기호((, [, ", ', %, /, ±)로 시작할 때
    - 앞 줄이 45자 이상으로 충분히 길 때 (줄 너비 제한으로 잘린 것으로 추정)
    - 앞 줄이 6단어 이상이고 뒷 줄이 3단어 이상일 때 (본문 문장으로 추정)

    매개변수:
        previous_line: 앞 줄
        next_line: 뒷 줄

    반환값:
        이어 붙여야 하면 True, 별도 줄로 유지해야 하면 False
    """
    prev = previous_line.strip()
    curr = next_line.strip()

    # 빈 줄이 있으면 이어 붙이지 않음
    if not prev or not curr:
        return False

    # 앞 줄이 하이픈으로 끝나면 단어가 잘린 것이므로 이어 붙임
    if prev.endswith("-"):
        return True

    # 앞 줄이 문장 종결 부호로 끝나고 뒷 줄이 대문자나 숫자로 시작하면
    # 새로운 문장이 시작된 것으로 보아 이어 붙이지 않음
    if prev.endswith((".", "!", "?", '."', '!"', '?"', ".'", "!'", "?'")):
        if curr[:1].isupper() or curr[:1].isdigit():
            return False

    # 뒷 줄이 소문자로 시작하면 문장이 이어지는 것으로 판단
    if curr[:1].islower():
        return True

    # 뒷 줄이 괄호, 따옴표, 특수 기호로 시작하면 이어지는 내용으로 판단
    if curr.startswith(("(", "[", '"', "'", "%", "/", "±")):
        return True

    # 앞 줄이 45자 이상이면 줄 너비 한계로 잘린 것으로 추정하여 이어 붙임
    if len(prev) >= 45:
        return True

    # 앞 줄이 6단어 이상이고 뒷 줄이 3단어 이상이면 본문 문장이 이어지는 것으로 판단
    if len(prev.split()) >= 6 and len(curr.split()) >= 3:
        return True

    return False


def normalize_hard_linebreaks(text: str) -> str:
    """
    명시적인 단락 구분은 유지하면서, 화면 표시를 위해 삽입된 줄바꿈은 이어 붙여 정규화한다.

    PDF나 OCR로 추출한 텍스트에는 실제 단락 구분과 단순히 줄이 넘쳐서 생긴
    줄바꿈이 섞여 있다. 이 함수는 두 가지를 구별하여 의미 있는 구조를 복원한다.

    매개변수:
        text: 정규화할 원본 텍스트

    반환값:
        줄바꿈이 정규화된 텍스트. 빈 문자열이 입력되면 빈 문자열을 반환.
    """
    if not text:
        return ""

    # 다양한 줄바꿈 형식(\r\n, \r)을 \n으로 통일하고 앞뒤 공백 제거
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return ""

    # 두 개 이상의 연속 빈 줄을 기준으로 단락(블록)을 분리
    # 각 블록 내에서 소프트 랩(디스플레이용 줄바꿈)을 처리
    parts = re.split(r"(\n{2,})", text)
    normalized_parts: list[str] = []

    for part in parts:
        if not part:
            continue

        # 구분자(빈 줄 연속)이면 길이에 따라 2개 또는 3개의 줄바꿈으로 정규화
        if set(part) == {"\n"}:
            # 3개 이상이면 3개로, 2개이면 2개 그대로 유지
            normalized_parts.append("\n\n\n" if len(part) >= 3 else "\n\n")
            continue

        # 단락 내 각 줄을 처리하여 논리적인 줄 목록을 만듦
        logical_lines: list[str] = []
        current = ""  # 현재 처리 중인 줄(여러 줄이 이어질 수 있음)

        for raw_line in part.split("\n"):
            # 연속 공백을 하나로 줄이고 앞뒤 공백 제거
            line = _SPACES_RE.sub(" ", raw_line).strip()

            # 빈 줄이거나 의미 없는 소음 줄이면 건너뜀
            if not line or _NOISE_LINE_RE.match(line):
                continue

            # 아직 처리 중인 줄이 없으면 현재 줄을 시작점으로 설정
            if not current:
                current = line
                continue

            # 줄바꿈을 유지해야 하는 경우: 현재까지의 줄을 저장하고 새 줄 시작
            if _should_keep_linebreak(current, line):
                logical_lines.append(current)
                current = line
                continue

            # 이어 붙이지 않아야 하는 경우: 마찬가지로 줄을 저장하고 새 줄 시작
            if not _should_merge_soft_wrap(current, line):
                logical_lines.append(current)
                current = line
                continue

            # 소프트 랩으로 판단된 경우: 두 줄을 이어 붙임
            if current.endswith("-"):
                # 하이픈으로 잘린 단어는 하이픈을 제거하고 이어 붙임
                # 예: "exam-" + "ple" → "example"
                current = current[:-1] + line
            else:
                # 일반적인 경우: 공백을 사이에 넣고 이어 붙임
                current = f"{current} {line}"

        # 마지막으로 처리 중이던 줄을 목록에 추가
        if current:
            logical_lines.append(current)

        # 처리된 논리 줄들을 \n으로 연결하여 결과에 추가
        if logical_lines:
            normalized_parts.append("\n".join(logical_lines))

    # 모든 부분을 이어 붙여 최종 결과 반환
    return "".join(normalized_parts)


@dataclass
class PreprocessResult:
    """
    전처리 결과를 담는 데이터 클래스.

    텍스트 처리 후의 정제된 내용과 처리 상태(준비 완료 또는 검토 필요)를
    함께 보관한다.

    속성:
        text: 정제된 텍스트 내용
        status: 처리 상태 코드 (예: Status.TEXT_READY, Status.REVIEW_REQUIRED)
        reason: 검토가 필요한 경우 그 이유 설명 (기본값: 빈 문자열)
        char_count: 공백을 제외한 문자 수
        line_count: 내용이 있는 줄의 수
    """

    text: str
    status: str
    reason: str = ""
    char_count: int = 0
    line_count: int = 0

    def is_ready(self) -> bool:
        """
        텍스트가 다음 처리 단계(예: 임베딩, 색인)로 넘어갈 준비가 되었는지 확인한다.

        반환값:
            상태가 TEXT_READY이면 True, 그렇지 않으면 False
        """
        return self.status == Status.TEXT_READY


class Preprocessor:
    """
    OCR 또는 파일에서 추출된 텍스트를 정제하고 품질을 검증하는 전처리기.

    텍스트를 정리한 뒤 글자 수와 OCR 품질 점수를 기준으로
    'TEXT_READY' 또는 'REVIEW_REQUIRED' 상태를 결정한다.
    """

    def process(self, text: str, ocr_quality_score: float = 1.0) -> PreprocessResult:
        """
        텍스트를 정제하고 처리 준비 상태를 결정한다.

        텍스트 정제 후 두 가지 조건을 검사한다:
        1. 정제된 텍스트의 글자 수가 최솟값(config.ocr_min_text_length) 이상인지
        2. OCR 품질 점수가 임계값(config.ocr_quality_threshold) 이상인지

        어느 하나라도 미달이면 REVIEW_REQUIRED 상태로 반환한다.

        매개변수:
            text: 원본 텍스트 (OCR 결과 또는 파일에서 추출한 텍스트)
            ocr_quality_score: OCR 신뢰도 점수 (0.0 ~ 1.0, 기본값 1.0은 최고 품질)

        반환값:
            PreprocessResult: 정제된 텍스트, 상태, 통계 정보를 담은 결과 객체
        """
        # 텍스트 정제 수행
        cleaned = self._clean(text)

        # 공백을 제외한 실제 글자 수 계산 (품질 판단 기준)
        char_count = len(re.sub(r"\s", "", cleaned))
        # 내용이 있는 줄의 수 계산
        line_count = sum(1 for ln in cleaned.splitlines() if ln.strip())

        # 글자 수가 최솟값 미달이면 검토 필요 상태로 반환
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

        # OCR 품질 점수가 임계값 미달이면 검토 필요 상태로 반환
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

        # 두 조건 모두 통과하면 처리 준비 완료 상태로 반환
        logger.debug("TEXT_READY - %d chars, %d logical lines", char_count, line_count)
        return PreprocessResult(
            text=cleaned,
            status=Status.TEXT_READY,
            char_count=char_count,
            line_count=line_count,
        )

    def _clean(self, text: str) -> str:
        """
        텍스트에서 불필요한 문자와 형식을 제거하여 정제된 텍스트를 반환한다.

        정제 단계:
        1. 제어 문자 제거 (출력 불가능한 특수 문자)
        2. 줄바꿈 형식 통일 (\r\n, \r → \n)
        3. 유니코드 공백 문자 정규화 (전각 공백, 줄 바꿈 없는 공백 등)
        4. 탭을 공백으로 변환
        5. 줄 끝 하이픈으로 나뉜 단어 복원
        6. 소프트 랩(디스플레이용 줄바꿈) 정규화
        7. 과도한 빈 줄 압축 (4개 이상 → 3개)

        매개변수:
            text: 정제할 원본 텍스트

        반환값:
            정제된 텍스트. 빈 문자열이 입력되면 빈 문자열을 반환.
        """
        if not text:
            return ""

        # 1단계: 제어 문자 제거 (화면에 표시되지 않는 특수 문자 삭제)
        text = _CTRL_RE.sub("", text)

        # 2단계: 줄바꿈 형식 통일 (Windows의 \r\n, 구형 Mac의 \r → 유닉스 스타일 \n)
        text = text.replace("\r\n", "\n").replace("\r", "\n")

        # 3단계: 유니코드 공백 문자 정규화
        # 　: 전각 공백(일본어 등에서 사용),  : 줄 바꿈 없는 공백(HTML &nbsp;)
        # ﻿: 바이트 순서 표시(BOM, 파일 앞에 붙는 보이지 않는 문자)
        text = (
            text.replace("　", " ")
            .replace(" ", " ")
            .replace("﻿", "")
        )

        # 4단계: 탭 문자를 일반 공백으로 변환 (정렬 일관성 유지)
        text = text.replace("\t", " ")

        # 5단계: 줄 끝 하이픈으로 나뉜 단어 복원
        # 예: "exam-\nple" → "example"
        text = _HYPHEN_RE.sub(r"\1\2", text)

        # 6단계: 소프트 랩 정규화 (디스플레이 목적의 줄바꿈을 의미 단위로 재구성)
        text = normalize_hard_linebreaks(text)

        # 7단계: 연속된 빈 줄을 최대 3개로 압축 (단락 구분은 유지하되 과도한 여백 제거)
        text = _NEWLINES_RE.sub("\n\n\n", text)

        return text.strip()


# 모듈 전역에서 사용할 수 있는 Preprocessor 싱글턴 인스턴스
# 다른 모듈에서 `from src.preprocessor import preprocessor` 로 바로 가져다 쓸 수 있음
preprocessor = Preprocessor()
