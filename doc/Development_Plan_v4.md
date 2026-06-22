# 개발 명세서 및 계획서 v4.1

> 기준일: 2026-04-16  
> 변경 이력: v4.0 → v4.1 — PDF 분류 분석 및 적응형 OCR 전략 추가

---

## 1. 프로젝트 개요

본 프로젝트는 PDF 문서를 자동으로 분류·분석한 뒤 최적의 OCR 전략을 선택하여 텍스트를 추출하고, 추출된 텍스트를 AnythingLLM에 등록함으로써 문서를 검색 가능한 상태로 만드는 것을 목표로 합니다.

| 항목 | 내용 |
|------|------|
| 실행 환경 | Windows 로컬 |
| 주요 기술 스택 | Python, SQLite, Tesseract OCR, OpenCV, AnythingLLM |
| 처리 대상 문서 | 한국어·영어·일본어 혼합 PDF (단일/다단 레이아웃, Digital/Scan본, 수식 포함 여부) |
| OCR 언어 설정 | 문서 분류 결과에 따라 `eng` / `kor+eng` / `kor+eng+jpn` 자동 선택 |

---

## 2. 주요 기술 결정

| 항목 | 결정 내용 |
|------|-----------|
| PDF 유형 판별 | pdfplumber 텍스트 레이어 추출 시도 → 추출량 기준으로 DIGITAL / SCANNED 분류 |
| 레이아웃 분석 | OpenCV 수평 projection profile로 단수(SINGLE) / 다단(MULTI) 판별 |
| 언어 감지 | 텍스트 레이어 샘플링(Digital) 또는 OCR 초안 결과에 `langdetect` 적용 |
| 수식 감지 | Unicode 수학 기호 밀도(Digital) + 이미지 영역 특징(Scanned, 픽셀 밀도·종횡비) 휴리스틱 |
| OCR 도구 | Tesseract tessdata-best 모델, PSM·언어 조합을 전략별로 지정 |
| 문서 등록 방식 | AnythingLLM raw-text API |
| 색인 완료 기준 | Parsing → Chunking → Embedding 순차 완료 |

---

## 3. PDF 분류 기준

### 3.1 소스 유형 (source_type)

| 유형 | 판별 기준 |
|------|-----------|
| `DIGITAL` | pdfplumber로 추출한 텍스트가 페이지당 평균 ≥ 50자 |
| `SCANNED` | 추출 텍스트 < 50자 또는 텍스트 레이어 없음 |

### 3.2 레이아웃 유형 (layout_type)

| 유형 | 판별 방법 |
|------|-----------|
| `SINGLE_COLUMN` | 페이지 이미지 수평 projection에서 중앙 여백 분리선 미검출 |
| `MULTI_COLUMN` | 중앙 여백 분리선 검출 (2단 이상 논문·저널 형식) |

> SCANNED 문서는 pdf2image로 변환 후 OpenCV 분석 적용  
> DIGITAL 문서는 pdfplumber bounding box 분포로 판별

### 3.3 포함 언어 (detected_languages)

| 언어 코드 | 감지 방법 |
|-----------|-----------|
| `kor` | 한글 Unicode 범위 (U+AC00–U+D7A3) 문자 비율 |
| `eng` | ASCII 알파벳 비율 |
| `jpn` | 히라가나(U+3040–U+309F) / 가타카나(U+30A0–U+30FF) 비율 |

> Digital: 텍스트 레이어 직접 분석  
> Scanned: 낮은 해상도 OCR 초안(eng 단일 패스)으로 샘플링 후 langdetect 보정

### 3.4 수식 포함 여부 (has_formula)

| 유형 | 감지 방법 |
|------|-----------|
| Digital | Unicode 수학 기호(U+2200–U+22FF, U+0370–U+03FF) 밀도 > 임계값 |
| Scanned | 페이지 이미지에서 가로선·분수선 패턴, 좁은 종횡비 영역(∫, Σ 등) 픽셀 밀도 분석 |

---

## 4. OCR 전략 매트릭스

`source_type` × `layout_type` × `detected_languages` × `has_formula` 조합으로 전략 자동 선택

