"""看下 GB 19577-2024 第 2 页长啥样, 把图片存成 PNG 看看。"""
import sys
import glob
import os
import fitz

sys.stdout.reconfigure(encoding="utf-8")
os.environ["TOKENIZERS_PARALLELISM"] = "false"

pdfs = glob.glob(r"E:\vscode_python\aic_building\test_pdfs\*GB 19577*.pdf")
pdf_path = pdfs[0]
print(f"PDF: {pdf_path}")

doc = fitz.open(pdf_path)
page = doc[1]  # 第 2 页
print(f"page rect: {page.rect}")

# 试不同 DPI
for dpi in [150, 200, 300]:
    pix = page.get_pixmap(dpi=dpi)
    out = rf"E:\vscode_python\aic_building\test_pdfs\_test_page2_dpi{dpi}.png"
    pix.save(out)
    print(f"dpi={dpi}, size={pix.w}x{pix.h}, saved={out}")

# 也试下封面
page0 = doc[0]
pix0 = page0.get_pixmap(dpi=200)
pix0.save(r"E:\vscode_python\aic_building\test_pdfs\_test_page0_dpi200.png")
print(f"page 0 saved (size={pix0.w}x{pix0.h})")

doc.close()
