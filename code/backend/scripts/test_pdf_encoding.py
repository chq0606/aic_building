"""验证国标 PDF 文字层是否只是编码错乱, 还是真要 OCR。
用 glob 拿 PDF 文件名, 避开硬编码中文路径。
"""
import sys
import os
import glob
import fitz  # PyMuPDF

sys.stdout.reconfigure(encoding="utf-8")

pdf_dir = r"E:\vscode_python\aic_building\test_pdfs"
pdfs = glob.glob(os.path.join(pdf_dir, "国家标准*.pdf")) + \
       glob.glob(os.path.join(pdf_dir, "国强规范*.pdf"))

print(f"found {len(pdfs)} national standard PDFs")

# 只测前 3 个
for pdf_path in pdfs[:3]:
    fname = os.path.basename(pdf_path)
    print(f"\n========== {fname} ==========")
    try:
        doc = fitz.open(pdf_path)
    except Exception as e:
        print(f"open failed: {e}")
        continue
    print(f"pages: {doc.page_count}")

    # 取第 2 页 (跳过封面)
    page_idx = min(1, doc.page_count - 1)
    page = doc[page_idx]
    text = page.get_text("text")
    print(f"page {page_idx} raw chars: {len(text)}")
    if text.strip():
        print(f"raw text (前 200): {text[:200]!r}")
        # 试 latin-1 -> gbk
        try:
            recovered = text.encode("latin-1").decode("gbk", errors="replace")
            print(f"latin1->gbk (前 200): {recovered[:200]}")
        except Exception as e:
            print(f"latin1->gbk 失败: {e}")
    else:
        print("(empty text - 可能是扫描件)")
    doc.close()
