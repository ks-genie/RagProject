# -*- coding: utf-8 -*-
"""
OCR 비교: Tesseract vs EasyOCR vs PaddleOCR  (GPU / CPU 타이밍 포함)
대상: 주요국과 환경 및 역량 비교를 통한 국내 AI 반도체 산업 발전 방향.pdf
"""
import sys, time, textwrap, logging, warnings, os
sys.stdout.reconfigure(line_buffering=True)  # 파이프 출력도 즉시 플러시
import numpy as np
from pathlib import Path
from PIL import Image
import fitz  # PyMuPDF

logging.getLogger().setLevel(logging.ERROR)
warnings.filterwarnings("ignore")
os.environ.setdefault("EASYOCR_QUIET", "1")

PDF_PATH = Path(r"d:\OneDrive\MyProject\AMP2\RagProject\data\pdf_watch"
                r"\주요국과 환경 및 역량 비교를 통한 국내 AI 반도체 산업 발전 방향.pdf")
OUT_PATH = Path(r"d:\OneDrive\MyProject\AMP2\RagProject\ocr_comparison.md")
MAX_PAGES = 3   # 비교할 최대 페이지 수
DPI       = 200
COL       = 60  # 목표 열 폭 (화면 기준)

# ─────────────────────────────────────────────
# PDF → PIL Image 변환
# ─────────────────────────────────────────────
def pdf_to_images(path, max_pages, dpi):
    doc  = fitz.open(str(path))
    imgs = []
    total = len(doc)
    for i in range(min(max_pages, total)):
        mat = fitz.Matrix(dpi / 72, dpi / 72)
        pix = doc[i].get_pixmap(matrix=mat)
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        imgs.append(img)
    print(f"  PDF 로드 완료: {len(imgs)}/{total} 페이지")
    return imgs

# ─────────────────────────────────────────────
# 텍스트 줄 바꿈 (한국어 2칸 고려)
# ─────────────────────────────────────────────
def wrap_col(text, col=COL):
    result = []
    for para in (text or "").split("\n"):
        para = para.strip()
        if not para:
            result.append("")
            continue
        # 한글 2-width 고려: fill width ~ col * 0.55
        wrapped = textwrap.fill(para, width=int(col * 0.55), break_long_words=True)
        result.extend(wrapped.split("\n"))
    return result

# ─────────────────────────────────────────────
# OCR 엔진 래퍼
# ─────────────────────────────────────────────
def run_tesseract(imgs):
    import pytesseract
    print("  [Tesseract] CPU 실행 중 ...")
    t0    = time.time()
    parts = []
    for i, img in enumerate(imgs):
        text = pytesseract.image_to_string(img, lang="kor+eng", config="--psm 3")
        parts.append(text.strip())
        print(f"    page {i+1} 완료")
    elapsed = time.time() - t0
    return "\n\n".join(parts), elapsed


def run_easyocr(imgs, gpu: bool):
    import easyocr
    label = "GPU" if gpu else "CPU"
    print(f"  [EasyOCR {label}] 모델 로드 + 실행 중 ...")
    t0     = time.time()
    reader = easyocr.Reader(["ko", "en"], gpu=gpu, verbose=False)
    parts  = []
    for i, img in enumerate(imgs):
        result = reader.readtext(np.array(img), detail=0, paragraph=True)
        parts.append("\n".join(result).strip())
        print(f"    page {i+1} 완료")
    elapsed = time.time() - t0
    return "\n\n".join(parts), elapsed


def run_paddleocr(imgs, use_gpu: bool):
    from paddleocr import PaddleOCR
    import paddle
    label = "GPU" if use_gpu else "CPU"
    print(f"  [PaddleOCR {label}] 모델 로드 + 실행 중 ...")
    paddle.set_device("gpu:0" if use_gpu else "cpu")
    t0  = time.time()
    ocr = PaddleOCR(use_angle_cls=True, lang="korean",
                    use_gpu=use_gpu, show_log=False)
    parts = []
    for i, img in enumerate(imgs):
        result = ocr.ocr(np.array(img), cls=True)
        lines  = []
        if result and result[0]:
            for line in result[0]:
                if line and len(line) >= 2:
                    lines.append(line[1][0])
        parts.append("\n".join(lines).strip())
        print(f"    page {i+1} 완료")
    elapsed = time.time() - t0
    return "\n\n".join(parts), elapsed

# ─────────────────────────────────────────────
# 3-컬럼 마크다운 테이블 생성
# ─────────────────────────────────────────────
def make_3col_table(t1, t2, t3, col=COL):
    c1 = wrap_col(t1, col)
    c2 = wrap_col(t2, col)
    c3 = wrap_col(t3, col)
    n  = max(len(c1), len(c2), len(c3))

    def pad(lst):
        return lst + [""] * (n - len(lst))

    rows = []
    for l1, l2, l3 in zip(pad(c1), pad(c2), pad(c3)):
        rows.append(f"| {l1} | {l2} | {l3} |")
    return "\n".join(rows)


def hdr_sep(col=COL):
    bar = "-" * (col + 2)
    return f"|{bar}|{bar}|{bar}|"

