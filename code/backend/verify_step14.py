"""Step 14 园区 3D 探索页后端验收脚本。

跑法:
    cd backend
    "/e/anaconda/envs/building_aic/python.exe" verify_step14.py

直调 service 层 + 走 HTTP (FastAPI TestClient) 双轨验收。HTTP 走
dependency_overrides 把 get_current_user 替换成测试 stub, 跳过 JWT
解码, 直接注入 demo 租户的 CurrentUser。这样不用真起 uvicorn 也不用
真去 /auth/login 拿 token, 跑得快。

验收项 (按 plan):
  1. list_sites(demo) 返 1 个 site (Bobcat), building_count=6, 字段完整
  2. GET /api/v1/sites HTTP 200 + 字段完整 (走 TestClient)
  3. GET /api/v1/sites/{bobcat_id}/scene?metric=eui&start=2017-01-01&end=2017-12-31
     返 6 栋楼 + 每栋字段完整 (building_id/dimensions/position/color_metric/
     anomaly_status/energy_composition/model_kind)
  4. 切 metric=total_kwh / anomaly_count, color_metric.level 合理变化
"""
import sys

# Windows 控制台默认 GBK 编码, 打印中文 building name 会炸 UnicodeEncodeError
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from fastapi.testclient import TestClient

from app.core.deps import CurrentUser, get_current_user
from app.db.session import close_pool, get_conn, init_pool
from app.main import app
from app.services.site_service import list_sites


def get_demo_tenant_id() -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM core.tenant WHERE tenant_code='demo'")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 租户不存在, 先跑 seed")
            return str(row[0])


def get_demo_user(tenant_id: str) -> CurrentUser:
    """构造测试用 CurrentUser (demo 用户身份, 跳过 JWT)。

    直接从 core.user 表查 demo 用户, 拿真实 user_id/tenant_id/username。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, tenant_id, username, is_demo
                FROM core."user" WHERE username = 'demo'
            """)
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 用户不存在, 先跑 ensure_demo_user")
    return CurrentUser(
        user_id=str(row[0]),
        tenant_id=str(row[1]),
        username=row[2],
        is_demo=row[3],
    )


def test_list_sites_service(tenant_id: str) -> dict:
    """测 1: 直调 list_sites service 层。

    demo 租户只有 Bobcat 一个 site, building_count=6。
    """
    print("\n=== test_list_sites_service ===")
    sites = list_sites(tenant_id)
    assert len(sites) == 1, f"demo 应只有 1 个 site, 实际 {len(sites)}"
    site = sites[0]
    assert site["site_code"] == "Bobcat", f"site_code 应 Bobcat, 实际 {site['site_code']}"
    assert site["building_count"] == 6, f"building_count 应 6, 实际 {site['building_count']}"
    # latitude/longitude demo 数据没填, 应为 None
    assert "latitude" in site
    assert "longitude" in site
    print(f"  list_sites 返 {len(sites)} 个 site")
    print(f"  site_id={site['site_id'][:8]}... code={site['site_code']} name={site['site_name']}")
    print(f"  building_count={site['building_count']} lat={site['latitude']} lng={site['longitude']}")
    return site


def test_get_sites_http(stub_user: CurrentUser) -> dict:
    """测 2: GET /api/v1/sites 走 HTTP。

    用 TestClient + dependency_overrides 跳过 JWT, 直接注入 stub_user。
    """
    print("\n=== test_get_sites_http ===")
    app.dependency_overrides[get_current_user] = lambda: stub_user
    try:
        client = TestClient(app)
        resp = client.get("/api/v1/sites")
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, f"HTTP 应 200, 实际 {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["code"] == 0, f"业务码应 0, 实际 {body['code']}"
    sites = body["data"]
    assert len(sites) == 1, f"应返 1 个 site, 实际 {len(sites)}"
    site = sites[0]
    assert site["site_code"] == "Bobcat"
    assert site["building_count"] == 6
    # 字段完整性
    for field in ("site_id", "site_code", "site_name", "latitude", "longitude", "building_count"):
        assert field in site, f"缺字段: {field}"
    print(f"  HTTP 200 ✓ 返 {len(sites)} 个 site, code={body['code']}")
    return site


