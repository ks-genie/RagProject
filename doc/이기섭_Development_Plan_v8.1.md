# 개발 명세서 및 계획서 v8.1

> 기준일: 2026-06-15  
> 변경 이력:
> - v4.0 → v4.1 — PDF 분류 분석 및 적응형 OCR 전략 추가  
> - v4.1 → v5.0 — Phase 9 UI 완성, 다단 감지 전면 개선, DIGITAL 다단 추출 전략 추가  
> - v5.0 → v5.1 — 코드-명세 정합성 보완, StatusTracker 동기 처리 방식 반영, LayoutSplitter 파라미터 명시, 설정 파라미터 표 추가, 실행 명령 확정  
> - v5.1 → v6.0 — Phase 10 E2E 통합 검증 완료, 실측 평가값 반영, 이슈 2건 수정, 이월 항목 정리  
> - v6.0 → v6.1 — 로그 파일 기능 추가 (logs/log_yyyymmdd_hhmmss.log), CHANGELOG.md 전체 개정 이력 관리 도입  
> - v6.1 → v6.2 — PDF 뷰어 OCR 텍스트 표시 고도화: 헤더/푸터 분리, 학술 논문 구조 인식([헤더]/[제목]/[저자]/[소속]/[본문]/[푸터]), 다단 문장 연결, DIGITAL_EXTRACT_MULTI 저자 분리 버그 수정 (app.py v0.9.6→v0.9.9)  
> - v6.2 → v7.0 — DIGITAL_EXTRACT_MULTI 2단 읽기 순서 보정, 텍스트 줄바꿈 정규화 고도화, 컬럼 경계 문장 자동 연결, 디지털 PDF 워터마크 자동 제거 (v0.9.10→v0.9.15a, 2026-04-27~05-27)  
> - v7.0 → v8.0 — Phase 12 문서 구조 분리 저장: document_metadata·page_contents 신규 테이블, page_db.py 신규 모듈, 파이프라인 구조화 저장, 뷰어 DB 우선 조회 (2026-06-09)
> - v8.0 → v8.1 — 대규모 운영 전략 추가 (6,000건+ 지속 추가 구조): Phase 13 배치 처리·AnythingLLM 부하 제어, Phase 14 PDF 예외 유형 강화, Phase 11 우선순위 재조정 (2026-06-15)

---

## 1. 프로젝트 개요

본 프로젝트는 PDF 문서를 자동으로 분류·분석한 뒤 최적의 OCR 전략을 선택하여 텍스트를 추출하고, 추출된 텍스트를 AnythingLLM에 등록함으로써 문서를 검색 가능한 상태로 만드는 것을 목표로 합니다.

| 항목 | 내용 |
|------|------|
| 실행 환경 | Windows 로컬 |
| 주요 기술 스택 | Python, SQLite, Tesseract OCR, OpenCV, pdfplumber, pypdfium2, Streamlit, AnythingLLM |
| 처리 대상 문서 | 한국어·영어·일본어 혼합 PDF (단일/다단 레이아웃, Digital/Scan본, 수식 포함 여부) |
| OCR 언어 설정 | 문서 분류 결과에 따라 `eng` / `kor+eng` / `kor+eng+jpn` 자동 선택 |
| 현재 버전 | app.py **v0.9.15a** (2026-05-27) |
| 운영 규모 | 초기 **약 6,000건** 일괄 처리 후 지속 추가 구조 |
| 문서 구성 비율 | 디지털 원본 다수 (~90%), 스캔 문서 소수 (~10%) |

### 앱 실행 명령 (확정)

```bash
python -m streamlit run app.py --server.headless true --browser.gatherUsageStats false
```

- `--server.headless true` : 이메일 입력 프롬프트 없이 서버 모드로 시작
- `--browser.gatherUsageStats false` : 사용 통계 수집 비활성화
- 접속 URL: **http://localhost:8501**

---

## 2. 주요 기술 결정

| 항목 | 결정 내용 |
|------|-----------|
| PDF 유형 판별 | pdfplumber 텍스트 레이어 추출 시도 → 추출량 기준으로 DIGITAL / SCANNED 분류 |
| 레이아웃 분석 (DIGITAL) | **줄(line) 단위 x-커버리지** 방식: 각 x 위치를 덮는 줄의 비율로 컬럼 갭 탐지 (헤더 전폭 줄 영향 배제) |
| 레이아웃 분석 (SCANNED) | OpenCV 수직 projection + **4% 스무딩** + 50% valley 임계값 |
| 언어 감지 | 텍스트 레이어 샘플링(Digital) 또는 OCR 초안 결과에 `langdetect` 적용 |
| 수식 감지 | Unicode 수학 기호 밀도(Digital) + 이미지 영역 특징(Scanned) 휴리스틱 |
| OCR 도구 | Tesseract tessdata-best 모델, PSM·언어 조합 전략별 지정 |
| DIGITAL 다단 추출 | **`DIGITAL_EXTRACT_MULTI`** 전략: 갭 위치 탐지 → pre-body 헤더 분리 → 좌/우 본문 추출 → 컬럼 경계 문장 연결 |
| DIGITAL 2단 읽기 순서 | **`_find_body_start_y()`**: 슬라이딩 윈도우(3줄)로 양쪽 컬럼에 단어 충족 시점을 본문 시작 y로 탐지 |
| DIGITAL 컬럼 경계 문장 연결 | **`_join_columns()`**: 좌측 끝 단락이 `.?!;` 없이 끝나고 우측 첫 단락이 소문자 시작이면 공백으로 이어붙임 |
| DIGITAL 워터마크 제거 | **`filter_watermarks()`**: pdfplumber `page.filter()` 활용 — ①`upright=False` 문자(대각선 스탬프) ②밝기>0.80 문자(연한 배경 워터마크) ③폰트>30pt+밝기>0.15(대형 스탬프 낱글자) 제거 |
| 스캔본 적응형 OCR | 모든 스캔 전략 공통: **페이지별** `LayoutSplitter._split_page()` 호출 → 페이지마다 컬럼 수 자동 판단 |
| 텍스트 줄바꿈 정규화 | **`normalize_hard_linebreaks()`**: 소프트랩 병합, 리스트·캡션·단락 제목 보존, 노이즈 줄 제거 |
| AnythingLLM 색인 방식 | `update-embeddings` API **동기 처리** — 단일 호출로 Parsing/Chunking/Embedding 완료, 폴링 불필요 |
| 문서 등록 방식 | AnythingLLM raw-text API |
| 로그 파일 관리 | 앱 시작 시 `logs/log_yyyymmdd_hhmmss.log` 자동 생성 — 실행 세션마다 별도 파일 누적, `logs/*.log`는 `.gitignore` 제외 |

---

## 3. PDF 분류 기준

### 3.1 소스 유형 (source_type)

| 유형 | 판별 기준 |
|------|-----------|
| `DIGITAL` | pdfplumber로 추출한 텍스트가 페이지당 평균 ≥ 50자 (`config.ocr_min_text_length`) |
| `SCANNED` | 추출 텍스트 < 50자 또는 텍스트 레이어 없음 |

### 3.2 레이아웃 유형 (layout_type)

| 유형 | 판별 방법 |
|------|-----------|
| `SINGLE_COLUMN` | 중앙 35~65% 구간에서 연속 저커버리지 빈(bin) 없음 |
| `MULTI_COLUMN` | 연속 저커버리지 빈 ≥ 3개 (1.5% 이상의 컬럼 갭 탐지) |

#### DIGITAL 레이아웃 판별 — 줄 단위 x-커버리지 알고리즘

```
1. pdfplumber extract_words()로 단어 위치 추출
2. y 좌표(LINE_H=3pt 허용오차)로 텍스트 줄 그룹핑
3. 각 bin(0.5% 단위, BINS=200)에 대해 "해당 bin을 덮는 줄의 비율" 계산
4. side_mean ≤ 0.10이면 텍스트 밀도 낮은 페이지 → 분석 제외
5. 중앙 35~65% 구간에서 coverage < min(0.25, side_mean×0.30)인 bin 탐색
6. 연속 빈 공간(max_gap)이 MIN_GAP=3 이상이면 MULTI_COLUMN
7. 텍스트 줄 수 < 10인 페이지(그림·참고문헌 페이지)는 분석에서 제외
8. 분석된 페이지의 2/3 이상이 MULTI_COLUMN이면 전체 문서 MULTI_COLUMN
9. 페이지 최대 5장 분석 (pdf.pages[:5])
```