| 전략 ID | 조건 | Tesseract 설정 | 비고 |
|---------|------|----------------|------|
| `DIGITAL_EXTRACT` | DIGITAL | — (OCR 불필요) | pdfplumber 직접 추출 |
| `OCR_ENG` | SCANNED + SINGLE + eng only + 수식 없음 | `--psm 3 -l eng` | 영문 단일컬럼 |
| `OCR_KOR_ENG` | SCANNED + SINGLE + kor 포함 + 수식 없음 | `--psm 3 -l kor+eng` | 한영 혼합 단일컬럼 |
| `OCR_JPN` | SCANNED + SINGLE + jpn 포함 + 수식 없음 | `--psm 3 -l kor+eng+jpn` | 일본어 포함 단일컬럼 |
| `OCR_MULTI_COL` | SCANNED + MULTI + 수식 없음 | 컬럼 분할 후 `--psm 6` 적용 | OpenCV로 컬럼 ROI 분리 |
| `OCR_FORMULA` | SCANNED + 수식 있음 (레이아웃 무관) | `--psm 6 --oem 1` + 수식 영역 별도 처리 | 수식 영역 마스킹 후 텍스트/수식 분리 추출 |
| `OCR_MULTI_FORMULA` | SCANNED + MULTI + 수식 있음 | 컬럼 분할 + 수식 영역 분리 | 위 두 전략 결합 |

> **전략 우선순위**: DIGITAL_EXTRACT > 수식 처리 > 다단 처리 > 언어별 단일컬럼

---

## 5. 시스템 아키텍처

### 메인 파이프라인

```
[PDF 탐지]
    │
    ▼
[PDF 분류 분석] ── PDF Analyzer
    ├── source_type  : DIGITAL / SCANNED
    ├── layout_type  : SINGLE_COLUMN / MULTI_COLUMN
    ├── languages    : {kor, eng, jpn}
    └── has_formula  : True / False
    │
    ▼
[OCR 전략 결정] ── Strategy Selector
    │
    ├─ DIGITAL ───────────────────────────────────────► [pdfplumber 추출]
    │                                                          │
    └─ SCANNED                                                 │
          │                                                    │
          ├─ SINGLE + 수식 없음 ──► Tesseract                  │
          │    └─ 언어에 따라 eng / kor+eng / kor+eng+jpn      │
          │                                                    │
          ├─ MULTI + 수식 없음 ──► OpenCV 컬럼 분할             │
          │    └─ 컬럼별 Tesseract (--psm 6)                   │
          │                                                    │
          ├─ SINGLE + 수식 있음 ──► 수식 영역 마스킹            │
          │    └─ 텍스트 영역 Tesseract + 수식 영역 별도 저장   │
          │                                                    │
          └─ MULTI + 수식 있음 ──► 컬럼 분할 + 수식 마스킹     │
               └─ 컬럼별 처리 + 수식 영역 별도 저장            │
                                                               │
    [OCR_DONE] → 품질 검사                                     │
          │                                                    │
          ├─ 품질 충족 (≥50자, 오류율 낮음) ─────────────► [TEXT_READY] ◄──┘
          └─ 품질 미충족 ──► [REVIEW_REQUIRED]
                                    │
                              수동 검토 후 TEXT_READY 또는 FAILED

[TEXT_READY]
    │
    ▼
[텍스트 전처리]
    │
    ▼
[API 업로드] (AnythingLLM raw-text)
    │
    ▼
[색인 상태 추적] (Parsing → Chunking → Embedding)
    │
    ▼
[INDEXED] ✓
```

---

## 6. 핵심 모듈

| 모듈 | 역할 |
|------|------|
| **File Scanner** | 지정 디렉토리 감시, 신규 PDF 탐지 및 DB 등록 |
| **PDF Analyzer** | source_type / layout_type / 언어 / 수식 분류. `PDFProfile` 반환 |
| **Strategy Selector** | `PDFProfile` → `ocr_strategy` 결정 |
| **OCR Engine** | 전략에 따라 Tesseract 파라미터 및 전처리 분기 실행, 품질 점수 산출 |
| **Layout Splitter** | OpenCV 기반 다단 컬럼 ROI 분리 (MULTI_COLUMN 전략용) |
| **Formula Handler** | 수식 영역 감지·마스킹, 텍스트와 수식 영역 분리 저장 |
| **Preprocessor** | 추출 텍스트 정제 (공백 정규화, 특수문자 처리, 최소 길이 검증) |
| **API Client** | AnythingLLM raw-text API 전송, `document_id` 수신 처리 |
| **Status Tracker** | AnythingLLM 색인 진행 상태 폴링 및 DB 업데이트 |
| **Database Manager** | `documents` 및 `process_logs` 테이블 관리 |

