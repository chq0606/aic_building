"""test_query.py - 查询聚合 API: 园区总览 / 建筑列表 / 单楼时序."""
from fastapi.testclient import TestClient


def test_park_overview(client: TestClient, auth_demo, demo_site_id):
    """GET /query/park/overview 园区总览返 6 栋楼 + 总能耗."""
    resp = client.get("/api/v1/query/park/overview", params={"site_id": demo_site_id})
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "building_count" in data
    assert data["building_count"] == 6  # BDG2 demo 6 栋楼
    assert "total_kwh" in data
    assert data["total_kwh"] > 0


def test_park_overview_nonexistent_site(client: TestClient, auth_demo):
    """不存在的 site_id 返 404."""
    resp = client.get("/api/v1/query/park/overview", params={
        "site_id": "00000000-0000-0000-0000-000000000000",
    })
    assert resp.status_code == 404


def test_buildings_list(client: TestClient, auth_demo, demo_site_id):
    """GET /query/buildings 返 6 栋楼, 按 total_kwh 降序.

    响应结构: {buildings: [...], site_id, sort, time_range}
    """
    resp = client.get("/api/v1/query/buildings", params={
        "site_id": demo_site_id,
        "sort": "total_kwh",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    # 响应是 {buildings: [...], ...}, 不是 list 直接
    assert "buildings" in data
    buildings = data["buildings"]
    assert len(buildings) == 6
    # 验证按 total_kwh 降序
    kwhs = [b["total_kwh"] for b in buildings]
    assert kwhs == sorted(kwhs, reverse=True)


def test_buildings_list_sort_eui(client: TestClient, auth_demo, demo_site_id):
    """GET /query/buildings sort=eui_kwh_per_m2 排序生效."""
    resp = client.get("/api/v1/query/buildings", params={
        "site_id": demo_site_id,
        "sort": "eui_kwh_per_m2",
    })
    assert resp.status_code == 200
    buildings = resp.json()["data"]["buildings"]
    euis = [b["eui_kwh_per_m2"] for b in buildings]
    assert euis == sorted(euis, reverse=True)


def test_building_timeseries(client: TestClient, auth_demo, demo_building_id):
    """GET /query/buildings/{id}/timeseries 返单楼时序 (日粒度 365 天)."""
    resp = client.get(f"/api/v1/query/buildings/{demo_building_id}/timeseries", params={
        "granularity": "day",
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "points" in data
    assert len(data["points"]) > 0
    p = data["points"][0]
    assert "ts" in p
    assert "value" in p


def test_building_timeseries_not_found(client: TestClient, auth_demo):
    """不存在的 building_id 返 404."""
    resp = client.get("/api/v1/query/buildings/00000000-0000-0000-0000-000000000000/timeseries")
    assert resp.status_code == 404


def test_building_weather(client: TestClient, auth_demo, demo_building_id):
    """GET /query/buildings/{id}/weather 返天气关联数据."""
    resp = client.get(f"/api/v1/query/buildings/{demo_building_id}/weather")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "points" in data


def test_building_energy_composition(client: TestClient, auth_demo, demo_building_id):
    """GET /query/buildings/{id}/energy-composition 返能源构成.

    响应结构: {building_id, building_code, display_name, composition: [{type, kwh, pct}, ...]}
    """
    resp = client.get(f"/api/v1/query/buildings/{demo_building_id}/energy-composition")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "composition" in data
    # demo 楼至少有一种能源 (electricity / gas)
    assert len(data["composition"]) > 0


def test_buildings_compare(client: TestClient, auth_demo, demo_building_id):
    """GET /query/buildings/compare 多楼对比.

    响应结构: {buildings: [...], granularity, metric, time_range}
    """
    resp = client.get("/api/v1/query/buildings/compare", params={
        "building_ids": demo_building_id,
    })
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "buildings" in data
    assert len(data["buildings"]) == 1