> **특징**: 논문 제목·저자 등 전폭(full-width) 줄이 일부 있어도 다수의 2단 본문 줄에 희석되어 정확히 감지  
> **검증**: IEEE ISSCC 논문 (A 1.2V 20nm 307GB/s HBM DRAM) — gap@47.5~48.5%, max_gap=3 → MULTI ✓

#### SCANNED 레이아웃 판별

```
1. 페이지 이미지 → 이진화 (THRESH_BINARY_INV + OTSU)
2. 수직 projection (열별 픽셀 합) 계산
3. 4% 스무딩 커널(k = w×0.04)로 글자 간 노이즈 제거
4. 중앙 35~65% 구간 valley_depth < side_mean×0.50이면 MULTI_COLUMN
5. 분석된 페이지의 2/3 이상이 MULTI_COLUMN이면 전체 문서 MULTI_COLUMN
6. 페이지 최대 3장 분석
```

### 3.3 포함 언어 (detected_languages)

| 언어 코드 | 감지 방법 |
|-----------|-----------|
| `kor` | 한글 Unicode 범위 (U+AC00–U+D7A3) 문자 비율 ≥ 3% |
| `eng` | ASCII 알파벳 비율 ≥ 3% |
| `jpn` | 히라가나(U+3040–U+309F) / 가타카나(U+30A0–U+30FF) 비율 ≥ 3% |

> Digital: 텍스트 레이어 직접 분석 + `langdetect` 보정  
> Scanned: 낮은 해상도 OCR 초안(eng 단일 패스)으로 샘플링 후 `langdetect` 보정

### 3.4 수식 포함 여부 (has_formula)

| 유형 | 감지 방법 |
|------|-----------|
| Digital | Unicode 수학 기호(U+2200–U+22FF, U+2A00–U+2AFF, U+0370–U+03FF) 밀도 > 0.8% |
| Scanned | 가로선·분수선 패턴(h≤3px, aspect>10, 페이지 너비 2% 이상) + 소형 윤곽 밀집 영역 |

---

## 4. OCR 전략 매트릭스

`source_type` × `layout_type` × `detected_languages` × `has_formula` 조합으로 전략 자동 선택

| 전략 ID | 조건 | 처리 방법 | PSM | 언어 |
|---------|------|-----------|-----|------|
| `DIGITAL_EXTRACT` | DIGITAL + SINGLE | pdfplumber `extract_text()` | — | — |
| `DIGITAL_EXTRACT_MULTI` | DIGITAL + MULTI | 컬럼 갭 탐지 → pre-body 헤더 분리 → 좌/우 추출 → 경계 문장 연결 | — | — |
| `OCR_ENG` | SCANNED + SINGLE + eng only | 페이지별 적응형 OCR | 3 | eng |
| `OCR_KOR_ENG` | SCANNED + SINGLE + kor | 페이지별 적응형 OCR | 3 | kor+eng |
| `OCR_JPN` | SCANNED + SINGLE + jpn | 페이지별 적응형 OCR | 3 | kor+eng+jpn |
| `OCR_MULTI_COL` | SCANNED + MULTI + 수식 없음 | 페이지별 적응형 OCR | 6 | kor+eng |
| `OCR_FORMULA` | SCANNED + 수식 있음 | 수식 영역 마스킹 + 페이지별 적응형 OCR | 6 (oem 1) | kor+eng |
| `OCR_MULTI_FORMULA` | SCANNED + MULTI + 수식 | 컬럼 분할 + 수식 마스킹 | 6 (oem 1) | kor+eng |

> **전략 우선순위**: DIGITAL_EXTRACT_MULTI > DIGITAL_EXTRACT > 수식 처리 > 다단 처리 > 언어별 단일컬럼

> **중요**: 모든 스캔 전략은 `_ocr_adaptive()`를 공통 경로로 사용하며, 전략 선택은 "어떤 Tesseract 설정(언어/PSM)을 사용할지"만 결정한다. **레이아웃은 항상 페이지별로 독립 감지**하므로 SINGLE_COLUMN으로 분류된 문서도 다단 페이지가 있으면 해당 페이지에서 자동 분할된다.

### DIGITAL_EXTRACT_MULTI 동작 원리 (v0.9.15a 기준)

```python
# 페이지별 처리
page = filter_watermarks(page)              # 워터마크 문자 제거 (v0.9.15)
words = page.extract_words()
gap_x = _find_column_gap(words, pw)

if gap_x:
    academic = _extract_academic_header(page, gap_x)  # gap_x 전달 (v0.9.10)
    if academic:
        return academic                     # 논문 형식 → 구조화 결과 반환

    left  = [w for w in words if x_mid(w) < gap_x]
    right = [w for w in words if x_mid(w) >= gap_x]

    body_start_y = _find_body_start_y(left, right)   # 본문 시작 y 탐지 (v0.9.12)
    text = _compose_multicol_text(left, right, body_start_y)
    # _compose_multicol_text 내부에서 _join_columns() 호출 (v0.9.14)
    # → 좌 컬럼 마지막 단락 미종결 + 우 컬럼 첫 단락 소문자 시작 시 공백 연결
else:
    text = page.extract_text()              # 갭 미탐지 시 폴백
```

---

## 5. 시스템 아키텍처

### 메인 파이프라인

```
[PDF 탐지] → File Scanner (디렉토리 감시)
    │
    ▼
[PDF 분류 분석] ── PDF Analyzer (pages[:5] 분석)
    ├── source_type  : DIGITAL / SCANNED
    ├── layout_type  : SINGLE_COLUMN / MULTI_COLUMN
    ├── languages    : {kor, eng, jpn}
    └── has_formula  : True / False
    │
    ▼
[OCR 전략 결정] ── Strategy Selector
    │
    ├─ DIGITAL + SINGLE ───────────────────► [pdfplumber 직접 추출]
    │                                          └─ filter_watermarks() 적용
    ├─ DIGITAL + MULTI  ───────────────────► [워터마크 제거 → 컬럼 인식 분리 추출]
    │                                          └─ _find_body_start_y / _compose_multicol_text
    │                                          └─ _join_columns() 경계 문장 연결
    │
    └─ SCANNED (모두 _ocr_adaptive 경유)
          ├─ 페이지별 LayoutSplitter._split_page() 호출
          │    ├─ 단일 → [img]
          │    └─ 다단 → [left_img, right_img]
          ├─ 수식 있음 → FormulaHandler 마스킹 후 OCR
          └─ Tesseract (전략 PSM·언어 적용)
    │
    ▼
[OCR 품질 검사]
    ├─ 품질 충족 (≥50자, score≥0.5) ──► [TEXT_READY]
    └─ 품질 미충족 ──────────────────► [REVIEW_REQUIRED]
    │
    ▼
[텍스트 전처리] → normalize_hard_linebreaks() → [API 업로드] → [색인 처리]
                                                                  │
                                                    AnythingLLM update-embeddings (동기 API)
                                                    → PARSING_DONE → CHUNKING_DONE
                                                    → EMBEDDING_DONE → INDEXED ✓
```

---

## 6. 핵심 모듈

