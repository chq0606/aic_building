"""
三路混检检索服务: pgvector 向量 + tsvector 关键词 + jsonb metadata 过滤。

四个函数:
  vector_search    query -> BGE 带前缀 embedding -> pgvector cosine top-k
  keyword_search   query -> jieba 分词 -> tsvector @@ plainto_tsquery -> ts_rank_cd top-k
  metadata_filter  对召回 chunks 按 standard_no / page_range 做后处理打分 (不删)
  hybrid_search    三路融合: min-max 归一化 + 加权求和 (向量 0.5 + 关键词 0.3 + metadata 0.2)

融合算法选加权归一化不选 RRF, 因为 metadata 是过滤不是排序, RRF 融不进去。
权重默认 0.5/0.3/0.2 是提示词指定的, 在 config.py 里可调。

metadata 过滤策略: SQL 层 pre-filter (先 WHERE 后 ORDER BY), 避免 ivfflat
后过滤丢召回。pre-filter 把候选集缩到租户内 + 命中过滤条件的 chunk,
再算向量相似度, 召回率有保证。metadata_filter 这个函数名虽然叫 filter
但实际只标 metadata_match flag 不删 chunk, 给融合阶段加分用 -- 因为
SQL 已经 pre-filter 过了, 这里再删没意义, 留着是为了融合阶段让命中条件
的 chunk 加权。

租户隔离: chunk 表没有 tenant_id 字段 (在 document 表上), SQL JOIN
knowledge.document 拿 tenant_id 过滤。1737 条数据 JOIN 不慢。

BGE query 前缀: 文档侧 embedding 没加前缀 (step09 已写明原因), query
侧必须加 "为这个句子生成表示以用于检索相关文章: ", 否则向量召回
掉点严重。前缀在 config.py 里可改。

standard_no 归一化: step09 的 _extract_standard_no 把文件名 "GB 55015-2021"
规整成 "GB55015-2021" (去空格, 不加 T)。用户传 filters 时可能写 "GB 55015-2021"
或 "gb55015-2021", 这里统一去空格 + 大写匹配, 避免对不上。
"""
from __future__ import annotations

import re
from typing import Any

from loguru import logger

from app.core.config import settings
from app.db.session import get_conn
from app.services.embedding_service import embed_one, format_vector_for_pg
from app.services.knowledge_service import _tokenize_for_search


class RetrievalError(Exception):
    """检索业务错误, 给 API 层用。"""


def _normalize_standard_no(s: str | None) -> str:
    """标准号归一化: 去所有空白 + 转大写。

    step09 写入时是 'GB55015-2021' (去空格 + 不加 T, 因为文件名 'GB 55015-2021' 没斜杠 T)。
    用户传 filters 可能写 'GB 55015-2021' 或 'gb55015-2021', 这里统一格式匹配。
    """
    if not s:
        return ""
    return re.sub(r"\s+", "", s).upper()


def _row_to_chunk_dict(row: tuple, score_field: str) -> dict[str, Any]:
    """把 SQL 查询行转 chunk dict。

    SELECT 字段顺序 (vector_search / keyword_search 一致):
      0 c.id | 1 c.chunk_text | 2 c.doc_id | 3 c.page_no | 4 c.section_title
      | 5 c.section_path | 6 c.metadata | 7 standard_no (from document.metadata)
      | 8 score (score_vector 或 score_keyword)

    score_field 是 'score_vector' 或 'score_keyword', 表示这一路召回的分数
    字段名。另一路的分数字段在 hybrid_search 合并时再填。
    """
    import json as _json
    chunk_meta = row[6]
    if isinstance(chunk_meta, str):
        chunk_meta = _json.loads(chunk_meta) if chunk_meta else {}

    return {
        "chunk_id": str(row[0]),
        "content": row[1],
        "doc_id": str(row[2]),
        "page_no": row[3],
        "section_title": row[4] or "",
        "section_path": row[5] or "",
        "metadata": chunk_meta,
        "standard_no": row[7] or "",
        score_field: float(row[8]) if row[8] is not None else 0.0,
    }


