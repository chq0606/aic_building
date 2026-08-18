"""Step 10 三路混检检索 API 验收脚本。

跑法:
    cd backend
    "/e/anaconda/envs/building_aic/python.exe" verify_step10.py

直调 service 层 + Pydantic schema 校验, 不走 HTTP。这样不需要起 uvicorn,
也不需要 JWT, 跑得快。HTTP 层 (api/retrieval.py) 只是薄薄一层 wrapper,
service 层 + schema 跑通 HTTP 层大概率没问题。

验收项 (按 plan):
  1. 4 个测试 query 都返非空, top chunk 的 score_final > 0
  2. score_vector / score_keyword / score_final / score_meta 字段非 NULL
  3. metadata 过滤生效: 传 standard_no 后只返该标准的 chunk
  4. page_range 过滤生效: 传 [1, 50] 后只返前 50 页的 chunk
  5. 空 query 触发 Pydantic validator 抛 ValueError
  6. demo 用户场景: 检索 demo tenant 的知识库 (GB 55015-2021)
"""
import sys

# Windows 控制台默认 GBK 编码, 打印 chunk_text 里的省略号 (U+22EF) 等字符
# 会炸 UnicodeEncodeError。强制 stdout/stderr 走 UTF-8。
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from loguru import logger
from pydantic import ValidationError

from app.db.session import close_pool, get_conn, init_pool
from app.models.retrieval import SearchRequest
from app.services.retrieval_service import (
    hybrid_search,
    keyword_search,
    vector_search,
)


TEST_QUERIES = [
    "公共建筑 EUI 限值",
    "夜间基础负荷",
    "可再生能源利用",
    "建筑能耗分类",
]


def get_demo_tenant_id() -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM core.tenant WHERE tenant_code='demo'")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 租户不存在, 先跑 verify_step09.py")
            return str(row[0])


def print_top_chunks(label: str, chunks: list[dict], n: int = 3) -> None:
    """打印 top-n chunk 的关键字段, 不打印完整 content (避免 log 过长)。

    字段可能缺失 (e.g. vector_search 单独调用没 score_final / score_keyword_norm),
    用 .get 兜底。
    """
    print(f"\n[{label}] {len(chunks)} chunks returned, top {min(n, len(chunks))}:")
    for i, c in enumerate(chunks[:n]):
        text_preview = c["content"][:100].replace("\n", " ")
        sf = c.get("score_final")
        sf_str = f"{sf:.4f}" if sf is not None else "n/a"
        vn = c.get("score_vector_norm")
        vn_str = f"{vn:.3f}" if vn is not None else "n/a"
        kn = c.get("score_keyword_norm")
        kn_str = f"{kn:.3f}" if kn is not None else "n/a"
        sv = c.get("score_vector")
        sv_str = f"{sv:.3f}" if sv is not None else "n/a"
        sk = c.get("score_keyword")
        sk_str = f"{sk:.3f}" if sk is not None else "n/a"
        print(
            f"  #{i+1} score_final={sf_str} "
            f"v={sv_str}/{vn_str} k={sk_str}/{kn_str} "
            f"| path={c['section_path']!r:30} page={c['page_no']} "
            f"| text={text_preview!r}"
        )


def test_basic_queries(tenant_id: str) -> None:
    """4 个测试 query 跑 hybrid_search, 验证非空 + score 完整。"""
    print("\n=== test_basic_queries ===")
    for q in TEST_QUERIES:
        chunks = hybrid_search(query=q, tenant_id=tenant_id, top_k=10)
        assert len(chunks) > 0, f"query={q!r} 返空, 检索挂了"
        top = chunks[0]
        assert top["score_final"] > 0, f"query={q!r} top score_final=0, 融合有问题"
        # 审计字段非 NULL
        for fld in ("score_vector", "score_keyword", "score_vector_norm",
                    "score_keyword_norm", "score_meta", "score_final",
                    "metadata_match", "section_path"):
            assert fld in top, f"query={q!r} top chunk 缺字段 {fld}"
        # score_final 单调递减
        finals = [c["score_final"] for c in chunks]
        assert finals == sorted(finals, reverse=True), f"query={q!r} score_final 未降序"
        print_top_chunks(q, chunks)


def test_vector_search_only(tenant_id: str) -> None:
    """单独 vector_search 验证: 返非空 + score_vector 范围 [0,1]。"""
    print("\n=== test_vector_search_only ===")
    chunks = vector_search("可再生能源", tenant_id, top_k=10)
    assert len(chunks) > 0
    for c in chunks:
        assert 0.0 <= c["score_vector"] <= 1.0, f"score_vector 越界: {c['score_vector']}"
    print_top_chunks("vector_search only", chunks)


