# -*- coding: utf-8 -*-
"""
Figure Extractor — Digital PDF에서 표·그림 탐지 및 이미지 크롭

사용 라이브러리:
  pdfplumber  : 표(find_tables) · 그림(page.images) 위치 탐지
  pypdfium2   : 해당 영역을 PNG 이미지로 렌더링

이 모듈의 주요 역할:
  PDF 파일의 특정 페이지에서 표(TABLE)와 그림(FIGURE)을 자동으로 찾아내고,
  해당 영역을 PNG 이미지로 잘라낸 뒤 반환합니다.
"""

import io           # 메모리 내 바이너리 스트림 처리 (파일 없이 이미지 데이터를 버퍼에 저장)
import logging      # 로그 메시지 출력용 표준 라이브러리
from dataclasses import dataclass  # 데이터 저장용 클래스를 간편하게 정의하는 데코레이터

import pdfplumber           # PDF에서 텍스트·표·이미지 위치를 파이썬 객체로 추출하는 라이브러리
import pypdfium2 as pdfium  # PDF 페이지를 픽셀 이미지(PIL)로 렌더링하는 고성능 라이브러리

# 이 모듈 전용 로거 생성 — 로그 출력 시 모듈 이름이 앞에 붙어 어디서 나온 로그인지 알 수 있음
logger = logging.getLogger(__name__)

# ── 전역 상수: 탐지 기준값 ──────────────────────────────────────────────────
_MIN_W_PT      = 50.0   # 최소 폭 (포인트 단위) — 이보다 좁은 요소는 너무 작아서 무시
_MIN_H_PT      = 30.0   # 최소 높이 (포인트 단위) — 이보다 낮은 요소는 너무 작아서 무시
_MIN_TABLE_ROWS = 2     # 표로 인정할 최소 행 수 — 행이 1개뿐인 것은 표라고 보기 어려워 제외
_RENDER_SCALE  = 2.0    # 렌더링 배율 (72dpi × 2 = 144dpi) — 클수록 이미지가 선명하지만 메모리 더 사용


@dataclass
class DetectedAsset:
    """PDF 페이지에서 탐지된 표 또는 그림 하나를 나타내는 데이터 클래스.

    dataclass는 __init__, __repr__ 등을 자동 생성해주므로
    저장용 구조체처럼 간단히 사용할 수 있습니다.

    Attributes:
        asset_type : 자산 종류 — 'TABLE'(표) 또는 'FIGURE'(그림)
        page_num   : PDF 내 페이지 번호 (0부터 시작)
        bbox       : 자산의 위치 정보 (x0, top, x1, bottom) — pdfplumber 좌표계 기준
                     top은 페이지 상단에서부터의 거리 (아래로 갈수록 증가)
        image_data : 크롭된 PNG 이미지의 바이트 데이터 (렌더링 전에는 None)
        caption    : 자산에 붙은 캡션 텍스트 (현재는 미사용, 확장을 위해 예약)
    """
    asset_type: str            # 'TABLE' | 'FIGURE'
    page_num:   int
    bbox:       tuple          # (x0, top, x1, bottom) — pdfplumber 좌표 (top=페이지 상단 기준)
    image_data: bytes | None = None   # 기본값 None: 아직 이미지를 렌더링하지 않은 상태
    caption:    str   | None = None   # 기본값 None: 캡션 없음