# ─────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────
def main():
    print("=" * 60)
    print("OCR 비교 스크립트 시작")
    print(f"PDF : {PDF_PATH.name}")
    print(f"페이지: 최대 {MAX_PAGES} / DPI: {DPI}")
    print("=" * 60)

    imgs = pdf_to_images(PDF_PATH, MAX_PAGES, DPI)
    n    = len(imgs)

    # 순차 실행
    tess_text,     tess_t      = run_tesseract(imgs)
    easy_gpu_text, easy_gpu_t  = run_easyocr(imgs, gpu=True)
    easy_cpu_text, easy_cpu_t  = run_easyocr(imgs, gpu=False)
    pad_gpu_text,  pad_gpu_t   = run_paddleocr(imgs, use_gpu=True)
    pad_cpu_text,  pad_cpu_t   = run_paddleocr(imgs, use_gpu=False)

    print("\n=== 처리 시간 요약 ===")
    print(f"  Tesseract CPU   : {tess_t:.1f}s  ({tess_t/n:.1f}s/p)")
    print(f"  EasyOCR   GPU   : {easy_gpu_t:.1f}s  ({easy_gpu_t/n:.1f}s/p)")
    print(f"  EasyOCR   CPU   : {easy_cpu_t:.1f}s  ({easy_cpu_t/n:.1f}s/p)")
    print(f"  PaddleOCR GPU   : {pad_gpu_t:.1f}s  ({pad_gpu_t/n:.1f}s/p)")
    print(f"  PaddleOCR CPU   : {pad_cpu_t:.1f}s  ({pad_cpu_t/n:.1f}s/p)")

    # ── 마크다운 생성 ───────────────────────────
    gpu_device = "NVIDIA GeForce RTX 4070 Laptop (8 GB, CUDA 12.4)"
    col_lbl    = f"약 {COL}열 (한글 2-width 고려 실질 ~{int(COL*0.55)}자)"

    def speedup(cpu_t, gpu_t):
        return f"{cpu_t/gpu_t:.1f}x" if gpu_t > 0 else "-"

    md = []
    md.append("# OCR 엔진 비교 결과")
    md.append("")
    md.append("| 항목 | 내용 |")
    md.append("|------|------|")
    md.append(f"| 대상 파일 | `{PDF_PATH.name}` |")
    md.append(f"| 비교 페이지 | 1~{n} 페이지 (전체 중 앞부분) |")
    md.append(f"| GPU 장치 | {gpu_device} |")
    md.append(f"| 컬럼 폭 | {col_lbl} |")
    md.append(f"| Tesseract | v5.5.0, lang=kor+eng, psm=3 |")
    md.append(f"| EasyOCR | v1.7.2, lang=[ko, en] |")
    md.append(f"| PaddleOCR | v2.8.1, lang=korean |")
    md.append("")
    md.append("---")
    md.append("")

    # 처리 시간 비교 표
    md.append("## 1. 처리 시간 비교")
    md.append("")
    md.append("> 모델 로드 시간 포함 (cold start 기준). Tesseract는 GPU 미지원.")
    md.append("")
    md.append("| 엔진 | 버전 | 모드 | 총 시간 (s) | 페이지당 (s) | GPU 가속비 |")
    md.append("|------|------|------|------------|-------------|-----------|")
    md.append(f"| Tesseract  | v5.5.0 | CPU     | {tess_t:.1f} | {tess_t/n:.1f} | GPU 미지원 |")
    md.append(f"| EasyOCR    | v1.7.2 | **GPU** | {easy_gpu_t:.1f} | {easy_gpu_t/n:.1f} | 기준 |")
    md.append(f"| EasyOCR    | v1.7.2 | CPU     | {easy_cpu_t:.1f} | {easy_cpu_t/n:.1f} | GPU 대비 {speedup(easy_cpu_t, easy_gpu_t)} 느림 |")
    md.append(f"| PaddleOCR  | v2.8.1 | **GPU** | {pad_gpu_t:.1f} | {pad_gpu_t/n:.1f} | 기준 |")
    md.append(f"| PaddleOCR  | v2.8.1 | CPU     | {pad_cpu_t:.1f} | {pad_cpu_t/n:.1f} | GPU 대비 {speedup(pad_cpu_t, pad_gpu_t)} 느림 |")
    md.append("")
    md.append("---")
    md.append("")

    # GPU 기준 3컬럼
    md.append("## 2. OCR 인식 결과 비교 - GPU 모드 기준")
    md.append("")
    md.append("> Tesseract(CPU) / EasyOCR(GPU) / PaddleOCR(GPU) 결과를 나란히 표시")
    md.append("> 각 컬럼 약 60열 폭 (한글 2-width 고려)")
    md.append("")
    gpu_hdr = (f"| {'Tesseract (CPU)':<{COL}} "
               f"| {'EasyOCR (GPU)':<{COL}} "
               f"| {'PaddleOCR (GPU)':<{COL}} |")
    md.append(gpu_hdr)
    md.append(hdr_sep())
    md.append(make_3col_table(tess_text, easy_gpu_text, pad_gpu_text))
    md.append("")
    md.append("---")
    md.append("")

    # CPU 기준 3컬럼
    md.append("## 3. OCR 인식 결과 비교 - CPU 모드 기준")
    md.append("")
    md.append("> Tesseract(CPU) / EasyOCR(CPU) / PaddleOCR(CPU) 결과를 나란히 표시")
    md.append("")
    cpu_hdr = (f"| {'Tesseract (CPU)':<{COL}} "
               f"| {'EasyOCR (CPU)':<{COL}} "
               f"| {'PaddleOCR (CPU)':<{COL}} |")
    md.append(cpu_hdr)
    md.append(hdr_sep())
    md.append(make_3col_table(tess_text, easy_cpu_text, pad_cpu_text))

    OUT_PATH.write_text("\n".join(md), encoding="utf-8")
    print(f"\n출력 완료 -> {OUT_PATH}")


if __name__ == "__main__":
    main()
