# -*- coding: utf-8 -*-
"""
Layout Splitter — OpenCV 기반 다단 컬럼 ROI 분리

MULTI_COLUMN 전략에서 페이지 이미지를 컬럼별로 분리하여 반환.
신문, 학술지 등 2단 또는 다단 레이아웃을 자동으로 감지하고 분할합니다.
"""

import logging
import cv2
import numpy as np
from PIL import Image as PILImage

# 이 모듈 전용 로거를 생성합니다. 로그 메시지 추적에 사용됩니다.
logger = logging.getLogger(__name__)


class LayoutSplitter:
    """페이지 이미지를 다단 컬럼으로 분리하는 클래스.

    OpenCV의 이진화(binarization)와 열 방향 픽셀 합산을 이용하여
    페이지 중앙의 여백(거터, gutter)을 감지하고, 왼쪽/오른쪽 컬럼으로 분할합니다.
    2단 레이아웃이 아니라고 판단되면 원본 이미지를 그대로 반환합니다.
    """

    def split_columns(self, images: list) -> list[list[PILImage.Image]]:
        """페이지 이미지 목록을 받아 각 페이지별 컬럼 이미지 목록을 반환합니다.

        각 페이지를 분석하여 다단 레이아웃이면 [왼쪽 컬럼, 오른쪽 컬럼] 형태로 분할하고,
        단일 레이아웃이면 [원본 이미지] 형태로 반환합니다.

        매개변수:
            images (list): PIL 이미지 객체의 목록. 각 원소는 한 페이지를 나타냅니다.

        반환값:
            list[list[PILImage.Image]]: 페이지별 컬럼 이미지 리스트의 리스트.
                예) [[page1_col1, page1_col2], [page2_col1], ...]
        """
        result = []
        for i, img in enumerate(images):
            # 각 페이지에 대해 컬럼 분리를 시도합니다.
            cols = self._split_page(img)
            logger.debug("페이지 %d: %d개 컬럼 분리", i + 1, len(cols))
            result.append(cols)
        return result

    def _split_page(self, img_pil: PILImage.Image) -> list[PILImage.Image]:
        """단일 페이지 이미지를 분석하여 컬럼으로 분할합니다.

        이미지를 흑백으로 변환 후 Otsu 이진화를 적용하고,
        페이지 본문 영역(상하 여백 제외)의 열별 픽셀 밀도를 분석하여
        중앙의 여백(거터)을 감지합니다. 거터가 명확하면 좌우로 분할하고,
        그렇지 않으면 원본 이미지를 단독으로 반환합니다.

        매개변수:
            img_pil (PILImage.Image): 분석할 페이지의 PIL 이미지 객체.

        반환값:
            list[PILImage.Image]: 분할된 컬럼 이미지 리스트.
                2단 레이아웃이면 [왼쪽 이미지, 오른쪽 이미지],
                단일 레이아웃이면 [원본 이미지].
        """
        # 이미지를 흑백(L 모드)으로 변환 후 numpy 배열로 변환합니다.
        # OpenCV 처리를 위해 numpy 배열이 필요합니다.
        gray = np.array(img_pil.convert("L"))

        # Otsu 이진화: 밝기 임계값을 자동으로 결정하여 흰색/검은색으로 변환합니다.
        # THRESH_BINARY_INV: 텍스트(어두운 부분)를 흰색(255)으로, 배경을 검은색(0)으로 반전합니다.
        # THRESH_OTSU: 히스토그램을 분석해 최적의 임계값을 자동으로 찾습니다.
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        h, w = binary.shape

        # 페이지 상단 18%와 하단 8%는 헤더/푸터 영역으로 분석에서 제외합니다.
        # 헤더/푸터의 가로 선 등이 거터 감지를 방해할 수 있기 때문입니다.
        band_top = int(h * 0.18)
        band_bottom = max(band_top + 1, int(h * 0.92))

        # 본문 영역만 잘라냅니다.
        body_band = binary[band_top:band_bottom, :]

        # 각 열(column)별로 흰색 픽셀(텍스트) 수를 합산합니다.
        # 결과 배열의 크기는 이미지 너비(w)와 동일하며,
        # 값이 크면 해당 열에 텍스트가 많다는 의미입니다.
        col_sums = body_band.sum(axis=0).astype(float)

        # 본문 영역에 텍스트가 전혀 없는 경우(빈 페이지 등) 전체 이미지로 재시도합니다.
        if col_sums.max() == 0:
            col_sums = binary.sum(axis=0).astype(float)

        max_val = col_sums.max()

        # 최댓값도 0이면 완전히 빈 이미지이므로 분할하지 않고 원본을 반환합니다.
        if max_val == 0:
            return [img_pil]

        col_sums /= max_val  # 모든 값을 0~1 범위로 정규화합니다.

        # 35~65% 구간에서 스무딩 후 최솟값 위치 탐색
        # 컬럼 사이의 여백(거터)은 페이지 중앙 부근(35~65%)에 위치한다고 가정합니다.
        s = int(w * 0.35)
        e = int(w * 0.65)
        center = col_sums[s:e]  # 중앙 구간만 추출합니다.

        # 이동 평균 커널을 만들어 노이즈를 제거합니다.
        # 커널 크기는 페이지 너비의 3%로, 작은 빈틈을 무시하기 위한 스무딩 폭입니다.
        kernel_size = max(1, int(w * 0.03))  # 페이지 너비의 3% 스무딩
        kernel = np.ones(kernel_size) / kernel_size

        # 이동 평균 합성곱으로 열별 픽셀 밀도를 부드럽게 만듭니다.
        smoothed = np.convolve(center, kernel, mode="same")

        # 스무딩된 중앙 구간에서 픽셀 밀도가 가장 낮은 위치(거터 후보)를 찾습니다.
        valley_pos = int(np.argmin(smoothed))

        # 전체 이미지 기준의 분할 x좌표로 변환합니다.
        split_x = s + valley_pos
        valley_val = smoothed[valley_pos]  # 거터 후보 지점의 밀도값 (낮을수록 좋음)

        # 중앙 구간 양쪽(왼쪽 35%, 오른쪽 35%) 텍스트 밀도의 평균을 계산합니다.
        # 이 값이 크면 좌우에 실제로 텍스트가 있는 것입니다.
        side_mean = (col_sums[:s].mean() + col_sums[e:].mean()) / 2

        # 골이 충분히 깊지 않으면 단일 컬럼으로 취급합니다.
        # side_mean이 0.01 미만이면 좌우에 텍스트가 거의 없는 것이므로 분할 의미가 없습니다.
        # valley_val이 side_mean의 35% 이상이면 거터가 뚜렷하지 않아 단일 컬럼으로 판단합니다.
        if side_mean < 0.01 or valley_val >= side_mean * 0.35:
            logger.debug("다단 분할 기준 미충족 (valley=%.3f, side_mean=%.3f)", valley_val, side_mean)
            return [img_pil]

        # split_x를 기준으로 이미지를 좌우로 잘라냅니다.
        # crop((left, top, right, bottom)) 형식입니다.
        left = img_pil.crop((0, 0, split_x, h))
        right = img_pil.crop((split_x, 0, w, h))
        return [left, right]


# 모듈 수준의 싱글턴 인스턴스입니다.
# 외부에서 LayoutSplitter()를 매번 생성하지 않고 이 객체를 가져다 쓸 수 있습니다.
splitter = LayoutSplitter()