def extract_assets(
    pdf_path:     str,
    page_num:     int,
    render_scale: float = _RENDER_SCALE,
) -> list[DetectedAsset]:
    """PDF 페이지에서 표·그림을 탐지하여 크롭 PNG 이미지 포함 목록을 반환합니다.

    내부 처리 흐름:
      1단계 — pdfplumber로 표와 그림의 위치(bbox)를 파악
      2단계 — pypdfium2로 해당 페이지를 고해상도로 렌더링 후 각 영역을 PNG로 크롭

    반환된 목록에는 image_data가 None인 항목이 없습니다.
    (렌더링에 실패한 자산은 결과에서 제외됩니다.)

    Args:
        pdf_path     : PDF 파일의 경로 (문자열)
        page_num     : 처리할 페이지 번호 (0부터 시작, 예: 0 = 첫 번째 페이지)
        render_scale : 렌더링 배율 — 기본값 2.0 (72dpi × 2 = 144dpi)

    Returns:
        image_data가 채워진 DetectedAsset 객체의 리스트.
        탐지된 자산이 없거나 오류 발생 시 빈 리스트를 반환합니다.
    """
    assets: list[DetectedAsset] = []  # 탐지된 자산을 누적할 빈 리스트

    # ── 1. pdfplumber로 위치 탐지 ──────────────────────────────────────
    # pdfplumber는 PDF의 구조를 파이썬 객체로 파싱해 표·이미지 위치를 알려줍니다.
    try:
        with pdfplumber.open(pdf_path) as pdf:
            # 요청된 페이지 번호가 실제 페이지 수를 초과하면 빈 결과 반환
            if page_num >= len(pdf.pages):
                return assets
            page = pdf.pages[page_num]  # 처리할 페이지 객체 가져오기

            # ── 표 탐지 ─────────────────────────────────────────────
            try:
                for tbl in page.find_tables():
                    # tbl.bbox: (x0, top, x1, bottom) — 표의 경계 상자 좌표
                    x0, top, x1, bottom = tbl.bbox

                    # 너무 작은 표는 아이콘이나 장식일 가능성이 높으므로 건너뜀
                    if (x1 - x0) < _MIN_W_PT or (bottom - top) < _MIN_H_PT:
                        continue

                    # tbl.extract(): 표 내용을 2차원 리스트로 추출
                    # 내용이 없거나 행이 너무 적으면 의미 있는 표가 아니므로 제외
                    extracted = tbl.extract()
                    if not extracted or len(extracted) < _MIN_TABLE_ROWS:
                        continue

                    # 유효한 표를 자산 목록에 추가 (좌표를 float으로 명시적 변환)
                    assets.append(DetectedAsset(
                        asset_type="TABLE",
                        page_num=page_num,
                        bbox=(float(x0), float(top), float(x1), float(bottom)),
                    ))
            except Exception as exc:
                # 표 탐지 중 오류가 발생해도 그림 탐지는 계속 진행
                logger.debug("표 탐지 스킵 (p%d, %s): %s", page_num, pdf_path, exc)

            # ── 그림 탐지 — 임베디드 이미지 ─────────────────────────
            # 이미 탐지된 표들의 bbox 목록 — 그림이 표 내부에 있는지 확인할 때 사용
            table_bboxes = [a.bbox for a in assets]

            # page.images: PDF 페이지에 임베디드된 이미지들의 딕셔너리 목록
            for img in page.images:
                # 이미지의 위치 좌표를 딕셔너리에서 안전하게 꺼냄 (없으면 0.0)
                x0     = float(img.get("x0",     0))
                top    = float(img.get("top",     0))
                x1     = float(img.get("x1",     0))
                bottom = float(img.get("bottom",  0))

                # 너무 작은 이미지(아이콘, 불릿 등)는 제외
                if (x1 - x0) < _MIN_W_PT or (bottom - top) < _MIN_H_PT:
                    continue

                # 이미지가 기존에 탐지된 표 영역과 40% 이상 겹치면 표 내부 아이콘으로 판단해 제외
                # 예: 표 안에 삽입된 작은 체크 아이콘 등을 걸러냄
                if any(_overlap((x0, top, x1, bottom), tb) > 0.4 for tb in table_bboxes):
                    continue

                # 유효한 그림을 자산 목록에 추가
                assets.append(DetectedAsset(
                    asset_type="FIGURE",
                    page_num=page_num,
                    bbox=(x0, top, x1, bottom),
                ))

    except Exception as exc:
        # PDF 열기 자체가 실패한 경우 (파일 없음, 손상된 PDF 등)
        logger.error("자산 탐지 실패 (p%d, %s): %s", page_num, pdf_path, exc)
        return assets  # 지금까지 모은 결과(비어있을 수 있음)를 반환하고 종료

    # 탐지된 자산이 없으면 렌더링 단계를 건너뛰고 바로 반환
    if not assets:
        return assets

    # ── 2. pypdfium2로 페이지 렌더링 후 크롭 ──────────────────────────
    # [좌표계 변환 설명]
    # pdfplumber bbox: (x0, top, x1, bottom) — top은 페이지 상단 기준, 아래로 증가
    # pypdfium2 render → PIL Image: (0,0) = 페이지 좌상단, y축 역시 아래로 증가
    # 따라서 두 좌표계가 같은 방향이므로 render_scale만 곱하면 픽셀 좌표로 변환됩니다.
    # 변환 공식: pixel 좌표 = pdf 포인트 좌표 × render_scale
    try:
        # pypdfium2로 PDF를 열고 해당 페이지를 PIL 이미지로 렌더링
        pdf_doc  = pdfium.PdfDocument(pdf_path)
        pil_page = pdf_doc[page_num].render(scale=render_scale).to_pil()
        pw, ph   = pil_page.size  # 렌더링된 전체 페이지 이미지의 너비(pw)와 높이(ph) (픽셀 단위)

        for asset in assets:
            x0, top, x1, bottom = asset.bbox

            # PDF 포인트 좌표를 픽셀 좌표로 변환 (render_scale 배율 적용)
            # max(0, ...) / min(pw or ph, ...) 로 이미지 경계를 벗어나지 않도록 클리핑
            left  = max(0,  int(x0     * render_scale))
            upper = max(0,  int(top    * render_scale))
            right = min(pw, int(x1     * render_scale))
            lower = min(ph, int(bottom * render_scale))

            # 크롭 영역이 유효하지 않으면 건너뜀 (좌표 역전 등 이상 케이스 방어)
            if right <= left or lower <= upper:
                continue

            try:
                # PIL의 crop()으로 페이지 이미지에서 해당 영역만 잘라냄
                crop = pil_page.crop((left, upper, right, lower))

                # 잘라낸 이미지를 PNG 형식으로 메모리 버퍼(BytesIO)에 저장
                # 실제 파일을 만들지 않고 메모리에만 저장하므로 디스크 I/O 없음
                buf  = io.BytesIO()
                crop.save(buf, format="PNG")

                # 버퍼에서 PNG 바이트 데이터를 꺼내 asset에 저장
                asset.image_data = buf.getvalue()
            except Exception as exc:
                # 특정 자산의 크롭이 실패해도 다른 자산 처리는 계속 진행
                logger.warning("자산 크롭 실패 (%s p%d %s): %s",
                               pdf_path, page_num, asset.asset_type, exc)

    except Exception as exc:
        # 페이지 렌더링 자체가 실패한 경우 (PDF 손상, 메모리 부족 등)
        logger.error("페이지 렌더링 실패 (%s p%d): %s", pdf_path, page_num, exc)

    # image_data가 None인 자산(크롭 실패한 것)은 제외하고 반환
    return [a for a in assets if a.image_data]