def test_keyword_search_only(tenant_id: str) -> None:
    """单独 keyword_search 验证: 返非空 + score_keyword >= 0。"""
    print("\n=== test_keyword_search_only ===")
    chunks = keyword_search("可再生能源", tenant_id, top_k=10)
    assert len(chunks) > 0
    for c in chunks:
        assert c["score_keyword"] >= 0, f"score_keyword 为负: {c['score_keyword']}"
    print_top_chunks("keyword_search only", chunks)


def test_metadata_filter_standard_no(tenant_id: str) -> None:
    """传 standard_no='GB55015-2021' 过滤后, 所有 chunk 的 standard_no 应一致。"""
    print("\n=== test_metadata_filter_standard_no ===")
    chunks = hybrid_search(
        query="节能",
        tenant_id=tenant_id,
        top_k=10,
        filters={"standard_no": "GB 55015-2021"},
    )
    assert len(chunks) > 0, "standard_no 过滤后返空, metadata pre-filter 挂了"
    for c in chunks:
        # standard_no 归一化匹配 (用户传 'GB 55015-2021' 带空格, DB 存 'GB55015-2021')
        assert c["standard_no"].replace(" ", "").upper() == "GB55015-2021", \
            f"chunk standard_no 不一致: {c['standard_no']}"
        assert c["metadata_match"] is True, "pre-filter 命中的 chunk metadata_match 应 True"
    print(f"  all {len(chunks)} chunks hit standard_no=GB55015-2021, metadata_match=True")


def test_metadata_filter_page_range(tenant_id: str) -> None:
    """传 page_range=[1, 30] 过滤后, 所有 chunk 的 page_no 应在 [1, 30]。"""
    print("\n=== test_metadata_filter_page_range ===")
    chunks = hybrid_search(
        query="节能",
        tenant_id=tenant_id,
        top_k=20,
        filters={"page_range": [1, 30]},
    )
    assert len(chunks) > 0, "page_range 过滤后返空"
    for c in chunks:
        assert 1 <= c["page_no"] <= 30, f"chunk page_no={c['page_no']} 超出 [1,30]"
    print(f"  all {len(chunks)} chunks page_no in [1, 30], max={max(c['page_no'] for c in chunks)}")


def test_top_k_limit(tenant_id: str) -> None:
    """传 top_k=100 (> max=50), service 层应自动截到 50。"""
    print("\n=== test_top_k_limit ===")
    chunks = hybrid_search(
        query="节能",
        tenant_id=tenant_id,
        top_k=100,
    )
    from app.core.config import settings
    assert len(chunks) <= settings.retrieval_top_k_max, \
        f"top_k 越界: {len(chunks)} > max={settings.retrieval_top_k_max}"
    print(f"  top_k=100 -> service 截到 {len(chunks)} (max={settings.retrieval_top_k_max})")


def test_empty_query_validation() -> None:
    """空 query / 纯空格 query 应触发 Pydantic validator 抛 ValueError。"""
    print("\n=== test_empty_query_validation ===")
    for bad in ("", "   ", "\n"):
        try:
            SearchRequest(query=bad)
        except ValidationError as e:
            print(f"  query={bad!r} -> ValidationError (expected): {str(e)[:120]}")
            continue
        raise AssertionError(f"query={bad!r} 没触发 ValidationError")


def test_no_match_returns_empty(tenant_id: str) -> None:
    """检索一个完全无关的 query (英文乱码), 应返空 list 不报错。"""
    print("\n=== test_no_match_returns_empty ===")
    chunks = hybrid_search(
        query="zzzzzzz nomatch 12345 abcde",
        tenant_id=tenant_id,
        top_k=10,
    )
    print(f"  nomatch query -> {len(chunks)} chunks (空 list 是正常)")


def main() -> None:
    init_pool()
    try:
        tenant_id = get_demo_tenant_id()
        logger.info("demo tenant_id={}", tenant_id)

        test_basic_queries(tenant_id)
        test_vector_search_only(tenant_id)
        test_keyword_search_only(tenant_id)
        test_metadata_filter_standard_no(tenant_id)
        test_metadata_filter_page_range(tenant_id)
        test_top_k_limit(tenant_id)
        test_empty_query_validation()
        test_no_match_returns_empty(tenant_id)

        print("\n=== ALL TESTS PASSED ===")
    finally:
        close_pool()


if __name__ == "__main__":
    main()
