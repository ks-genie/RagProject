# Phase 11 상세 계획서 — OCR 품질 고도화 및 미검증 전략 검증

> 작성일: 2026-05-27 / 확인: 2026-05-28  
> 선행 단계: Phase 10 E2E 검증 완료 (37건 INDEXED, 9/9 기준 통과, v0.9.15a)  
> filter_watermarks() 최종 동작 확인 완료 (2026-05-28) — 회전 스탬프·연한 배경·대형 낱글자 정상 제거  
> 예상 기간: 3~5일

---

## 1. 배경 및 목적

Phase 10에서 37건 전체가 INDEXED에 도달하여 9/9 평가 기준을 통과하였다. 그러나 37건이 전부 DIGITAL 타입 문서였기 때문에 다음 전략·시나리오는 실 검증을 거치지 못했다.

| 미검증 영역 | 현재 상태 |
|------------|----------|
| SCANNED 문서 (Tesseract OCR 실 처리) | 샘플 없음 |
| OCR_FORMULA / OCR_MULTI_FORMULA 전략 | 실 실행 없음 |
| 한국어 문서 (3건 이외 추가 샘플) | 부족 |
| 일본어 문서 | 0건 |

추가로 Phase 10 이후 작업(v0.9.10~v0.9.15a, 2026-04-27~05-27)에서 새로운 미해결 이슈가 확인되었다.

| 이슈 | 상태 |
|------|------|
| JEDEC 법적 고지 텍스트 워터마크 처리 | 보류 (Phase 11 이월) |
| JESD270-4_HBM4 Test Standard.pdf AnythingLLM 미등록 | 재처리 필요 |

Phase 11의 목적:
1. SCANNED 전략 실 PDF로 검증, 파라미터 튜닝
2. OCR_FORMULA 전략 실 검증
3. 다국어 샘플 확보 및 언어 감지 정확도 확인
4. JEDEC 순수 검정 워터마크 해결 방안 구현
5. JESD270-4_HBM4 재처리 및 AnythingLLM 등록 완료

---

## 2. 이월 항목 상세 및 접근 방안

### 2.1 SCANNED 문서 검증

**배경**: Phase 10에서는 SCANNED 타입 PDF가 없어 Tesseract OCR 실 처리 경로가 한 번도 실행되지 않았다.

**목표**: SCANNED 전략(OCR_ENG, OCR_KOR_ENG)으로 실 PDF 처리 성공률 ≥ 85% 확인.

**접근 방법**:

1. 스캔 PDF 샘플 확보
   - `data/pdf_watch/` 내 텍스트 레이어 없는 파일 식별 (pdfplumber로 텍스트 길이 = 0 확인)
   - 없을 경우: 공개 스캔 논문 PDF를 추가 (JEDEC 구 버전 표준, IEEE 구형 논문)
2. Streamlit 진단 탭에서 source_type=SCANNED 판별 확인
3. OCR_ENG 전략으로 파이프라인 실행, 추출 텍스트 육안 확인
4. 품질 임계값 조정 필요 여부 판단 (`quality_score < 0.5` 시 scale 파라미터 상향 검토)

**체크리스트**:
- [ ] SCANNED 샘플 최소 2건 확보 (영문 1건, 한국어 1건 이상)
- [ ] source_type=SCANNED 자동 판별 확인
- [ ] OCR_ENG 전략 실행 성공, INDEXED 도달 확인
- [ ] OCR_KOR_ENG 전략 실행 성공 (한국어 샘플)
- [ ] 추출 텍스트 가독성 육안 검토 (5건)

---

### 2.2 OCR_FORMULA 전략 검증

**배경**: `formula_handler.py`가 구현되어 있으나 Phase 10 실 실행에서 수식 문서가 없어 OCR_FORMULA 전략이 선택된 적이 없다.

**목표**: 수식 포함 PDF에서 OCR_FORMULA 전략이 선택되고 정상 처리됨을 확인.

**접근 방법**:

1. 수식이 포함된 DIGITAL PDF 확인 (pdf_analyzer.py has_formula 판별 결과 재확인)
2. 필요 시 공개 수식 논문 PDF 추가
3. 전략 선택 기준(`strategy_selector.py`) 재검토 — has_formula=True + DIGITAL → OCR_FORMULA 분기 확인
4. OCR_FORMULA 처리 결과에서 수식 영역이 마스킹 처리되는지 확인

