"""
PDF 解析服务。

输入一个 PDF 路径, 输出每页的纯文本。策略:
  1. 先用 PyMuPDF 直接 get_text, 快且免费
  2. 文本层是空的 -> 走 EasyOCR (扫描件)
  3. 文本层不空但全是 mojibake (GBK 字节被当 UTF-8 读, 出现
     "涓鍗庝汉姘戝叡鍜屽浗" 这种) -> 也走 OCR

国标 PDF 的字体没嵌 ToUnicode CMap, PyMuPDF 取出来的中文全是
乱码, 直接用没法检索。EasyOCR 走图片识别能拿到正常中文。

EasyOCR 实例复用: 模型加载 ~3-5s, 不能每页重建, 用模块级
单例懒加载。Reader 一次能 OCR 多页, 但同时把整本 PDF 装进
内存压力大, 这里改成逐页 OCR + 流式返回。

GPU: torch 装的是 cu130 GPU 版, EasyOCR 自动用 CUDA 加速,
单页 OCR ~1-2s (RTX 5060), 比纯 CPU 快 ~10x。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import fitz
import numpy as np
from loguru import logger


# mojibake 检测: GBK 字节被当 UTF-8 读, 会出现一批 CJK 扩展区的
# 罕见汉字。检测文本里罕见字占比, 超过 30% 就判定为 mojibake,
# 走 OCR。这阈值是经验值, 正常中文里也偶尔有罕见字 (生僻人名),
# 但占比不会超过 30%。
_MOJIBAKE_RATIO_THRESHOLD = 0.30


@dataclass
class PageText:
    """PDF 单页解析结果。"""
    page_no: int          # 1-indexed
    text: str
    source: str           # "text_layer" / "ocr" / "empty"


def _looks_like_mojibake(text: str) -> bool:
    """检测文本是否是 GBK 字节被当 UTF-8 读出来的乱码。

    判据: 文本里的字符落在 CJK 扩展区 (U+4E00~U+9FFF 之外的汉字区)
    占比超过阈值, 就是 mojibake。正常中文用字绝大多数在 U+4E00-U+9FFF,
    mojibake 的字符散落在扩展 A/B/C 区。
    """
    if not text:
        return False

    total_cjk = 0
    rare_cjk = 0
    for ch in text:
        cp = ord(ch)
        if 0x4E00 <= cp <= 0x9FFF:
            total_cjk += 1
        elif 0x3400 <= cp <= 0x4DBF:  # 扩展 A
            rare_cjk += 1
        elif 0x20000 <= cp <= 0x2A6DF:  # 扩展 B
            rare_cjk += 1
        elif 0x2A700 <= cp <= 0x2EBEF:  # 扩展 C-F
            rare_cjk += 1

    if total_cjk + rare_cjk == 0:
        return False
    return rare_cjk / (total_cjk + rare_cjk) > _MOJIBAKE_RATIO_THRESHOLD


# EasyOCR Reader 模块级懒加载。首次 _ocr_page 调用时才 import + 建
# Reader, 避免不调 OCR 的场景 (文字层正常的 PDF) 也吃 3s 加载时间。
_reader = None


def _get_reader():
    global _reader
    if _reader is None:
        import easyocr
        # ch_sim + en: 中文简体 + 英文。ch_sim 模型 ~100MB, 第一次会下载
        # 到 ~/.EasyOCR/model/, 后续走本地缓存。
        _reader = easyocr.Reader(["ch_sim", "en"], gpu=True)
        logger.info("EasyOCR Reader 初始化完成 (ch_sim+en, gpu=True)")
    return _reader


def _ocr_page(page: fitz.Page) -> str:
    """对单页做 OCR, 返回拼接后的纯文本。"""
    reader = _get_reader()

    # 200 DPI 是 OCR 清晰度 vs 速度的平衡点。150 DPI 小字会糊,
    # 300 DPI 慢 2 倍但提升不大。
    pix = page.get_pixmap(dpi=200)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)

    # EasyOCR 接受 RGB 或灰度。pixmap 可能是 RGBA (n=4) 或 RGB (n=3),
    # 转成 RGB 给 EasyOCR, alpha 通道没用。
    if pix.n >= 3:
        img = img[:, :, :3]

    # detail=0: 只返回识别文本不返回 bbox。batch_size=1 单页处理。
    # paragraph=True: 把同一段落的多行拼起来, 避免每行一个字符串。
    result = reader.readtext(img, detail=0, paragraph=True)
    return "\n".join(result)


def parse_pdf(pdf_path: str | Path) -> list[PageText]:
    """解析 PDF, 返回每页文本列表。

    Args:
        pdf_path: PDF 文件路径

    Returns:
        list[PageText], 长度 = PDF 页数。单页解析失败不影响其他页,
        失败页 text="" source="empty"。
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF 不存在: {pdf_path}")

    pages: list[PageText] = []
    with fitz.open(pdf_path) as doc:
        for page_idx in range(doc.page_count):
            page = doc[page_idx]
            page_no = page_idx + 1

            # 先试文字层
            raw_text = page.get_text("text").strip()

            if not raw_text:
                # 没文字层, OCR
                try:
                    text = _ocr_page(page)
                    source = "ocr" if text else "empty"
                except Exception as e:
                    logger.warning("OCR 失败 pdf={} page={}: {}", pdf_path.name, page_no, e)
                    text = ""
                    source = "empty"
            elif _looks_like_mojibake(raw_text):
                # 有文字层但乱码, 走 OCR
                logger.debug("page {} 文字层乱码, 改用 OCR: {}", page_no, pdf_path.name)
                try:
                    text = _ocr_page(page)
                    source = "ocr" if text else "empty"
                except Exception as e:
                    logger.warning("OCR 失败 (mojibake fallback) pdf={} page={}: {}",
                                   pdf_path.name, page_no, e)
                    text = ""
                    source = "empty"
            else:
                text = raw_text
                source = "text_layer"

            pages.append(PageText(page_no=page_no, text=text, source=source))

    return pages


def get_pdf_meta(pdf_path: str | Path) -> dict:
    """取 PDF 元信息: 页数、文件大小。"""
    pdf_path = Path(pdf_path)
    with fitz.open(pdf_path) as doc:
        return {
            "page_count": doc.page_count,
            "file_size_bytes": pdf_path.stat().st_size,
        }
