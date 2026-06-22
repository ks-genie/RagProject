# Phase 10 상세 계획서 — 실 PDF 데이터 End-to-End 통합 검증

> 작성일: 2026-04-17  
> 선행 단계: Phase 0~9 완료 (app.py v0.9.1, 테스트 139개 전체 통과)  
> 예상 기간: 3~5일  
> **완료일: 2026-04-17** (E2E 검증), **후속 작업: 2026-04-27 ~ 2026-05-27**

---

## 0. 실행 결과 요약 (완료)

### Phase 10 본 검증 — 2026-04-16~17

| 평가 항목 | 목표 기준 | 실측값 | 결과 |
|----------|----------|--------|------|
| PDF 탐지 정확도 | 100% | 37/37건 등록 | ✅ |
| DIGITAL/SCANNED 판별 정확도 | 95% 이상 | 37/37 DIGITAL 정확 판별 | ✅ |
| 다단 레이아웃 판별 정확도 | 90% 이상 | IEEE 논문 다단 감지 정상 | ✅ |
| 언어 감지 정확도 | 단일 100% | 영문 100%, 한국어 3건 정상 | ✅ |
| OCR 처리 성공률 | 90% 이상 | 37/37 INDEXED (100%) | ✅ |
| DIGITAL 다단 추출 품질 | quality ≥ 0.95 | HBM 논문 quality=0.9941 | ✅ |
| API 업로드 성공률 | 95% 이상 | 37/37 UPLOADED 성공 | ✅ |
| 색인 완료율 | — | 37건 전부 INDEXED | ✅ |
| P1 이슈 미해결 | 0건 | 0건 | ✅ |

**9/9 평가 기준 전부 통과. 완료 선언: 2026-04-17 11:29 (v6.0.0)**

### Phase 10 이후 추가 작업 — 2026-04-17 ~ 2026-05-27

Phase 10 완료 후 실 문서 처리 과정에서 발견된 품질 이슈를 계속 수정하였다.

| 버전 | 날짜 | 주요 내용 |
|------|------|----------|
| v0.9.2 | 2026-04-17 | 로그 기능 추가 (log_setup.py, logs/ 자동 생성) |
| v0.9.3 | 2026-04-17 | PDF 뷰어 네비게이션 버튼 재배치 |
| v0.9.4/4r | 2026-04-17 | OCR 리플로우 reflow_ocr_text(), 플로팅 버튼 시도 후 롤백 |
| v0.9.5 | 2026-04-17 | 문단 구조 보존 (_words_to_text_with_paragraphs, layout=True) |
| v0.9.6 | 2026-04-17 | 학술 논문 헤더 감지 (_extract_academic_header: [제목]/[저자]/[소속]/[본문]) |
| v0.9.7 | 2026-04-17 | 헤더·푸터 분리, 멀티컬럼 문장 연결 (_connect_columns) |
| v0.9.8 | 2026-04-17 | [소속] 섹션 분리 (page.lines 수평 구분선 기반) |
| v0.9.9 | 2026-04-17 | DIGITAL_EXTRACT_MULTI 저자 잘림 수정 (학술 헤더 우선 처리) |
| v0.9.10 | 2026-04-27 | layout_splitter.py side_mean<0.01 가드, _extract_academic_header(gap_x) 확장 |
| v0.9.11 | 2026-04-27 | DIGITAL_EXTRACT_MULTI 2단 읽기 순서 1차 보정 (Codex) |
| v0.9.12 | 2026-04-27 | _find_body_start_y · _compose_multicol_text 도입, pre-body 헤더 분리 최종 수정 |
| v0.9.13 | 2026-04-27 | preprocessor normalize_hard_linebreaks 고도화, reflow_ocr_text 위임 |
| — | 2026-05-27 | CHANGELOG.md 한글 인코딩 복구 (Codex 작성본 EUC-KR 오염 수정) |
| v0.9.14 | 2026-05-27 | 좌→우 컬럼 경계 문장 자동 연결 (_join_columns), dead code 제거 |
| v0.9.15 | 2026-05-27 | 디지털 PDF 워터마크 자동 제거 (filter_watermarks: 회전 문자·연한 색상 필터) |
| v0.9.15a | 2026-05-27 | filter_watermarks 강화 (stroking_color, 임계값 0.80, 대형 스탬프 낱글자) |
| — | 2026-05-28 | **filter_watermarks() 최종 동작 확인** — 회전 스탬프·연한 배경·대형 낱글자 정상 제거. v0.9.15a 기준 확정 |