def vector_search(
    query: str,
    tenant_id: str,
    top_k: int,
    filters: dict | None = None,
) -> list[dict[str, Any]]:
    """向量召回: BGE 带 query 前缀 embedding -> pgvector cosine 距离 top-k。

    返回 chunk dict 列表 (各带 score_vector 字段, 范围 [0,1] 因为 cosine
    相似度 = 1 - cosine 距离, 归一化后的 score 也在 [0,1])。

    embedding IS NULL 的 chunk 在 WHERE 里跳过 (step09 设计: embed 失败
    的 chunk 走 BM25 兜底, 不进向量召回)。
    """
    filters = filters or {}
    over_fetch = top_k * settings.retrieval_over_fetch_ratio

    # BGE 检索 query 侧前缀, 文档侧没加 (见 embedding_service 注释)
    prefixed_query = settings.retrieval_bge_query_prefix + query
    query_vec = embed_one(prefixed_query)
    vec_str = format_vector_for_pg(query_vec)

    where_clauses = [
        "c.embedding IS NOT NULL",
        "d.tenant_id = %s::uuid",
    ]
    params: list[Any] = [tenant_id]

    if filters.get("standard_no"):
        where_clauses.append("d.metadata->>'standard_no' = %s")
        params.append(_normalize_standard_no(filters["standard_no"]))

    if filters.get("page_range"):
        page_min, page_max = filters["page_range"]
        where_clauses.append("c.page_no BETWEEN %s AND %s")
        params.extend([page_min, page_max])

    # score_vector = 1 - cosine_distance, cosine_distance 范围 [0,2] (BGE 归一化后实际 [0,1])
    sql = f"""
        SELECT c.id, c.chunk_text, c.doc_id, c.page_no, c.section_title, c.section_path,
               c.metadata,
               d.metadata->>'standard_no' AS standard_no,
               1 - (c.embedding <=> %s::vector) AS score_vector
        FROM knowledge.chunk c
        JOIN knowledge.document d ON d.id = c.doc_id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY c.embedding <=> %s::vector
        LIMIT %s
    """
    # 参数顺序: score_vector 表达式里的 vec_str, 然后 where_clauses 的 tenant_id / standard_no / page_range,
    # 最后 ORDER BY 的 vec_str + LIMIT 的 over_fetch
    full_params = [vec_str] + params + [vec_str, over_fetch]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, full_params)
            rows = cur.fetchall()

    logger.debug("vector_search query={!r} tenant={} hit={}", query[:50], tenant_id[:8], len(rows))
    return [_row_to_chunk_dict(r, "score_vector") for r in rows]


def keyword_search(
    query: str,
    tenant_id: str,
    top_k: int,
    filters: dict | None = None,
) -> list[dict[str, Any]]:
    """关键词召回: jieba 分词 -> tsvector @@ plainto_tsquery -> ts_rank_cd top-k。

    用 'simple' 配置 (PG 不分词, 按 jieba 预分词后的空格切分), 跟 step09
    写入时的格式对齐。ts_rank_cd 算 cover density, 比 ts_rank 略好,
    归一化前范围不固定 ([0,16] 左右), hybrid_search 里做 min-max 归一化。

    search_vector @@ plainto_tsquery('simple', %s): plainto_tsquery 把整个
    分词字符串当一个 phrase 查 (AND 关系), 比 to_tsquery 安全 (后者对
    特殊字符会报错)。
    """
    filters = filters or {}
    over_fetch = top_k * settings.retrieval_over_fetch_ratio

    tokenized = _tokenize_for_search(query)

    where_clauses = [
        "c.search_vector @@ plainto_tsquery('simple', %s)",
        "d.tenant_id = %s::uuid",
    ]
    params: list[Any] = [tokenized, tenant_id]

    if filters.get("standard_no"):
        where_clauses.append("d.metadata->>'standard_no' = %s")
        params.append(_normalize_standard_no(filters["standard_no"]))

    if filters.get("page_range"):
        page_min, page_max = filters["page_range"]
        where_clauses.append("c.page_no BETWEEN %s AND %s")
        params.extend([page_min, page_max])

    sql = f"""
        SELECT c.id, c.chunk_text, c.doc_id, c.page_no, c.section_title, c.section_path,
               c.metadata,
               d.metadata->>'standard_no' AS standard_no,
               ts_rank_cd(c.search_vector, plainto_tsquery('simple', %s)) AS score_keyword
        FROM knowledge.chunk c
        JOIN knowledge.document d ON d.id = c.doc_id
        WHERE {' AND '.join(where_clauses)}
        ORDER BY score_keyword DESC
        LIMIT %s
    """
    # 参数顺序: SELECT 的 tokenized, 然后 where_clauses 的 tokenized + tenant_id + filters, 最后 LIMIT 的 over_fetch
    full_params = [tokenized] + params + [over_fetch]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, full_params)
            rows = cur.fetchall()

    logger.debug("keyword_search query={!r} tenant={} hit={}", query[:50], tenant_id[:8], len(rows))
    return [_row_to_chunk_dict(r, "score_keyword") for r in rows]


def metadata_filter(
    chunks: list[dict[str, Any]],
    filters: dict | None,
) -> list[dict[str, Any]]:
    """对召回的 chunks 按 filters 标 metadata_match flag (不删 chunk)。

    vector_search 和 keyword_search 已经在 SQL 层 pre-filter 过了, 这里再
    标一次是为了给融合阶段加分用 -- 命中过滤条件的 chunk 加 0.2 权重。
    如果 filters 为空 (用户没传), 所有 chunk 都标 True (不加 metadata 加分)。
    """
    if not filters:
        for c in chunks:
            c["metadata_match"] = True
        return chunks

    for c in chunks:
        c["metadata_match"] = _matches_filters(c, filters)
    return chunks