| 모듈 | 파일 | 역할 | 비고 |
|------|------|------|------|
| **Config** | `src/config.py` | config.yaml + .env 통합 로딩 | 싱글턴 `config` |
| **File Scanner** | `src/file_scanner.py` | 디렉토리 감시, 신규 PDF 탐지 및 DB 등록 | MD5 해시 중복 비교 |
| **PDF Analyzer** | `src/pdf_analyzer.py` | source_type/layout_type/언어/수식 분류 | 줄 커버리지 알고리즘, pages[:5] |
| **Strategy Selector** | `src/strategy_selector.py` | `PDFProfile` → `ocr_strategy` 결정 | 8가지 전략 |
| **OCR Engine** | `src/ocr_engine.py` | 전략별 텍스트 추출 및 품질 점수 산출 | `filter_watermarks()`, `_join_columns()`, `_find_body_start_y()`, `_compose_multicol_text()`, `_luminance()` 포함 |
| **Layout Splitter** | `src/layout_splitter.py` | OpenCV 기반 다단 컬럼 ROI 분리 | `_split_page()`: 3% 스무딩, valley < side_mean×0.35, **side_mean<0.01 가드** (v0.9.10) |
| **Formula Handler** | `src/formula_handler.py` | 수식 영역 감지·마스킹 | 분수선·소형 윤곽 패턴 |
| **Retry Handler** | `src/retry_handler.py` | 재시도 로직, Exponential Backoff | `sleep_fn=None` 기본값 (테스트 패치 가능) |
| **Preprocessor** | `src/preprocessor.py` | 추출 텍스트 정제 | `normalize_hard_linebreaks()` 포함 (v0.9.13): 소프트랩 병합, 리스트·캡션 보존, 노이즈 줄 제거 |
| **API Client** | `src/api_client.py` | AnythingLLM 업로드 | raw-text API |
| **Status Tracker** | `src/status_tracker.py` | 색인 상태 전이 관리 | 동기 처리: embed 완료 즉시 INDEXED |
| **Database Manager** | `src/database.py` | `documents` 및 `process_logs` 테이블 관리 | `reset_all_for_reocr()`, WAL 모드 |
| **Page DB** | `src/page_db.py` | `document_metadata`·`page_contents` 테이블 관리 | 신규 (Phase 12) |
| **Log Setup** | `src/log_setup.py` | 타임스탬프 로그 파일 생성 및 root logger 초기화 | `setup_logging()` / `get_log_files()`, 중복 초기화 방지 |
| **Pipeline** | `src/pipeline.py` | Phase 2→9 오케스트레이터 | `PipelineStats` 반환 |
| **Streamlit UI** | `app.py` | 파이프라인 제어 및 문서 관리 UI | **v0.9.15a**, 시작 시 `setup_logging()` 자동 호출 |

---

## 7. Streamlit UI 기능 명세

### 7.1 공통 레이아웃

| 영역 | 내용 |
|------|------|
| 상단 우측 | **버전 배지** (`v0.9.15a`) + **최종 수정 시각** (app.py mtime 자동 갱신) |
| 사이드바 상단 | 버전 + 수정 시각 표시 |
| 사이드바 | 파이프라인 실행 / 새로고침 / **전체 재OCR** 버튼 |
| 메인 | 요약 지표 5개 (전체·INDEXED·검토 필요·실패·대기) + 탭 5개 |

### 7.2 탭 구성

| 탭 | 기능 |
|----|------|
| **📋 전체 파일 목록** | 파일명 검색, 상태 필터, 문서별 상세 정보, 재처리 버튼 |
| **⚠️ 검토 필요** | REVIEW_REQUIRED 문서 목록 + 즉시 재처리 |
| **❌ 실패 목록** | FAILED 문서 목록 + 재처리 / NEW 초기화 |
| **📊 대시보드** | 상태별 분포, PDF 분류 통계, OCR 전략 분포, 품질 점수 구간 통계 |
| **🔬 PDF 분석 진단** | 파일 경로 입력 → 페이지별 줄 커버리지 차트 + 갭 위치 + 감지 결과 표시 |

### 7.3 데이터 캐시 설정

| 대상 | TTL |
|------|-----|
| 문서 목록 (`load_documents`) | 5초 |
| 처리 로그 (`load_logs`) | 10초 |
| PDF 페이지 이미지 (`get_pdf_page_image_bytes`) | 600초 |
| PDF 페이지 수 (`get_pdf_page_count`) | 600초 |
| 진단 분석 (`_diag_analyze`) | 60초 |

### 7.4 PDF 뷰어 (원본/OCR 비교)

- 문서 행의 **📖 원본/OCR 비교** 체크박스로 활성화
- 레이아웃: 전체 너비를 **좌(원본 PDF) / 우(OCR 텍스트)** 50:50 분할
- 네비게이션 바: 문서 바로 위에 **반투명 글래스(backdrop-filter: blur)** 스타일로 표시
  - ⏮ ◀ [페이지 번호 직접 입력] / 전체 페이지 | 전략 ▶ ⏭
- `DIGITAL_EXTRACT_MULTI` 전략: `_compose_multicol_text()` 재사용 (ocr_engine.py와 동일 로직)

**Phase 12 이후 뷰어 텍스트 소스 (우측 패널)**

| 조건 | 텍스트 소스 |
|------|-------------|
| `page_contents` DB에 해당 페이지 존재 | DB의 `body_text` 직접 반환 (빠름·일관성) |
| DB 미존재 (미처리 문서, 구버전) | 기존 `extract_text_page()` 직접 추출 폴백 |

- DB에서 읽을 경우 헤더·푸터는 우측 패널에 표시하지 않음
- 표시 내용이 파이프라인이 AnythingLLM에 업로드한 본문과 동일하여 RAG 결과와 일치

#### 텍스트 추출 고도화 (v0.9.6 ~ v0.9.9)

**헤더/푸터 분리 (`_split_header_footer`)**

| 상수 | 값 | 의미 |
|------|----|------|
| `_HEADER_PCT` | 0.08 | 페이지 상단 8% → 헤더 영역 |
| `_FOOTER_PCT` | 0.93 | 페이지 하단 7% → 푸터 영역 |

단어 목록을 (header_words, main_words, footer_words) 세 그룹으로 분리하여 각각 `[헤더]` / 본문 / `[푸터]` 레이블로 출력한다.

**학술 논문 구조 인식 (`_extract_academic_header`)**

논문 형식 PDF의 페이지 0에 대해 다음 섹션을 자동 인식하여 구조화된 텍스트를 반환한다.

```
[헤더]  — 저널명 / 권호 (페이지 상단 헤더 영역)
[제목]  — 논문 제목 (body_size × 1.5 이상 폰트, 전폭 또는 좌측 전용 배치)
[저자]  — 저자 목록 (제목 직하 전폭 줄)
[소속]  — 소속 기관·주소·이메일·Corresponding Author
[본문]  — 초록, 색인어, 본문 섹션
[푸터]  — 페이지 하단 푸터 영역
```

소속 감지 우선순위:
1. **Primary**: `page.lines`에서 각주 구분 수평선(height < 2pt, width < page_mid_x, top > 60%) 탐지
2. **Fallback**: `body_size - 1.5pt` 미만 소폰트 글자 클러스터(좌측 컬럼, 페이지 하단 30%)

`in_skip(top, x0)` 함수에 `x0` 파라미터를 추가하여, 같은 y 범위라도 x0 위치로 소속(좌측)과 본문(우측)을 분리한다.

**다단 문장 연결 (`_connect_columns` → `_join_columns` 으로 대체)**

`_connect_columns`(app.py)는 v0.9.7에 추가되었으나 v0.9.14에서 `ocr_engine._join_columns()`로 통합되었다. 현재 app.py의 뷰어 경로도 `_compose_multicol_text()`를 재사용하여 동일 로직을 공유한다.

**DIGITAL_EXTRACT_MULTI 저자 분리 버그 수정 (v0.9.9)**

`gap_x` 탐지 후 컬럼 분리 이전에 `_extract_academic_header(page)`를 먼저 호출하도록 순서를 변경했다. 전폭 배치된 제목·저자 줄이 `gap_x` 기준으로 좌/우로 분할되는 문제를 방지한다.

### 7.5 전체 재OCR

- 사이드바 **🔁 전체 재OCR** 버튼 → `db.reset_all_for_reocr()` 호출
- NEW·ANALYZING 이외 모든 문서를 NEW로 초기화 (OCR 결과·업로드 정보·retry_count 초기화)
- 이후 ▶ 파이프라인 실행으로 재처리 시작

### 7.6 DIGITAL_EXTRACT_MULTI 2단 읽기 순서 보정 (v0.9.10~v0.9.12)

**배경**: 제목·저자가 좌측 컬럼에만 있는 논문에서 우측 컬럼 도입부가 좌측 본문보다 앞에 출력되는 문제.

**`_find_body_start_y(left_words, right_words)`** (ocr_engine.py, v0.9.12)

```
슬라이딩 윈도우(3줄) 방식으로 양쪽 컬럼에 모두 단어가 있는 첫 y 좌표를 탐지한다.
→ 이 y 이전 구간 = pre-body (전폭 헤더 또는 좌측 전용 섹션)
→ 이 y 이후 구간 = 실제 2단 본문
```

