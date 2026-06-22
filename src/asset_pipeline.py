# -*- coding: utf-8 -*-
"""
Asset Pipeline — 표·그림 추출, OCR, DB 저장, 텍스트 참조 태그 삽입

디지털 PDF:
  pdfplumber 단어 좌표 기반으로 자산 위치를 파악하여
  - 표 셀 텍스트를 참조 태그로 교체
  - 그림 위치에 참조 태그 삽입
  - 분리된 문장 재연결

스캔 PDF:
  OCR 텍스트에 캡션 탐지 또는 위치 추정으로 참조 태그 삽입하고
  분리된 문장 재연결.

참조 태그 형식:  [TABLE_001], [FIGURE_001], ...
"""

import io
import logging
import os
import re
from collections import defaultdict
from pathlib import Path

import pdfplumber
import pytesseract
from PIL import Image as PILImage

from src.asset_db import AssetDatabase, asset_db
from src.config import config
from src.figure_extractor import extract_assets
from src.page_db import PageDatabase, page_db

logger = logging.getLogger(__name__)

# 문장 종결 문자
_SENT_END = frozenset(".?!;:。！？")

# 캡션 패턴 (표·그림 캡션 감지) — Fig / Fig. / Figure / 그림 모두 포함
_TABLE_CAPTION_RE = re.compile(r"^(?:표|Table)\s*\d+(?:[-\.]\d+)*(?![.-]\d)\s*[\.:]", re.IGNORECASE)
_FIGURE_CAPTION_RE = re.compile(r"^(?:그림|Figure|Fig\.?)\s*\d+(?:[-\.]\d+)*(?![.-]\d)\s*[\.:]", re.IGNORECASE)

# index만 포함된 캡션 라인 판정 (설명 텍스트 없이 번호만 존재)
# 예) "Fig 1", "Fig. 2", "Figure 3-1", "Table 4.", "그림 5"
_INDEX_ONLY_RE = re.compile(
    r"^(?:표|Table|그림|Figure|Fig\.?)\s*[\d][\d\-\.]*\.?\s*$",
    re.IGNORECASE,
)

# 이미 삽입된 참조 태그 감지 (중복 삽입 방지용)
_REF_TAG_RE = re.compile(r"\[(?:TABLE|FIGURE)_\d{3}\]")


def _extract_simple_title(body_text: str) -> str:
    """첫 페이지 본문의 첫 번째 비어있지 않은 줄을 제목으로 추정."""
    for line in body_text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:200]
    return ""