def _matches_filters(chunk: dict[str, Any], filters: dict) -> bool:
    """检查单个 chunk 是否命中所有 filters (AND 关系)。

    standard_no 做归一化匹配 (去空格 + 大写), page_range 闭区间。
    section_type 字段 step09 没写入 chunk.metadata, 一期保留接口不实现,
    传了会被忽略 (不影响其他过滤)。
    """
    if filters.get("standard_no"):
        chunk_std = _normalize_standard_no(chunk.get("standard_no", ""))
        want_std = _normalize_standard_no(filters["standard_no"])
        if chunk_std != want_std:
            return False

    if filters.get("page_range"):
        page_min, page_max = filters["page_range"]
        page_no = chunk.get("page_no")
        if page_no is None or not (page_min <= page_no <= page_max):
            return False

    return True


def _min_max_normalize(values: list[float]) -> list[float]:
    """min-max 归一化到 [0,1]。全 0 或单值时返 1.0 (避免除零, 单值视为最相关)。"""
    if not values:
        return []
    vmin, vmax = min(values), max(values)
    if vmax - vmin < 1e-9:
        return [1.0] * len(values)
    return [(v - vmin) / (vmax - vmin) for v in values]


def hybrid_search(
    query: str,
    tenant_id: str,
    top_k: int | None = None,
    filters: dict | None = None,
    weights: dict | None = None,
) -> list[dict[str, Any]]:
    """三路混检: 向量 + 关键词 + metadata 融合排序。

    流程:
      1. 两路召回 (各 over-fetch top_k * 3)
      2. metadata 后处理打标
      3. 按 chunk_id 合并去重, 两路都命中的 chunk 各取一路分数
      4. 各路分数 min-max 归一化到 [0,1]
      5. score_final = w_vector * score_vector_norm + w_keyword * score_keyword_norm + w_meta * score_meta
      6. 按 score_final 排序取 top_k

    返回每个 chunk 的字段: chunk_id / content / doc_id / page_no /
    section_title / section_path / metadata / standard_no / score_vector /
    score_keyword / score_vector_norm / score_keyword_norm / score_meta /
    score_final / metadata_match。
    """
    top_k = top_k or settings.retrieval_top_k_default
    top_k = min(top_k, settings.retrieval_top_k_max)

    weights = weights or {
        "vector": settings.retrieval_weight_vector,
        "keyword": settings.retrieval_weight_keyword,
        "metadata": settings.retrieval_weight_metadata,
    }

    vec_results = vector_search(query, tenant_id, top_k, filters)
    kw_results = keyword_search(query, tenant_id, top_k, filters)

    vec_results = metadata_filter(vec_results, filters)
    kw_results = metadata_filter(kw_results, filters)

    # 合并去重: 同一 chunk 在两路都召回时, 把两路分数都填上
    merged: dict[str, dict[str, Any]] = {}
    for r in vec_results:
        merged[r["chunk_id"]] = dict(r)
    for r in kw_results:
        cid = r["chunk_id"]
        if cid in merged:
            existing = merged[cid]
            existing["score_keyword"] = r.get("score_keyword", 0.0)
            # metadata_match 两路都应该一致 (因为 SQL pre-filter 一样), 不动
        else:
            merged[cid] = dict(r)

    chunks = list(merged.values())

    if not chunks:
        return []

    # 给只在 keyword 路出现的 chunk 补 score_vector=0, 反之亦然
    for c in chunks:
        if "score_vector" not in c:
            c["score_vector"] = 0.0
        if "score_keyword" not in c:
            c["score_keyword"] = 0.0

    # min-max 归一化 (只在有过召回的那路算, 全 0 时归一化也成 0)
    vec_scores = [c["score_vector"] for c in chunks]
    kw_scores = [c["score_keyword"] for c in chunks]

    vec_norms = _min_max_normalize(vec_scores)
    kw_norms = _min_max_normalize(kw_scores)

    for c, vn, kn in zip(chunks, vec_norms, kw_norms):
        c["score_vector_norm"] = vn
        c["score_keyword_norm"] = kn
        c["score_meta"] = 1.0 if c.get("metadata_match") else 0.0

    for c in chunks:
        c["score_final"] = (
            weights["vector"] * c["score_vector_norm"]
            + weights["keyword"] * c["score_keyword_norm"]
            + weights["metadata"] * c["score_meta"]
        )

    chunks.sort(key=lambda c: c["score_final"], reverse=True)

    logger.info(
        "hybrid_search query={!r} tenant={} vec_hit={} kw_hit={} merged={} top_k={}",
        query[:50], tenant_id[:8], len(vec_results), len(kw_results), len(chunks), top_k,
    )
    return chunks[:top_k]
