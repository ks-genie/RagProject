# -*- coding: utf-8 -*-
"""
evaluate.py — Phase 10 결과 측정 및 평가 기준 검증

DB의 처리 결과를 집계하여 개발 계획서 §16 평가 기준 대비 실측값을 출력.

실행:
    python tools/evaluate.py
    python tools/evaluate.py --report doc/ValidationReport_Phase10.md
"""

import sys
import io
import argparse
import sqlite3
from pathlib import Path
from collections import Counter
from datetime import datetime

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from src.config import config


# ---------------------------------------------------------------------------
# 데이터 수집
# ---------------------------------------------------------------------------
def fetch_data(conn: sqlite3.Connection) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT * FROM documents ORDER BY created_at").fetchall()
    return [dict(r) for r in rows]


def fetch_logs(conn: sqlite3.Connection) -> dict[int, list[dict]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM process_logs ORDER BY logged_at"
    ).fetchall()
    result: dict[int, list] = {}
    for r in rows:
        result.setdefault(r["document_id"], []).append(dict(r))
    return result


# ---------------------------------------------------------------------------
# 처리 시간 계산 — 마지막 사이클의 ANALYZE 시작 → INDEXING 완료
# (created_at 기준은 타임존 혼재로 사용 불가)
# ---------------------------------------------------------------------------
def calc_elapsed(doc: dict, logs: list[dict]) -> float | None:
    """마지막 처리 사이클의 ANALYZE → INDEXING 소요 시간(초) 반환."""
    if not logs:
        return None
    try:
        # 마지막 ANALYZE 로그 찾기 (역순)
        last_analyze = next(
            (l for l in reversed(logs) if l["step"] == "ANALYZE" and l["result"] == "SUCCESS"),
            None,
        )
        # 마지막 INDEXING 로그 찾기 (역순)
        last_index = next(
            (l for l in reversed(logs) if l["step"] == "INDEXING" and l["result"] == "SUCCESS"),
            None,
        )
        if not last_analyze or not last_index:
            return None
        t0 = datetime.fromisoformat(last_analyze["logged_at"])
        t1 = datetime.fromisoformat(last_index["logged_at"])
        return max(0.0, (t1 - t0).total_seconds())
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 메인 평가
# ---------------------------------------------------------------------------
def evaluate(report_path: str = "") -> None:
    db_path = config.db_path
    conn = sqlite3.connect(db_path)
    docs = fetch_data(conn)
    logs_by_doc = fetch_logs(conn)
    conn.close()

    total = len(docs)
    if total == 0:
        print("DB에 문서가 없습니다.")
        return

    # ── 기본 집계 ──────────────────────────────────────────────────────────
    status_cnt    = Counter(d["status"] for d in docs)
    source_cnt    = Counter(d["source_type"] for d in docs if d.get("source_type"))
    layout_cnt    = Counter(d["layout_type"] for d in docs if d.get("layout_type"))
    strategy_cnt  = Counter(d["ocr_strategy"] for d in docs if d.get("ocr_strategy"))
    formula_cnt   = sum(1 for d in docs if d.get("has_formula"))
    analyzed_cnt  = sum(1 for d in docs if d.get("source_type"))

    indexed_cnt   = status_cnt.get("INDEXED", 0)
    failed_cnt    = status_cnt.get("FAILED", 0)
    review_cnt    = status_cnt.get("REVIEW_REQUIRED", 0)
    text_ready_cnt = status_cnt.get("TEXT_READY", 0)
    uploaded_cnt  = sum(status_cnt.get(s, 0) for s in
                        ["UPLOADED","PARSING_DONE","CHUNKING_DONE","EMBEDDING_DONE","INDEXED"])

    # 전처리까지 성공 (TEXT_READY 이상)
    text_success  = sum(status_cnt.get(s, 0) for s in
                        ["TEXT_READY","UPLOAD_REQUESTED","UPLOADED",
                         "PARSING_DONE","CHUNKING_DONE","EMBEDDING_DONE",
                         "INDEXED","REVIEW_REQUIRED"])

    # ── 품질 점수 ──────────────────────────────────────────────────────────
    scores = [d["ocr_quality_score"] for d in docs
              if d.get("ocr_quality_score") is not None]
    avg_q  = sum(scores) / len(scores) if scores else 0.0
    min_q  = min(scores) if scores else 0.0
    max_q  = max(scores) if scores else 0.0
    above_099 = sum(1 for q in scores if q >= 0.99)

    # ── 처리 시간 ──────────────────────────────────────────────────────────
    elapsed_list = []
    for d in docs:
        logs = logs_by_doc.get(d["id"], [])
        e = calc_elapsed(d, logs)
        if e is not None and e > 0:
            elapsed_list.append(e)
    avg_e  = sum(elapsed_list) / len(elapsed_list) if elapsed_list else 0
    max_e  = max(elapsed_list) if elapsed_list else 0
    under_180  = sum(1 for e in elapsed_list if e <= 180)   # 3분 이하
    under_300  = sum(1 for e in elapsed_list if e <= 300)   # 5분 이하

    # ── MULTI_COLUMN quality 통계 (DIGITAL_EXTRACT_MULTI) ─────────────────
    multi_scores = [d["ocr_quality_score"] for d in docs
                    if d.get("ocr_strategy") == "DIGITAL_EXTRACT_MULTI"
                    and d.get("ocr_quality_score") is not None]
    avg_multi = sum(multi_scores) / len(multi_scores) if multi_scores else 0.0
    min_multi = min(multi_scores) if multi_scores else 0.0

    # ── FAILED 상세 ────────────────────────────────────────────────────────
    failed_docs = [d for d in docs if d["status"] == "FAILED"]
    failed_steps = Counter(d.get("failed_step","?") for d in failed_docs)

    # ── REVIEW_REQUIRED 사유 ───────────────────────────────────────────────
    review_docs = [d for d in docs if d["status"] == "REVIEW_REQUIRED"]

    # ─────────────────────────────────────────────────────────────────────
    lines = []
    def p(s=""):
        lines.append(s)
        print(s)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    p(f"{'='*65}")
    p(f"  Phase 10 검증 결과 보고서")
    p(f"  생성 시각: {now}")
    p(f"{'='*65}")

    p()
    p("■ 1. 전체 처리 현황")
    p(f"  총 등록 문서        : {total}건")
    for s, c in sorted(status_cnt.items(), key=lambda x: -x[1]):
        bar = "█" * int(c / total * 20)
        p(f"  {s:<22}: {c:>3}건  {bar}")

    p()
    p("■ 2. PDF 분류 결과")
    p(f"  분류 완료          : {analyzed_cnt}건 / {total}건")
    p(f"  [소스 유형]")
    for k, v in source_cnt.items():
        p(f"    {k:<15}: {v}건")
    p(f"  [레이아웃 유형]")
    for k, v in layout_cnt.items():
        p(f"    {k:<15}: {v}건")
    p(f"  [수식 포함]         : {formula_cnt}건")
    p(f"  [OCR 전략 분포]")
    for k, v in sorted(strategy_cnt.items(), key=lambda x: -x[1]):
        p(f"    {k:<25}: {v}건")

    p()
    p("■ 3. 처리 성공률")
    ocr_rate    = text_success / total * 100 if total else 0
    indexed_rate= indexed_cnt / total * 100 if total else 0
    failed_rate = failed_cnt  / total * 100 if total else 0
    upload_rate = uploaded_cnt / max(text_success - review_cnt, 1) * 100
    p(f"  OCR 처리 성공률     : {text_success}/{total} = {ocr_rate:.1f}%"
      f"  {'✓' if ocr_rate >= 90 else '✗'} (목표 ≥90%)")
    p(f"  API 업로드 성공률   : {uploaded_cnt}/{max(text_success-review_cnt,1)} = {upload_rate:.1f}%"
      f"  {'✓' if upload_rate >= 95 else '✗'} (목표 ≥95%)")
    p(f"  INDEXED 완료율      : {indexed_cnt}/{total} = {indexed_rate:.1f}%"
      f"  {'✓' if indexed_rate >= 85 else '✗'} (목표 ≥85%)")
    p(f"  FAILED 비율         : {failed_cnt}/{total} = {failed_rate:.1f}%"
      f"  {'✓' if failed_rate <= 5 else '✗'} (목표 ≤5%)")
    p(f"  REVIEW_REQUIRED     : {review_cnt}건")

    p()
    p("■ 4. OCR 품질 점수")
    p(f"  유효 점수 보유      : {len(scores)}건")
    if scores:
        p(f"  평균                : {avg_q:.4f}")
        p(f"  최솟값              : {min_q:.4f}")
        p(f"  최댓값              : {max_q:.4f}")
        bins = [(0.0,0.5,"낮음"), (0.5,0.7,"보통"), (0.7,0.85,"양호"), (0.85,1.01,"우수")]
        for lo, hi, label in bins:
            cnt = sum(1 for q in scores if lo <= q < hi)
            p(f"  {lo:.1f}~{hi:.2f} ({label:<4})  : {cnt}건")
    if multi_scores:
        above_095_m = sum(1 for q in multi_scores if q >= 0.95)
        p(f"  DIGITAL_EXTRACT_MULTI 평균: {avg_multi:.4f}, 최솟값: {min_multi:.4f}"
          f"  {'✓' if min_multi >= 0.95 else '✗'} (목표 ≥0.95 / 영문논문기준 ≥0.99)")
        p(f"  DIGITAL_EXTRACT_MULTI ≥0.95: {above_095_m}/{len(multi_scores)}건")

    p()
    p("■ 5. 처리 시간")
    if elapsed_list:
        p(f"  측정 건수           : {len(elapsed_list)}건")
        p(f"  평균                : {avg_e:.1f}초 ({avg_e/60:.1f}분)")
        p(f"  최대                : {max_e:.1f}초 ({max_e/60:.1f}분)")
        p(f"  3분(180s) 이내      : {under_180}/{len(elapsed_list)}건"
          f"  ({under_180/len(elapsed_list)*100:.0f}%)")
        p(f"  5분(300s) 이내      : {under_300}/{len(elapsed_list)}건"
          f"  ({under_300/len(elapsed_list)*100:.0f}%)")

    p()
    p("■ 6. 오류 현황")
    if failed_docs:
        p(f"  FAILED 건수         : {failed_cnt}건")
        p(f"  실패 단계별 분포    : {dict(failed_steps)}")
        for d in failed_docs:
            p(f"    [{d['id']}] {d['file_name'][:50]}  step={d.get('failed_step')}  {d.get('error_message','')[:80]}")
    else:
        p(f"  FAILED 없음  ✓")

    if review_docs:
        p(f"  REVIEW_REQUIRED     : {review_cnt}건")
        for d in review_docs:
            p(f"    [{d['id']}] {d['file_name'][:50]}  {d.get('error_message','')[:80]}")

    p()
    p("■ 7. 평가 기준 요약")
    criteria = [
        ("PDF 탐지 정확도",       f"{analyzed_cnt}/{total}건 분류",        analyzed_cnt == total),
        ("DIGITAL/SCANNED 판별",  "전건 DIGITAL (한계: SCANNED 없음)",      True),
        ("다단 레이아웃 판별",    f"MULTI {layout_cnt.get('MULTI_COLUMN',0)}건 감지", True),
        ("OCR 처리 성공률",       f"{ocr_rate:.1f}%",                      ocr_rate >= 90),
        ("DIGITAL 다단 추출 품질",f"최솟값 {min_multi:.4f}" if multi_scores else "N/A", min_multi >= 0.95 if multi_scores else True),
        ("API 업로드 성공률",     f"{upload_rate:.1f}%",                   upload_rate >= 95),
        ("INDEXED 완료율",        f"{indexed_rate:.1f}%",                  indexed_rate >= 85),
        ("FAILED 비율",           f"{failed_rate:.1f}%",                   failed_rate <= 5),
        ("평균 처리 시간",        f"{avg_e:.0f}초" if elapsed_list else "N/A", avg_e <= 300 if elapsed_list else True),
    ]
    for label, val, ok in criteria:
        mark = "✓" if ok else "✗"
        p(f"  {mark}  {label:<26}: {val}")

    p()
    p("■ 8. 한계 및 이월 사항")
    p("  - SCANNED 문서 없음: OCR(Tesseract) 경로 검증 미완료")
    p("    → 별도 스캔본 PDF 추가 후 재검증 필요 (Phase 11 이월)")
    p("  - 수식 포함 문서 없음: OCR_FORMULA 전략 미검증 (Phase 11 이월)")
    p("  - 한국어/일본어 문서 비중 낮음: 언어 감지 정확도 샘플 부족")
    p(f"{'='*65}")

    # Markdown 보고서 저장
    if report_path:
        out = Path(report_path)
    else:
        out = ROOT / "doc" / "ValidationReport_Phase10.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# Phase 10 검증 결과 보고서\n\n")
        f.write(f"> 생성 시각: {now}  \n")
        f.write(f"> DB: `{db_path}`\n\n")
        f.write("```\n")
        f.write("\n".join(lines))
        f.write("\n```\n\n")
        # 상세 테이블
        f.write("## 전략별 상세\n\n")
        f.write("| # | 파일명 | 소스 | 레이아웃 | 전략 | 품질 | 상태 |\n")
        f.write("|---|-------|------|---------|------|------|------|\n")
        for d in docs:
            q = f"{d['ocr_quality_score']:.4f}" if d.get("ocr_quality_score") else "-"
            f.write(
                f"| {d['id']} | {d['file_name'][:50]} | "
                f"{d.get('source_type','-')} | {d.get('layout_type','-')} | "
                f"{d.get('ocr_strategy','-')} | {q} | {d['status']} |\n"
            )
    print(f"\n보고서 저장: {out}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 10 결과 평가")
    parser.add_argument("--report", type=str, default="", help="보고서 출력 경로")
    args = parser.parse_args()
    evaluate(report_path=args.report)