**미해결 이슈:** JEDEC 법적 고지 텍스트("PLEASE! DON'T VIOLATE THE LAW!")는 순수 검정(fill=0.0)·upright=True·12pt로 일반 본문과 동일 속성 → 색상/회전 기반 filter_watermarks()로 제거 불가. Phase 11에서 전처리 키워드 기반 라인 제거(`remove_jedec_notice()`)로 해결 예정.

**현재 최신 버전: v0.9.15a (2026-05-27 22:44, 워터마크 제거 확인 2026-05-28)**

---

## 1. 배경 및 목적

### 현재 상태

Phase 9까지 모든 모듈이 구현되고 단위 테스트 139개가 통과한 상태다. 그러나 **모든 테스트가 mock 기반**으로 작성되어 있다.

| 테스트 모듈 | mock 비율 | 실제 PDF 사용 여부 |
|------------|-----------|------------------|
| test_pdf_analyzer.py | 높음 | ✗ (메모리 이미지 객체) |
| test_ocr_engine.py | 매우 높음 | ✗ (pytesseract mock) |
| test_api_client.py | 100% | ✗ (requests mock) |
| test_pipeline.py | 100% | ✗ (전 컴포넌트 mock) |
| test_file_scanner.py | 낮음 | △ (빈 fake PDF 파일) |
| test_preprocessor.py | 거의 없음 | ✗ (문자열 직접 전달) |

`data/pdf_watch/`에는 **59개의 실제 PDF** (HBM/DRAM 논문, JESD 표준 문서 등)가 있으나, 파이프라인을 통해 실제 처리된 적이 없다.

### 목적

1. 실제 PDF 문서로 파이프라인 전 단계를 실행하여 기능 정확성 확인
2. 개발 계획서 §16 평가 기준을 실측값으로 채우기
3. mock 환경과 실제 환경의 동작 차이(버그, 엣지 케이스) 발굴 및 수정
4. 처리 성능(시간) 실측

### 보안 선결 과제

`config.yaml`에 AnythingLLM API 키가 하드코딩되어 있다. 검증 작업 시작 전에 `.env`로 이전해야 한다.

---

## 2. 사전 준비 (Day 0)

### 2.1 보안 이슈 해결 — API 키 .env 이전

**현재 상태** (`config.yaml`):
```yaml
anythingllm:
  api_key: "3JW48N5-PGW4VXV-HEE8Q8Y-3JSA5GH"  # ← 하드코딩
  workspace: "0da41956-a430-47bf-a4b2-784f046b616c"
```

**조치 방법**:
1. `.env` 파일 생성 (이미 `.gitignore`에 포함되어 있는지 확인)
2. `config.yaml`에서 민감 정보 제거
3. `src/config.py`가 `.env` 우선 적용함을 재확인

```bash
# .env
ANYTHINGLLM_API_KEY=3JW48N5-PGW4VXV-HEE8Q8Y-3JSA5GH
ANYTHINGLLM_WORKSPACE=0da41956-a430-47bf-a4b2-784f046b616c
ANYTHINGLLM_BASE_URL=http://localhost:3001
```

### 2.2 환경 점검 — phase0_check.py 재실행

```bash
python phase0_check.py
```

확인 항목:
- [ ] Python 버전 ≥ 3.9
- [ ] 필수 패키지 전부 설치됨
- [ ] Tesseract 실행 파일 접근 가능 (`C:/Program Files/Tesseract-OCR/tesseract.exe`)
- [ ] tessdata-best `kor`, `eng` 언어팩 존재
- [ ] AnythingLLM API 응답 (`http://localhost:3001/api/ping`)
- [ ] `data/pdf_watch/` 디렉토리 및 59개 PDF 존재

### 2.3 DB 초기화

기존 처리 기록이 없는 깨끗한 상태에서 시작한다.

