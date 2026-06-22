# -*- coding: utf-8 -*-
"""
Figure Extractor — Digital PDF에서 표·그림 탐지 및 이미지 크롭

pdfplumber  : 표(find_tables) · 그림(page.images) 위치 탐지
pypdfium2   : 해당 영역을 PNG 이미지로 렌더링
"""

import io
import logging
from dataclasses import dataclass

import pdfplumber
import pypdfium2 as pdfium

logger = logging.getLogger(__name__)

_MIN_W_PT      = 50.0   # 최소 폭 (points)
_MIN_H_PT      = 30.0   # 최소 높이 (points)
_MIN_TABLE_ROWS = 2     # 단일 행 테이블 제외
_RENDER_SCALE  = 2.0    # 렌더링 배율 (72dpi × 2 = 144dpi)


@dataclass
class DetectedAsset:
    asset_type: str            # 'TABLE' | 'FIGURE'
    page_num:   int
    bbox:       tuple          # (x0, top, x1, bottom) — pdfplumber 좌표 (top=페이지 상단 기준)
    image_data: bytes | None = None
    caption:    str   | None = None


def extract_assets(
    pdf_path:     str,
    page_num:     int,
    render_scale: float = _RENDER_SCALE,
) -> list[DetectedAsset]:
    """PDF 페이지에서 표·그림을 탐지하여 크롭 PNG 이미지 포함 목록 반환.

    반환된 목록에는 image_data가 None인 항목이 없다 (렌더링 실패 시 제외).
    """
    assets: list[DetectedAsset] = []

    # ── 1. pdfplumber로 위치 탐지 ──────────────────────────────────────
    try:
        with pdfplumber.open(pdf_path) as pdf:
            if page_num >= len(pdf.pages):
                return assets
            page = pdf.pages[page_num]

            # 표 탐지
            try:
                for tbl in page.find_tables():
                    x0, top, x1, bottom = tbl.bbox
                    if (x1 - x0) < _MIN_W_PT or (bottom - top) < _MIN_H_PT:
                        continue
                    extracted = tbl.extract()
                    if not extracted or len(extracted) < _MIN_TABLE_ROWS:
                        continue
                    assets.append(DetectedAsset(
                        asset_type="TABLE",
                        page_num=page_num,
                        bbox=(float(x0), float(top), float(x1), float(bottom)),
                    ))
            except Exception as exc:
                logger.debug("표 탐지 스킵 (p%d, %s): %s", page_num, pdf_path, exc)

            # 그림 탐지 — 임베디드 이미지
            table_bboxes = [a.bbox for a in assets]
            for img in page.images:
                x0     = float(img.get("x0",     0))
                top    = float(img.get("top",     0))
                x1     = float(img.get("x1",     0))
                bottom = float(img.get("bottom",  0))
                if (x1 - x0) < _MIN_W_PT or (bottom - top) < _MIN_H_PT:
                    continue
                # 표 내부 아이콘 제외
                if any(_overlap((x0, top, x1, bottom), tb) > 0.4 for tb in table_bboxes):
                    continue
                assets.append(DetectedAsset(
                    asset_type="FIGURE",
                    page_num=page_num,
                    bbox=(x0, top, x1, bottom),
                ))

    except Exception as exc:
        logger.error("자산 탐지 실패 (p%d, %s): %s", page_num, pdf_path, exc)
        return assets

    if not assets:
        return assets

    # ── 2. pypdfium2로 페이지 렌더링 후 크롭 ──────────────────────────
    # pdfplumber bbox: (x0, top, x1, bottom) — top은 페이지 상단 기준, 아래로 증가
    # pypdfium2 render → PIL Image: (0,0) = 페이지 좌상단
    # 변환: pixel = pdf_coord × render_scale
    try:
        pdf_doc  = pdfium.PdfDocument(pdf_path)
        pil_page = pdf_doc[page_num].render(scale=render_scale).to_pil()
        pw, ph   = pil_page.size

        for asset in assets:
            x0, top, x1, bottom = asset.bbox
            left  = max(0,  int(x0     * render_scale))
            upper = max(0,  int(top    * render_scale))
            right = min(pw, int(x1     * render_scale))
            lower = min(ph, int(bottom * render_scale))
            if right <= left or lower <= upper:
                continue
            try:
                crop = pil_page.crop((left, upper, right, lower))
                buf  = io.BytesIO()
                crop.save(buf, format="PNG")
                asset.image_data = buf.getvalue()
            except Exception as exc:
                logger.warning("자산 크롭 실패 (%s p%d %s): %s",
                               pdf_path, page_num, asset.asset_type, exc)

    except Exception as exc:
        logger.error("페이지 렌더링 실패 (%s p%d): %s", pdf_path, page_num, exc)

    return [a for a in assets if a.image_data]


def _overlap(a: tuple, b: tuple) -> float:
    """두 bbox의 겹침 비율 (a 면적 기준). (x0, top, x1, bottom) 형식."""
    ix0 = max(a[0], b[0]); iy0 = max(a[1], b[1])
    ix1 = min(a[2], b[2]); iy1 = min(a[3], b[3])
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0
    inter  = (ix1 - ix0) * (iy1 - iy0)
    area_a = max(1e-9, (a[2] - a[0]) * (a[3] - a[1]))
    return inter / area_a
