# -*- coding: utf-8 -*-
"""align_ocr_table.py
Tesseract 행 기준으로 ocr_comparison.md 섹션3 두 테이블을 재정렬.
"""
import re
from pathlib import Path

MD_PATH = Path(__file__).parent.parent / "ocr_comparison.md"


def parse_md_table_rows(lines, start, end):
    rows = []
    header_skipped = False
    header_line = None
    sep_line = None
    for i in range(start, end):
        line = lines[i]
        if not line.strip().startswith('|'):
            continue
        if re.match(r'\s*\|[-| :]+\|\s*$', line):
            sep_line = line.rstrip('\n')
            continue
        parts = line.split('|')
        if len(parts) < 4:
            continue
        tess   = parts[1].strip()
        easy   = parts[2].strip()
        paddle = parts[3].strip()
        if not header_skipped:
            header_skipped = True
            header_line = line.rstrip('\n')
            continue
        rows.append([tess, easy, paddle])
    return rows, header_line, sep_line


def build_aligned_rows(rows):
    tess_lines = [r[0] for r in rows]

    easy_words = []
    for r in rows:
        if r[1]:
            easy_words.extend(r[1].split())

    paddle_words = []
    for r in rows:
        if r[2]:
            paddle_words.extend(r[2].split())

    tess_word_counts = [len(t.split()) if t else 0 for t in tess_lines]
    tess_total   = sum(tess_word_counts)
    easy_total   = len(easy_words)
    paddle_total = len(paddle_words)

    easy_result   = []
    paddle_result = []
    easy_idx   = 0
    paddle_idx = 0

    for i, tline in enumerate(tess_lines):
        if not tline:
            easy_result.append('')
            paddle_result.append('')
            continue

        tw = tess_word_counts[i]
        remaining_non_empty = sum(1 for j in range(i + 1, len(tess_lines)) if tess_lines[j])

        # EasyOCR 분배
        if tess_total > 0 and easy_total > 0:
            if remaining_non_empty == 0:
                easy_chunk = easy_words[easy_idx:]
                easy_idx = len(easy_words)
            else:
                take = max(1, round(tw / tess_total * easy_total))
                easy_chunk = easy_words[easy_idx:easy_idx + take]
                easy_idx += take
        else:
            easy_chunk = []
        easy_result.append(' '.join(easy_chunk))

        # PaddleOCR 분배
        if tess_total > 0 and paddle_total > 0:
            if remaining_non_empty == 0:
                paddle_chunk = paddle_words[paddle_idx:]
                paddle_idx = len(paddle_words)
            else:
                take = max(1, round(tw / tess_total * paddle_total))
                paddle_chunk = paddle_words[paddle_idx:paddle_idx + take]
                paddle_idx += take
        else:
            paddle_chunk = []
        paddle_result.append(' '.join(paddle_chunk))

    new_rows = []
    for i, tline in enumerate(tess_lines):
        new_rows.append([tline, easy_result[i], paddle_result[i]])
    return new_rows


def find_section_table_range(lines, section_marker):
    """section_marker 이후 첫 번째 테이블 블록의 (start, end) 라인 인덱스 반환."""
    sec_idx = None
    for i, line in enumerate(lines):
        if section_marker in line:
            sec_idx = i
            break
    if sec_idx is None:
        return None, None

    # 테이블 시작: | 로 시작하는 첫 행
    tbl_start = None
    for i in range(sec_idx + 1, len(lines)):
        if lines[i].strip().startswith('|'):
            tbl_start = i
            break
    if tbl_start is None:
        return None, None

    # 테이블 끝: | 로 시작하지 않는 첫 행 (빈 줄 포함)
    tbl_end = tbl_start
    for i in range(tbl_start, len(lines)):
        s = lines[i].strip()
        if s.startswith('|'):
            tbl_end = i + 1
        elif s == '':
            # 빈 줄은 허용 (테이블 내부일 수도 있으니 계속 탐색)
            continue
        else:
            break

    return tbl_start, tbl_end


def process_table(lines, section_marker):
    tbl_start, tbl_end = find_section_table_range(lines, section_marker)
    if tbl_start is None:
        print(f"[WARN] 섹션 '{section_marker}' 테이블을 찾지 못함.")
        return lines

    rows, header_line, sep_line = parse_md_table_rows(lines, tbl_start, tbl_end)
    if not rows:
        print(f"[WARN] 섹션 '{section_marker}' 파싱된 행 없음.")
        return lines

    new_rows = build_aligned_rows(rows)

    new_table_lines = []
    if header_line:
        new_table_lines.append(header_line + '\n')
    if sep_line:
        new_table_lines.append(sep_line + '\n')
    for r in new_rows:
        new_table_lines.append(f'| {r[0]} | {r[1]} | {r[2]} |\n')

    result = lines[:tbl_start] + new_table_lines + lines[tbl_end:]
    print(f"[INFO] '{section_marker}': {len(rows)}행 → 재정렬 완료 ({len(new_rows)}행)")
    return result


def main():
    text = MD_PATH.read_text(encoding='utf-8')
    lines = text.splitlines(keepends=True)
    print(f"원본 파일 총 {len(lines)}줄")

    lines = process_table(lines, "GPU 모드 기준")
    lines = process_table(lines, "CPU 모드 기준")

    MD_PATH.write_text(''.join(lines), encoding='utf-8')
    print(f"저장 완료: {MD_PATH}")

    # GPU 테이블 앞 10행 + 마지막 5행 샘플 출력
    _, gpu_end = find_section_table_range(lines, "GPU 모드 기준")
    _, gpu_start = find_section_table_range(lines, "GPU 모드 기준")
    gpu_s, gpu_e = find_section_table_range(lines, "GPU 모드 기준")
    if gpu_s is not None:
        data_lines = [l for l in lines[gpu_s:gpu_e] if l.strip().startswith('|')
                      and not re.match(r'\s*\|[-| :]+\|\s*$', l)][2:]  # 헤더+구분선 제외
        print("\n=== GPU 테이블 처음 10행 ===")
        for l in data_lines[:10]:
            print(l.rstrip())
        print("\n=== GPU 테이블 마지막 5행 ===")
        for l in data_lines[-5:]:
            print(l.rstrip())


if __name__ == "__main__":
    main()