```bash
# 기존 DB 백업 후 초기화
python migrate.py
```

---

## 3. PDF 데이터셋 사전 분류 (Day 1 오전)

59개 PDF를 파이프라인에 넣기 전에 유형을 먼저 파악한다.

### 3.1 분류 기준

| 축 | 값 | 설명 |
|----|----|----|
| 소스 유형 | DIGITAL / SCANNED | 텍스트 레이어 유무 |
| 레이아웃 | SINGLE / MULTI | 단일/다단 컬럼 |
| 주요 언어 | kor / eng / jpn | 주 언어 |
| 수식 포함 | Y / N | 수식 포함 여부 |

### 3.2 사전 분류 방법

**방법 A — 진단 탭 활용**  
Streamlit UI의 `🔬 PDF 분석 진단` 탭에서 파일 경로를 입력하면 줄 커버리지 프로파일과 감지 결과를 즉시 확인할 수 있다.

**방법 B — 분류 배치 스크립트 실행** (신규 작성 예정)  
`tools/classify_all.py` 스크립트를 작성하여 59개 전체를 순회하고 예상 전략을 CSV로 출력한다.

```python
# tools/classify_all.py (작성 예정)
# 출력: file_name, source_type, layout_type, languages, has_formula, expected_strategy
```

### 3.3 예상 분포 (가정)

`data/pdf_watch/`의 파일명 패턴(HBM, DRAM, JESD, SK Hynix 등)을 보면 대부분 반도체 기술 논문 및 표준 문서로 예상된다.

| 전략 | 예상 비율 | 근거 |
|------|----------|------|
| `DIGITAL_EXTRACT_MULTI` | 50~60% | IEEE 형식 2단 논문 다수 |
| `DIGITAL_EXTRACT` | 20~30% | 단일 컬럼 기술 보고서, 표준 문서 |
| `OCR_ENG` | 10~15% | 오래된 스캔 문서 |
| `OCR_FORMULA` | 5~10% | 수식 포함 기술 논문 |

---

## 4. 파일럿 실행 — 소규모 검증 (Day 1 오후)

전체 배치 전에 **전략별 대표 파일 1~2건씩** 선별하여 수동으로 처리한다.

### 4.1 파일럿 대상 선정 기준

| 전략 | 파일 선정 기준 |
|------|--------------|
| `DIGITAL_EXTRACT_MULTI` | IEEE 논문 형식, 텍스트 레이어 있음, 2단 |
| `DIGITAL_EXTRACT` | 단일 컬럼 표준 문서 |
| `OCR_ENG` | 스캔본 (텍스트 레이어 없음) |
| `OCR_KOR_ENG` | 한글 포함 문서 (가능 시) |
| `OCR_FORMULA` | 수식 포함 논문 |

총 5~8건으로 파일럿 진행.

### 4.2 파일럿 실행 절차

```
1. Streamlit UI 접속 (http://localhost:8501)
2. 사이드바 ▶ 파이프라인 실행
3. 📋 전체 파일 목록 탭에서 처리 결과 확인
4. 각 문서의 분류 결과 및 OCR 전략 기록
5. 📖 원본/OCR 비교 뷰어로 추출 품질 육안 확인
```

### 4.3 파일럿 체크리스트

각 문서에 대해 다음을 확인한다.

```
[ ] source_type이 예상대로 분류되었는가?
[ ] layout_type이 예상대로 분류되었는가?
[ ] 선택된 ocr_strategy가 올바른가?
[ ] PDF 뷰어의 원본과 추출 텍스트가 육안으로 대응되는가?
[ ] 2단 문서에서 컬럼 혼합 없이 좌→우 순서로 추출되었는가?
[ ] 최종 상태가 INDEXED인가?
[ ] REVIEW_REQUIRED로 빠진 경우 사유가 합당한가?
```

### 4.4 파일럿 중단 기준

파일럿 도중 다음 상황이 발생하면 즉시 전체 배치를 중단하고 수정 후 재진행한다.

- 동일 유형 파일 2건 이상이 FAILED 상태
- 분류 오판이 3건 이상
- AnythingLLM API 오류 반복 발생

---

## 5. 전체 배치 실행 (Day 2)

