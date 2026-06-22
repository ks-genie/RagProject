# -*- coding: utf-8 -*-
"""
classify_all.py — data/pdf_watch/ 의 PDF를 분류하여 CSV 출력

실행:
    python tools/classify_all.py
    python tools/classify_all.py --max 10   # 최대 10건
    python tools/classify_all.py --csv out.csv
"""

import sys
import io
import argparse
import csv
import time
from pathlib import Path

# 프로젝트 루트를 sys.path에 추가
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from src.config import config
from src.pdf_analyzer import PDFAnalyzer
from src.strategy_selector import StrategySelector


def classify_all(max_files: int = 0, csv_path: str = "") -> list[dict]:
    analyzer = PDFAnalyzer()
    selector = StrategySelector()

    watch_dir = config.watch_dir
    pdf_files = sorted(watch_dir.glob("**/*.pdf"))
    if max_files:
        pdf_files = pdf_files[:max_files]

    total = len(pdf_files)
    print(f"분류 대상: {total}건  ({watch_dir})")
    print("-" * 90)

    results = []
    for i, pdf_path in enumerate(pdf_files, 1):
        name = pdf_path.name
        size_kb = pdf_path.stat().st_size // 1024
        t0 = time.time()
        try:
            profile = analyzer.analyze(str(pdf_path))
            strategy = selector.select(profile)
            elapsed = time.time() - t0
            row = {
                "no":         i,
                "file_name":  name,
                "size_kb":    size_kb,
                "source_type":    profile.source_type,
                "layout_type":    profile.layout_type,
                "languages":      ",".join(profile.detected_languages),
                "has_formula":    "Y" if profile.has_formula else "N",
                "page_count":     profile.page_count,
                "avg_chars":      f"{profile.avg_chars_per_page:.0f}",
                "strategy":       strategy,
                "elapsed_s":      f"{elapsed:.1f}",
                "error":          "",
            }
            flag = "✓" if strategy.startswith("DIGITAL") else "◎"
            print(
                f"[{i:02d}/{total}] {flag} {name[:55]:<55}"
                f"  {profile.source_type:<8} {profile.layout_type:<15}"
                f"  {strategy:<25}  {elapsed:.1f}s"
            )
        except Exception as e:
            elapsed = time.time() - t0
            row = {
                "no": i, "file_name": name, "size_kb": size_kb,
                "source_type": "ERROR", "layout_type": "", "languages": "",
                "has_formula": "", "page_count": 0, "avg_chars": "",
                "strategy": "ERROR", "elapsed_s": f"{elapsed:.1f}", "error": str(e),
            }
            print(f"[{i:02d}/{total}] ✗ {name[:55]:<55}  ERROR: {e}")
        results.append(row)

    # 요약
    print("-" * 90)
    from collections import Counter
    st = Counter(r["source_type"] for r in results)
    lt = Counter(r["layout_type"] for r in results if r["layout_type"])
    sg = Counter(r["strategy"] for r in results)
    print(f"source_type : {dict(st)}")
    print(f"layout_type : {dict(lt)}")
    print(f"strategy    : {dict(sg)}")
    total_t = sum(float(r["elapsed_s"]) for r in results)
    print(f"총 분석 시간: {total_t:.1f}초  (평균 {total_t/len(results):.1f}초/건)")

    # CSV 저장
    if csv_path:
        out = Path(csv_path)
    else:
        out = ROOT / "data" / "classify_result.csv"
    fieldnames = ["no","file_name","size_kb","source_type","layout_type",
                  "languages","has_formula","page_count","avg_chars",
                  "strategy","elapsed_s","error"]
    with open(out, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(results)
    print(f"\nCSV 저장: {out}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PDF 분류 배치 실행")
    parser.add_argument("--max", type=int, default=0, help="최대 처리 건수 (0=전체)")
    parser.add_argument("--csv", type=str, default="", help="CSV 출력 경로")
    args = parser.parse_args()
    classify_all(max_files=args.max, csv_path=args.csv)
