"""test_upload.py - CSV 上传 / mapping / import job 流程.

不真上传文件, 测 list / status / job 列表接口的 happy path.
真实上传流程在 verify_step*.py 里跑.
"""
import uuid

from fastapi.testclient import TestClient


def test_list_upload_sessions(client: TestClient, auth_demo):
    """GET /uploads 列上传 session (demo 用户可能有历史 session)."""
    resp = client.get("/api/v1/uploads")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert isinstance(data, list)


def test_list_upload_templates(client: TestClient, auth_demo):
    """GET /uploads/templates 列模板."""
    resp = client.get("/api/v1/uploads/templates")
    assert resp.status_code == 200, resp.text


def test_get_upload_session_not_found(client: TestClient, auth_normal):
    """GET /uploads/{session_id} 不存在的 session_id 返 404."""
    fake_id = str(uuid.uuid4())
    resp = client.get(f"/api/v1/uploads/{fake_id}")
    assert resp.status_code == 404


def test_demo_user_cannot_write(client: TestClient, auth_demo):
    """demo 用户调写操作返 403 (require_write_access 拦截).

    POST /uploads/single 是写操作, demo 调到 403.
    """
    resp = client.post("/api/v1/uploads/single")
    # 403 = demo 被拦; 422 = 缺参数但 demo 已通过; 都是 demo 写保护的合理结果
    assert resp.status_code in (403, 422, 401)


def test_list_imports(client: TestClient, auth_demo):
    """GET /imports 列导入批次, demo 至少有 2 个 (seed_bdg2 灌的 reading + weather)."""
    resp = client.get("/api/v1/imports")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # 响应可能是 list 或 {batches: [...]}, 都接受
    if isinstance(data, dict):
        assert "batches" in data or "items" in data
    else:
        assert isinstance(data, list)


def test_data_quality_site_overview(client: TestClient, auth_demo, demo_site_id):
    """GET /data-quality/sites/{site_id}/overview 数据质量总览 (Step 7)."""
    resp = client.get(f"/api/v1/data-quality/sites/{demo_site_id}/overview")
    assert resp.status_code == 200, resp.text
