"""快速测 EasyOCR 能不能识别国标 PDF 的中文。
跑 GB 19577-2024 第 2 页 (有 mojibake 文字层, 测 OCR 替代效果)。
"""
import sys
import time
import glob
import os

import fitz

sys.stdout.reconfigure(encoding="utf-8")
os.environ["TOKENIZERS_PARALLELISM"] = "false"

pdfs = glob.glob(r"E:\vscode_python\aic_building\test_pdfs\*GB 19577*.pdf")
print(f"找到 PDF: {pdfs}")
pdf_path = pdfs[0]

print("打开 PDF...")
doc = fitz.open(pdf_path)
page = doc[1]  # 第 2 页
print(f"页码: 2, 总页数: {doc.page_count}")

# 先看文字层
text = page.get_text("text").strip()
print(f"\n文字层 ({len(text)} chars):")
print(text[:500])

# OCR
print("\n\n初始化 EasyOCR Reader (首次会下载模型)...")
t0 = time.time()
import easyocr
reader = easyocr.Reader(["ch_sim", "en"], gpu=True)
print(f"Reader 初始化耗时: {time.time() - t0:.1f}s")

print("\nOCR 第 2 页...")
t0 = time.time()
pix = page.get_pixmap(dpi=200)
print(f"pixmap: {pix.w}x{pix.h}, n={pix.n}")
import numpy as np
img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
if pix.n >= 3:
    img = img[:, :, :3]

result = reader.readtext(img, detail=0, paragraph=True)
print(f"OCR 耗时: {time.time() - t0:.1f}s, 识别段落数: {len(result)}")
print("\nOCR 结果:")
for i, line in enumerate(result):
    print(f"[{i}] {line}")

doc.close()
