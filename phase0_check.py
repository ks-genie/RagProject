# -*- coding: utf-8 -*-
"""
Phase 0 환경 확인 스크립트
실행: python phase0_check.py
"""

import sys
import io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
import shutil
import subprocess
import importlib

REQUIRED_PACKAGES = [
    ("pytesseract", "pytesseract"),
    ("PIL",         "Pillow"),
    ("pdf2image",   "pdf2image"),
    ("pdfplumber",  "pdfplumber"),
    ("requests",    "requests"),
    ("streamlit",   "streamlit"),
    ("dotenv",      "python-dotenv"),
    ("yaml",        "pyyaml"),
]

TESSERACT_COMMON_PATHS = [
    r"C:\Program Files\Tesseract-OCR\tesseract.exe",
    r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
]


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = "  [OK]  " if ok else "  [FAIL]"
    print(f"{status} {label}" + (f"  →  {detail}" if detail else ""))
    return ok


def check_python():
    v = sys.version_info
    ok = v >= (3, 9)
    check(f"Python {v.major}.{v.minor}.{v.micro}", ok,
          "" if ok else "Python 3.9 이상 필요")
    return ok


def check_packages():
    all_ok = True
    for import_name, pkg_name in REQUIRED_PACKAGES:
        try:
            importlib.import_module(import_name)
            check(f"패키지: {pkg_name}", True)
        except ImportError:
            check(f"패키지: {pkg_name}", False, f"pip install {pkg_name}")
            all_ok = False
    return all_ok


def check_tesseract():
    # 1) PATH에서 찾기
    path = shutil.which("tesseract")
    if not path:
        # 2) 일반 설치 경로 탐색
        import os
        for p in TESSERACT_COMMON_PATHS:
            if os.path.exists(p):
                path = p
                break

    if not path:
        check("Tesseract 설치", False,
              "https://github.com/UB-Mannheim/tesseract/wiki 에서 설치 후 PATH 등록")
        return False

    try:
        result = subprocess.run([path, "--version"],
                                capture_output=True, text=True, timeout=5)
        version_line = result.stderr.splitlines()[0] if result.stderr else ""
        check("Tesseract 설치", True, version_line or path)
    except Exception as e:
        check("Tesseract 설치", False, str(e))
        return False

    # kor+eng 언어팩 확인
    try:
        langs = subprocess.run([path, "--list-langs"],
                               capture_output=True, text=True, timeout=5)
        lang_list = langs.stderr.lower() + langs.stdout.lower()
        has_kor = "kor" in lang_list
        has_eng = "eng" in lang_list
        check("Tesseract 언어팩: kor", has_kor,
              "" if has_kor else "tessdata 폴더에 kor.traineddata 추가 필요")
        check("Tesseract 언어팩: eng", has_eng,
              "" if has_eng else "tessdata 폴더에 eng.traineddata 추가 필요")
        return has_kor and has_eng
    except Exception as e:
        check("Tesseract 언어팩 확인", False, str(e))
        return False


def check_anythingllm():
    try:
        import requests
        import yaml
        with open("config.yaml", encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        base_url = cfg.get("anythingllm", {}).get("base_url", "http://localhost:3001")
        resp = requests.get(f"{base_url}/api/ping", timeout=5)
        ok = resp.status_code == 200
        check(f"AnythingLLM API ({base_url})", ok,
              f"HTTP {resp.status_code}" if not ok else "연결 성공")
        return ok
    except Exception as e:
        check("AnythingLLM API", False, f"{e}  →  AnythingLLM 실행 여부 확인")
        return False


def check_directories():
    import os
    dirs = ["data/pdf_watch", "data/sample", "logs", "src", "tests"]
    all_ok = True
    for d in dirs:
        exists = os.path.isdir(d)
        check(f"디렉토리: {d}", exists,
              "" if exists else "자동 생성 필요 (mkdir)")
        if not exists:
            all_ok = False
    return all_ok


def main():
    print("=" * 55)
    print("  Phase 0 - 환경 확인 스크립트")
    print("=" * 55)

    results = {
        "Python":       check_python(),
        "패키지":       check_packages(),
        "Tesseract":    check_tesseract(),
        "디렉토리":     check_directories(),
        "AnythingLLM":  check_anythingllm(),
    }

    print()
    print("=" * 55)
    failed = [k for k, v in results.items() if not v]
    if not failed:
        print("  모든 항목 통과 — Phase 1 진행 가능")
    else:
        print(f"  미해결 항목: {', '.join(failed)}")
        print("  위 항목을 해결한 후 다시 실행하세요.")
    print("=" * 55)


if __name__ == "__main__":
    main()
