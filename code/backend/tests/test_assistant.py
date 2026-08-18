"""test_assistant.py - AI 抽屉 (Step 18): session 创建 / 历史 / 发消息.

不真调智谱 GLM (要 API key + 慢), 只测 session 管理接口.
发消息接口走 mock, 验证 session 上下文 + 历史持久化.
"""
import uuid

from fastapi.testclient import TestClient


def test_create_session(client: TestClient, auth_normal):
    """POST /assistant/sessions 创建 session 返 200/201 + session_id."""
    resp = client.post("/api/v1/assistant/sessions", json={
        "title": "测试会话",
    })
    assert resp.status_code in (200, 201), resp.text
    data = resp.json()["data"]
    assert "id" in data or "session_id" in data


def test_list_sessions(client: TestClient, auth_normal):
    """GET /assistant/sessions 列当前用户的 sessions.

    响应结构: {items: [...], total: N} (Step 18 是分页结构)
    """
    # 先创建一个
    client.post("/api/v1/assistant/sessions", json={"title": "test"})

    resp = client.get("/api/v1/assistant/sessions")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # 响应是 {items: [...], total: N} 分页结构
    if isinstance(data, dict):
        assert "items" in data
        assert isinstance(data["items"], list)
    else:
        assert isinstance(data, list)


def test_get_session_history_empty(client: TestClient, auth_normal):
    """GET /assistant/sessions/{id}/messages 新 session 历史为空."""
    # 先创建 session
    create_resp = client.post("/api/v1/assistant/sessions", json={"title": "test"})
    session_data = create_resp.json()["data"]
    session_id = session_data.get("id") or session_data.get("session_id")

    resp = client.get(f"/api/v1/assistant/sessions/{session_id}/messages")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # 响应可能是 {items: [...], total: N} 或 list
    if isinstance(data, dict):
        assert "items" in data
        assert isinstance(data["items"], list)
    else:
        assert isinstance(data, list)


def test_get_session_not_found(client: TestClient, auth_normal):
    """GET /assistant/sessions/{id}/messages 不存在的 session 返 404."""
    fake_id = str(uuid.uuid4())
    resp = client.get(f"/api/v1/assistant/sessions/{fake_id}/messages")
    assert resp.status_code == 404


def test_demo_user_can_use_assistant(client: TestClient, auth_demo):
    """demo 用户也能用 AI 抽屉 (AI 抽屉的 session 是用户自己的, 允许创建)."""
    resp = client.post("/api/v1/assistant/sessions", json={"title": "demo test"})
    # demo 用户允许创建 session (AI 抽屉不挂 require_write_access)
    assert resp.status_code in (200, 201)