def test_get_scene_http(stub_user: CurrentUser, site_id: str) -> dict:
    """测 3: GET /api/v1/sites/{id}/scene 走 HTTP。

    传 metric=eui + 2017 全年时间范围, 返 6 栋楼 + 每栋字段完整。
    """
    print("\n=== test_get_scene_http ===")
    app.dependency_overrides[get_current_user] = lambda: stub_user
    try:
        client = TestClient(app)
        resp = client.get(
            f"/api/v1/sites/{site_id}/scene",
            params={"metric": "eui", "start": "2017-01-01T00:00:00+00:00", "end": "2017-12-31T23:59:59+00:00"},
        )
    finally:
        app.dependency_overrides.clear()

    assert resp.status_code == 200, f"HTTP 应 200, 实际 {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["code"] == 0, f"业务码应 0, 实际 {body['code']}"
    data = body["data"]
    assert data["site_id"] == site_id
    assert data["metric"] == "eui"
    assert "time_range" in data
    buildings = data["buildings"]
    assert len(buildings) == 6, f"应返 6 栋楼, 实际 {len(buildings)}"
    print(f"  HTTP 200 ✓ 返 {len(buildings)} 栋楼, metric={data['metric']}")

    # 每栋楼字段完整性
    required_fields = (
        "building_id", "building_code", "display_name", "model_kind",
        "dimensions", "position", "color_metric", "anomaly_status",
        "energy_composition", "model_id",
    )
    for b in buildings:
        for f in required_fields:
            assert f in b, f"building {b.get('building_code')} 缺字段: {f}"
        # dimensions 子字段
        dims = b["dimensions"]
        for df in ("length_m", "width_m", "height_m", "floors_count"):
            assert df in dims, f"dimensions 缺字段: {df}"
        # color_metric 子字段
        cm = b["color_metric"]
        for cf in ("metric", "value", "level"):
            assert cf in cm, f"color_metric 缺字段: {cf}"
        assert cm["level"] in ("low", "mid", "high", "critical"), f"level 非法: {cm['level']}"
        # anomaly_status 子字段
        ans = b["anomaly_status"]
        for af in ("has_anomaly", "severity_max", "count", "by_severity"):
            assert af in ans, f"anomaly_status 缺字段: {af}"
        assert "composition" in b["energy_composition"], "energy_composition 缺 composition"
    print(f"  所有楼字段完整 ✓ (building_id/dimensions/position/color_metric/anomaly_status/energy_composition)")

    # 打印每栋楼的着色等级, 方便人眼检查
    print("  各楼 color_metric:")
    for b in buildings:
        print(f"    {b['building_code']:32s} kind={b['model_kind']:9s} "
              f"value={b['color_metric']['value']} level={b['color_metric']['level']} "
              f"anomaly={b['anomaly_status']['count']}")
    return data


def test_metric_switch(stub_user: CurrentUser, site_id: str) -> None:
    """测 4: 切 metric, 验证 color_metric.level 跟着变。

    demo 数据 6 栋楼, EUI 和总能耗的排序应该不一样, level 分布也应不同。
    """
    print("\n=== test_metric_switch ===")
    app.dependency_overrides[get_current_user] = lambda: stub_user
    try:
        client = TestClient(app)
        levels_by_metric: dict[str, list[str]] = {}
        for metric in ("eui", "total_kwh", "anomaly_count"):
            resp = client.get(
                f"/api/v1/sites/{site_id}/scene",
                params={"metric": metric, "start": "2017-01-01T00:00:00+00:00", "end": "2017-12-31T23:59:59+00:00"},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["metric"] == metric
            levels = [b["color_metric"]["level"] for b in data["buildings"]]
            levels_by_metric[metric] = levels
            print(f"  metric={metric:14s} levels={levels}")
    finally:
        app.dependency_overrides.clear()

    # 三个 metric 的 levels 至少要有差异 (如果都一样说明着色没生效)
    unique_tuples = set(tuple(v) for v in levels_by_metric.values())
    assert len(unique_tuples) >= 2, f"三个 metric 的 level 分布完全一样, 着色没生效: {levels_by_metric}"
    print(f"  三个 metric level 分布至少 2 种 ✓")


def main() -> None:
    init_pool()
    try:
        tenant_id = get_demo_tenant_id()
        stub_user = get_demo_user(tenant_id)
        logger_info = f"demo tenant_id={tenant_id[:8]} user={stub_user.username} is_demo={stub_user.is_demo}"
        print(logger_info)

        # 直调 service
        site = test_list_sites_service(tenant_id)

        # 走 HTTP
        test_get_sites_http(stub_user)
        test_get_scene_http(stub_user, site["site_id"])
        test_metric_switch(stub_user, site["site_id"])

        print("\n=== ALL TESTS PASSED ===")
    finally:
        close_pool()


if __name__ == "__main__":
    main()
