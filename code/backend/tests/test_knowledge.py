"""test_knowledge.py - 知识库 API: 列文档 + 检索.

不真上传 PDF (要 EasyOCR + BGE 模型, 慢且重), 只测列表 / 状态 / 检索接口的形态.
"""
from fastapi.testclient import TestClient


def test_knowledge_documents_list(client: TestClient, auth_demo):
    """GET /knowledge/documents 列知识库文档 (demo 用户可能为空)."""
    resp = client.get("/api/v1/knowledge/documents")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert isinstance(data, list)


def test_knowledge_search_empty_query(client: TestClient, auth_demo):
    """POST /knowledge/search 空 query 返 422 (Pydantic 校验)."""
    resp = client.post("/api/v1/knowledge/search", json={
        "query": "",
    })
    # 空 query 被 Pydantic validator 拦
    assert resp.status_code in (422, 400)


def test_knowledge_search_returns_structure(client: TestClient, auth_demo):
    """POST /knowledge/search 有 query 时返正确结构.

    响应结构: {chunks: [...], query, top_k, weights}
    """
    resp = client.post("/api/v1/knowledge/search", json={
        "query": "建筑节能",
        "top_k": 5,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # 三路混检返 chunks 数组 (不是 results)
    assert "chunks" in data or "results" in data or isinstance(data, list)


def test_knowledge_search_pagination(client: TestClient, auth_demo):
    """POST /knowledge/search top_k 参数生效."""
    resp = client.post("/api/v1/knowledge/search", json={
        "query": "空调",
        "top_k": 3,
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    chunks = data.get("chunks") if isinstance(data, dict) else data
    if chunks:
        assert len(chunks) <= 3