---

## 7. 상태 설계

### 상태 흐름도

```
NEW
 │
 ▼
ANALYZING ── PDF 분류 분석 중
 │
 ├──► TEXT_EXTRACTED ──┐   (DIGITAL, 직접 추출 성공)
 │                     │
 └──► OCR_DONE ────────┤   (SCANNED, OCR 완료)
       │               │
       ├──► TEXT_READY ◄┘
       │       │
       │       ▼
       │  UPLOAD_REQUESTED
       │       │
       │       ▼
       │    UPLOADED
       │       │
       │       ▼
       │  PARSING_DONE
       │       │
       │       ▼
       │  CHUNKING_DONE
       │       │
       │       ▼
       │  EMBEDDING_DONE
       │       │
       │       ▼
       │    INDEXED ✓
       │
       └──► REVIEW_REQUIRED ──► (수동 전이) ──► TEXT_READY 또는 FAILED

FAILED ◄──── (오류 발생 시 모든 단계에서 전이 가능)
  │
  └──► (수동 재처리 시 NEW로 초기화)
```

### 상태 설명

| 상태 | 설명 |
|------|------|
| `NEW` | 파일 탐지 직후 최초 등록 상태 |
| `ANALYZING` | PDF 분류 분석 진행 중 (source_type, layout, 언어, 수식 감지) |
| `TEXT_EXTRACTED` | DIGITAL 문서에서 pdfplumber로 텍스트 직접 추출 성공 |
| `OCR_DONE` | Tesseract OCR 처리 완료 (품질 검사 전) |
| `REVIEW_REQUIRED` | OCR 품질 미충족, 사용자 수동 검토 필요 |
| `TEXT_READY` | 텍스트 전처리 완료, API 업로드 대기 상태 |
| `UPLOAD_REQUESTED` | API 업로드 요청 발송 (비동기 응답 대기 중) |
| `UPLOADED` | API 업로드 성공, `document_id` 수신 완료 |
| `PARSING_DONE` | AnythingLLM 파싱 단계 완료 |
| `CHUNKING_DONE` | 청킹 단계 완료 |
| `EMBEDDING_DONE` | 임베딩 단계 완료 |
| `INDEXED` | 색인 완전 완료, 검색 가능 상태 |
| `FAILED` | 처리 중 오류 발생. `error_message` 및 `failed_step` 기록 |

> **재처리 규칙**: `FAILED` 또는 `REVIEW_REQUIRED` 상태에서만 수동으로 `NEW`로 초기화 가능

---

## 8. 데이터베이스 설계

### 8.1 `documents` 테이블

```sql
CREATE TABLE documents (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    file_path           TEXT    NOT NULL UNIQUE,   -- 파일 절대 경로
    file_name           TEXT    NOT NULL,
    file_hash           TEXT,                      -- 중복 처리 방지용 MD5
    status              TEXT    NOT NULL DEFAULT 'NEW',

    -- PDF 분류 분석 결과
    source_type         TEXT,                      -- DIGITAL / SCANNED
    layout_type         TEXT,                      -- SINGLE_COLUMN / MULTI_COLUMN
    detected_languages  TEXT,                      -- 콤마 구분: "kor,eng" / "kor,eng,jpn"
    has_formula         INTEGER DEFAULT 0,         -- 0: 없음, 1: 있음
    ocr_strategy        TEXT,                      -- 선택된 OCR 전략 ID

    -- OCR 결과
    anythingllm_doc_id  TEXT,
    ocr_quality_score   REAL,                      -- 0.0 ~ 1.0
    retry_count         INTEGER NOT NULL DEFAULT 0,
    error_message       TEXT,
    failed_step         TEXT,

    created_at          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME
);
```

