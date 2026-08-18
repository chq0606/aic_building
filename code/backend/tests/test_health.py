"""test_health.py - 健康检查端点."""
from fastapi.testclient import TestClient


def test_health(client: TestClient):
    """GET /api/v1/health 进程存活就返 200."""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["code"] == 0
    assert data["data"]["status"] == "up"
    assert "version" in data["data"]
    assert "app_name" in data["data"]


def test_ready(client: TestClient, db_pool):
    """GET /api/v1/ready 数据库连得上返 200."""
    resp = client.get("/api/v1/ready")
    assert resp.status_code == 200
    data = resp.json()
    assert data["data"]["status"] == "ready"
    assert data["data"]["db"] == "ok"