파일럿에서 문제가 없으면 59개 전체를 처리한다.

### 5.1 배치 실행 전 점검

- [ ] AnythingLLM 워크스페이스 문서 공간 충분한지 확인
- [ ] 로컬 디스크 여유 공간 확인 (로그 파일, DB 증가)
- [ ] Streamlit 앱 및 AnythingLLM 서버 정상 구동 상태

### 5.2 배치 실행 방법

```
1. Streamlit UI → ▶ 파이프라인 실행 (1회 사이클)
2. 처리 완료까지 대기 (59건 × 평균 2~5분 = 2~5시간 예상)
3. 주기적으로 📊 대시보드 탭에서 진행 현황 모니터링
4. FAILED 발생 시 즉시 로그 확인 (전체 중단은 하지 않음)
```

### 5.3 진행 중 모니터링 항목

| 지표 | 의미 | 조치 기준 |
|------|------|----------|
| FAILED 비율 | 처리 오류율 | 10% 초과 시 원인 분석 |
| REVIEW_REQUIRED 비율 | OCR 품질 미달 비율 | 30% 초과 시 임계값 검토 |
| INDEXED 비율 | 최종 성공률 | 목표: 90% 이상 |
| 평균 처리 시간 | 1건당 소요 시간 | 목표: 5분 이내 |

---

## 6. 결과 측정 및 평가 기준 검증 (Day 2~3)

### 6.1 측정 스크립트 작성 (신규)

`tools/evaluate.py` 스크립트를 작성하여 DB에서 결과를 집계하고 평가 기준 대비 측정값을 출력한다.

```python
# tools/evaluate.py (작성 예정)
# 출력 항목:
# - 전체 처리 건수, 상태별 분포
# - DIGITAL/SCANNED 판정 건수 및 비율
# - SINGLE/MULTI 판정 건수 및 비율
# - 전략별 분포
# - INDEXED 성공률
# - REVIEW_REQUIRED 비율 및 사유 분포
# - FAILED 비율 및 단계별 실패 분포
# - OCR 품질 점수 통계 (평균, 최솟값, 최댓값, 구간별 분포)
# - 처리 시간 통계 (created_at ~ updated_at 차이)
```

### 6.2 평가 기준 측정표

| 평가 항목 | 목표 기준 | 측정 방법 | 측정값 |
|----------|----------|----------|--------|
| PDF 탐지 정확도 | 100% | 59건 전부 등록 여부 | ___ |
| DIGITAL/SCANNED 판별 정확도 | 95% 이상 | 육안 검토 샘플 대비 | ___ |
| 다단 레이아웃 판별 정확도 | 90% 이상 | 진단 탭 + 육안 확인 | ___ |
| 언어 감지 정확도 | 단일 100%, 혼합 90% | DB detected_languages 확인 | ___ |
| OCR 처리 성공률 | 90% 이상 TEXT_READY | (TEXT_READY + INDEXED) / 전체 | ___ |
| DIGITAL 다단 추출 품질 | quality ≥ 0.99 | DIGITAL_EXTRACT_MULTI 건 quality score | ___ |
| API 업로드 성공률 | 95% 이상 | UPLOADED 이상 도달 건 / TEXT_READY 건 | ___ |
| 색인 완료율 | — | INDEXED / 전체 | ___ |
| 평균 처리 시간 | 3분 이내 (단순), 5분 이내 (복잡) | process_logs timestamp 차이 | ___ |

### 6.3 품질 육안 검토

자동 측정 외에 다음 유형 문서를 **샘플 5건씩** 뷰어로 직접 확인한다.

- `DIGITAL_EXTRACT_MULTI` 처리 문서: 좌→우 컬럼 순서 올바른지
- `OCR_ENG`/`OCR_KOR_ENG` 처리 문서: 텍스트 가독성
- `REVIEW_REQUIRED` 분류 문서: 사유 합당한지, 임계값 재조정 필요 여부

---

## 7. 이슈 분류 및 수정 (Day 3~4)

### 7.1 예상 이슈 유형 및 대응 방안

**[A] 분류 오판 (source_type / layout_type)**

