"""test_floor.py - 楼层分析 API 测试.

覆盖 8 个 GET 接口 + 加总约束自检 + 404/400 错误路径。
fixture 走 conftest.py: db_pool session 级, demo_building_id session 级缓存,
client + auth_demo 函数级。

Step 11a: 加了 auto-split POST 接口 + floors.csv 上传的测试。
"""
import uuid
from io import BytesIO

from fastapi.testclient import TestClient

from app.db.session import get_conn


def _list_floors(client: TestClient, building_id: str) -> list[dict]:
    """辅助: 拿楼层列表, 返回 floors 数组。多个 test 复用。"""
    resp = client.get(f"/api/v1/buildings/{building_id}/floors")
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["floors"]


def test_list_floors(client: TestClient, auth_demo, demo_building_id):
    """GET /buildings/{id}/floors 返楼层列表 + 每层摘要。

    Franklin 是 demo_building_id (alphabetically first), 有 3 层。
    """
    resp = client.get(f"/api/v1/buildings/{demo_building_id}/floors")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    floors = data["floors"]
    assert len(floors) >= 2, "demo 楼至少 2 层"
    # 顶层在前 (floor_number DESC)
    nums = [f["floor_number"] for f in floors]
    assert nums == sorted(nums, reverse=True), "楼层应按 number 降序"
    # 每层都有基础字段
    for f in floors:
        assert f["area_sqm"] > 0
        assert f["floor_type"] in (
            "LOBBY", "CLASSROOM", "OFFICE", "LAB", "MECHANICAL",
            "LIBRARY", "SPORTS", "STUDENT_CENTER", "OTHER",
        )
        assert f["device_count"] >= 0
        assert f["fault_device_count"] >= 0
        assert f["total_kwh"] >= 0
    # building 摘要含 floor_source_dataset (Step 11b 加, badge 用)
    assert "floor_source_dataset" in data["building"], "building 缺 floor_source_dataset"
    assert data["building"]["floor_source_dataset"] == "synthetic_floor", \
        f"demo 楼应是 synthetic_floor, 实际: {data['building']['floor_source_dataset']}"


def test_list_floors_includes_metadata_rooms(client: TestClient, auth_demo, demo_building_id):
    """楼层 metadata 含 rooms 数组 (前端 IsometricFloor.vue 画 SVG 用)。"""
    floors = _list_floors(client, demo_building_id)
    for f in floors:
        meta = f["metadata"]
        assert "rooms" in meta, f"F{f['floor_number']} metadata 缺 rooms"
        assert isinstance(meta["rooms"], list)
        assert len(meta["rooms"]) >= 1
        room = meta["rooms"][0]
        assert {"name", "x", "y", "w", "h", "usage"} <= set(room.keys())


def test_list_floors_404(client: TestClient, auth_demo):
    """不存在的 building_id 返 404。"""
    resp = client.get(f"/api/v1/buildings/{uuid.uuid4()}/floors")
    assert resp.status_code == 404


def test_floor_detail(client: TestClient, auth_demo, demo_building_id):
    """GET /buildings/{id}/floors/{floor_id} 返单楼层详情 + 设备列表。"""
    floors = _list_floors(client, demo_building_id)
    floor_id = floors[0]["id"]

    resp = client.get(f"/api/v1/buildings/{demo_building_id}/floors/{floor_id}")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["floor"]["id"] == floor_id
    assert data["summary"]["total_kwh"] >= 0
    assert isinstance(data["devices"], list)
    # 楼层至少有 1 个 SENSOR (demo 楼都生成了楼层数据)
    assert len(data["devices"]) >= 1
    dev = data["devices"][0]
    assert dev["status"] in ("ONLINE", "OFFLINE", "FAULT", "STALE")
    assert dev["energy_type"]


def test_floor_detail_wrong_building(client: TestClient, auth_demo, demo_building_id):
    """floor_id 存在但不属于当前 building, 返 404 (越权访问)。"""
    floors = _list_floors(client, demo_building_id)
    floor_id = floors[0]["id"]
    # 用一个不同的 building_id (随机 uuid)
    resp = client.get(f"/api/v1/buildings/{uuid.uuid4()}/floors/{floor_id}")
    assert resp.status_code == 404