**`_compose_multicol_text(left_words, right_words, body_start_y)`** (ocr_engine.py, v0.9.12)

```
1. y < body_start_y 구간:
   - 양쪽 컬럼에 걸친 단어(전폭 줄) → 헤더로 처리
   - 한쪽에만 있는 단어 → 좌측 전용 섹션으로 처리
2. y >= body_start_y 구간:
   - 좌측 컬럼 전체 → 우측 컬럼 전체 순서로 조합
3. _join_columns()로 경계 문장 연결 (v0.9.14)
```

**`layout_splitter.py` side_mean < 0.01 가드** (v0.9.10)

잉크가 거의 없는 빈 페이지(커버·뒷면 등)에서 false positive 컬럼 탐지를 방지한다.

**`_extract_academic_header(page, gap_x)`** gap_x 파라미터 추가 (v0.9.10)

좌측 컬럼 chars만으로 상태 기계 실행. `full_width_title` 판별(좌측 전용 vs 전체 폭), 서브스크립트 오탐 방지 최소 8pt 조건, HARD_TOP=4% 섹션 라인 제외.

### 7.7 디지털 PDF 워터마크 자동 제거 (v0.9.15~v0.9.15a)

pdfplumber `page.filter()`를 활용하여 텍스트 추출 전에 워터마크 문자를 제거한다.

**`_luminance(color)` 함수**

| 색상 공간 | 변환 방식 |
|---------|----------|
| DeviceGray | `tuple[0]` 그대로 사용 |
| RGB | `0.2126R + 0.7152G + 0.0722B` (Rec.709) |
| CMYK | `1 − min(1, K + max(C, M, Y))` 근사 |

**`filter_watermarks(page, light_threshold=0.80, stamp_size_pt=30.0, stamp_lum_min=0.15)`**

다음 조건 중 하나라도 해당하는 문자를 제거한다.

| 조건 | 대상 |
|------|------|
| `upright == False` | 대각선 스탬프 (회전 렌더) |
| `non_stroking_color` 밝기 > `light_threshold` | 연한 배경 채우기 워터마크 |
| `stroking_color` 밝기 > `light_threshold` | 획(stroke) 전용 렌더 워터마크 |
| `size > stamp_size_pt` AND `non_stroking_color` 밝기 > `stamp_lum_min` | 대형 스탬프 낱글자 (CTM 회전으로 upright=True인 경우) |

**적용 범위**: `_extract_digital()`, `_extract_digital_multicol()` (ocr_engine.py), DIGITAL_EXTRACT·DIGITAL_EXTRACT_MULTI 뷰어 경로 (app.py).

**알려진 한계**: JEDEC 표준 문서의 법적 고지 텍스트("PLEASE! DON'T VIOLATE THE LAW!")는 `fill=(0.0,)`, `upright=True`, `size≈12pt`로 일반 본문과 동일한 속성이므로 이 방법으로 제거 불가. Phase 11에서 전처리 단계 키워드 기반 라인 제거로 해결 예정.

### 7.8 컬럼 경계 문장 자동 연결 (v0.9.14)

**`_join_columns(left_text, right_text)`** (ocr_engine.py)

좌측 컬럼 마지막 단락과 우측 컬럼 첫 단락이 같은 문장의 연속인 경우 공백으로 이어붙인다.

| 조건 | 처리 |
|------|------|
| 좌측 끝 문자 ∉ `.?!;` AND 우측 첫 문자가 소문자 | `left + " " + right` |
| 좌측이 `-`로 끝남 | 하이픈 제거 후 직접 연결 |
| 그 외 | `left + "\n\n" + right` |

`_compose_multicol_text()`가 `"\n\n".join()` 대신 이 함수를 사용한다.

**dead code 제거**: app.py에서 `_compose_multicol_text()` 도입 이전에 잔류하던 `_connect_columns()` 호출 블록(좌·우 분리 후 즉시 덮어씌워지던 621~629줄)을 제거했다.

### 7.9 텍스트 줄바꿈 정규화 (v0.9.13)

**`normalize_hard_linebreaks(text)`** (preprocessor.py)

PDF에서 추출된 텍스트의 인위적 줄바꿈을 정규화한다.

| 규칙 | 동작 |
|------|------|
| `_should_merge_soft_wrap()` | 하이픈 이음, 소문자 시작, 긴 줄 등 → 줄 병합 |
| `_should_keep_linebreak()` | 리스트 항목(`- `, `• `, `1. `), 캡션(`Fig.`, `Table`), 단락 제목 → 줄 유지 |
| `_NOISE_LINE_RE` | 의미 없는 특수문자만 있는 줄 → 제거 |

`Preprocessor._clean()` 내에서 `normalize_hard_linebreaks()` 호출. app.py의 `reflow_ocr_text()`도 이 함수에 위임한다.

---

## 8. 상태 설계

### 상태 흐름도

```
NEW
 │
 ▼
ANALYZING ── PDF 분류 분석 중
 │
 ├──► TEXT_EXTRACTED ──┐   (DIGITAL, 단일 컬럼 직접 추출 성공)
 ├──► TEXT_EXTRACTED ──┤   (DIGITAL, 다단 컬럼 인식 추출 성공)
 │                     │
 └──► OCR_DONE ────────┤   (SCANNED, OCR 완료)
       │               │
       ├──► TEXT_READY ◄┘
       │       │
       │       ▼
       │  UPLOAD_REQUESTED
       │       │
       │       ▼
       │  UPLOADED
       │       │
       │       ▼  (AnythingLLM update-embeddings 동기 API — 단일 호출로 일괄 전이)
       │  PARSING_DONE → CHUNKING_DONE → EMBEDDING_DONE → INDEXED ✓
       │
       └──► REVIEW_REQUIRED ──► (수동 전이) ──► TEXT_READY 또는 FAILED

FAILED ◄──── (오류 발생 시 모든 단계에서 전이 가능)
  └──► 수동 재처리 시 NEW로 초기화 (reset_for_retry)
  └──► 전체 재OCR 시 모든 문서 NEW 초기화 (reset_all_for_reocr)
```

### 상태 설명

| 상태 | 설명 |
|------|------|
| `NEW` | 파일 탐지 직후 최초 등록 상태 |
| `ANALYZING` | PDF 분류 분석 진행 중 |
| `TEXT_EXTRACTED` | DIGITAL 문서 텍스트 추출 성공 (단일/다단 포함) |
| `OCR_DONE` | Tesseract OCR 처리 완료 |
| `REVIEW_REQUIRED` | OCR 품질 미충족, 수동 검토 필요 (`RETRYABLE` 포함) |
| `TEXT_READY` | 전처리 완료, API 업로드 대기 |
| `UPLOAD_REQUESTED` | API 업로드 요청 발송 |
| `UPLOADED` | API 업로드 성공, `anythingllm_doc_id` 수신 |
| `PARSING_DONE` | AnythingLLM 파싱 완료 (동기 처리 중 순차 기록) |
| `CHUNKING_DONE` | 청킹 완료 (동기 처리 중 순차 기록) |
| `EMBEDDING_DONE` | 임베딩 완료 (동기 처리 중 순차 기록) |
| `INDEXED` | 색인 완전 완료, 검색 가능 |
| `FAILED` | 처리 중 오류, `error_message` 및 `failed_step` 기록 (`RETRYABLE` 포함) |

> `Status.RETRYABLE = {FAILED, REVIEW_REQUIRED}` — UI에서 즉시 재처리 / NEW 초기화 버튼 표시 대상

---

## 9. 데이터베이스 설계

### 9.1 `documents` 테이블

```sql
CREATE TABLE documents (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path           TEXT    NOT NULL UNIQUE,
    file_name           TEXT    NOT NULL,
    file_hash           TEXT,
    status              TEXT    NOT NULL DEFAULT 'NEW',

    source_type         TEXT,              -- DIGITAL / SCANNED
    layout_type         TEXT,              -- SINGLE_COLUMN / MULTI_COLUMN
    detected_languages  TEXT,              -- "kor,eng" / "kor,eng,jpn"
    has_formula         INTEGER DEFAULT 0,
    ocr_strategy        TEXT,              -- DIGITAL_EXTRACT / DIGITAL_EXTRACT_MULTI / OCR_* 등

    anythingllm_doc_id  TEXT,
    ocr_quality_score   REAL,
    retry_count         INTEGER NOT NULL DEFAULT 0,
    error_message       TEXT,
    failed_step         TEXT,

    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME
);
```

