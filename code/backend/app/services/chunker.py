"""
国标 PDF 文本切分器。

策略: 按条款号切分 (国标结构通常为 "3.1 术语" / "3.1.1 一般规定"),
单 chunk 不超过 800 字。

为什么按条款切而不是定长滑窗:
  - 国标条款是语义最小单位, 检索时回答 "3.2.1 是什么意思" 直接定位
  - 定长滑窗会切断条款, 检索结果含半句话读不顺
  - 800 字上限保证不超 BGE 模型 512 token 上下文 (1024 token 也能塞,
    但 800 字留点余量给 system prompt)

跨页条款处理: 同一条款跨页时按页切分, 各自作为独立 chunk。理由是
chunk 的 page_no 字段需要单值, 给前端展示用。检索时同一条款的多个
chunk 会同时召回, 拼起来读。

超长条款处理: 单条款 > 800 字按段落 (\n\n) 切。段落还超就硬切
(很少见, 通常是表格或长公式)。

section_path (完整条款路径) 维护:
  chunk_pages 跨页维护一个条款段栈 stack。新条款号按 "." 拆段, 与栈
  找最长公共前缀 LCP, 栈截断到 LCP 后追加新段。path = " -> ".join
  (前 i+1 段拼接), 如 stack=["3","1","1"] -> "3 -> 3.1 -> 3.1.1"。
  检索结果返回完整路径, 前端按层级树展示, AI 抽屉引用国标条款时也带
  完整上下文给 LLM。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .pdf_parser import PageText


# 单 chunk 字符上限。BGE-large-zh 上下文 512 token, 中文 1 字 ~1 token,
# 800 字留余量给 prompt 调度。超长就按段落或硬切。
MAX_CHUNK_CHARS = 800

# 国标条款号: 1 / 1.1 / 1.1.1 / 1.1.1.1 (最多 4 层, 实际很少超过 3 层)
# 行首匹配 (前面是 \n 或文本开头), 后面跟空格 + 标题文字。
# 不匹配 "1、" "1." (这种是列表项, 不是条款号)。
CLAUSE_PATTERN = re.compile(
    r"(?:^|\n)\s*(\d+(?:\.\d+){0,3})\s+(?=\S)",
    re.MULTILINE,
)


@dataclass
class Chunk:
    """切分后的 chunk。"""
    page_no: int            # 1-indexed, 来源 PDF 页码
    section_title: str      # 当前条款号 "3.1.1", 无条款号时是 ""
    section_path: str       # 完整条款路径 "3 -> 3.1 -> 3.1.1", 无条款号时是 ""
    chunk_text: str


def _split_long_text(text: str, max_chars: int) -> list[str]:
    """把超长文本切成 <= max_chars 的段。

    优先按段落 (\n\n) 切, 段落还超就按行 (\n) 切, 还超就硬切。
    """
    if len(text) <= max_chars:
        return [text]

    parts: list[str] = []

    # 先按双换行切段
    paragraphs = text.split("\n\n")
    buf = ""
    for para in paragraphs:
        if len(para) > max_chars:
            # 段落本身超长, 先把 buf 收尾
            if buf:
                parts.append(buf)
                buf = ""
            # 按单换行再切
            lines = para.split("\n")
            line_buf = ""
            for line in lines:
                if len(line) > max_chars:
                    # 单行超长, 硬切
                    if line_buf:
                        parts.append(line_buf)
                        line_buf = ""
                    for i in range(0, len(line), max_chars):
                        parts.append(line[i:i + max_chars])
                elif len(line_buf) + len(line) + 1 <= max_chars:
                    line_buf = (line_buf + "\n" + line) if line_buf else line
                else:
                    parts.append(line_buf)
                    line_buf = line
            if line_buf:
                parts.append(line_buf)
        elif len(buf) + len(para) + 2 <= max_chars:
            buf = (buf + "\n\n" + para) if buf else para
        else:
            parts.append(buf)
            buf = para
    if buf:
        parts.append(buf)

    return parts


def _split_page_by_clauses(text: str) -> list[tuple[str, str]]:
    """按条款号切单页文本, 返回 [(section_title, body), ...]。

    没找到条款号的整页作为一条 "" section。
    """
    if not text.strip():
        return []

    matches = list(CLAUSE_PATTERN.finditer(text))
    if not matches:
        return [("", text.strip())]

    parts: list[tuple[str, str]] = []
    for i, m in enumerate(matches):
        section_no = m.group(1)
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()
        if body:
            parts.append((section_no, body))

    # 第一个条款号之前的内容 (封面/扉页页眉等), 单独成块
    pre = text[:matches[0].start()].strip()
    if pre:
        parts.insert(0, ("", pre))

    return parts


def chunk_pages(pages: list[PageText]) -> list[Chunk]:
    """把 PageText 列表按条款切成 Chunk 列表。

    Args:
        pages: pdf_parser.parse_pdf() 的输出

    Returns:
        list[Chunk], 顺序按页码 + chunk 内顺序。chunk_index 由调用方
        在写库时自增 (knowledge_service 负责)。

    section_path 维护: 跨页保持栈状态, 同一条款跨页时栈不变 (新页继续
    用上一页切到的栈), 新条款号按 LCP 算法更新栈。空 section_no (封面/
    扉页/页眉) 不动栈, 用当前栈作为 path。
    """
    chunks: list[Chunk] = []
    stack: list[str] = []  # 当前条款段栈, e.g. ["3", "1", "1"] 对应 "3.1.1"

    for page in pages:
        if not page.text.strip():
            continue

        page_parts = _split_page_by_clauses(page.text)
        for section_no, body in page_parts:
            if section_no:
                segments = section_no.split(".")
                # 找最长公共前缀 LCP, 截断栈后追加新段
                lcp = 0
                while (lcp < len(stack) and lcp < len(segments)
                       and stack[lcp] == segments[lcp]):
                    lcp += 1
                stack = stack[:lcp] + segments[lcp:]

            # stack=["3","1","1"] -> "3 -> 3.1 -> 3.1.1"
            # 每段 path[i] = 前 i+1 段用 "." 拼接
            section_path = " -> ".join(
                ".".join(stack[:i + 1]) for i in range(len(stack))
            )

            sub_parts = _split_long_text(body, MAX_CHUNK_CHARS)
            for sub in sub_parts:
                if sub.strip():
                    chunks.append(Chunk(
                        page_no=page.page_no,
                        section_title=section_no,
                        section_path=section_path,
                        chunk_text=sub.strip(),
                    ))

    return chunks