def test_floor_timeseries_day(client: TestClient, auth_demo, demo_building_id):
    """GET /buildings/{id}/floors/{floor_id}/timeseries granularity=day 返 365 点。"""
    floors = _list_floors(client, demo_building_id)
    floor_id = floors[0]["id"]

    resp = client.get(
        f"/api/v1/buildings/{demo_building_id}/floors/{floor_id}/timeseries",
        params={"granularity": "day"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["granularity"] == "day"
    assert isinstance(data["series"], list)
    assert len(data["series"]) >= 1
    # 每条 series 至少 1 个点 (demo 楼 2017 全年)
    for s in data["series"]:
        assert s["energy_type"]
        assert len(s["points"]) >= 1
        p = s["points"][0]
        assert "ts" in p
        assert "value" in p


def test_floor_timeseries_month(client: TestClient, auth_demo, demo_building_id):
    """granularity=month 返 12 点 (2017 全年)。"""
    floors = _list_floors(client, demo_building_id)
    floor_id = floors[0]["id"]

    resp = client.get(
        f"/api/v1/buildings/{demo_building_id}/floors/{floor_id}/timeseries",
        params={"granularity": "month"},
    )
    assert resp.status_code == 200
    series = resp.json()["data"]["series"]
    for s in series:
        assert len(s["points"]) <= 12, "month 粒度最多 12 点"


def test_floor_timeseries_invalid_granularity(client: TestClient, auth_demo, demo_building_id):
    """非法 granularity 返 400。"""
    floors = _list_floors(client, demo_building_id)
    floor_id = floors[0]["id"]

    resp = client.get(
        f"/api/v1/buildings/{demo_building_id}/floors/{floor_id}/timeseries",
        params={"granularity": "minute"},
    )
    assert resp.status_code == 400


def test_compare_floors(client: TestClient, auth_demo, demo_building_id):
    """GET /buildings/{id}/floors/compare 返楼层对比 (每层 × 每能源)。"""
    resp = client.get(f"/api/v1/buildings/{demo_building_id}/floors/compare")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "floors" in data
    assert len(data["floors"]) >= 2
    for f in data["floors"]:
        assert f["total_kwh"] >= 0
        assert isinstance(f["energy_breakdown"], list)
        assert len(f["energy_breakdown"]) >= 1


def test_compare_floors_filter_energy(client: TestClient, auth_demo, demo_building_id):
    """energy_type 过滤: 只返该能源的对比。"""
    resp = client.get(
        f"/api/v1/buildings/{demo_building_id}/floors/compare",
        params={"energy_type": "electricity"},
    )
    assert resp.status_code == 200
    floors = resp.json()["data"]["floors"]
    for f in floors:
        # 每层 energy_breakdown 里只有 electricity
        ets = [eb["energy_type"] for eb in f["energy_breakdown"]]
        assert ets == ["electricity"]


def test_floor_composition(client: TestClient, auth_demo, demo_building_id):
    """GET /buildings/{id}/floors/composition 返全楼能源构成 + 按层细分。"""
    resp = client.get(f"/api/v1/buildings/{demo_building_id}/floors/composition")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "overall" in data
    assert "by_floor" in data
    assert len(data["overall"]) >= 1
    # overall 的 pct 加起来应接近 100
    total_pct = sum(c["pct"] for c in data["overall"])
    assert 99 <= total_pct <= 101, f"overall pct 总和应 ~100, 实际 {total_pct}"


def test_floor_composition_single_floor(client: TestClient, auth_demo, demo_building_id):
    """floor_id 过滤: 只返该楼层的能源构成。"""
    floors = _list_floors(client, demo_building_id)
    floor_id = floors[0]["id"]

    resp = client.get(
        f"/api/v1/buildings/{demo_building_id}/floors/composition",
        params={"floor_id": floor_id},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["floor_id"] == floor_id
    # 单楼层时 by_floor 为空 (整体已在 overall)
    assert data["by_floor"] == []


def test_floor_devices(client: TestClient, auth_demo, demo_building_id):
    """GET /buildings/{id}/floors/devices 返设备状态列表 + 汇总。"""
    resp = client.get(f"/api/v1/buildings/{demo_building_id}/floors/devices")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "summary" in data
    assert "devices" in data
    s = data["summary"]
    assert s["total"] == len(data["devices"])
    assert s["online"] + s["offline"] + s["fault"] + s["stale"] == s["total"]


def test_floor_devices_filter_status(client: TestClient, auth_demo, demo_building_id):
    """status 过滤: 只返该状态的设备。"""
    # 先拿全量, 找一个有 FAULT/STALE/OFFLINE 的楼 (Alissa 或 Seth)
    # demo_building_id 是 Franklin, 它有 2 个 STALE, 可以测
    resp = client.get(
        f"/api/v1/buildings/{demo_building_id}/floors/devices",
        params={"status": "STALE"},
    )
    assert resp.status_code == 200
    devices = resp.json()["data"]["devices"]
    # Franklin 应该有 STALE 设备 (seed 时按 8% 故障率, 14 个点期望 1.12 个故障)
    # 但 STALE 不一定有, 至少接口能返 200 + list
    for d in devices:
        assert d["status"] == "STALE"


def test_floor_devices_invalid_status(client: TestClient, auth_demo, demo_building_id):
    """非法 status 返 400。"""
    resp = client.get(
        f"/api/v1/buildings/{demo_building_id}/floors/devices",
        params={"status": "BROKEN"},
    )
    assert resp.status_code == 400


def test_floor_anomalies(client: TestClient, auth_demo, demo_building_id):
    """GET /buildings/{id}/floors/anomalies 返异常事件 (可能为空, Step 08 跑完才有)。"""
    resp = client.get(f"/api/v1/buildings/{demo_building_id}/floors/anomalies")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "anomalies" in data
    assert isinstance(data["anomalies"], list)


def test_floor_consistency_check(client: TestClient, auth_demo, demo_building_id):
    """GET /buildings/{id}/floors/consistency-check 加总约束自检。

    核心验证: Σ floor readings = building METER readings (逐小时),
    max_diff_pct 应 < 0.001% (浮点级误差)。
    """
    resp = client.get(f"/api/v1/buildings/{demo_building_id}/floors/consistency-check")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "passed" in data
    assert "results" in data
    assert "threshold_pct" in data
    # demo 楼 (Franklin) 应该通过自检
    assert data["passed"] is True, f"加总约束未通过: {data['results']}"
    for r in data["results"]:
        assert r["max_diff_pct"] < data["threshold_pct"], (
            f"{r['energy_type']} max_diff={r['max_diff_pct']}% 超阈值 {data['threshold_pct']}%"
        )


def test_floor_consistency_check_filter_energy(client: TestClient, auth_demo, demo_building_id):
    """energy_type 过滤: 只检该能源。"""
    resp = client.get(
        f"/api/v1/buildings/{demo_building_id}/floors/consistency-check",
        params={"energy_type": "electricity"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["passed"] is True
    # 只检 electricity, results 里应只有 electricity
    ets = [r["energy_type"] for r in data["results"]]
    assert ets == ["electricity"]


# ============================================================================
# Step 11a: auto-split POST 接口 + floors.csv 上传测试
# ----------------------------------------------------------------------------
# auto-split 是写操作, demo 用户 403 (require_write_access 拦)。普通用户能调,
# 但要满足前置条件: 楼栋有 METER 读数 + 楼栋没已有楼层。
# ============================================================================


def test_auto_split_demo_forbidden(client: TestClient, auth_demo, demo_building_id):
    """demo 用户调 POST auto-split 返 403 (写保护)。"""
    resp = client.post(f"/api/v1/buildings/{demo_building_id}/floors/auto-split")
    assert resp.status_code == 403, resp.text


def test_auto_split_already_has_floors(client: TestClient, auth_normal, demo_building_id):
    """楼栋已有楼层时 auto-split 返 400 (避免覆盖)。

    demo_building_id 是 Franklin, 已有 3 层 (seed 灌的)。
    """
    resp = client.post(f"/api/v1/buildings/{demo_building_id}/floors/auto-split")
    assert resp.status_code == 400, resp.text
    assert "已有" in resp.json()["detail"]["message"]


def test_auto_split_building_not_found(client: TestClient, auth_normal):
    """不存在的 building_id 返 404。"""
    fake_id = str(uuid.uuid4())
    resp = client.post(f"/api/v1/buildings/{fake_id}/floors/auto-split")
    assert resp.status_code == 404


def test_auto_split_no_meter_data(client: TestClient, auth_normal, demo_site_id):
    """楼栋没有 METER 读数时 auto-split 返 400 (没 ground truth 拆不了)。

    建一个测试楼栋 (有 floors_count 但没读数), 调 auto-split 应报错。
    """
    # 建测试楼栋
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO core.building
                    (tenant_id, site_id, building_code, display_name, sqm,
                     floors_count, source_dataset)
                SELECT t.id, %s, 'TEST_NO_METER', 'Test No Meter', 1000.0, 3, 'test'
                FROM core.tenant t WHERE t.tenant_code = 'demo'
                RETURNING id
            """, (demo_site_id,))
            test_bid = str(cur.fetchone()[0])
        conn.commit()

    try:
        resp = client.post(f"/api/v1/buildings/{test_bid}/floors/auto-split")
        assert resp.status_code == 400, resp.text
        msg = resp.json()["detail"]["message"]
        assert "METER" in msg or "读数" in msg
    finally:
        # 清理测试楼栋 (CASCADE 会删关联的 floor / point / reading)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("DELETE FROM core.building WHERE id = %s", (test_bid,))
            conn.commit()


def test_floor_csv_template(client: TestClient, auth_demo):
    """GET /uploads/templates/floors 返楼层 CSV 模板。"""
    resp = client.get("/api/v1/uploads/templates/floors")
    assert resp.status_code == 200, resp.text
    content = resp.text
    # 模板应含表头 + 字段说明
    assert "building_id" in content
    assert "floor_number" in content
    assert "floor_type" in content
    assert "area_sqm" in content
    assert "is_rooftop" in content
    # 含示例数据 (LOBBY / MECHANICAL floor_type)
    assert "LOBBY" in content
    assert "MECHANICAL" in content


def test_floors_csv_upload_and_commit(client: TestClient, auth_normal, demo_site_id):
    """端到端: 上传 floors.csv -> commit -> core.floor 写入。

    建一个测试楼栋 (无楼层数据), 上传 floors.csv, commit, 验证 core.floor 有 2 层。
    """
    # 建测试楼栋 (floors_count=2 但没楼层实体)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO core.building
                    (tenant_id, site_id, building_code, display_name, sqm,
                     floors_count, source_dataset)
                SELECT t.id, %s, 'TEST_FLOOR_CSV', 'Test Floor CSV', 800.0, 2, 'test'
                FROM core.tenant t WHERE t.tenant_code = 'demo'
                RETURNING id
            """, (demo_site_id,))
            test_bid = str(cur.fetchone()[0])
        conn.commit()

    session_id = None
    try:
        # 1. 上传 floors.csv (POST /uploads/single 返 201, target_type 走 query param)
        csv_content = (
            b"# test\n"
            b"building_id,floor_number,floor_name,floor_type,area_sqm,is_rooftop\n"
            b"TEST_FLOOR_CSV,1,1F Test Lobby,LOBBY,400.0,false\n"
            b"TEST_FLOOR_CSV,2,2F Test Office,OFFICE,400.0,true\n"
        )
        resp = client.post(
            "/api/v1/uploads/single?target_type=FLOOR",
            files={"file": ("floors.csv", BytesIO(csv_content), "text/csv")},
        )
        assert resp.status_code == 201, resp.text
        session_id = resp.json()["data"]["session_id"]

        # 2. commit (FLOOR commit 不需要 mapping/validate, 直接 commit)
        resp = client.post(f"/api/v1/uploads/{session_id}/commit")
        assert resp.status_code == 200, resp.text
        data = resp.json()["data"]
        assert data["row_count_inserted"] == 2

        # 3. 验证 core.floor 写入
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT floor_number, floor_name, floor_type, area_sqm, is_rooftop,
                           source_dataset
                    FROM core.floor
                    WHERE building_id = %s
                    ORDER BY floor_number
                """, (test_bid,))
                rows = cur.fetchall()

        assert len(rows) == 2, f"应有 2 层, 实际 {len(rows)}"
        assert rows[0][0] == 1  # floor_number
        assert rows[0][1] == "1F Test Lobby"
        assert rows[0][2] == "LOBBY"
        assert rows[0][5] == "user_uploaded"  # source_dataset
        assert rows[1][0] == 2
        assert rows[1][2] == "OFFICE"
        assert rows[1][4] is True  # is_rooftop

    finally:
        # 清理: 删 session + batch + 测试楼栋 (CASCADE 删 floor)
        with get_conn() as conn:
            with conn.cursor() as cur:
                if session_id:
                    cur.execute("DELETE FROM ingest.upload_session WHERE id = %s", (session_id,))
                cur.execute("DELETE FROM core.building WHERE id = %s", (test_bid,))
            conn.commit()


def test_floors_csv_commit_invalid_floor_type(client: TestClient, auth_normal, demo_site_id):
    """floors.csv 含非法 floor_type 时 commit 返 400。"""
    # 建测试楼栋
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO core.building
                    (tenant_id, site_id, building_code, display_name, sqm,
                     floors_count, source_dataset)
                SELECT t.id, %s, 'TEST_BAD_TYPE', 'Test Bad Type', 500.0, 1, 'test'
                FROM core.tenant t WHERE t.tenant_code = 'demo'
                RETURNING id
            """, (demo_site_id,))
            test_bid = str(cur.fetchone()[0])
        conn.commit()

    session_id = None
    try:
        csv_content = (
            b"building_id,floor_number,floor_name,floor_type,area_sqm,is_rooftop\n"
            b"TEST_BAD_TYPE,1,Bad Floor,INVALID_TYPE,500.0,true\n"
        )
        resp = client.post(
            "/api/v1/uploads/single?target_type=FLOOR",
            files={"file": ("floors.csv", BytesIO(csv_content), "text/csv")},
        )
        assert resp.status_code == 201
        session_id = resp.json()["data"]["session_id"]

        resp = client.post(f"/api/v1/uploads/{session_id}/commit")
        assert resp.status_code == 400, resp.text
        assert "floor_type" in resp.json()["detail"]["message"]

    finally:
        with get_conn() as conn:
            with conn.cursor() as cur:
                if session_id:
                    cur.execute("DELETE FROM ingest.upload_session WHERE id = %s", (session_id,))
                cur.execute("DELETE FROM core.building WHERE id = %s", (test_bid,))
            conn.commit()