### 8.2 `process_logs` 테이블

```sql
CREATE TABLE process_logs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id  INTEGER  NOT NULL,
    step         TEXT     NOT NULL,   -- ANALYZE, OCR, UPLOAD, INDEXING 등
    result       TEXT     NOT NULL,   -- SUCCESS / FAILURE
    message      TEXT,
    logged_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (document_id) REFERENCES documents(id)
);
```

---

## 9. 개발 단계 (Phase)

| Phase | 내용 | 상세 작업 |
|-------|------|-----------|
| **Phase 0** | 환경 설정 ✅ | Tesseract `kor+eng` 언어팩, AnythingLLM API 연결 확인 |
| **Phase 1** | DB 초기화 ✅ | 테이블 생성, 마이그레이션 스크립트 |
| **Phase 2** | PDF 탐지 및 DB 저장 | File Scanner, 중복 hash 비교 |
| **Phase 3** | PDF 분류 분석 | source_type 판별, 레이아웃 분석(OpenCV), 언어 감지, 수식 감지 |
| **Phase 4** | OCR 전략 선택 및 실행 | Strategy Selector, Layout Splitter, Formula Handler, Tesseract 실행 |
| **Phase 5** | 텍스트 전처리 | 정제, 최소 길이 검증, `REVIEW_REQUIRED` 분기 |
| **Phase 6** | API 연동 | AnythingLLM raw-text 업로드, `document_id` 저장 |
| **Phase 7** | 색인 상태 추적 | 폴링 간격: 10초, 타임아웃: 5분 |
| **Phase 8** | 재시도 로직 | 최대 3회, Exponential Backoff (1분 → 2분 → 4분) |
| **Phase 9** | UI (Streamlit) | 파일 목록, 분류 결과 표시, 상태 현황, 수동 재시도, `REVIEW_REQUIRED` 검토 화면 |

---

## 10. 개발 일정 (6주)

```
Week 1  │ Phase 0 ~ 2  │ 환경 설정 ✅, DB 초기화 ✅, PDF 탐지
Week 2  │ Phase 3      │ PDF 분류 분석 (Digital/Scan, 레이아웃, 언어, 수식)
Week 3  │ Phase 4      │ OCR 전략 선택 및 실행 (다단·수식 처리 포함)
Week 4  │ Phase 5 ~ 6  │ 텍스트 전처리, API 연동
Week 5  │ Phase 7 ~ 8  │ 색인 추적, 재시도 로직
Week 6  │ Phase 9      │ UI 구현, 통합 테스트, 안정화
```

---

## 11. 추가 패키지

```
opencv-python>=4.9.0   # 레이아웃 분석, 컬럼 분할, 수식 영역 감지
langdetect>=1.0.9      # 텍스트 언어 감지 보정
```

---

## 12. 평가 기준

| 항목 | 기준 |
|------|------|
| PDF 탐지 정확도 | 지정 폴더 내 PDF 100% 탐지 (누락 0건) |
| 분류 정확도 | 테스트 셋 기준 Digital/Scan 판별 95% 이상, 다단 레이아웃 판별 90% 이상 |
| 언어 감지 정확도 | 단일 언어 문서 100%, 혼합 언어 문서 90% 이상 정확 감지 |
| 수식 감지 정확도 | 수식 포함 문서에서 오탐(False Negative) 10% 미만 |
| OCR 처리 성공률 | 전략별 샘플 문서 기준 90% 이상 `TEXT_READY` 도달 |
| API 업로드 성공률 | 업로드 시도 대비 95% 이상 `UPLOADED` 상태 도달 |
| 색인 상태 추적 정확도 | AnythingLLM 실제 상태와 DB 상태 일치율 100% |
| 재시도 동작 | 단위 테스트로 정상 동작 검증 |
| UI 사용성 | 비개발자가 파일 상태 및 분류 결과 확인을 5분 이내에 수행 가능 |
| 처리 성능 | PDF 1건(10페이지 기준) 전체 처리 3분 이내 (다단/수식 포함 시 5분 이내) |