> SQLite WAL 모드 + `PRAGMA foreign_keys=ON` 적용. 인덱스: `status`, `file_hash`, `document_id`.

### 9.2 `process_logs` 테이블

```sql
CREATE TABLE process_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER  NOT NULL,
    step         TEXT     NOT NULL,
    result       TEXT     NOT NULL,   -- SUCCESS / FAILURE / REVIEW_REQUIRED
    message      TEXT,
    logged_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);
```

### 9.3 `document_assets` 테이블 (기존 유지)

```sql
-- asset_db.py 관리 — Phase 11에서 추가됨
CREATE TABLE document_assets (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER NOT NULL,
    file_path    TEXT,
    page_num     INTEGER,
    asset_type   TEXT NOT NULL,     -- TABLE / FIGURE
    seq_in_doc   INTEGER,
    ref_tag      TEXT UNIQUE,       -- [TABLE_001] / [FIGURE_001]
    bbox_x0      REAL, bbox_y0 REAL, bbox_x1 REAL, bbox_y1 REAL,
    image_data   BLOB,
    ocr_text     TEXT,
    caption      TEXT,
    created_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);
```

### 9.4 `document_metadata` 테이블 (Phase 12 신규)

```sql
CREATE TABLE document_metadata (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   INTEGER NOT NULL UNIQUE,
    title         TEXT,
    authors       TEXT,    -- JSON 배열: ["Author A", "Author B"]
    affiliations  TEXT,    -- JSON 배열: ["Inst A", "Inst B"]
    abstract      TEXT,
    keywords      TEXT,
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);
```

> 문서 레벨 메타데이터. 학술 논문은 `_extract_academic_header()` 결과로 채움.  
> 일반 문서(기술 사양서 등)는 title만 채우고 나머지는 NULL.

### 9.5 `page_contents` 테이블 (Phase 12 신규)

```sql
CREATE TABLE page_contents (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id   INTEGER NOT NULL,
    page_num      INTEGER NOT NULL,
    header_text   TEXT,    -- 페이지 상단 8% 영역 텍스트
    footer_text   TEXT,    -- 페이지 하단 7% 영역 텍스트
    body_text     TEXT,    -- 본문 (헤더·푸터·그림/표 캡션 제외, ref_tag 포함)
    created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (document_id, page_num),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);
```

> `body_text`는 캡션이 ref_tag([FIGURE_001] 등)으로 치환된 상태.  
> 헤더/푸터는 AnythingLLM 업로드 대상에서 제외; body_text만 업로드.  
> 뷰어에서 현재 페이지 조회 시 이 테이블에서 직접 읽음.

### 9.6 주요 DB 메서드

| 메서드 | 설명 |
|--------|------|
| `register_document()` | 신규 문서 등록 (UNIQUE 제약으로 중복 방지) |
| `update_analysis()` | 분류 분석 결과 저장 |
| `update_status()` | 상태 및 부가 정보 갱신 |
| `increment_retry()` | retry_count 1 증가 |
| `reset_for_retry()` | FAILED/REVIEW_REQUIRED → NEW |
| `reset_all_for_reocr()` | 전체 문서 NEW 초기화 → 반환값: 초기화 건수 |
| `log_step()` | 단계별 처리 로그 기록 |

---

## 10. 설정 파라미터 (config.yaml)

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `watch_dir` | `data/pdf_watch` | PDF 감시 디렉토리 |
| `ocr.min_text_length` | `50` | DIGITAL 판정 기준 (페이지당 평균 글자 수) / OCR 품질 최소 길이 |
| `ocr.quality_threshold` | `0.5` | OCR 품질 점수 하한 (유효 문자 비율) |
| `ocr.tesseract_cmd` | (경로 지정) | tesseract 실행 파일 경로 |
| `ocr.tessdata_dir` | (경로 지정) | tessdata-best 디렉토리 경로 |
| `database.path` | `data/rag_project.db` | SQLite DB 파일 경로 |
| `anythingllm.base_url` | `http://localhost:3001` | AnythingLLM 서버 URL (.env 우선) |
| `anythingllm.api_key` | — | API 키 (.env 우선) |
| `anythingllm.workspace` | — | 대상 워크스페이스 (.env 우선) |
| `anythingllm.upload_timeout` | `30` | 업로드 요청 타임아웃 (초) |
| `indexing.poll_interval` | `10` | 색인 폴링 간격 (초, 현재 미사용) |
| `indexing.timeout` | `300` | 색인 최대 대기 시간 (초, 현재 미사용) |
| `retry.max_attempts` | `3` | API 최대 재시도 횟수 |
| `retry.backoff_seconds` | `[60, 120, 240]` | 재시도 대기 시간 (1분·2분·4분) |
| `logging.level` | `INFO` | 로그 레벨 |
| `logging.file` | `logs/rag_project.log` | 로그 파일 경로 |

---

## 11. 개발 단계 (Phase)

| Phase | 내용 | 상태 | 비고 |
|-------|------|------|------|
| **Phase 0** | 환경 설정 | ✅ 완료 | Tesseract, AnythingLLM API |
| **Phase 1** | DB 초기화 | ✅ 완료 | 테이블, 인덱스, 마이그레이션 |
| **Phase 2** | PDF 탐지 및 DB 저장 | ✅ 완료 | File Scanner, 중복 hash 비교 |
| **Phase 3** | PDF 분류 분석 | ✅ 완료 | source_type, 레이아웃, 언어, 수식 |
| **Phase 4** | OCR 전략 선택 및 실행 | ✅ 완료 | Strategy Selector, Layout Splitter, Formula Handler |
| **Phase 5** | 텍스트 전처리 | ✅ 완료 | 정제, 최소 길이 검증 |
| **Phase 6** | API 연동 | ✅ 완료 | AnythingLLM raw-text 업로드 |
| **Phase 7** | 색인 상태 추적 | ✅ 완료 | 동기 처리 기반 상태 전이 |
| **Phase 8** | 재시도 로직 | ✅ 완료 | 최대 3회, Exponential Backoff |
| **Phase 9** | Streamlit UI | ✅ 완료 | 전체 기능 구현 (v0.9.1) |
| **Phase 10** | E2E 통합 검증 | ✅ 완료 | 37건 전체 INDEXED, 9/9 기준 통과 (2026-04-17) |
| **기능 추가** | 로그 파일 관리 | ✅ 완료 | `src/log_setup.py`, `logs/log_yyyymmdd_hhmmss.log` 자동 생성 (2026-04-17) |
| **기능 추가** | PDF 뷰어 OCR 표시 고도화 | ✅ 완료 | 헤더/푸터 분리, 학술 논문 구조 인식, 소속 블록 분리, 다단 문장 연결, 저자 분리 버그 수정 (v0.9.6→v0.9.9, 2026-04-17) |
| **기능 추가** | DIGITAL_EXTRACT_MULTI 2단 읽기 순서 보정 | ✅ 완료 | `_find_body_start_y()`, `_compose_multicol_text()`, `side_mean<0.01` 가드, `_extract_academic_header(gap_x)` 확장 (v0.9.10→v0.9.12, 2026-04-27) |
| **기능 추가** | 텍스트 줄바꿈 정규화 고도화 | ✅ 완료 | `normalize_hard_linebreaks()` (v0.9.13, 2026-04-27) |
| **기능 추가** | 컬럼 경계 문장 자동 연결 + dead code 제거 | ✅ 완료 | `_join_columns()` (v0.9.14, 2026-05-27) |
| **기능 추가** | 디지털 PDF 워터마크 자동 제거 | ✅ 완료 | `filter_watermarks()`, `_luminance()` (v0.9.15→v0.9.15a, 2026-05-27) |
| **Phase 11** | OCR 품질 고도화 및 미검증 전략 검증 | 🔲 이월 | SCANNED 검증, OCR_FORMULA 검증, JEDEC 워터마크 후처리, JESD270-4 재처리. **대규모 운영 전 스캔 문서 샘플 확보 후 진행 권장** |
| **Phase 12** | 문서 구조 분리 저장 및 뷰어 연동 | 🔲 예정 | document_metadata·page_contents 신규 테이블, 파이프라인 구조화 저장, 뷰어 DB 우선 조회 |
| **Phase 13** | 대규모 배치 처리 및 부하 제어 | 🔲 예정 | 6,000건 초기 일괄 처리 + 지속 추가 구조 대응. 상세 내용은 섹션 19 참조 |
| **Phase 14** | PDF 예외 유형 강화 | 🔲 예정 | 표 추출 품질, 2단 컬럼 오인식, 이미지 삽입 페이지, 암호화 PDF 처리. 상세 내용은 섹션 20 참조 |

