import pdfplumber

# 실제 내용 페이지(5~10)에서 고립 단글자 패턴 확인
pdf_path = r'data\pdf_watch\JESD235 Bandwidth Memory (HBM) OCTOBER 2013.pdf'
with pdfplumber.open(pdf_path) as pdf:
    for pg_num in range(3, min(10, len(pdf.pages))):
        page = pdf.pages[pg_num]
        words = page.extract_words()
        print("\n=== Page %d | words=%d ===" % (pg_num, len(words)))
        for w in words[:30]:
            print("  [%s] x0=%.1f top=%.1f" % (w['text'], w['x0'], w['top']))
