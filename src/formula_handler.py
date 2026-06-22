# -*- coding: utf-8 -*-
"""
Formula Handler — 수식 영역 감지·마스킹·분리

수식 포함 전략에서 이미지의 수식 영역을 흰색으로 마스킹하여
Tesseract가 텍스트만 인식하도록 처리.
"""

import logging
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image as PILImage

logger = logging.getLogger(__name__)


@dataclass
class FormulaRegion:
    x: int
    y: int
    w: int
    h: int

    def as_tuple(self) -> tuple[int, int, int, int]:
        return (self.x, self.y, self.w, self.h)


class FormulaHandler:

    def separate_formula(
        self, img_pil: PILImage.Image
    ) -> tuple[PILImage.Image, list[FormulaRegion]]:
        """수식 영역을 감지하고 텍스트 이미지(수식 마스킹)와 영역 목록 반환.

        Returns:
            text_img: 수식 영역이 흰색으로 마스킹된 PIL 이미지
            regions: 감지된 수식 영역 목록 (없으면 빈 리스트)
        """
        gray = np.array(img_pil.convert("L"))
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

        mask = self._build_formula_mask(binary, gray.shape)
        regions = self._extract_regions(mask)

        # 마스킹: 수식 영역을 원본 이미지에서 흰색으로 덮음
        text_arr = gray.copy()
        text_arr[mask > 0] = 255
        text_img = PILImage.fromarray(text_arr)

        logger.debug("수식 영역 감지: %d개", len(regions))
        return text_img, regions

    # ------------------------------------------------------------------

    def _build_formula_mask(self, binary: np.ndarray, shape: tuple) -> np.ndarray:
        h, w = shape
        mask = np.zeros((h, w), dtype=np.uint8)

        # ── 1. 수평 세선 (분수선) 감지 ──────────────────────────────────
        frac_w = max(20, int(w * 0.04))  # 페이지 폭의 4% 이상
        h_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (frac_w, 1))
        h_lines = cv2.morphologyEx(binary, cv2.MORPH_OPEN, h_kernel)

        cnts, _ = cv2.findContours(h_lines, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in cnts:
            x, y, cw, ch = cv2.boundingRect(cnt)
            # 분수선 주변 위아래 확장
            pad = max(30, int(h * 0.04))
            y1, y2 = max(0, y - pad), min(h, y + ch + pad)
            x1, x2 = max(0, x - 10), min(w, x + cw + 10)
            mask[y1:y2, x1:x2] = 255

        # ── 2. 밀집된 소형 윤곽 클러스터 (∑ ∫ 등) 감지 ──────────────────
        cnts_all, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        symbol_boxes = []
        for cnt in cnts_all:
            x, y, cw, ch = cv2.boundingRect(cnt)
            # 작고 정사각형에 가까운 심볼 (수식 기호 특징)
            if 4 < cw < 60 and 4 < ch < 60 and 0.2 < cw / max(ch, 1) < 5.0:
                symbol_boxes.append((x, y, cw, ch))

        # 인접 심볼 클러스터 병합 → 수식 블록 추정
        cluster_mask = self._cluster_symbols(symbol_boxes, (h, w))
        mask = cv2.bitwise_or(mask, cluster_mask)

        # 노이즈 제거: 작은 독립 영역 제거
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

        return mask

    def _cluster_symbols(
        self, boxes: list[tuple], shape: tuple, min_count: int = 20, gap: int = 20
    ) -> np.ndarray:
        """인접한 심볼이 min_count개 이상 모인 영역을 수식 블록으로 마스킹."""
        h, w = shape
        mask = np.zeros((h, w), dtype=np.uint8)
        if not boxes:
            return mask

        # 간단한 그리디 클러스터링: 인접 박스 병합
        merged: list[list[int]] = []  # [x1, y1, x2, y2, count]
        for bx, by, bw, bh in boxes:
            x2, y2 = bx + bw, by + bh
            placed = False
            for m in merged:
                if bx <= m[2] + gap and x2 >= m[0] - gap and by <= m[3] + gap and y2 >= m[1] - gap:
                    m[0] = min(m[0], bx)
                    m[1] = min(m[1], by)
                    m[2] = max(m[2], x2)
                    m[3] = max(m[3], y2)
                    m[4] += 1
                    placed = True
                    break
            if not placed:
                merged.append([bx, by, x2, y2, 1])

        for m in merged:
            if m[4] >= min_count:
                pad = 10
                x1, y1 = max(0, m[0] - pad), max(0, m[1] - pad)
                x2, y2 = min(w, m[2] + pad), min(h, m[3] + pad)
                mask[y1:y2, x1:x2] = 255

        return mask

    def _extract_regions(self, mask: np.ndarray) -> list[FormulaRegion]:
        cnts, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        regions = []
        for cnt in cnts:
            x, y, w, h = cv2.boundingRect(cnt)
            if w * h > 200:  # 너무 작은 노이즈 제외
                regions.append(FormulaRegion(x, y, w, h))
        return regions


# 싱글턴
formula_handler = FormulaHandler()