---

## 12. 평가 기준 및 Phase 10 실측값

| 항목 | 목표 기준 | Phase 10 실측값 | 결과 |
|------|-----------|-----------------|------|
| PDF 탐지 정확도 | 지정 폴더 내 PDF 100% 탐지 | 37/37건 (100%) | ✅ |
| DIGITAL/SCANNED 판별 | 95% 이상 정확 판별 | 전건 DIGITAL 정확 분류 | ✅ (한계: SCANNED 샘플 없음) |
| 다단 레이아웃 판별 | 90% 이상 | MULTI 7건 정확 감지 | ✅ |
| OCR 처리 성공률 | ≥90% TEXT_READY 도달 | **100.0%** (37/37) | ✅ |
| DIGITAL 다단 추출 품질 | quality score ≥ 0.95¹ | 최솟값 **0.9528** (평균 0.9818) | ✅ |
| API 업로드 성공률 | ≥95% UPLOADED 도달 | **100.0%** (37/37) | ✅ |
| INDEXED 완료율 | ≥85% | **100.0%** (37/37) | ✅ |
| FAILED 비율 | ≤5% | **0.0%** | ✅ |
| 평균 처리 시간 | ≤300초/건 | **40.5초** (최대 190초) | ✅ |

> ¹ ≥0.95는 한국어 문서 기준. 영문 논문 기준은 ≥0.99 (실측 HBM DRAM IEEE paper = 0.9941).

### 이월 항목 (Phase 11)

| 항목 | 현황 |
|------|------|
| SCANNED 문서 검증 | 샘플 없음 — Tesseract OCR 경로 미검증 |
| 수식 포함 문서 검증 | 샘플 없음 — OCR_FORMULA/OCR_MULTI_FORMULA 미검증 |
| 한국어/일본어 샘플 부족 | 한국어 3건, 일본어 0건 |
| AnythingLLM 미등록 문서 1건 | JESD270-4_HBM4 Test Standard.pdf 재처리 필요 |
| JEDEC 법적 고지 텍스트 | fill=0.0·upright=True·12pt → color/rotation 필터 불가, 전처리 키워드 라인 제거로 Phase 11 해결 예정 |

---

## 13. 추가 패키지

```
pdfplumber>=0.10       # PDF 텍스트·단어 위치 추출, page.filter() (워터마크 제거)
pypdfium2>=4.0         # PDF → PIL 이미지 변환 (Poppler 불필요)
opencv-python>=4.9     # 레이아웃 분석, 컬럼 분할, 수식 감지
langdetect>=1.0.9      # 텍스트 언어 감지 보정
pytesseract>=0.3       # Tesseract OCR Python 래퍼
streamlit>=1.30        # 웹 UI
numpy>=1.26            # 수치 계산 (커버리지 프로파일)
pyyaml>=6.0            # config.yaml 로딩
python-dotenv>=1.0     # .env 환경변수 로딩
```

---

## 14. 테스트 현황

| 테스트 모듈 | 테스트 수 | 상태 |
|-------------|-----------|------|
| `test_pdf_analyzer.py` | ~60 | ✅ 전체 통과 |
| `test_pipeline.py` | ~79 | ✅ 전체 통과 |
| `test_preprocessor.py` (TestLayoutAwareLinebreaks 추가) | 보강 | ✅ 전체 통과 |
| `test_ocr_engine.py` (_compose_multicol_text·_find_body_start_y 추가) | 보강 | ✅ 전체 통과 |
| **합계** | **139+** | **✅ 전체 통과** |

### 주요 테스트 항목

- `TestSourceTypeDetection`: DIGITAL/SCANNED 판별, pdfplumber 실패 폴백
- `TestLayoutDetection`: 단일/다단 판별 (디지털·스캔, 10줄 다중 모의 데이터)
- `TestLanguageDetection`: 한글·영문·일문·혼합 언어 감지
- `TestFormulaDetection`: 수식 유무 감지 (디지털·스캔)
- `TestStrategySelector`: 8개 전략 모든 조합 검증 (`DIGITAL_EXTRACT_MULTI` 포함)
- `TestPipeline`: 전체 파이프라인 통합 (재시도, 타임아웃, 상태 전이)
- `TestLayoutAwareLinebreaks`: `normalize_hard_linebreaks()` 소프트랩 병합·리스트 보존·노이즈 제거
- `TestComposeMulticol`: `_compose_multicol_text()` / `_find_body_start_y()` 단위 검증

---

## 15. v7.0 주요 변경 이력 (2026-04-27 ~ 2026-05-27)

### 15.1 DIGITAL_EXTRACT_MULTI 2단 읽기 순서 보정

**문제**: 제목·저자가 좌측 컬럼에만 있는 논문에서 우측 컬럼 도입부가 좌측 본문 앞에 출력됨.

**해결**:

| 버전 | 내용 |
|------|------|
| v0.9.10 | `layout_splitter.py` `side_mean < 0.01` 가드 추가 (빈 페이지 false positive 방지). `_extract_academic_header(page, gap_x)` 확장 — 좌측 컬럼 chars만으로 상태 기계 실행, `full_width_title` 판별 |
| v0.9.11 | `_find_body_start_y()` 1차 구현 (슬라이딩 윈도우, Codex) |
| v0.9.12 | `_compose_multicol_text()` 도입 — pre-body 헤더 분리 + 좌/우 본문 순서 보정 최종 수정 |

**검증**: ISSCC 2016 HBM DRAM 논문(좌측 전용 제목/저자) · HBM3 Interface 논문(전체 폭 제목/저자) 모두 정상 추출 확인.

### 15.2 텍스트 줄바꿈 정규화 고도화 (v0.9.13)

`Preprocessor._clean()`에 `normalize_hard_linebreaks()` 통합. PDF 2단 추출 과정에서 삽입된 인위적 줄바꿈을 문맥 기반으로 정규화한다.

- `app.py`: `reflow_ocr_text()` → `normalize_hard_linebreaks()` 위임 호출

### 15.3 컬럼 경계 문장 자동 연결 및 dead code 제거 (v0.9.14)

- `_join_columns()` 신규 구현 — `_compose_multicol_text()`에서 `"\n\n".join()` 대신 사용
- app.py 621~629줄 dead code 제거: `_connect_columns()` 호출 후 즉시 `_compose_multicol_text()`로 덮어씌워지던 불필요 코드

### 15.4 디지털 PDF 워터마크 자동 제거 (v0.9.15~v0.9.15a)

| 버전 | 변경 내용 |
|------|-----------|
| v0.9.15 | `_luminance()` + `filter_watermarks()` 신규 구현. upright=False 문자, 밝기>0.85 문자 제거. ocr_engine.py 양 경로 + app.py 뷰어 경로 적용 |
| v0.9.15a | `stroking_color` 밝기 체크 추가. `light_threshold` 0.85→0.80. 폰트>30pt + 밝기>0.15 대형 스탬프 조건 추가 |

**미해결**: JEDEC 법적 고지 텍스트 — Phase 11에서 `normalize_hard_linebreaks()` 노이즈 패턴 또는 별도 `remove_jedec_notice()` 함수로 처리 예정.

---

## 16. v6.2 주요 변경 이력 (PDF 뷰어 OCR 텍스트 표시 고도화)

### 16.1 신규 헬퍼 함수

| 함수 | 위치 | 역할 |
|------|------|------|
| `_split_header_footer(page, words)` | `app.py` | 단어 목록 → (header_words, main_words, footer_words) 분리 |
| `_words_one_line(words)` | `app.py` | 단어 목록 → 단일 라인 문자열 (행 바뀜 시 ` \| ` 삽입) |
| `_connect_columns(left_text, right_text)` | `app.py` | ※ v0.9.14에서 `_join_columns()`로 통합됨 |

