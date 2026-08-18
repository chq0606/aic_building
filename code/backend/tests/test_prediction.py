"""test_prediction.py - 能耗预测 API (Step 19): 创建 job / 查状态 / 拿结果.

不真跑 Prophet/LSTM (要 30-60s), 只测接口形态 + 校验逻辑.
worker 集成测试见 verify_step19.py (作为冒烟测试保留).

ForecastRequest 字段是 model_type 不是 model (跟后端 models/prediction.py 对齐).
"""
import uuid

from fastapi.testclient import TestClient


def test_submit_forecast_demo_forbidden(client: TestClient, auth_demo, demo_building_id):
    """demo 用户 POST /prediction/buildings/{id}/forecast 返 403 (写保护)."""
    resp = client.post(f"/api/v1/prediction/buildings/{demo_building_id}/forecast", json={
        "model_type": "prophet",
        "horizon_days": 7,
    })
    assert resp.status_code == 403


def test_submit_forecast_normal_user(client: TestClient, auth_normal, demo_building_id):
    """非 demo 用户 POST /prediction/buildings/{id}/forecast 返 202 + job_id."""
    resp = client.post(f"/api/v1/prediction/buildings/{demo_building_id}/forecast", json={
        "model_type": "linear",  # linear 跑得快, 适合测试
        "horizon_days": 7,
    })
    assert resp.status_code == 202, resp.text
    data = resp.json()["data"]
    assert "job_id" in data or "id" in data


def test_submit_forecast_invalid_model(client: TestClient, auth_normal, demo_building_id):
    """POST 不支持的 model_type 返 422 (Pydantic Literal validator)."""
    resp = client.post(f"/api/v1/prediction/buildings/{demo_building_id}/forecast", json={
        "model_type": "arima",  # 不支持
        "horizon_days": 7,
    })
    assert resp.status_code == 422


def test_submit_forecast_invalid_horizon(client: TestClient, auth_normal, demo_building_id):
    """POST horizon_days=0 或 >90 返 422 (Pydantic ge=1 le=90 校验)."""
    resp = client.post(f"/api/v1/prediction/buildings/{demo_building_id}/forecast", json={
        "model_type": "linear",
        "horizon_days": 0,
    })
    assert resp.status_code == 422

    resp = client.post(f"/api/v1/prediction/buildings/{demo_building_id}/forecast", json={
        "model_type": "linear",
        "horizon_days": 91,
    })
    assert resp.status_code == 422


def test_submit_forecast_building_not_found(client: TestClient, auth_normal):
    """POST 不存在的 building_id 返 404 (Pydantic 接受 str, 走到 _verify_building_access 才 404)."""
    resp = client.post("/api/v1/prediction/buildings/00000000-0000-0000-0000-000000000000/forecast", json={
        "model_type": "linear",
        "horizon_days": 7,
    })
    assert resp.status_code == 404


def test_get_job_not_found(client: TestClient, auth_demo):
    """GET /prediction/jobs/{id} 不存在的 job 返 404."""
    fake_id = str(uuid.uuid4())
    resp = client.get(f"/api/v1/prediction/jobs/{fake_id}")
    assert resp.status_code == 404


def test_get_latest_prediction(client: TestClient, auth_demo, demo_building_id):
    """GET /prediction/buildings/{id}/latest 拿最近预测 (可能为空, 但接口形态对)."""
    resp = client.get(f"/api/v1/prediction/buildings/{demo_building_id}/latest")
    # 200 (有预测) 或 404 (没预测过), 都是合法
    assert resp.status_code in (200, 404), resp.text


def test_get_job_after_submit(client: TestClient, auth_normal, demo_building_id):
    """提交 -> GET /prediction/jobs/{id} 返完整 job 详情."""
    submit_resp = client.post(f"/api/v1/prediction/buildings/{demo_building_id}/forecast", json={
        "model_type": "linear",
        "horizon_days": 7,
    })
    data = submit_resp.json()["data"]
    job_id = data.get("job_id") or data.get("id")

    # 立即查 (status 应该是 PENDING 或 RUNNING)
    resp = client.get(f"/api/v1/prediction/jobs/{job_id}")
    assert resp.status_code == 200, resp.text
    job = resp.json()["data"]
    assert job["id"] == job_id or job.get("job_id") == job_id
    assert "status" in job
    assert job["status"] in ("PENDING", "RUNNING", "SUCCEEDED", "FAILED")
