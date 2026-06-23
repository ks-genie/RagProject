# -*- coding: utf-8 -*-
"""
Formula Handler — 수식 영역 감지·마스킹·분리

수식 포함 전략에서 이미지의 수식 영역을 흰색으로 마스킹하여
Tesseract가 텍스트만 인식하도록 처리.

주요 흐름:
  1. 이미지를 흑백(그레이스케일)으로 변환한 뒤 이진화(픽셀을 검정/흰색 두 값으로 분리)
  2. 수평 가로선(분수선)과 밀집된 작은 기호들(∑, ∫ 등)을 기준으로 수식 영역을 탐지
  3. 탐지된 수식 영역을 흰색으로 덮어(마스킹) Tesseract OCR이 텍스트만 읽도록 함
"""

import logging
from dataclasses import dataclass

import cv2               # OpenCV: 이미지 처리 라이브러리
import numpy as np       # NumPy: 배열(행렬) 연산 라이브러리
from PIL import Image as PILImage  # Pillow: 이미지 입출력 라이브러리

# 이 모듈 전용 로거 생성 — 로그 메시지를 기록할 때 사용
logger = logging.getLogger(__name__)


@dataclass
class FormulaRegion:
    """이미지에서 감지된 수식 영역 하나를 나타내는 데이터 클래스.

    이미지 좌표계에서 수식이 차지하는 직사각형 영역을 저장합니다.
    (x, y)는 직사각형의 좌상단 꼭짓점 좌표이고,
    w, h는 각각 가로·세로 픽셀 크기입니다.

    Attributes:
        x: 수식 영역 좌상단의 가로(열) 좌표 (픽셀)
        y: 수식 영역 좌상단의 세로(행) 좌표 (픽셀)
        w: 수식 영역의 가로 길이 (픽셀)
        h: 수식 영역의 세로 길이 (픽셀)
    """
    x: int
    y: int
    w: int
    h: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        """(x, y, w, h) 형태의 튜플로 변환하여 반환합니다.

        OpenCV 등의 함수에 좌표를 넘길 때 편리하게 쓸 수 있습니다.

        Returns:
            (x, y, w, h) 정수 튜플
        """
        return (self.x, self.y, self.w, self.h)