### 16.2 `_extract_academic_header` 확장

| 항목 | 내용 |
|------|------|
| 신규 섹션 | `[소속]` — 소속 기관, 주소, 이메일, Corresponding Author |
| 소속 감지 1 (Primary) | `page.lines` 수평선: height<2pt, width<page_mid_x, top>60%h |
| 소속 감지 2 (Fallback) | 폰트 크기 `body_size - 1.5pt` 미만 글자 클러스터 (좌측 컬럼) |
| 출력 순서 | `[헤더]` → `[제목]` → `[저자]` → `[소속]` → `[본문]` → `[푸터]` |

### 16.3 app.py 버전 이력

| 버전 | 변경 내용 |
|------|-----------|
| v0.9.6 | `_extract_academic_header` 신규 구현: [헤더]/[제목]/[저자]/[본문] 구조화 |
| v0.9.7 | `_split_header_footer`, `_words_one_line`, `_connect_columns` 추가 |
| v0.9.8 | `_extract_academic_header`에 [소속] 섹션 추가; `page.lines` 수평선 탐지 |
| v0.9.9 | DIGITAL_EXTRACT_MULTI에서 `_extract_academic_header` 우선 호출로 저자 분리 버그 수정 |

---

## 17. v6.0~v6.1 주요 변경 이력

### 17.1 Phase 10 E2E 통합 검증 완료 (v6.0)

| 항목 | 내용 |
|------|------|
| 검증 일자 | 2026-04-17 |
| 검증 문서 수 | 37건 전체 INDEXED |
| 평가 기준 통과 | 9/9 (100%) |
| 측정 평균 처리 시간 | 40.5초/건 (목표 300초 대비 7.4배 여유) |

**이슈 수정**:
- P2-1: evaluate.py 처리 시간 계산 오류 — `created_at`(KST) vs `updated_at`(UTC) 타임존 혼재 → `process_logs` ANALYZE→INDEXING 간격 측정으로 변경
- P2-2: DIGITAL_EXTRACT_MULTI 품질 임계값 0.99 → 0.95 (한국어 문서 기준)

**신규 도구**:

| 도구 | 경로 | 기능 |
|------|------|------|
| 배치 분류 | `tools/classify_all.py` | 전체 PDF 분류 결과 CSV 출력 |
| 결과 평가 | `tools/evaluate.py` | DB 집계 → 9개 평가 기준 측정 → Markdown 보고서 저장 |

**보안 정보 분리**: `config.yaml`에 하드코딩된 `api_key`, `workspace`를 `.env` 파일로 이동. `.gitignore` 신규 생성.

### 17.2 로그 파일 기능 추가 (v6.1)

| 항목 | 내용 |
|------|------|
| 신규 파일 | `src/log_setup.py` |
| 로그 파일 경로 | `logs/log_yyyymmdd_hhmmss.log` (실행 세션마다 자동 생성) |
| 핸들러 | 파일 핸들러 + 콘솔 핸들러 동시 등록 |
| 중복 방지 | `_initialized` 플래그로 콘솔 핸들러 이중 등록 차단 |
| 포맷 | `2026-04-17 16:00:00 [INFO    ] src.module — 메시지` |

**CHANGELOG.md 신규 도입**: v0.1.0 (20260416) ~ 최신까지 전체 개정 이력 관리.

---

## 18. Phase 12 — 문서 구조 분리 저장 및 뷰어 연동

> 기준일: 2026-06-15 / 상태: 🔲 설계 완료, 구현 예정

### 18.1 목표

| # | 목표 | 세부 내용 |
|---|------|-----------|
| 1 | **DB 구조화 저장** | 제목·저자·소속·헤더·푸터·본문을 페이지 단위로 DB에 분리 저장 |
| 2 | **DB 최적화** | document_assets(기존)와 통합 고려한 신규 테이블 설계 |
| 3 | **뷰어 일관성** | 뷰어 우측 패널이 파이프라인 처리 결과(본문)와 동일한 내용을 표시 |

### 18.2 신규 모듈: `src/page_db.py`

```python
# 주요 공개 인터페이스
def init_tables(conn: sqlite3.Connection) -> None
    """document_metadata 및 page_contents 테이블 생성 (IF NOT EXISTS)."""

def save_document_metadata(
    conn, document_id: int, *,
    title: str = "", authors: list[str] = (), affiliations: list[str] = (),
    abstract: str = "", keywords: str = ""
) -> None
    """문서 레벨 메타데이터 저장 (UPSERT)."""

def save_page_content(
    conn, document_id: int, page_num: int, *,
    header_text: str = "", footer_text: str = "", body_text: str = ""
) -> None
    """페이지 구조화 콘텐츠 저장 (UPSERT)."""

def get_page_content(conn, document_id: int, page_num: int) -> dict | None
    """특정 페이지의 구조화 콘텐츠 조회. 없으면 None."""

def get_document_metadata(conn, document_id: int) -> dict | None
    """문서 레벨 메타데이터 조회. 없으면 None."""

def delete_page_contents(conn, document_id: int) -> None
    """문서 재처리 시 기존 page_contents 삭제."""

def delete_document_metadata(conn, document_id: int) -> None
    """문서 재처리 시 기존 document_metadata 삭제."""
```

### 18.3 데이터 흐름 변경

#### 현재 (v1.x)

```
extract all pages → concatenate text → normalize → upload to AnythingLLM
```

#### Phase 12 이후

```
per-page extraction:
  page 0 → _extract_academic_header()
    ├── title, authors, affiliations → document_metadata (DB)
    └── body_text (page 0) → page_contents (DB)
  page N → _split_header_footer() + body extraction
    ├── header_text → page_contents (DB)
    ├── footer_text → page_contents (DB)
    └── body_text   → page_contents (DB)

upload to AnythingLLM:
  ← body_text (page 0..N 합산, 헤더·푸터 제외)
```

#### 업로드 텍스트 품질 향상

| 구분 | 현재 | Phase 12 |
|------|------|-----------|
| 업로드 내용 | 헤더·푸터·본문 혼합 | body_text만 (헤더·푸터 제외) |
| RAG 검색 노이즈 | 저널명·페이지 번호 포함 | 본문만 포함 |
| 뷰어 표시와 일치 여부 | 불일치 가능 | 완전 일치 |

### 18.4 `PageStructure` 데이터클래스 (`src/ocr_engine.py` 추가)

```python
@dataclass
class PageStructure:
    header_text:  str = ""
    footer_text:  str = ""
    body_text:    str = ""
    title:        str = ""            # page 0 전용
    authors:      list[str] = field(default_factory=list)   # page 0 전용
    affiliations: list[str] = field(default_factory=list)   # page 0 전용
```

- 기존 `extract()` 메서드는 `str` 반환으로 그대로 유지 (하위 호환)
- 신규 `extract_structured()` 메서드가 `list[PageStructure]` 반환

### 18.5 파이프라인 변경 (`src/asset_pipeline.py`)

```
process_digital() / process_docx() / process_pptx()
  → extract_structured() 호출
  → per page: save_page_content()
  → page 0: save_document_metadata()
  → 재처리 전: delete_page_contents() + delete_document_metadata()
```

### 18.6 뷰어 변경 (`app.py`)

```python
def extract_text_page(file_path, page_num, strategy):
    # Phase 12: DB 우선 조회
    doc = db.get_document_by_path(file_path)
    if doc:
        pc = page_db.get_page_content(db.conn, doc["id"], page_num)
        if pc and pc["body_text"]:
            return pc["body_text"]   # DB 캐시 히트
    # 폴백: 기존 직접 추출 로직 (미처리 문서 / 구버전 호환)
    ...
```

### 18.7 구현 순서

| 단계 | 파일 | 작업 내용 |
|------|------|-----------|
| 1 | `src/page_db.py` | 신규 생성: 테이블 DDL + CRUD 함수 |
| 2 | `src/database.py` | DB 초기화 시 `page_db.init_tables()` 연결 |
| 3 | `src/ocr_engine.py` | `PageStructure` 데이터클래스 + `extract_structured()` 신규 메서드 |
| 4 | `src/asset_pipeline.py` | `extract_structured()` 호출 및 page_db 저장 연동 |
| 5 | `app.py` | `extract_text_page()` DB 우선 조회 로직 추가 |
| 6 | `app.py` | 문서 목록에 title 컬럼 표시 (document_metadata 참조) |