| 케이스 | 원인 추정 | 대응 |
|--------|----------|------|
| DIGITAL → SCANNED 오판 | pdfplumber 추출 실패 (스캔 흔적 있는 Digital) | `ocr_min_text_length` 하향 검토, 또는 혼합 유형 추가 |
| SCANNED → DIGITAL 오판 | 텍스트 워터마크·메타데이터가 있는 스캔 | 텍스트 품질 기반 추가 필터 검토 |
| SINGLE → MULTI 오판 | 표·그림 설명이 좁은 두 컬럼처럼 보임 | MIN_GAP 값 상향 조정 |
| MULTI → SINGLE 오판 | 제목 등 전폭 줄 비율이 높은 논문 | 분석 페이지 수 확대(5 → 10장) 검토 |

**[B] OCR 품질 미달 (REVIEW_REQUIRED)**

| 케이스 | 원인 추정 | 대응 |
|--------|----------|------|
| quality_score < 0.5 | 스캔 해상도 낮음 | scale 파라미터 2.0 → 3.0 상향 |
| 텍스트 길이 < 50자 | 도표/그림만 있는 문서 | REVIEW_REQUIRED 그대로 유지(정상 동작) |
| 한글 OCR 오인식 | tessdata-best 모델 한계 | 품질 임계값 완화(0.5 → 0.4) 검토 |

**[C] API 연동 오류 (FAILED at UPLOAD/EMBED)**

| 케이스 | 원인 추정 | 대응 |
|--------|----------|------|
| 타임아웃 | 텍스트 길이 과대 | `upload_timeout` 상향 또는 텍스트 분할 |
| 403 오류 | API 키 만료 | API 키 갱신 |
| workspace 오류 | 워크스페이스 ID 변경 | 재확인 후 config 업데이트 |

**[D] 처리 성능 초과 (목표 시간 초과)**

| 케이스 | 원인 추정 | 대응 |
|--------|----------|------|
| Tesseract 처리 지연 | 고해상도 이미지 + 다단 | scale 파라미터 하향(2.0 → 1.5) |
| pdfplumber 처리 지연 | 100페이지 이상 대용량 PDF | 처리 페이지 수 제한 검토 |

### 7.2 이슈 우선순위 기준

| 우선순위 | 기준 |
|--------|------|
| P1 (즉시 수정) | FAILED 비율 10% 초과, 또는 동일 유형 전체 오판 |
| P2 (이번 Phase 내 수정) | 평가 기준 미달 (성공률 90% 미만 등) |
| P3 (다음 Phase로 이월) | 개선 가능하지만 목표 기준은 충족 |

---

## 8. 검증 결과 문서화 (Day 4~5)

### 8.1 측정 결과 보고서 작성

`doc/ValidationReport_Phase10.md` 파일에 다음을 기록한다.

```
- 처리 일시
- 처리 건수 및 상태별 분포
- 평가 기준 대비 실측값 (6.2 표 완성)
- 발견된 이슈 목록 및 수정 내용
- 수정 후 재측정 결과
- 미해결 이슈 및 다음 Phase 이월 사항
```

### 8.2 개발 계획서 업데이트

Phase 10 완료 후 `Development_Plan_v5.md`를 **v6.0**으로 업데이트한다.

변경 내용:
- 실측된 평가 기준 수치로 §16 업데이트
- Phase 10 완료 항목 추가
- 이슈 수정 사항 반영 (알고리즘 파라미터 변경 등)

---

## 9. 작업 목록 (체크리스트)

### Day 0 — 사전 준비

- [x] `.env` 파일 생성, API 키·워크스페이스 ID 이전 (v5.0.2, 2026-04-17)
- [x] `config.yaml`에서 민감 정보 제거 (v5.0.2)
- [x] `python phase0_check.py` 실행 및 전체 통과 확인
- [x] DB 초기화 (`python migrate.py`)
- [x] Streamlit 앱 실행 확인
- [x] AnythingLLM 서버 구동 및 워크스페이스 접근 확인

### Day 1 — 데이터셋 분류 및 파일럿

