# -*- coding: utf-8 -*-
"""
OCR 엔진 비교: Tesseract vs EasyOCR vs PaddleOCR
GPU/CPU 처리 시간 및 한글 인식률 비교

사용법:
    python tools/compare_ocr_engines.py [--pages N]   (기본: 3페이지)
    python tools/compare_ocr_engines.py --pages 13    (전체 페이지)
"""

import argparse
import io
import re
import sys
import time

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import cv2
import numpy as np
import pypdfium2 as pdfium
import pytesseract
from PIL import Image

PDF_PATH = r"D:\OneDrive\MyProject\AMP2\RagProject\data\pdf_watch\주요국과 환경 및 역량 비교를 통한 국내 AI 반도체 산업 발전 방향.pdf"
SCALE = 4.17  # 300 DPI
KOR_RE = re.compile(r"[가-힣]")


# ── 유틸 ─────────────────────────────────────────────────────────────────────

def render_pages(pdf_path: str, n_pages: int) -> list[Image.Image]:
    doc = pdfium.PdfDocument(pdf_path)
    total = len(doc)
    pages = []
    for i in range(min(n_pages, total)):
        try:
            page = doc[i]
            bitmap = page.render(scale=SCALE)
            pages.append(bitmap.to_pil())
        except Exception:
            pages.append(Image.new("RGB", (1240, 1754), "white"))
    return pages


def enhance(img: Image.Image) -> Image.Image:
    g = np.array(img.convert("L"))
    b = cv2.adaptiveThreshold(g, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10)
    return Image.fromarray(b)


def stats(text: str) -> dict:
    non_space = [c for c in text if not c.isspace()]
    kor = KOR_RE.findall(text)
    total = len(non_space)
    return {
        "kor": len(kor),
        "total": total,
        "ratio": len(kor) / max(total, 1),
    }


def section(title: str):
    print(f"\n{'─'*60}")
    print(f"  {title}")
    print(f"{'─'*60}")


# ── 각 엔진 실행 함수 ─────────────────────────────────────────────────────────

def run_tesseract(images: list[Image.Image]) -> tuple[str, float]:
    cfg = "--oem 1 --psm 6"
    t0 = time.time()
    texts = []
    for img in images:
        enhanced = enhance(img)
        texts.append(pytesseract.image_to_string(enhanced, lang="kor+eng", config=cfg))
    return "\n".join(texts), time.time() - t0


def run_easyocr(images: list[Image.Image], gpu: bool) -> tuple[str, float]:
    import easyocr
    print(f"    모델 로딩 중... (gpu={gpu})", end=" ", flush=True)
    t0 = time.time()
    reader = easyocr.Reader(["ko", "en"], gpu=gpu, verbose=False)
    load_time = time.time() - t0
    print(f"{load_time:.1f}s")

    texts = []
    infer_t0 = time.time()
    for img in images:
        result = reader.readtext(np.array(img), detail=0, paragraph=True)
        texts.append("\n".join(result))
    infer_time = time.time() - infer_t0

    total_time = load_time + infer_time
    print(f"    로딩: {load_time:.1f}s | 추론: {infer_time:.1f}s")
    return "\n".join(texts), total_time


def run_paddleocr(images: list[Image.Image], gpu: bool) -> tuple[str, float]:
    from paddleocr import PaddleOCR
    print(f"    모델 로딩 중... (gpu={gpu})", end=" ", flush=True)
    t0 = time.time()
    ocr = PaddleOCR(use_angle_cls=True, lang="korean", use_gpu=gpu,
                    show_log=False, use_mp=False)
    load_time = time.time() - t0
    print(f"{load_time:.1f}s")

    texts = []
    infer_t0 = time.time()
    for img in images:
        result = ocr.ocr(np.array(img), cls=True)
        if result and result[0]:
            page_text = "\n".join(
                line[1][0] for line in result[0] if line and line[1]
            )
        else:
            page_text = ""
        texts.append(page_text)
    infer_time = time.time() - infer_t0

    total_time = load_time + infer_time
    print(f"    로딩: {load_time:.1f}s | 추론: {infer_time:.1f}s")
    return "\n".join(texts), total_time


# ── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pages", type=int, default=3, help="테스트할 페이지 수 (기본 3)")
    args = parser.parse_args()
    n = args.pages

    print(f"\n=== OCR 엔진 비교 ({n}페이지) ===")
    print(f"PDF: {PDF_PATH}")

    section("PDF 렌더링 (300 DPI)")
    t0 = time.time()
    images = render_pages(PDF_PATH, n)
    print(f"  {len(images)}페이지 렌더링 완료 ({time.time()-t0:.1f}s), 크기: {images[0].size}")

    runs: dict[str, tuple[str, float]] = {}

    section("Tesseract (CPU only)")
    text, elapsed = run_tesseract(images)
    runs["Tesseract (CPU)"] = (text, elapsed)
    print(f"  완료: {elapsed:.1f}s")

    section("EasyOCR [GPU]")
    try:
        text, elapsed = run_easyocr(images, gpu=True)
        runs["EasyOCR (GPU)"] = (text, elapsed)
    except Exception as e:
        print(f"  실패: {e}")

    section("EasyOCR [CPU]")
    try:
        text, elapsed = run_easyocr(images, gpu=False)
        runs["EasyOCR (CPU)"] = (text, elapsed)
    except Exception as e:
        print(f"  실패: {e}")

    section("PaddleOCR [GPU]")
    try:
        text, elapsed = run_paddleocr(images, gpu=True)
        runs["PaddleOCR (GPU)"] = (text, elapsed)
    except Exception as e:
        print(f"  실패: {e}")

    section("PaddleOCR [CPU]")
    try:
        text, elapsed = run_paddleocr(images, gpu=False)
        runs["PaddleOCR (CPU)"] = (text, elapsed)
    except Exception as e:
        print(f"  실패: {e}")

    # ── 결과 표 ────────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print(f"  결과 요약 ({n}페이지 기준)")
    print(f"{'='*70}")
    print(f"{'엔진':<22} {'시간(s)':>8} {'한글자수':>8} {'전체자수':>8} {'한글비율':>9}")
    print(f"{'─'*70}")
    for name, (text, elapsed) in runs.items():
        s = stats(text)
        print(f"{name:<22} {elapsed:>8.1f} {s['kor']:>8,} {s['total']:>8,} {s['ratio']:>8.1%}")

    # ── 샘플 텍스트 ────────────────────────────────────────────────────────────
    print(f"\n{'='*70}")
    print("  페이지 1 샘플 텍스트 (첫 400자)")
    print(f"{'='*70}")
    for name, (text, _) in runs.items():
        print(f"\n▶ {name}")
        print(text[:400].strip())
        print()


if __name__ == "__main__":
    main()
