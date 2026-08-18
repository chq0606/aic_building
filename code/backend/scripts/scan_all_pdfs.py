"""快速扫所有 15 个国标 PDF, 看哪些有文字层、哪些是纯扫描。
"""
import sys
import os
import glob
import fitz

sys.stdout.reconfigure(encoding="utf-8")

pdf_dir = r"E:\vscode_python\aic_building\test_pdfs"
pdfs = glob.glob(os.path.join(pdf_dir, "国家标准*.pdf")) + \
       glob.glob(os.path.join(pdf_dir, "国强规范*.pdf"))

print(f"扫描 {len(pdfs)} 个国标 PDF")
print()

for pdf_path in sorted(pdfs):
    fname = os.path.basename(pdf_path)
    doc = fitz.open(pdf_path)
    pages_with_text = 0
    total_chars = 0
    sample_text = ""
    for i in range(min(doc.page_count, 5)):  # 只看前 5 页
        text = doc[i].get_text("text")
        if text.strip():
            pages_with_text += 1
            total_chars += len(text)
            if not sample_text:
                sample_text = text[:100]
    print(f"{fname[:60]:60s} | pages={doc.page_count:4d} | 前5页有文字页数={pages_with_text} | 字符={total_chars}")
    if sample_text:
        print(f"  sample: {sample_text!r}")
    doc.close()