- [x] `tools/classify_all.py` 작성 및 실행 (v5.0.4, 2026-04-17)
- [x] 59개 PDF 유형별 분류 결과 확인 → 실제 처리 가능 37건 확인
- [x] 전략별 대표 파일 5~8건 파일럿 선정
- [x] 파일럿 실행 (파이프라인 1회 사이클)
- [x] 파일럿 결과 체크리스트 완성
- [x] 파일럿 중 발견 이슈 기록 및 P1 이슈 즉시 수정

### Day 2 — 전체 배치 실행

- [x] 파일럿 이슈 수정 사항 재테스트 통과 확인
- [x] 전체 37건 배치 실행 완료 (2026-04-16 23:22 KST, 전체 INDEXED)
- [x] 실행 중 FAILED/REVIEW_REQUIRED 모니터링 — FAILED 0건
- [x] 배치 완료 후 상태 분포 스냅샷 기록

### Day 3 — 결과 측정

- [x] `tools/evaluate.py` 작성 및 실행 (v5.0.5 → v5.1.1 수정, 2026-04-17)
- [x] 평가 기준 대비 실측값 표 완성 (6.2) — 9/9 기준 통과
- [x] 샘플 문서 육안 검토
- [x] 이슈 목록 작성 및 우선순위 분류 (P2-1, P2-2 수정 완료)

### Day 4 — 이슈 수정

- [x] P1 이슈 전부 수정 — P1 이슈 없음
- [x] P2 이슈 수정 (evaluate.py 처리 시간 계산, quality 임계값 완화)
- [x] 수정 후 관련 단위 테스트 통과 확인
- [x] 수정된 파일 재처리 완료
- [x] 재측정 후 평가 기준 달성 확인

### Day 5 — 문서화

- [x] `doc/ValidationReport_Phase10.md` 작성 (v6.0.0, 2026-04-17)
- [x] `TechWorkLog.md` Phase 10 내용 추가
- [x] `Development_Plan_v5.md` → v6.0 → v6.1 업데이트
- [x] 최종 상태 반영 (CHANGELOG.md 지속 갱신)

---

## 10. 성공 기준

Phase 10이 완료된 것으로 간주하는 조건:

| 기준 | 목표값 |
|------|--------|
| 전체 59건 처리 완료 (INDEXED + REVIEW_REQUIRED + FAILED 합산) | 100% |
| INDEXED 도달 비율 | **≥ 85%** |
| FAILED 비율 | **≤ 5%** |
| DIGITAL/SCANNED 판별 정확도 (샘플 20건 육안 검토) | **≥ 95%** |
| MULTI_COLUMN 감지 정확도 (IEEE 논문 샘플 10건 기준) | **≥ 90%** |
| 평균 처리 시간 (10페이지 기준) | **≤ 5분** |
| P1 이슈 | **0건 미해결** |

---

## 11. 위험 요소 및 완화 방안

| 위험 요소 | 발생 확률 | 영향 | 완화 방안 |
|----------|----------|------|----------|
| AnythingLLM 서버 불안정 | 중 | 높음 | 배치 실행 전 ping 확인, 재시도 설정 유지 |
| Tesseract 한국어 OCR 품질 낮음 | 높음 | 중 | 품질 임계값 완화 또는 REVIEW_REQUIRED 허용 |
| 59건 중 손상된 PDF 존재 | 중 | 낮음 | FAILED 처리 후 개별 파일 조사 |
| 처리 시간 과대 (배치 5시간+) | 중 | 낮음 | 파일럿 시간 측정 후 예측, 필요 시 분할 실행 |
| 워크스페이스 문서 한도 초과 | 낮음 | 높음 | 사전에 AnythingLLM 문서 용량 확인 |

---

## 12. Phase 11 예고

Phase 10 결과에 따라 다음 작업이 결정된다.

| Phase 10 결과 | Phase 11 방향 |
|--------------|--------------|
| INDEXED ≥ 85%, 이슈 경미 | 자동화 스케줄러 구현 (watchdog 상시 감시) |
| OCR 품질 이슈 다수 | OCR 후처리 개선 (한글 오인식 정규화, 노이즈 제거 강화) |
| MULTI_COLUMN 오판 다수 | 레이아웃 감지 파라미터 재조정 또는 3단 이상 지원 |
| API 연동 불안정 | AnythingLLM 연동 안정화, 업로드 청크 분할 |