**체크리스트**:
- [ ] has_formula=True로 판별되는 PDF 1건 이상 확보
- [ ] OCR_FORMULA 전략 자동 선택 확인
- [ ] formula_handler.py 수식 마스킹 정상 동작 확인
- [ ] 수식 제외 텍스트 추출 품질 확인

---

### 2.3 다국어 샘플 확보

**배경**: 현재 한국어 문서 3건만 존재하며 일본어 문서는 0건이다. 언어 감지 정확도 "혼합 90%" 기준이 검증된 바 없다.

**접근 방법**:
1. 한국어 PDF 추가 확보 (반도체 관련 국내 논문, KEC 표준 등)
2. 일본어 PDF 확보 가능 여부 검토 (JEDEC 일어판, EIAJ 표준)
3. tessdata-best `jpn` 언어팩 설치 여부 확인
4. 혼합 언어(영문+한국어) 문서에서 detected_languages 정확도 확인

**체크리스트**:
- [ ] 한국어 PDF 추가 2건 이상 확보
- [ ] 일본어 PDF 1건 이상 확보 (가능한 경우)
- [ ] tessdata-best jpn 언어팩 설치 확인
- [ ] 혼합 언어 문서 언어 감지 정확도 확인

---

### 2.4 JESD270-4_HBM4 Test Standard.pdf 재처리

**배경**: Phase 10 전체 배치에서 해당 파일이 AnythingLLM에 등록되지 않은 것이 확인되었다.

**접근 방법**:
1. DB에서 해당 파일 상태 확인 (`SELECT * FROM documents WHERE file_name LIKE '%JESD270%'`)
2. 상태 reset_for_retry 실행 또는 수동 파이프라인 재실행
3. AnythingLLM 워크스페이스에서 등록 확인

**체크리스트**:
- [ ] DB 상태 확인 및 실패 원인 파악
- [ ] 재처리 실행
- [ ] AnythingLLM INDEXED 상태 도달 확인

---

### 2.5 JEDEC 법적 고지 텍스트 워터마크 처리

**배경**: `filter_watermarks()`(v0.9.15a)는 2026-05-28 최종 확인 완료 — 회전 스탬프, 연한 배경 워터마크, 대형 낱글자 스탬프를 정상 제거한다. 단, JESD 표준 문서에 존재하는 "PLEASE! DON'T VIOLATE THE LAW! ..." 법적 고지 텍스트는 예외이다. 이 텍스트는 순수 검정(fill=0.0), upright=True, 12pt로 일반 본문과 동일한 속성이라 색상·회전 기반 `filter_watermarks()`로 제거 불가하다. Phase 10에서 페이지 단위 키워드 감지 방안(`_is_legal_notice_page()`)을 시도하였으나 사용자 판단에 따라 v0.9.15a로 롤백하고 이월하였다.

**접근 방법 (권장)**:

키워드 기반 라인 제거 — 페이지 건너뛰기 방식보다 텍스트 후처리 방식으로 구현한다.

```python
# preprocessor.py 또는 ocr_engine.py 후처리
_JEDEC_NOTICE_RE = re.compile(
    r"PLEASE[!,]?\s+DON'T\s+VIOLATE\s+THE\s+LAW",
    re.IGNORECASE
)

def remove_jedec_notice(text: str) -> str:
    """JEDEC 법적 고지 줄을 텍스트에서 제거한다."""
    lines = text.splitlines()
    filtered = [ln for ln in lines if not _JEDEC_NOTICE_RE.search(ln)]
    return "\n".join(filtered)
```

이 방식은:
- 페이지를 건너뛰지 않으므로 본문 손실 없음
- 정규식 패턴이 명확하여 오탐 위험 낮음
- `Preprocessor._clean()` 또는 `normalize_hard_linebreaks()` 전에 삽입 가능

**체크리스트**:
- [ ] 제거 대상 패턴 확정 (JEDEC 문서 샘플 재확인)
- [ ] `remove_jedec_notice()` 또는 `_NOTICE_LINE_RE` 규칙 추가 구현
- [ ] `Preprocessor._clean()`에 통합
- [ ] JESD235 등 JEDEC 문서 재처리 후 법적 고지 텍스트 미포함 확인
- [ ] 단위 테스트 추가 (`test_preprocessor.py`)

---

### 2.6 스캔 PDF 워터마크 처리 (추가 검토)