def _make_placeholder_png() -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (1, 1), (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


# 이미지 없는 캡션 전용 자산 저장 시 사용하는 1×1 흰색 PNG
_PLACEHOLDER_PNG: bytes = _make_placeholder_png()


class AssetPipeline:
    """표·그림 자산을 추출하여 별도 DB에 저장하고, 텍스트에 참조 태그를 삽입한다."""

    def __init__(self, adb: AssetDatabase = None, pdb: PageDatabase = None):
        self._adb = adb or asset_db
        self._pdb = pdb or page_db

    # ──────────────────────────────────────────────────────────────────────
    # 공개 인터페이스
    # ──────────────────────────────────────────────────────────────────────

    def process_digital(self, file_path: str, doc_id: int) -> tuple[str, int]:
        """디지털 문서(PDF/DOCX/PPTX): 자산 추출·저장 + 참조 태그가 삽입된 전체 텍스트 반환.

        Returns:
            (text_with_refs, n_assets)  — text_with_refs는 body_text만 (헤더·푸터 제외)
        """
        self._adb.delete_assets_for_document(doc_id)
        self._pdb.delete_all(doc_id)
        suffix = Path(file_path).suffix.lower()
        if suffix == ".docx":
            return self._process_docx(file_path, doc_id)
        if suffix == ".pptx":
            return self._process_pptx(file_path, doc_id)
        pdf_path = file_path
        page_bodies, page_headers, page_footers, n_assets = \
            self._extract_digital_with_assets(pdf_path, doc_id)

        # 벡터 figure 등 이미지 미탐지 캡션을 텍스트 스캔으로 보완
        counts = self._adb.count_assets(doc_id)
        page_bodies, fig_seq, tbl_seq = self._inject_caption_refs(
            page_bodies, pdf_path, doc_id,
            fig_seq=counts.get("FIGURE", 0),
            tbl_seq=counts.get("TABLE", 0),
        )
        n_assets += (fig_seq - counts.get("FIGURE", 0)) + (tbl_seq - counts.get("TABLE", 0))

        # page_contents DB 저장
        for page_num, (header, body, footer) in enumerate(
            zip(page_headers, page_bodies, page_footers)
        ):
            self._pdb.save_page_content(
                doc_id, page_num,
                header_text=header, body_text=body, footer_text=footer,
            )

        # document_metadata: 제목은 page 0 body 첫 비어있지 않은 줄로 추정
        title = _extract_simple_title(page_bodies[0] if page_bodies else "")
        self._pdb.save_document_metadata(doc_id, title=title)

        full_text = "\n\n".join(page_bodies)   # 본문만 AnythingLLM 업로드
        logger.info("디지털 자산 처리 완료: %d건 (doc_id=%d)", n_assets, doc_id)
        return full_text, n_assets

    def process_scanned(
        self,
        pdf_path: str,
        doc_id: int,
        page_texts: list[str],
    ) -> tuple[str, int]:
        """스캔 PDF: 기존 OCR 텍스트에 참조 태그 삽입.

        Args:
            page_texts: OCR 결과의 페이지별 텍스트 목록

        Returns:
            (text_with_refs, n_assets)
        """
        self._adb.delete_assets_for_document(doc_id)
        all_assets, n_assets = self._extract_all_assets(pdf_path, doc_id)

        modified = (
            self._inject_refs_scanned(page_texts, all_assets)
            if all_assets
            else list(page_texts)
        )

        # 이미지 미탐지 캡션을 텍스트 스캔으로 보완
        counts = self._adb.count_assets(doc_id)
        modified, fig_seq, tbl_seq = self._inject_caption_refs(
            modified, pdf_path, doc_id,
            fig_seq=counts.get("FIGURE", 0),
            tbl_seq=counts.get("TABLE", 0),
        )
        n_assets += (fig_seq - counts.get("FIGURE", 0)) + (tbl_seq - counts.get("TABLE", 0))

        # page_contents DB 저장 — 웹 UI가 EasyOCR 결과를 표시하려면 반드시 필요
        # (process_digital과 동일 패턴; 스캔 PDF는 header/footer 미분리이므로 body_text만)
        self._pdb.delete_all(doc_id)
        for page_num, body in enumerate(modified):
            self._pdb.save_page_content(doc_id, page_num, body_text=body)
        title = _extract_simple_title(modified[0] if modified else "")
        self._pdb.save_document_metadata(doc_id, title=title)

        logger.info("스캔 자산 처리 완료: %d건 (doc_id=%d)", n_assets, doc_id)
        return "\n\n".join(modified), n_assets

    # ──────────────────────────────────────────────────────────────────────
    # Office 문서 처리 (DOCX / PPTX)
    # ──────────────────────────────────────────────────────────────────────

    def _process_docx(self, file_path: str, doc_id: int) -> tuple[str, int]:
        """Word 문서(.docx): 본문 텍스트 추출 + 임베디드 이미지 DB 저장."""
        from docx import Document
        try:
            doc = Document(file_path)
        except Exception as e:
            logger.error("DOCX 열기 실패 (%s): %s", file_path, e)
            return "", 0

        # 본문 텍스트 조합 (문단 + 표)
        parts: list[str] = []
        for para in doc.paragraphs:
            t = para.text.strip()
            if t:
                parts.append(t)
        for table in doc.tables:
            for row in table.rows:
                cells = [c.text.strip() for c in row.cells if c.text.strip()]
                if cells:
                    parts.append(" | ".join(cells))
        text = "\n\n".join(parts)

        # 이미지 추출
        n_assets = 0
        fig_seq = 0
        for rel in doc.part.rels.values():
            if "image" not in rel.reltype:
                continue
            try:
                image_blob = rel.target_part.blob
                image_blob = self._to_png_safe(image_blob)
                ref_tag = self._adb.save_asset(
                    document_id=doc_id,
                    file_path=file_path,
                    page_num=0,
                    asset_type="FIGURE",
                    seq_in_doc=fig_seq,
                    bbox=None,
                    image_data=image_blob,
                )
                fig_seq += 1
                n_assets += 1
                logger.debug("DOCX 이미지 저장: %s (doc_id=%d)", ref_tag, doc_id)
            except Exception as e:
                logger.warning("DOCX 이미지 추출 실패 (doc_id=%d): %s", doc_id, e)

        # 캡션 텍스트 기반 ref_tag 보완
        counts = self._adb.count_assets(doc_id)
        page_texts = [text]
        page_texts, _, _ = self._inject_caption_refs(
            page_texts, file_path, doc_id,
            fig_seq=counts.get("FIGURE", 0),
            tbl_seq=counts.get("TABLE", 0),
        )
        body = page_texts[0]
        self._pdb.save_page_content(doc_id, 0, body_text=body)
        title = _extract_simple_title(body)
        self._pdb.save_document_metadata(doc_id, title=title)
        logger.info("DOCX 처리 완료: %d건 (doc_id=%d)", n_assets, doc_id)
        return body, n_assets

    def _process_pptx(self, file_path: str, doc_id: int) -> tuple[str, int]:
        """PowerPoint(.pptx): 슬라이드 텍스트 추출 + 임베디드 이미지 DB 저장."""
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE
        try:
            prs = Presentation(file_path)
        except Exception as e:
            logger.error("PPTX 열기 실패 (%s): %s", file_path, e)
            return "", 0

        page_texts: list[str] = []
        n_assets = 0
        fig_seq = 0

        for slide_num, slide in enumerate(prs.slides):
            slide_texts: list[str] = []
            for shape in slide.shapes:
                if shape.has_text_frame:
                    t = shape.text_frame.text.strip()
                    if t:
                        slide_texts.append(t)
                elif shape.has_table:
                    for row in shape.table.rows:
                        cells = [c.text_frame.text.strip() for c in row.cells
                                 if c.text_frame.text.strip()]
                        if cells:
                            slide_texts.append(" | ".join(cells))
                # 이미지 추출
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    try:
                        image_blob = shape.image.blob
                        image_blob = self._to_png_safe(image_blob)
                        ref_tag = self._adb.save_asset(
                            document_id=doc_id,
                            file_path=file_path,
                            page_num=slide_num,
                            asset_type="FIGURE",
                            seq_in_doc=fig_seq,
                            bbox=None,
                            image_data=image_blob,
                        )
                        fig_seq += 1
                        n_assets += 1
                        slide_texts.append(ref_tag)
                        logger.debug("PPTX 이미지 저장: %s (슬라이드 %d)", ref_tag, slide_num)
                    except Exception as e:
                        logger.warning("PPTX 이미지 추출 실패 (슬라이드 %d): %s", slide_num, e)
            page_texts.append("\n\n".join(slide_texts))

        # 캡션 텍스트 기반 ref_tag 보완
        counts = self._adb.count_assets(doc_id)
        page_texts, _, _ = self._inject_caption_refs(
            page_texts, file_path, doc_id,
            fig_seq=counts.get("FIGURE", 0),
            tbl_seq=counts.get("TABLE", 0),
        )
        for slide_num, slide_body in enumerate(page_texts):
            self._pdb.save_page_content(doc_id, slide_num, body_text=slide_body)
        title = _extract_simple_title(page_texts[0] if page_texts else "")
        self._pdb.save_document_metadata(doc_id, title=title)
        full_text = "\n\n".join(page_texts)
        logger.info("PPTX 처리 완료: %d건 (doc_id=%d)", n_assets, doc_id)
        return full_text, n_assets

    @staticmethod
    def _to_png_safe(image_blob: bytes) -> bytes:
        """이미지 바이트를 PIL로 열어 PNG로 변환. 변환 실패 시 원본 반환."""
        try:
            buf = io.BytesIO()
            PILImage.open(io.BytesIO(image_blob)).convert("RGBA").save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            return image_blob

    # ──────────────────────────────────────────────────────────────────────
    # 디지털 PDF 처리
    # ──────────────────────────────────────────────────────────────────────

    def _extract_digital_with_assets(
        self,
        pdf_path: str,
        doc_id: int,
    ) -> tuple[list[str], list[str], list[str], int]:
        """페이지별 자산 추출 + 참조 태그 포함 텍스트 구성.

        Returns: (page_bodies, page_headers, page_footers, n_assets)
        """
        from src.ocr_engine import OcrEngine  # 순환 import 방지

        page_bodies:  list[str] = []
        page_headers: list[str] = []
        page_footers: list[str] = []
        n_assets = 0
        table_seq = 0
        figure_seq = 0

        with pdfplumber.open(pdf_path) as pdf:
            for page_num, page in enumerate(pdf.pages):
                filtered = OcrEngine.filter_watermarks(page)
                page_height = float(page.height)
                page_width  = float(page.width)

                # ① 자산 탐지 + PNG 렌더링
                detected = extract_assets(pdf_path, page_num)

                # 전체 단어 추출 후 헤더/본문/푸터 분리
                all_words = filtered.extract_words()
                header_words, main_words, footer_words = OcrEngine._split_header_footer_words(
                    all_words, page_height
                )
                header_text = OcrEngine._words_to_text(header_words) if header_words else ""
                footer_text = OcrEngine._words_to_text(footer_words) if footer_words else ""

                if not detected:
                    if main_words:
                        gap_x = OcrEngine._find_column_gap(main_words, page_width)
                        if gap_x is None:
                            body_text = OcrEngine._words_to_text_with_paragraphs(main_words)
                        else:
                            body_start_y = OcrEngine._find_body_start_y(main_words, gap_x)
                            body_text = self._compose_multicol_text_with_captions(main_words, gap_x, body_start_y)
                    else:
                        body_text = filtered.extract_text() or ""
                    page_bodies.append(body_text)
                    page_headers.append(header_text)
                    page_footers.append(footer_text)
                    continue

                # ② DB 저장 + ref_tag 할당
                asset_refs: list[dict] = []
                for asset in detected:
                    ocr_text = self._ocr_image(asset.image_data, asset.asset_type)
                    if asset.asset_type == "TABLE":
                        seq = table_seq
                        table_seq += 1
                    else:
                        seq = figure_seq
                        figure_seq += 1

                    ref_tag = self._adb.save_asset(
                        document_id=doc_id,
                        file_path=pdf_path,
                        page_num=page_num,
                        asset_type=asset.asset_type,
                        seq_in_doc=seq,
                        bbox=asset.bbox,
                        image_data=asset.image_data,
                        ocr_text=ocr_text,
                        caption=asset.caption,
                    )
                    asset_refs.append({
                        "ref_tag":    ref_tag,
                        "bbox":       asset.bbox,
                        "asset_type": asset.asset_type,
                    })
                    n_assets += 1
                    logger.debug("자산 저장: %s (p%d)", ref_tag, page_num)

                # ③ 본문 단어 + 참조 태그 삽입 (다단 여부 판단)
                gap_x = OcrEngine._find_column_gap(main_words, page_width) if main_words else None
                if gap_x is None:
                    body_text = self._build_digital_page_text(main_words, asset_refs, page_height)
                else:
                    body_start_y = OcrEngine._find_body_start_y(main_words, gap_x)
                    body_text = self._build_digital_page_text_multicol(
                        main_words, asset_refs, page_height, gap_x, body_start_y
                    )
                page_bodies.append(body_text)
                page_headers.append(header_text)
                page_footers.append(footer_text)

        return page_bodies, page_headers, page_footers, n_assets

    def _build_digital_page_text(
        self,
        words: list,
        asset_refs: list[dict],
        page_height: float,
    ) -> str:
        """단어 목록과 자산 목록으로 참조 태그 포함 페이지 텍스트 구성."""
        from src.ocr_engine import OcrEngine

        if not asset_refs:
            return OcrEngine._words_to_text_with_paragraphs(words) if words else ""

        if not words:
            return "\n".join(
                a["ref_tag"] for a in sorted(asset_refs, key=lambda a: a["bbox"][1])
            )

        sorted_assets = sorted(asset_refs, key=lambda a: a["bbox"][1])

        # 자산 bbox 내부 단어 제거 (표 셀 텍스트 등)
        clean_words = [
            w for w in words
            if not any(self._word_in_bbox(w, a["bbox"]) for a in sorted_assets)
        ]

        # 구역 분리: 자산 위치를 기준으로 텍스트를 위/아래로 나눔
        zones: list[tuple[str, str]] = []
        current_y = 0.0

        for asset in sorted_assets:
            top    = asset["bbox"][1]
            bottom = asset["bbox"][3]

            above = [w for w in clean_words
                     if current_y - 2 <= float(w.get("top", 0)) < top - 2]
            if above:
                zones.append(("text", OcrEngine._words_to_text_with_paragraphs(above)))

            zones.append(("asset", asset["ref_tag"]))
            current_y = bottom + 2

        remaining = [w for w in clean_words if float(w.get("top", 0)) >= current_y - 2]
        if remaining:
            zones.append(("text", OcrEngine._words_to_text_with_paragraphs(remaining)))

        return self._join_zones(zones)

    def _build_digital_page_text_multicol(
        self,
        words: list,
        asset_refs: list[dict],
        page_height: float,
        gap_x: float,
        body_start_y: float | None,
    ) -> str:
        """다단 페이지: 컬럼별로 자산 참조 태그 삽입 후 좌→우 연결."""
        from src.ocr_engine import OcrEngine

        sorted_assets = sorted(asset_refs, key=lambda a: a["bbox"][1])

        # 자산 bbox 내부 단어 제거
        clean_words = [
            w for w in words
            if not any(self._word_in_bbox(w, a["bbox"]) for a in sorted_assets)
        ]

        # 헤더(전체폭)와 본문 분리
        if body_start_y is not None:
            header_words = [w for w in clean_words if float(w.get("top", 0)) < body_start_y]
            body_words   = [w for w in clean_words if float(w.get("top", 0)) >= body_start_y]
        else:
            header_words = []
            body_words   = clean_words

        header_text = OcrEngine._words_to_text_with_paragraphs(header_words) if header_words else ""

        # 본문 단어를 컬럼별 분리
        left_words, right_words = OcrEngine._split_words_by_column(body_words, gap_x)

        # 자산을 컬럼별 분류 (좌·우·스패닝)
        left_assets:  list[dict] = []
        right_assets: list[dict] = []
        span_assets:  list[dict] = []
        for asset in sorted_assets:
            ax0, _, ax1, _ = asset["bbox"]
            if ax1 <= gap_x + 5:
                left_assets.append(asset)
            elif ax0 >= gap_x - 5:
                right_assets.append(asset)
            else:
                span_assets.append(asset)

        # 각 컬럼 텍스트 구성
        left_text  = self._build_digital_page_text(left_words,  left_assets,  page_height)
        right_text = self._build_digital_page_text(right_words, right_assets, page_height)

        # 스패닝 자산: 컬럼 사이에 삽입
        if span_assets:
            span_part = "\n".join(a["ref_tag"] for a in span_assets)
            body_text = "\n\n".join(s for s in [left_text, span_part, right_text] if s)
        else:
            body_text = self._join_columns_strip_captions(left_text, right_text)

        return "\n\n".join(s for s in [header_text, body_text] if s)

    def _join_columns_strip_captions(self, left_text: str, right_text: str) -> str:
        """캡션·런닝헤더를 제거한 뒤 _join_columns로 연결하고, 제거된 항목을 재삽입.

        캡션이 좌 컬럼 끝 또는 우 컬럼 머리에 있으면 _join_columns가 연결을 막음.
        - 좌 컬럼 꼬리: caption 단락 제거
        - 우 컬럼 머리: caption 및 런닝헤더("…" 종결) 단락 제거
        제거 후 연결 → 연결된 첫 단락 뒤에 제거 항목 복원.
        """
        from src.ocr_engine import OcrEngine

        left_paras  = [p for p in left_text.strip().split('\n\n')  if p.strip()]
        right_paras = [p for p in right_text.strip().split('\n\n') if p.strip()]

        if not left_paras or not right_paras:
            return OcrEngine._join_columns(left_text, right_text)

        # 좌 컬럼 꼬리 caption 제거
        left_tail: list[str] = []
        while left_paras:
            s = left_paras[-1].strip()
            if _TABLE_CAPTION_RE.match(s) or _FIGURE_CAPTION_RE.match(s):
                left_tail.insert(0, left_paras.pop())
            else:
                break

        # 우 컬럼 머리 caption + 런닝헤더 + ref_tag 제거
        right_head: list[str] = []
        while right_paras:
            s = right_paras[0].strip()
            if (_TABLE_CAPTION_RE.match(s) or _FIGURE_CAPTION_RE.match(s)
                    or s.endswith('…')          # "…" 로 끝나는 런닝헤더
                    or _REF_TAG_RE.match(s)):   # 비트맵 탐지 ref_tag
                right_head.append(right_paras.pop(0))
            else:
                break

        if not left_tail and not right_head:
            return OcrEngine._join_columns(left_text, right_text)

        n_left = len(left_paras)   # 좌 컬럼 단락 수 (join 후에도 기준점으로 사용)
        clean_left  = '\n\n'.join(left_paras)
        clean_right = '\n\n'.join(right_paras)
        joined = OcrEngine._join_columns(clean_left, clean_right)
        joined_paras = [p for p in joined.split('\n\n') if p.strip()]
        if not joined_paras:
            return '\n\n'.join(left_tail + right_head)

        # joined_paras[:n_left] = 좌 컬럼 단락들(마지막은 우 컬럼 첫 단락과 합쳐질 수 있음)
        # joined_paras[n_left:] = 우 컬럼 나머지 단락들
        result_parts = joined_paras[:n_left] + left_tail + right_head + joined_paras[n_left:]
        return '\n\n'.join(result_parts)

    def _compose_multicol_text_with_captions(
        self,
        words: list,
        gap_x: float,
        body_start_y: float | None,
    ) -> str:
        """OcrEngine._compose_multicol_text와 동일하나 _join_columns_strip_captions 사용."""
        from src.ocr_engine import OcrEngine

        if body_start_y is None:
            left, right = OcrEngine._split_words_by_column(words, gap_x)
            return self._join_columns_strip_captions(
                OcrEngine._words_to_text_with_paragraphs(left),
                OcrEngine._words_to_text_with_paragraphs(right),
            )

        header: list = []
        left_body: list = []
        right_body: list = []

        pre_body_words = [w for w in words if float(w["top"]) < body_start_y]
        for _, line_words in OcrEngine._group_words_by_line(pre_body_words):
            if OcrEngine._line_spans_both_columns(line_words, gap_x):
                header.extend(line_words)
                continue
            left_line, right_line = OcrEngine._split_words_by_column(line_words, gap_x)
            left_body.extend(left_line)
            right_body.extend(right_line)

        body_words = [w for w in words if float(w["top"]) >= body_start_y]
        left_words, right_words = OcrEngine._split_words_by_column(body_words, gap_x)
        left_body.extend(left_words)
        right_body.extend(right_words)

        header_text = OcrEngine._words_to_text_with_paragraphs(header)
        body_joined = self._join_columns_strip_captions(
            OcrEngine._words_to_text_with_paragraphs(left_body),
            OcrEngine._words_to_text_with_paragraphs(right_body),
        )
        return "\n\n".join(s for s in (header_text, body_joined) if s)

    # ──────────────────────────────────────────────────────────────────────
    # 스캔 PDF 처리
    # ──────────────────────────────────────────────────────────────────────

    def _extract_all_assets(
        self,
        pdf_path: str,
        doc_id: int,
    ) -> tuple[list[dict], int]:
        """모든 페이지에서 자산 추출, DB 저장. Returns (asset_list, n_assets)."""
        all_assets: list[dict] = []
        n_assets = 0
        table_seq = 0
        figure_seq = 0

        try:
            with pdfplumber.open(pdf_path) as pdf:
                n_pages = len(pdf.pages)
                page_heights = [float(p.height) for p in pdf.pages]
        except Exception as e:
            logger.error("페이지 정보 조회 실패 (%s): %s", pdf_path, e)
            return [], 0

        for page_num in range(n_pages):
            detected = extract_assets(pdf_path, page_num)
            for asset in detected:
                ocr_text = self._ocr_image(asset.image_data, asset.asset_type)
                if asset.asset_type == "TABLE":
                    seq = table_seq
                    table_seq += 1
                else:
                    seq = figure_seq
                    figure_seq += 1

                ref_tag = self._adb.save_asset(
                    document_id=doc_id,
                    file_path=pdf_path,
                    page_num=page_num,
                    asset_type=asset.asset_type,
                    seq_in_doc=seq,
                    bbox=asset.bbox,
                    image_data=asset.image_data,
                    ocr_text=ocr_text,
                    caption=asset.caption,
                )
                all_assets.append({
                    "ref_tag":     ref_tag,
                    "page_num":    page_num,
                    "bbox":        asset.bbox,
                    "asset_type":  asset.asset_type,
                    "page_height": page_heights[page_num] if page_num < len(page_heights) else None,
                })
                n_assets += 1
                logger.debug("자산 저장: %s (p%d)", ref_tag, page_num)

        return all_assets, n_assets

    def _inject_refs_scanned(
        self,
        page_texts: list[str],
        assets: list[dict],
    ) -> list[str]:
        """스캔 PDF OCR 텍스트에 페이지별로 참조 태그 삽입."""
        page_asset_map: dict[int, list[dict]] = defaultdict(list)
        for a in sorted(assets, key=lambda x: (x["page_num"], x["bbox"][1])):
            page_asset_map[a["page_num"]].append(a)

        result = list(page_texts)
        for page_num, page_assets in page_asset_map.items():
            if page_num >= len(result):
                continue
            text = result[page_num]
            if not text.strip():
                result[page_num] = "\n".join(a["ref_tag"] for a in page_assets)
                continue
            result[page_num] = self._inject_into_page_text(text, page_assets)

        return result

    def _inject_into_page_text(
        self,
        text: str,
        sorted_assets: list[dict],
    ) -> str:
        """단일 페이지 텍스트에 자산 참조 태그 삽입."""
        lines = text.split("\n")

        for asset in sorted_assets:
            ref_tag      = asset["ref_tag"]
            asset_type   = asset["asset_type"]
            page_height  = asset.get("page_height")

            # ① 캡션 라인 탐색
            caption_idx = self._find_caption_line(lines, asset_type)
            if caption_idx is not None:
                lines = self._insert_after_line(lines, caption_idx, ref_tag)
                continue

            # ② 위치 추정 (bbox y 좌표 기반)
            if page_height and page_height > 0:
                rel_y  = asset["bbox"][1] / page_height
                target = max(0, min(len(lines) - 1, int(rel_y * len(lines))))
            else:
                target = len(lines) // 2

            lines = self._insert_at_boundary(lines, target, ref_tag)

        return "\n".join(lines)

    def _find_caption_line(self, lines: list[str], asset_type: str) -> int | None:
        """캡션 패턴과 일치하는 라인 인덱스 반환 (처음 발견된 것)."""
        pattern = _TABLE_CAPTION_RE if asset_type == "TABLE" else _FIGURE_CAPTION_RE
        for i, line in enumerate(lines):
            if pattern.match(line.strip()):
                return i
        return None

    def _insert_after_line(
        self, lines: list[str], idx: int, ref_tag: str
    ) -> list[str]:
        """idx 라인에 ref_tag 삽입.

        우선순위:
        1. index만 있는 라인("Fig 1", "Table 2" 등) → 같은 줄 끝에 인라인 삽입
        2. 다음 줄이 소문자로 시작(분리 문장) → 같은 줄 끝에 인라인 삽입
        3. 그 외 → 다음 줄로 삽입
        """
        new_lines = list(lines)
        before    = new_lines[idx].rstrip()

        # ① index만 포함된 라인이면 같은 줄 끝에 태그 추가
        if _INDEX_ONLY_RE.match(before.strip()):
            new_lines[idx] = before + " " + ref_tag
            return new_lines

        next_idx = idx + 1
        if next_idx < len(new_lines):
            after = new_lines[next_idx].lstrip()
            if after and after[0].islower():
                # ② 분리 문장 재연결: 캡션 끝 + ref_tag (다음 줄은 그대로 유지)
                new_lines[idx] = before + " " + ref_tag
                return new_lines

        # ③ 다음 줄로 삽입
        new_lines.insert(idx + 1, ref_tag)
        return new_lines

    def _insert_at_boundary(
        self,
        lines: list[str],
        near: int,
        ref_tag: str,
    ) -> list[str]:
        """near 근처에서 문단 경계를 찾아 ref_tag 삽입. 분리 문장이면 재연결."""
        n = len(lines)
        if n == 0:
            return [ref_tag]

        # near ±6 범위에서 문장 종결 또는 빈 라인 탐색
        best_idx = near
        found = False
        for delta in range(min(n, 7)):
            for sign in ([0] if delta == 0 else [1, -1]):
                idx = near + sign * delta
                if not (0 <= idx < n):
                    continue
                line = lines[idx].rstrip()
                if not line:                       # 빈 라인 → 단락 경계
                    best_idx = idx + 1
                    found = True
                    break
                if line[-1:] in _SENT_END:         # 문장 종결
                    best_idx = idx + 1
                    found = True
                    break
            if found:
                break

        best_idx = max(0, min(n, best_idx))

        # 분리 문장 재연결 검사
        before_line = lines[best_idx - 1].rstrip() if best_idx > 0 else ""
        after_line  = lines[best_idx].lstrip()     if best_idx < n else ""

        if (before_line
                and before_line[-1:] not in _SENT_END
                and after_line
                and after_line[0:1].islower()):
            new_lines = list(lines)
            new_lines[best_idx - 1] = before_line + " " + ref_tag + " " + after_line
            if best_idx < len(new_lines):
                del new_lines[best_idx]
            return new_lines

        new_lines = list(lines)
        new_lines.insert(best_idx, ref_tag)
        return new_lines

    # ──────────────────────────────────────────────────────────────────────
    # 캡션 텍스트 기반 보완
    # ──────────────────────────────────────────────────────────────────────

    def _inject_caption_refs(
        self,
        page_texts: list[str],
        pdf_path: str,
        doc_id: int,
        fig_seq: int,
        tbl_seq: int,
    ) -> tuple[list[str], int, int]:
        """텍스트 캡션 스캔으로 ref_tag 미삽입 Fig/Table 보완.

        이미 ref_tag가 있는 캡션 줄은 건너뜀.
        Returns: (modified_page_texts, next_fig_seq, next_tbl_seq)
        """
        result: list[str] = []
        for page_num, text in enumerate(page_texts):
            lines = text.split("\n")
            i = 0
            while i < len(lines):
                stripped = lines[i].strip()

                # 현재 줄 또는 인접 줄에 이미 ref_tag 존재 → 스킵
                if _REF_TAG_RE.search(lines[i]):
                    i += 1
                    continue
                if i + 1 < len(lines) and _REF_TAG_RE.search(lines[i + 1]):
                    i += 1
                    continue
                # 바로 이전 비어있지 않은 줄이 ref_tag → 비트맵 탐지와 중복 방지
                prev_ne = i - 1
                while prev_ne >= 0 and not lines[prev_ne].strip():
                    prev_ne -= 1
                if prev_ne >= 0 and _REF_TAG_RE.search(lines[prev_ne]):
                    i += 1
                    continue

                if _TABLE_CAPTION_RE.match(stripped):
                    asset_type, seq = "TABLE", tbl_seq
                    tbl_seq += 1
                elif _FIGURE_CAPTION_RE.match(stripped):
                    asset_type, seq = "FIGURE", fig_seq
                    fig_seq += 1
                else:
                    i += 1
                    continue

                try:
                    ref_tag = self._adb.save_asset(
                        document_id=doc_id,
                        file_path=pdf_path,
                        page_num=page_num,
                        asset_type=asset_type,
                        seq_in_doc=seq,
                        bbox=None,
                        image_data=_PLACEHOLDER_PNG,
                        ocr_text=None,
                        caption=stripped,
                    )
                    # 캡션 라인을 ref_tag으로 교체 (캡션 텍스트는 DB caption 컬럼에 보존)
                    lines[i] = ref_tag
                    logger.debug("캡션 ref_tag 교체: %s p%d '%s'",
                                 ref_tag, page_num, stripped[:50])

                    # 캡션이 문장 사이에 끼어 있으면 앞뒤 문장 재연결
                    # 조건: 앞줄이 문장 종결 기호 없음 + 뒷줄이 소문자 시작
                    prev_idx = i - 1
                    while prev_idx >= 0 and not lines[prev_idx].strip():
                        prev_idx -= 1
                    next_idx = i + 1
                    while next_idx < len(lines) and not lines[next_idx].strip():
                        next_idx += 1
                    if 0 <= prev_idx and next_idx < len(lines):
                        ptext = lines[prev_idx].rstrip()
                        ntext = lines[next_idx].lstrip()
                        if (ptext and ptext[-1] not in _SENT_END
                                and ntext and ntext[0].islower()):
                            # 앞문장 + 뒷문장으로 문장 완성 후 끝에 ref_tag 삽입
                            lines[prev_idx] = ptext + " " + ntext + " " + ref_tag
                            for k in range(next_idx, i - 1, -1):
                                del lines[k]
                            i = prev_idx
                except Exception as exc:
                    logger.warning("캡션 ref_tag 보완 실패 (p%d '%s'): %s",
                                   page_num, stripped[:40], exc)
                i += 1

            result.append("\n".join(lines))

        return result, fig_seq, tbl_seq

    # ──────────────────────────────────────────────────────────────────────
    # 공통 유틸리티
    # ──────────────────────────────────────────────────────────────────────

    def _join_zones(self, zones: list[tuple[str, str]]) -> str:
        """텍스트·자산 구역 병합. 자산으로 분리된 문장 재연결."""
        if not zones:
            return ""

        # 자산 구역별 인라인 여부 사전 계산
        inline_flags = [False] * len(zones)
        for i, (zone_type, _) in enumerate(zones):
            if zone_type != "asset":
                continue

            prev_text = ""
            for j in range(i - 1, -1, -1):
                if zones[j][0] == "text" and zones[j][1].strip():
                    prev_text = zones[j][1]
                    break

            next_text = ""
            for j in range(i + 1, len(zones)):
                if zones[j][0] == "text" and zones[j][1].strip():
                    next_text = zones[j][1]
                    break

            prev_last  = prev_text.rstrip().rsplit("\n", 1)[-1]  if prev_text.strip() else ""
            next_first = next_text.lstrip().split("\n",  1)[0]   if next_text.strip() else ""

            inline_flags[i] = (
                prev_last
                and prev_last[-1:]  not in _SENT_END
                and next_first
                and next_first[0:1].islower()
            )

        # 출력 구성
        parts: list[str] = []

        for i, (zone_type, content) in enumerate(zones):
            if zone_type == "text":
                prev_was_inline = (
                    i > 0
                    and zones[i - 1][0] == "asset"
                    and inline_flags[i - 1]
                )
                if prev_was_inline and parts:
                    parts[-1] = parts[-1] + " " + content.lstrip()
                else:
                    parts.append(content)
            else:
                if inline_flags[i] and parts:
                    parts[-1] = parts[-1].rstrip() + " " + content
                else:
                    parts.append(content)

        return "\n\n".join(p.strip() for p in parts if p.strip())

    @staticmethod
    def _word_in_bbox(word: dict, bbox: tuple, margin: float = 2.0) -> bool:
        """단어 중심점이 bbox 내부에 있는지 확인."""
        x0, top, x1, bottom = bbox
        wx0    = float(word.get("x0", 0))
        wx1    = float(word.get("x1", 0))
        w_top  = float(word.get("top", 0))
        w_bot  = float(word.get("bottom", w_top))
        wcx    = (wx0 + wx1) / 2
        wcy    = (w_top + w_bot) / 2
        return (x0 - margin <= wcx <= x1 + margin and
                top - margin <= wcy <= bottom + margin)

    def _ocr_image(self, image_data: bytes, asset_type: str) -> str:
        """Tesseract로 자산 이미지 OCR. 실패 시 빈 문자열 반환."""
        if not image_data:
            return ""
        try:
            if config.ocr_tesseract_cmd:
                pytesseract.pytesseract.tesseract_cmd = config.ocr_tesseract_cmd
            if config.ocr_tessdata_dir:
                os.environ["TESSDATA_PREFIX"] = config.ocr_tessdata_dir

            img  = PILImage.open(io.BytesIO(image_data))
            cfg  = "--psm 6" if asset_type == "TABLE" else "--psm 3"
            lang = getattr(config, "ocr_language", "kor+eng")
            return pytesseract.image_to_string(img, lang=lang, config=cfg).strip()
        except Exception as e:
            logger.warning("자산 이미지 OCR 실패 (%s): %s", asset_type, e)
            return ""


# 싱글턴
asset_pipeline = AssetPipeline()
