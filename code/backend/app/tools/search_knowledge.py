"""
工具: search_knowledge - 三路混检知识库。

LLM 用这个工具检索上传的国标 / 行业标准 / 技术文档, 用于回答
'EUI 限值是多少' '照明功率密度要求' 等标准查询问题, 以及节能优化
场景引用国标条款。

实现直接调 retrieval_service.hybrid_search (Step 10), 复用三路融合
(向量 + 关键词 + metadata) 排序逻辑。返简化结构 (chunk_id / content /
standard_no / section_title / score_final / doc_id), 不返内部 score_vector
等细字段, 给 LLM 看够了。
"""
from __future__ import annotations

from app.services.retrieval_service import hybrid_search


TOOL_NAME = "search_knowledge"

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "三路混检知识库 (向量 + 关键词 + metadata 融合)。"
            "用于回答 'EUI 限值是多少' 'GB 55015 强条内容' '照明功率密度要求' 等标准查询, "
            "或节能优化场景下引用国标条款。"
            "返回 top_k 个最相关文档片段, 含标准号 / 条款号 / 原文片段 / 相关度分数。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "检索查询, 自然语言即可。如 'EUI 限值' '照明功率密度' 'GB 55015 强条'。",
                },
                "top_k": {
                    "type": "integer",
                    "description": "返回 top-k 个最相关片段, 默认 5, 上限 50。",
                    "default": 5,
                },
            },
            "required": ["query"],
        },
    },
}


# chunk content 可能很长 (chunker.MAX_CHUNK_CHARS=800), 截到 300 字符给 LLM 看
_MAX_CONTENT_CHARS = 300


def call(conn, tenant_id: str, query: str, top_k: int = 5) -> dict:
    """三路混检知识库, 返 top-k chunk 的简化结构。"""
    chunks = hybrid_search(query=query, tenant_id=tenant_id, top_k=top_k)

    items = []
    for c in chunks:
        content = c.get("content", "")
        if len(content) > _MAX_CONTENT_CHARS:
            content = content[:_MAX_CONTENT_CHARS] + "..."
        items.append({
            "chunk_id": c["chunk_id"],
            "doc_id": c.get("doc_id"),
            "standard_no": c.get("standard_no", ""),
            "section_title": c.get("section_title", ""),
            "section_path": c.get("section_path", ""),
            "page_no": c.get("page_no"),
            "content_snippet": content,
            "score_final": round(c.get("score_final", 0), 4),
        })

    return {
        "query": query,
        "top_k": len(items),
        "items": items,
        "hint": (
            f"找到 {len(items)} 条相关文档片段"
            + (f", 最相关标准: {items[0]['standard_no']}" if items and items[0].get("standard_no") else "")
        ),
    }