**배경**: `filter_watermarks()`는 pdfplumber의 `page.filter()`를 사용하여 DIGITAL PDF의 문자 속성 기반으로 동작한다. SCANNED PDF는 이미지 레이어이므로 이 방식이 적용되지 않는다.

**접근 방법** (선택적):
- SCANNED PDF에서 워터마크가 실제로 문제가 되는지 확인 후 결정
- 이미지 전처리(OpenCV)로 워터마크 제거가 필요한 경우 별도 함수 구현 검토
- 우선순위: 낮음 (실제 SCANNED 샘플 확인 후 필요성 판단)

---

## 3. 작업 목록 (체크리스트)

### 준비 단계

- [ ] `data/pdf_watch/` 현재 파일 목록 재확인 — SCANNED/수식 PDF 존재 여부
- [ ] SCANNED 샘플 확보 (없을 경우 공개 스캔 PDF 추가)
- [ ] tessdata-best jpn 언어팩 설치 여부 확인 (`C:/Program Files/Tesseract-OCR/tessdata/jpn.traineddata`)
- [ ] Streamlit 앱 및 AnythingLLM 서버 정상 구동 확인

### SCANNED 전략 검증

- [ ] SCANNED 샘플 2건 이상 파이프라인 실행
- [ ] 추출 텍스트 품질 육안 확인
- [ ] quality_score 분포 확인, 필요 시 scale 파라미터 조정
- [ ] INDEXED 도달 확인

### 수식 전략 검증

- [ ] has_formula=True 문서 파이프라인 실행
- [ ] OCR_FORMULA 전략 선택 확인
- [ ] 처리 결과 품질 확인

### 다국어 검증

- [ ] 한국어 추가 샘플 처리 및 언어 감지 정확도 확인
- [ ] 일본어 샘플 처리 (가능 시)

### JEDEC 워터마크 처리

- [ ] `remove_jedec_notice()` 구현 (preprocessor.py)
- [ ] 단위 테스트 추가
- [ ] JESD 문서 재처리 후 검증

### JESD270-4 재처리

- [ ] DB 상태 확인 및 재처리 실행
- [ ] INDEXED 확인

### 문서화

- [ ] CHANGELOG.md Phase 11 항목 추가 (작업별 버전 기록)
- [ ] `doc/ValidationReport_Phase11.md` 작성 (검증 결과)
- [ ] `Phase11_Plan.md` 완료 체크리스트 업데이트
- [ ] 메모리 파일(`project_state.md`) 업데이트

---

## 4. 성공 기준

| 기준 | 목표값 |
|------|--------|
| SCANNED 전략 처리 성공률 | ≥ 85% INDEXED |
| OCR_FORMULA 전략 실 실행 확인 | 1건 이상 정상 처리 |
| JEDEC 법적 고지 텍스트 미포함 | JESD 문서 재처리 후 0건 |
| JESD270-4_HBM4 INDEXED | 완료 |
| 신규 단위 테스트 | Phase 11 추가 기능 커버 |

---

## 5. 위험 요소

| 위험 요소 | 발생 확률 | 영향 | 완화 방안 |
|----------|----------|------|----------|
| SCANNED 샘플 확보 불가 | 중 | 중 | 공개 PDF 사용, 테스트용 이미지→PDF 변환 |
| Tesseract 한국어/일본어 OCR 품질 낮음 | 높음 | 중 | quality 임계값 완화, REVIEW_REQUIRED 허용 |
| JEDEC 패턴 정규식 오탐 | 낮음 | 중 | 패턴 범위를 JEDEC 고지문에 한정, 테스트로 검증 |
| AnythingLLM JESD270-4 재처리 실패 원인 미파악 | 중 | 중 | 로그 상세 분석, 수동 업로드 폴백 |

---

## 6. Phase 12 예고

Phase 11 결과에 따라 다음 방향이 결정된다.

| Phase 11 결과 | Phase 12 방향 |
|--------------|--------------|
| SCANNED OCR 품질 이슈 | Tesseract 전처리 개선 (이미지 해상도 업스케일, 노이즈 제거) |
| OCR_FORMULA 오인식 다수 | formula_handler.py 수식 마스킹 알고리즘 개선 |
| 전체 안정화 완료 | 자동화 스케줄러 구현 (watchdog 상시 감시, 새 PDF 자동 처리) |
