# -*- coding: utf-8 -*-
"""
Layout Splitter — OpenCV 기반 다단 컬럼 ROI 분리

MULTI_COLUMN 전략에서 페이지 이미지를 컬럼별로 분리하여 반환.
"""

import logging
import cv2
import numpy as np
from PIL import Image as PILImage

logger = logging.getLogger(__name__)


class LayoutSplitter:

    def split_columns(self, images: list) -> list[list[PILImage.Image]]:
        """페이지별 컬럼 이미지 목록 반환.
        각 페이지는 [left_col_img, right_col_img, ...] 리스트.
        분할 불가 시 원본 이미지 단독 반환.
        """
        result = []
        for i, img in enumerate(images):
            cols = self._split_page(img)
            logger.debug("페이지 %d: %d개 컬럼 분리", i + 1, len(cols))
            result.append(cols)
        return result

    def _split_page(self, img_pil: PILImage.Image) -> list[PILImage.Image]:
        gray = np.array(img_pil.convert("L"))
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        h, w = binary.shape
        band_top = int(h * 0.18)
        band_bottom = max(band_top + 1, int(h * 0.92))
        body_band = binary[band_top:band_bottom, :]
        col_sums = body_band.sum(axis=0).astype(float)
        if col_sums.max() == 0:
            col_sums = binary.sum(axis=0).astype(float)
        max_val = col_sums.max()
        if max_val == 0:
            return [img_pil]

        col_sums /= max_val  # 0~1 정규화

        # 35~65% 구간에서 스무딩 후 최솟값 위치 탐색
        s = int(w * 0.35)
        e = int(w * 0.65)
        center = col_sums[s:e]
        kernel_size = max(1, int(w * 0.03))  # 페이지 너비의 3% 스무딩
        kernel = np.ones(kernel_size) / kernel_size
        smoothed = np.convolve(center, kernel, mode="same")

        valley_pos = int(np.argmin(smoothed))
        split_x = s + valley_pos
        valley_val = smoothed[valley_pos]
        side_mean = (col_sums[:s].mean() + col_sums[e:].mean()) / 2

        # 골이 충분히 깊지 않으면 단일 컬럼으로 취급
        if side_mean < 0.01 or valley_val >= side_mean * 0.35:
            logger.debug("다단 분할 기준 미충족 (valley=%.3f, side_mean=%.3f)", valley_val, side_mean)
            return [img_pil]

        left = img_pil.crop((0, 0, split_x, h))
        right = img_pil.crop((split_x, 0, w, h))
        return [left, right]


# 싱글턴
splitter = LayoutSplitter()