### 18.8 하위 호환 정책

- `page_contents`에 데이터가 없는 문서(구버전 처리 결과)는 기존 직접 추출 경로로 폴백
- `document_metadata`가 없는 문서는 title 컬럼 빈 칸으로 표시
- 강제 재처리(🔁) 시 page_contents + document_metadata도 함께 삭제 후 재생성

### 18.9 검증 기준

| 항목 | 기준 |
|------|------|
| 구조화 저장 커버리지 | INDEXED 전환 후 모든 문서에 page_contents 레코드 존재 |
| 뷰어 일관성 | DB에서 읽은 body_text가 AnythingLLM 업로드 텍스트와 동일 |
| 헤더·푸터 분리 정확도 | 학술 논문 샘플 5건 수동 검증 (페이지 번호·저널명이 body_text에 미포함) |
| 제목 추출 정확도 | 학술 논문 샘플 5건 (document_metadata.title 일치 여부) |
| 폴백 동작 | page_contents 미존재 문서에서 기존 추출 로직 정상 실행 |

---

## 19. Phase 13 — 대규모 배치 처리 및 부하 제어

> 상태: 🔲 예정 / 배경: 초기 약 6,000건 일괄 처리 + 이후 지속 추가 구조

### 19.1 배경 및 과제

현재 파이프라인은 소규모 검증(37건) 기준으로 설계되어 있어 6,000건 규모에서 다음 문제가 발생할 수 있다.

| 과제 | 내용 |
|------|------|
| 처리 시간 | 평균 40.5초/건 × 6,000건 = 약 67.5시간. 단순 순차 처리 시 3일 소요 |
| AnythingLLM 부하 | 대량 연속 API 호출 시 임베딩 큐 과부하 및 응답 지연 가능 |
| 중단 후 재개 | 처리 중 중단 시 NEW 상태 문서부터 자동 재개되나, 진행률 추적 UI 미비 |
| 디스크 관리 | 추출 텍스트 및 로그 파일 누적 시 용량 관리 필요 |

### 19.2 배치 처리 전략

#### 초기 일괄 처리 (6,000건)

```
전체 PDF를 배치 단위(기본 50건)로 분할하여 순차 처리
  ├── 배치 완료 후 짧은 대기(기본 5초)로 AnythingLLM 안정화
  ├── 각 배치 처리 결과를 process_logs에 기록
  └── 중단 후 재시작 시 NEW 상태 문서만 이어서 처리 (자동 재개)
```

#### 지속 추가 구조 (운영 중)

```
File Scanner 감시 주기: 60초 (config: watch_interval_sec)
  ├── 신규 파일 탐지 → NEW 등록 → 즉시 파이프라인 투입
  └── 우선순위 큐: NEW 신규 파일 > FAILED 재시도 순 처리
```

### 19.3 AnythingLLM 부하 제어

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `batch.size` | `50` | 한 배치당 처리 문서 수 |
| `batch.inter_batch_wait_sec` | `5` | 배치 간 대기 시간 (AnythingLLM 안정화) |
| `batch.inter_doc_wait_sec` | `0.5` | 문서 간 API 호출 간격 |
| `anythingllm.upload_timeout` | `30` | 업로드 타임아웃 (현행 유지) |

> **근거**: Phase 10 실측에서 AnythingLLM 동기 API가 평균 ~1초/건 처리. 0.5초 간격으로 CPU 여유 확보 및 임베딩 큐 과부하 방지.

### 19.4 진행률 추적 UI 추가 (app.py)

| 항목 | 내용 |
|------|------|
| 배치 진행률 바 | 전체 대비 INDEXED 완료 비율 실시간 표시 |
| 예상 잔여 시간 | 최근 10건 평균 처리 시간 기반 ETA 계산 |
| 배치 일시 중지 | 실행 중 일시 중지 / 재개 버튼 추가 |
| 완료 알림 | 배치 처리 완료 시 브라우저 알림 (Streamlit toast) |

### 19.5 설정 파라미터 추가 (config.yaml)

| 파라미터 | 기본값 | 설명 |
|----------|--------|------|
| `batch.size` | `50` | 배치 크기 |
| `batch.inter_batch_wait_sec` | `5` | 배치 간 대기 |
| `batch.inter_doc_wait_sec` | `0.5` | 문서 간 대기 |
| `scanner.watch_interval_sec` | `60` | 파일 감시 주기 |
| `storage.max_log_files` | `30` | 보관할 최대 로그 파일 수 (초과 시 오래된 파일 자동 삭제) |

---

## 20. Phase 14 — PDF 예외 유형 강화

> 상태: 🔲 예정 / 배경: 6,000건 중 예외 케이스가 다수 포함될 가능성 대비

### 20.1 예외 유형 분류

6,000건 규모에서 발생 가능한 예외 PDF 유형과 현재 처리 상태:

| 예외 유형 | 현재 처리 | 개선 방향 |
|-----------|-----------|-----------|
| **표(Table) 많은 문서** | 셀 순서 혼재 가능 | 표 영역 감지 후 행/열 순서 재구성 또는 ref_tag 치환 |
| **2단 컬럼 오인식** | 헤더 전폭 줄로 간혹 SINGLE 오판 | 오판 감지 시 REVIEW_REQUIRED 자동 분류 |
| **텍스트가 이미지로 삽입된 페이지** | 디지털 PDF 판정 후 해당 페이지 빈 텍스트 | 페이지별 텍스트 밀도 재검사 → 부족한 페이지 OCR 보완 |
| **암호화(DRM) PDF** | pdfplumber 예외 발생 → FAILED | 암호화 감지 후 별도 `ENCRYPTED` 상태로 분류, UI에서 구분 표시 |
| **손상된 PDF** | 예외 발생 → FAILED | 손상 감지 후 `CORRUPTED` 상태 분류 |
| **초대형 PDF (100페이지+)** | 처리 가능하나 시간 초과 위험 | 페이지 수 기반 타임아웃 동적 조정 |

### 20.2 혼합 페이지 처리 (디지털 + 이미지 삽입)

디지털 PDF라도 특정 페이지가 이미지로만 구성된 경우 현재 파이프라인에서 해당 페이지 텍스트가 누락된다.

```
페이지별 텍스트 밀도 검사 (Phase 14 추가):
  각 페이지 추출 텍스트 글자 수 확인
  ├── 글자 수 ≥ ocr_min_text_length(50) → 정상 추출
  └── 글자 수 < 50 → 해당 페이지만 OCR 보완 처리
      └── 보완 결과를 page_contents에 병합 저장 (Phase 12 연동)
```

### 20.3 암호화 PDF 처리

```python
# pdf_analyzer.py에 추가
def check_encryption(file_path: str) -> bool:
    """pdfplumber 열기 전 PyPDF2 또는 pypdfium2로 암호화 여부 사전 확인."""
```

| 상태 | 설명 |
|------|------|
| `ENCRYPTED` | 암호화로 텍스트 추출 불가. UI에서 별도 목록 표시 |
| `CORRUPTED` | 파일 손상으로 열기 실패. UI에서 별도 목록 표시 |

> `Status.UNPROCESSABLE = {ENCRYPTED, CORRUPTED}` — 재시도 불가, 수동 조치 필요 표시

### 20.4 동적 타임아웃

초대형 PDF(100페이지 이상)에서 현재 고정 타임아웃(300초)이 부족할 수 있다.

| 파라미터 | 계산 방식 |
|----------|-----------|
| `effective_timeout` | `base_timeout + page_count × 2초` (최대 600초) |

### 20.5 검증 기준

| 항목 | 기준 |
|------|------|
| 혼합 페이지 보완율 | 이미지 삽입 페이지 포함 문서에서 해당 페이지 OCR 보완 정상 동작 |
| 암호화 감지 정확도 | 암호화 PDF 샘플 5건 → ENCRYPTED 상태 정확 분류 |
| 손상 파일 처리 | 손상 PDF 샘플 → CORRUPTED 분류 후 파이프라인 중단 없이 다음 문서 처리 |
| 대형 PDF 처리 | 100페이지+ PDF → 동적 타임아웃 내 정상 완료 또는 FAILED 기록 |