class FormulaHandler:
    """이미지에서 수식 영역을 탐지하고 마스킹(흰색으로 덮기)하는 클래스.

    Tesseract OCR이 수식 기호를 오인식하지 않도록,
    수식이 있는 영역을 미리 찾아 흰색으로 덮은 이미지를 만들어 줍니다.
    수식 자체는 별도로 좌표(FormulaRegion 목록)로 반환되어 나중에 활용할 수 있습니다.
    """

    def separate_formula(
        self, img_pil: PILImage.Image
    ) -> tuple[PILImage.Image, list[FormulaRegion]]:
        """수식 영역을 감지하고 텍스트 이미지(수식 마스킹)와 영역 목록을 반환합니다.

        입력 이미지를 분석하여 수식이 있을 것으로 판단되는 영역을 흰색으로 가린
        새로운 이미지를 생성합니다. 동시에 수식 영역의 좌표 목록도 반환합니다.

        Args:
            img_pil: 처리할 원본 PIL 이미지 (컬러 또는 흑백 모두 가능)

        Returns:
            text_img: 수식 영역이 흰색으로 마스킹된 PIL 그레이스케일 이미지
            regions: 감지된 수식 영역 목록 (수식이 없으면 빈 리스트)
        """
        # PIL 이미지를 그레이스케일("L" 모드)로 변환한 뒤 NumPy 배열로 바꿈
        # OpenCV 함수들은 NumPy 배열을 입력으로 받기 때문에 변환이 필요
        gray = np.array(img_pil.convert("L"))

        # 오츠(Otsu) 알고리즘으로 자동 임계값을 구해 이진화
        # THRESH_BINARY_INV: 어두운 픽셀(글자·기호)이 흰색(255)으로, 밝은 배경은 검정(0)으로
        # 이렇게 반전해야 윤곽선(contour) 탐지가 더 잘 됨
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        # 분수선·기호 클러스터를 분석해 수식이 있는 영역의 마스크(흑백 지도)를 생성
        mask = self._build_formula_mask(binary, gray.shape)

        # 마스크에서 실제 직사각형 영역 좌표 목록 추출
        regions = self._extract_regions(mask)

        # 마스킹: 수식 영역을 원본 이미지에서 흰색으로 덮음
        # gray.copy()로 원본 배열을 복사해 원본이 훼손되지 않도록 함
        text_arr = gray.copy()
        # mask > 0 인 픽셀(수식 영역)의 밝기값을 255(흰색)로 설정
        text_arr[mask > 0] = 255
        # NumPy 배열을 다시 PIL 이미지로 변환
        text_img = PILImage.fromarray(text_arr)

        logger.debug("수식 영역 감지: %d개", len(regions))
        return text_img, regions

    # ------------------------------------------------------------------

    def _build_formula_mask(self, binary: np.ndarray, shape: tuple) -> np.ndarray:
        """이진화 이미지에서 수식 영역에 해당하는 마스크를 생성합니다.

        두 가지 방법을 함께 사용합니다:
          1. 수평 가로선(분수선) 탐지 — 길고 가는 수평 흰 선을 찾아 분수 영역으로 판단
          2. 밀집 기호 클러스터 탐지 — ∑, ∫ 같은 작은 기호가 많이 모인 영역을 수식으로 판단

        Args:
            binary: 이진화된 NumPy 배열 (픽셀값 0 또는 255)
            shape:  원본 이미지의 (높이, 너비) 튜플

        Returns:
            수식 영역이 255, 나머지가 0인 uint8 NumPy 마스크 배열
        """
        h, w = shape
        # 마스크 초기화: 모든 픽셀을 0(수식 아님)으로 채운 빈 캔버스
        mask = np.zeros((h, w), dtype=np.uint8)

        # ── 1. 수평 세선 (분수선) 감지 ──────────────────────────────────
        # 분수선은 페이지 너비의 4% 이상 되는 가로로 긴 선이어야 함
        frac_w = max(20, int(w * 0.04))  # 최소 20픽셀, 또는 페이지 폭의 4% 중 큰 값
        # MORPH_RECT(frac_w, 1): 매우 납작한 수평 막대 모양의 커널(필터)
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (frac_w, 1))
        # MORPH_OPEN: 침식(erosion) 후 팽창(dilation) — 커널보다 짧은 선은 제거되고
        #             커널 이상으로 긴 수평선만 살아남음
        h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)

        # 살아남은 수평선들의 윤곽선 목록을 찾음
        cnts, _ = cv2.findContours(h_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in cnts:
            x, y, cw, ch = cv2.boundingRect(cnt)  # 각 수평선을 감싸는 최소 직사각형
            # 분수선 주변 위아래 확장: 분자·분모도 수식 영역에 포함시키기 위해 패딩 추가
            # 페이지 높이의 4% 또는 최소 30픽셀 중 큰 값을 위아래로 늘림
            pad = max(30, int(h * 0.04))
            y1, y2 = max(0, y - pad), min(h, y + ch + pad)   # 이미지 경계를 벗어나지 않도록 clamp
            x1, x2 = max(0, x - 10), min(w, x + cw + 10)     # 좌우로 10픽셀 여유
            mask[y1:y2, x1:x2] = 255  # 해당 영역을 마스크에 표시

        # ── 2. 밀집된 소형 윤곽 클러스터 (∑ ∫ 등) 감지 ──────────────────
        # 이진화 이미지 전체에서 모든 윤곽선을 탐색
        cnts_all, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        symbol_boxes = []  # 수식 기호일 가능성이 있는 작은 박스들을 담을 리스트
        for cnt in cnts_all:
            x, y, cw, ch = cv2.boundingRect(cnt)
            # 작고 정사각형에 가까운 심볼 (수식 기호 특징):
            #   - 가로·세로 모두 4~60픽셀 사이 (너무 크면 일반 글자, 너무 작으면 노이즈)
            #   - 가로/세로 비율이 0.2~5.0 사이 (극단적으로 납작하거나 길쭉한 건 제외)
            if 4 < cw < 60 and 4 < ch < 60 and 0.2 < cw / max(ch, 1) < 5.0:
                symbol_boxes.append((x, y, cw, ch))

        # 인접 심볼 클러스터 병합 → 수식 블록 추정
        # 기호들이 일정 거리 이내에 여러 개 모여 있으면 수식 영역으로 판단
        cluster_mask = self._cluster_symbols(symbol_boxes, (h, w))
        # 분수선 마스크와 클러스터 마스크를 OR 연산으로 합침 (둘 중 하나라도 수식이면 표시)
        mask = cv2.bitwise_or(mask, cluster_mask)

        # 노이즈 제거: 작은 독립 영역 제거
        # MORPH_CLOSE(닫힘 연산): 팽창 후 침식 — 마스크 내 작은 구멍을 메우고
        #                          인접한 영역을 연결하여 깔끔한 덩어리로 만듦
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        return mask

    def _cluster_symbols(
        self, boxes: list[tuple], shape: tuple, min_count: int = 20, gap: int = 20
    ) -> np.ndarray:
        """인접한 심볼이 min_count개 이상 모인 영역을 수식 블록으로 마스킹합니다.

        그리디(greedy) 방식으로 가까운 박스들을 하나씩 병합해 나갑니다.
        병합된 클러스터 안에 심볼이 min_count개 이상 있으면 수식 영역으로 확정합니다.

        Args:
            boxes:     수식 기호 후보 박스 목록 [(x, y, w, h), ...]
            shape:     마스크 크기 (높이, 너비)
            min_count: 수식으로 판단하기 위한 최소 심볼 개수 (기본값: 20)
            gap:       두 박스가 이 픽셀 거리 이내면 같은 클러스터로 묶음 (기본값: 20)

        Returns:
            수식 클러스터 영역이 255, 나머지가 0인 uint8 NumPy 마스크 배열
        """
        h, w = shape
        mask = np.zeros((h, w), dtype=np.uint8)
        # 박스가 없으면 빈 마스크를 바로 반환
        if not boxes:
            return mask

        # 간단한 그리디 클러스터링: 인접 박스 병합
        # merged 각 원소: [x1, y1, x2, y2, count]
        #   x1,y1: 클러스터 좌상단, x2,y2: 우하단, count: 포함된 심볼 수
        merged: list[list[int]] = []  # [x1, y1, x2, y2, count]
        for bx, by, bw, bh in boxes:
            x2, y2 = bx + bw, by + bh  # 현재 박스의 우하단 좌표
            placed = False  # 아직 어느 클러스터에도 배정되지 않은 상태
            for m in merged:
                # 현재 박스가 기존 클러스터 m과 gap 이내에 겹치거나 인접하면 병합
                # gap을 더하고 빼서 "살짝 떨어진" 박스도 같은 클러스터로 묶음
                if bx <= m[2] + gap and x2 >= m[0] - gap and by <= m[3] + gap and y2 >= m[1] - gap:
                    # 클러스터의 경계를 현재 박스를 포함하도록 확장
                    m[0] = min(m[0], bx)   # 좌상단 x를 더 왼쪽으로
                    m[1] = min(m[1], by)   # 좌상단 y를 더 위로
                    m[2] = max(m[2], x2)   # 우하단 x를 더 오른쪽으로
                    m[3] = max(m[3], y2)   # 우하단 y를 더 아래로
                    m[4] += 1              # 클러스터 내 심볼 수 증가
                    placed = True
                    break  # 첫 번째로 겹치는 클러스터에만 배정하고 탐색 종료
            if not placed:
                # 어느 클러스터와도 인접하지 않으면 새 클러스터를 생성
                merged.append([bx, by, x2, y2, 1])

        # 심볼이 min_count개 이상 모인 클러스터만 마스크에 표시
        for m in merged:
            if m[4] >= min_count:
                pad = 10  # 클러스터 경계에서 10픽셀 여유를 두어 수식이 잘리지 않도록
                x1, y1 = max(0, m[0] - pad), max(0, m[1] - pad)
                x2, y2 = min(w, m[2] + pad), min(h, m[3] + pad)
                mask[y1:y2, x1:x2] = 255

        return mask

    def _extract_regions(self, mask: np.ndarray) -> list[FormulaRegion]:
        """마스크에서 개별 수식 영역의 좌표를 추출하여 FormulaRegion 목록으로 반환합니다.

        마스크(흑백 지도)에서 흰색으로 표시된 덩어리(connected component)마다
        경계 직사각형을 구하고, 너무 작은 것(노이즈)은 걸러냅니다.

        Args:
            mask: 수식 영역이 255(흰색)로 표시된 NumPy 마스크 배열

        Returns:
            FormulaRegion 객체 목록 (면적이 200픽셀 이하인 영역은 제외)
        """
        # 마스크에서 윤곽선(각 수식 덩어리의 외곽선)을 모두 찾음
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        regions = []
        for cnt in cnts:
            x, y, w, h = cv2.boundingRect(cnt)  # 윤곽선을 감싸는 최소 직사각형
            if w * h > 200:  # 너무 작은 노이즈 제외 (면적 200픽셀 이하는 무시)
                regions.append(FormulaRegion(x, y, w, h))
        return regions


# 싱글턴(Singleton): 모듈 로드 시 FormulaHandler 인스턴스를 하나만 생성해 두고
# 다른 모듈에서 `from formula_handler import formula_handler` 로 바로 가져다 쓸 수 있음
formula_handler = FormulaHandler()