def _overlap(a: tuple, b: tuple) -> float:
    """두 bbox가 겹치는 비율을 계산합니다 (a의 면적을 기준으로).

    그림이 표 내부에 포함되어 있는지 판단할 때 사용합니다.
    예: 반환값이 0.8이면 a의 80%가 b와 겹침을 의미합니다.

    Args:
        a : 첫 번째 경계 상자 — (x0, top, x1, bottom) 형식의 tuple
        b : 두 번째 경계 상자 — (x0, top, x1, bottom) 형식의 tuple

    Returns:
        0.0 ~ 1.0 사이의 float 값.
        0.0 = 전혀 겹치지 않음, 1.0 = a 전체가 b 안에 포함됨.
    """
    # 두 사각형의 교차 영역(intersection)의 좌상단과 우하단 좌표를 계산
    # 교차 영역의 x0는 두 x0 중 더 큰 값, x1은 두 x1 중 더 작은 값
    ix0 = max(a[0], b[0]); iy0 = max(a[1], b[1])
    ix1 = min(a[2], b[2]); iy1 = min(a[3], b[3])

    # 교차 영역이 존재하지 않으면(우하단이 좌상단보다 작거나 같으면) 겹침 없음
    if ix1 <= ix0 or iy1 <= iy0:
        return 0.0

    # 교차 영역의 넓이 계산
    inter  = (ix1 - ix0) * (iy1 - iy0)

    # a의 면적 계산 — 분모가 0이 되는 것을 방지하기 위해 최솟값 1e-9 적용
    area_a = max(1e-9, (a[2] - a[0]) * (a[3] - a[1]))

    # 겹침 비율 = 교차 면적 / a의 면적
    return inter / area_a
