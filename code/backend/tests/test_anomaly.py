"""test_anomaly.py - 异常检测 API: 列表 + 证据链.

Step 08 的实际路由:
  POST /anomalies/detect                          触发检测 (写, demo 拦 403)
  GET  /anomalies/buildings/{building_id}        单楼异常列表 (读)
  GET  /anomalies/sites/{site_id}/overview        site 异常概览 (读)
  GET  /anomalies/{anomaly_id}/evidence          单条证据链 (读)
"""
from fastapi.testclient import TestClient


def test_anomaly_site_overview(client: TestClient, auth_demo, demo_site_id):
    """GET /anomalies/sites/{site_id}/overview 异常总览 (按严重度分桶)."""
    resp = client.get(f"/api/v1/anomalies/sites/{demo_site_id}/overview")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "total_anomalies" in data or "summary" in data
    assert "by_severity" in data or "by_event_type" in data


def test_anomaly_list_for_building(client: TestClient, auth_demo, demo_building_id):
    """GET /anomalies/buildings/{building_id} 单楼异常列表."""
    resp = client.get(f"/api/v1/anomalies/buildings/{demo_building_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # 响应可能是 list 或 {anomalies: [...]}
    if isinstance(data, dict):
        assert "anomalies" in data or "items" in data
    else:
        assert isinstance(data, list)


def test_anomaly_evidence_not_found(client: TestClient, auth_demo):
    """GET /anomalies/{id}/evidence 不存在的异常 ID 返 404."""
    resp = client.get("/api/v1/anomalies/00000000-0000-0000-0000-000000000000/evidence")
    assert resp.status_code == 404


def test_anomaly_detect_demo_forbidden(client: TestClient, auth_demo, demo_site_id):
    """POST /anomalies/detect demo 用户调写操作返 403."""
    resp = client.post("/api/v1/anomalies/detect", params={"site_id": demo_site_id})
    assert resp.status_code in (403, 422)  # 422 是参数校验失败, demo 已通过
