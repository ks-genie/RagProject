# -*- coding: utf-8 -*-
"""EasyOCR 한국어 인식 결과 후처리 — 반복 오인식 패턴 보정."""
import re

# 알려진 오인식 패턴 → 정답
# 긴 구 패턴을 앞에 두어 부분 치환 충돌 방지
_WORD_FIXES: list[tuple[str, str]] = [
    # ── 구(phrase) 단위 ──────────────────────────────
    ("끈 노이만",   "폰 노이만"),   # Von Neumann
    ("전락 기술",   "전략 기술"),   # 단독 '전락'(downfall)과 구분하기 위해 구 단위로만 교정
    # ── 외래어·기술 용어 ─────────────────────────────
    ("세그만트",    "세그먼트"),    # segment
    ("헬스레어",    "헬스케어"),    # healthcare (ㄹ 오인식)
    ("헬스웨어",    "헬스케어"),    # healthcare (ㅇ→ㅋ 오인식)
    ("스마트포",    "스마트폰"),    # smartphone (ㄴ 종성 누락)
    ("옛지",        "엣지"),        # edge (ㅅ·ㅅ 분리 오인식)
    ("가드너",      "가트너"),      # Gartner
    # ── 한국어 어휘 ─────────────────────────────────
    ("용용적",      "응용적"),      # 응용(application) — 초성 ㅇ·ㅇ 중복 오인식
    ("학신적",      "혁신적"),      # 혁신(innovation)
    ("학신올",      "혁신을"),      # 혁신 + 목적격 조사
    ("확기적",      "획기적"),      # 획기(epoch-making)
    ("파악하엿",    "파악하였"),    # 과거형 어미 (변형 전체 커버)
    ("활용월",      "활용될"),      # 활용될 — 월(month) 오인식
    ("소비률",      "소비를"),      # 소비를 — 률(rate) 오인식
]

# 받침 오인식: 종성 있는 한글 음절 뒤 '올'→'을', '름'→'를'
# lookahead: 공백·구두점·줄끝 위치에서만 교정 (단어 중간의 올/름은 제외)
_JOSA_RE = re.compile(r'([가-힣])(올|름)(?=[\s\.,;:!?\n\r]|$)')


def _josa_fix(m: re.Match) -> str:
    char, josa = m.group(1), m.group(2)
    has_jongseong = (ord(char) - 0xAC00) % 28 != 0
    if not has_jongseong:
        return m.group(0)  # 받침 없는 글자 뒤 → 변경 안 함
    return char + ("을" if josa == "올" else "를")


def fix_easyocr_korean(text: str) -> str:
    """EasyOCR 한국어 오인식 보정.

    1단계: 알려진 외래어·어휘 오류 사전 치환
    2단계: 종성 있는 음절 뒤 조사 '올'→'을', '름'→'를' 교정
    """
    for wrong, right in _WORD_FIXES:
        text = text.replace(wrong, right)
    return _JOSA_RE.sub(_josa_fix, text)
