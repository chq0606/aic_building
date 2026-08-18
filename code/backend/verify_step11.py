"""Step 11 体块模式后端验收脚本。

跑法:
    cd backend
    "/e/anaconda/envs/building_aic/python.exe" verify_step11.py

直调 service 层 + Pydantic schema 校验, 不走 HTTP。这样不需要起 uvicorn,
也不需要 JWT, 跑得快。HTTP 层 (api/visual.py) 只是薄壳, service 层 +
schema 跑通 HTTP 层大概率没问题。

验收项 (按 plan):
  1. 提交体块参数 (POST /buildings/{id}/visual-models/block)
     - DB 落库 is_active=true
     - 再提交一次, 旧记录 is_active=false
  2. GET /buildings/{id}/visual-model 返刚提交的 model
  3. GET /sites/{id}/scene 返 6 栋楼, 每栋字段完整:
     - dimensions (length/width/height/floors_count)
     - position (x/y)
     - color_metric ({metric, value, level})
     - anomaly_status ({has_anomaly, severity_max, count, by_severity})
     - energy_composition ({composition: [{type, kwh, pct}]})
  4. 没设过 visual_model 的 building 走自动估算 (height = floors * 3.5)
  5. 三种 metric (eui/total_kwh/anomaly_count) 都能算出 color_metric.level
  6. DELETE 后再查返 None
"""
import sys

# Windows 控制台默认 GBK 编码, 打印中文 building name 会炸 UnicodeEncodeError
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

from loguru import logger
from pydantic import ValidationError

from app.db.session import close_pool, get_conn, init_pool
from app.models.visual import BlockModelRequest
from app.services.visual_model_service import (
    _compute_color_level,
    _estimate_dimensions,
    delete_visual_model,
    get_building_visual_model,
    list_site_scene,
    upsert_block_model,
)


TEST_BUILDING_CODE = "Bobcat_education_Alissa"  # sqm=11254.80, floors=2
ESTIMATE_BUILDING_CODE = "Bobcat_science_Tammy"  # sqm=10150.90, floors=6, 不设 visual_model 验自动估算


def get_demo_tenant_id() -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM core.tenant WHERE tenant_code='demo'")
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo 租户不存在, 先跑 seed")
            return str(row[0])


def get_demo_site_id(tenant_id: str) -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM core.site WHERE tenant_id = %s::uuid
                ORDER BY created_at LIMIT 1
            """, (tenant_id,))
            row = cur.fetchone()
            if row is None:
                raise RuntimeError("demo site 不存在")
            return str(row[0])


def get_building_id_by_code(building_code: str, tenant_id: str) -> str:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM core.building
                WHERE building_code = %s AND tenant_id = %s::uuid
            """, (building_code, tenant_id))
            row = cur.fetchone()
            if row is None:
                raise RuntimeError(f"building 不存在: {building_code}")
            return str(row[0])


def cleanup_visual_models(tenant_id: str, building_id: str) -> None:
    """跑前清场: 删该 building 所有 visual_model, 避免上次跑残留干扰。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM core.building_visual_model
                WHERE tenant_id = %s::uuid AND building_id = %s::uuid
            """, (tenant_id, building_id))
        conn.commit()


def test_submit_block_model(tenant_id: str, building_id: str) -> str:
    """提交体块参数, 验证返回 model_id + DB 落库正确 + 旧记录 is_active=false。"""
    print("\n=== test_submit_block_model ===")
    cleanup_visual_models(tenant_id, building_id)

    req = BlockModelRequest(
        length_m=120.0, width_m=80.0, height_m=7.0,
        floors_count=2, position_x=10.0, position_y=20.0,
    )
    result = upsert_block_model(tenant_id, building_id, req)
    assert "model_id" in result
    model_id = result["model_id"]
    assert result["is_active"] is True
    print(f"  提交成功 model_id={model_id[:8]}...")

    # DB 落库校验
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT model_mode, render_format, length_m, width_m, height_m,
                       floors_count, position_x, position_y, is_active
                FROM core.building_visual_model WHERE id = %s::uuid
            """, (model_id,))
            row = cur.fetchone()
    assert row[0] == "BLOCK", f"model_mode 应 BLOCK, 实际 {row[0]}"
    assert row[1] == "BOX", f"render_format 应 BOX, 实际 {row[1]}"
    assert float(row[2]) == 120.0
    assert float(row[3]) == 80.0
    assert float(row[4]) == 7.0
    assert row[5] == 2
    assert float(row[6]) == 10.0
    assert float(row[7]) == 20.0
    assert row[8] is True
    print(f"  DB 落库校验通过: mode=BLOCK format=BOX dims=120x80x7 floors=2 pos=(10,20)")

    # 再提交一次, 验证旧记录 is_active=false
    req2 = BlockModelRequest(
        length_m=130.0, width_m=90.0, height_m=10.5,
        floors_count=3, position_x=15.0, position_y=25.0,
    )
    result2 = upsert_block_model(tenant_id, building_id, req2)
    new_model_id = result2["model_id"]
    assert new_model_id != model_id, "第二次提交应生成新 model_id"
    print(f"  二次提交 model_id={new_model_id[:8]}...")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT is_active FROM core.building_visual_model
                WHERE id = %s::uuid
            """, (model_id,))
            old_active = cur.fetchone()[0]
    assert old_active is False, "旧记录 is_active 应 false"
    print(f"  旧记录 is_active=false (符合预期)")

    return new_model_id


def test_get_building_visual_model(tenant_id: str, building_id: str, expected_model_id: str) -> None:
    """查 building 最新 active visual_model, 应返刚才提交的那条。"""
    print("\n=== test_get_building_visual_model ===")
    result = get_building_visual_model(tenant_id, building_id)
    assert result is not None, "应返 visual_model dict"
    assert result["id"] == expected_model_id, f"应返最新 model_id, 实际 {result['id']}"
    assert result["model_mode"] == "BLOCK"
    assert result["is_active"] is True
    assert result["dimensions"]["length_m"] == 130.0
    assert result["dimensions"]["floors_count"] == 3
    assert result["position"]["x"] == 15.0
    print(f"  GET 返回最新 active model: id={result['id'][:8]}...")
    print(f"    dimensions={result['dimensions']}")
    print(f"    position={result['position']}")


def test_list_site_scene(tenant_id: str, site_id: str, expected_building_id: str) -> None:
    """scene API 验证: 6 栋楼, 每栋字段完整。"""
    print("\n=== test_list_site_scene ===")
    result = list_site_scene(tenant_id, site_id, metric="eui", start=None, end=None)
    assert "buildings" in result
    buildings = result["buildings"]
    assert len(buildings) == 6, f"应返 6 栋楼, 实际 {len(buildings)}"

    for b in buildings:
        # dimensions 必须有值
        assert b["dimensions"]["length_m"] is not None and b["dimensions"]["length_m"] > 0
        assert b["dimensions"]["width_m"] is not None and b["dimensions"]["width_m"] > 0
        assert b["dimensions"]["height_m"] is not None and b["dimensions"]["height_m"] > 0
        # color_metric
        assert b["color_metric"]["metric"] == "eui"
        assert b["color_metric"]["level"] in ("low", "mid", "high", "critical"), \
            f"level 非法: {b['color_metric']['level']}"
        # anomaly_status
        assert "by_severity" in b["anomaly_status"]
        assert set(b["anomaly_status"]["by_severity"].keys()) == {"LOW", "MEDIUM", "HIGH"}
        # energy_composition
        assert isinstance(b["energy_composition"]["composition"], list)
        # model_kind
        assert b["model_kind"] in ("block", "splat", "estimated"), \
            f"model_kind 非法: {b['model_kind']}"

    # 找到刚提交体块的那栋楼, 验证 model_kind=block
    test_building = next(b for b in buildings if b["building_id"] == expected_building_id)
    assert test_building["model_kind"] == "block", \
        f"应 model_kind=block, 实际 {test_building['model_kind']}"
    assert test_building["model_id"] is not None, "block 模式应有 model_id"
    assert test_building["dimensions"]["length_m"] == 130.0, "block 模式应使用 DB 值"
    print(f"  6 栋楼都返完整字段")
    print(f"  test_building (刚提交体块): model_kind=block, dims={test_building['dimensions']}")

    # 打印所有 building 的 model_kind
    for b in buildings:
        print(f"  {b['building_code']:30} kind={b['model_kind']:10} "
              f"level={b['color_metric']['level']:8} "
              f"value={b['color_metric']['value']}")


def test_auto_estimate_dimensions(tenant_id: str, site_id: str) -> None:
    """没设过 visual_model 的 building 走自动估算。"""
    print("\n=== test_auto_estimate_dimensions ===")
    result = list_site_scene(tenant_id, site_id, metric="eui", start=None, end=None)
    # 找 Bobcat_science_Tammy (sqm=10150.90, floors=6)
    tammy = next(
        (b for b in result["buildings"] if b["building_code"] == ESTIMATE_BUILDING_CODE),
        None,
    )
    assert tammy is not None, f"没找到 {ESTIMATE_BUILDING_CODE}"
    assert tammy["model_kind"] == "estimated", \
        f"应 model_kind=estimated, 实际 {tammy['model_kind']}"
    assert tammy["model_id"] is None, "estimated 模式 model_id 应 None"

    # 验证估算结果: height = 6 * 3.5 = 21.0
    #                width = sqrt(10150.90 / 1.5) ≈ 82.27
    #                length = 1.5 * width ≈ 123.40
    dims = tammy["dimensions"]
    assert abs(dims["height_m"] - 21.0) < 0.01, f"height 应 21.0, 实际 {dims['height_m']}"
    assert abs(dims["width_m"] - 82.27) < 0.1, f"width 应 ≈82.27, 实际 {dims['width_m']}"
    assert abs(dims["length_m"] - 123.40) < 0.1, f"length 应 ≈123.40, 实际 {dims['length_m']}"
    assert dims["floors_count"] == 6
    print(f"  Tammy 自动估算: dims={dims}")
    print(f"  height=6*3.5=21.0 ✓, width=sqrt(10150.90/1.5)=82.27 ✓, length=1.5*width=123.40 ✓")


def test_color_metric_levels(tenant_id: str, site_id: str) -> None:
    """三种 metric 都能算出 color_metric.level。"""
    print("\n=== test_color_metric_levels ===")
    for m in ("eui", "total_kwh", "anomaly_count"):
        result = list_site_scene(tenant_id, site_id, metric=m, start=None, end=None)
        for b in result["buildings"]:
            assert b["color_metric"]["metric"] == m, \
                f"metric 应 {m}, 实际 {b['color_metric']['metric']}"
            assert b["color_metric"]["level"] in ("low", "mid", "high", "critical")
        levels = [b["color_metric"]["level"] for b in result["buildings"]]
        print(f"  metric={m}: levels={levels}")


def test_compute_color_level_unit() -> None:
    """单独测 _compute_color_level 函数边界。"""
    print("\n=== test_compute_color_level_unit ===")
    # EUI 阈值 80/150/250
    assert _compute_color_level("eui", None) == "low"
    assert _compute_color_level("eui", 50) == "low"
    assert _compute_color_level("eui", 80) == "mid"  # 80 不 < 80
    assert _compute_color_level("eui", 150) == "high"  # 150 不 < 150
    assert _compute_color_level("eui", 250) == "critical"  # 250 不 < 250
    assert _compute_color_level("eui", 1000) == "critical"
    # total_kwh 阈值 5000/20000/50000
    assert _compute_color_level("total_kwh", 0) == "low"
    assert _compute_color_level("total_kwh", 4999) == "low"
    assert _compute_color_level("total_kwh", 5000) == "mid"
    assert _compute_color_level("total_kwh", 50000) == "critical"
    # anomaly_count 阈值 1/6/21
    assert _compute_color_level("anomaly_count", 0) == "low"
    assert _compute_color_level("anomaly_count", 1) == "mid"
    assert _compute_color_level("anomaly_count", 6) == "high"
    assert _compute_color_level("anomaly_count", 21) == "critical"
    print("  EUI/total_kwh/anomaly_count 阈值边界全部通过")


def test_estimate_dimensions_unit() -> None:
    """单独测 _estimate_dimensions 函数。"""
    print("\n=== test_estimate_dimensions_unit ===")
    # 正常: sqm=100, floors=3
    # width = sqrt(100/1.5) ≈ 8.165
    # length = 1.5 * 8.165 ≈ 12.247
    # height = 3 * 3.5 = 10.5
    dims = _estimate_dimensions(sqm=100.0, floors_count=3)
    assert abs(dims.height_m - 10.5) < 0.01
    assert abs(dims.width_m - 8.16) < 0.01
    assert abs(dims.length_m - 12.25) < 0.01
    assert dims.floors_count == 3
    print(f"  sqm=100 floors=3 -> {dims.model_dump()}")

    # floors_count NULL -> 默认 1
    dims2 = _estimate_dimensions(sqm=15.0, floors_count=None)
    assert dims2.floors_count == 1
    assert abs(dims2.height_m - 3.5) < 0.01
    print(f"  sqm=15 floors=None -> {dims2.model_dump()}")

    # sqm 也 NULL -> 用默认 10x15
    dims3 = _estimate_dimensions(sqm=None, floors_count=None)
    assert dims3.floors_count == 1
    assert dims3.length_m == 15.0
    assert dims3.width_m == 10.0
    print(f"  sqm=None floors=None -> {dims3.model_dump()}")


def test_delete_visual_model(tenant_id: str, building_id: str, model_id: str) -> None:
    """删 visual_model, 再查返 None。"""
    print("\n=== test_delete_visual_model ===")
    result = delete_visual_model(tenant_id, model_id)
    assert result is not None
    assert result["deleted_id"] == model_id
    print(f"  删除成功: {model_id[:8]}...")

    # 再查应该返 None
    result2 = get_building_visual_model(tenant_id, building_id)
    assert result2 is None, "删除后再查应返 None"
    print(f"  删除后 GET 返 None ✓")


def test_block_model_request_validation() -> None:
    """Pydantic schema 校验: 非法参数应触发 ValidationError。"""
    print("\n=== test_block_model_request_validation ===")
    # length <= 0
    try:
        BlockModelRequest(length_m=0, width_m=10, height_m=10, floors_count=1)
    except ValidationError:
        print("  length_m=0 -> ValidationError ✓")
    else:
        raise AssertionError("length_m=0 应触发 ValidationError")

    # floors_count < 1
    try:
        BlockModelRequest(length_m=10, width_m=10, height_m=10, floors_count=0)
    except ValidationError:
        print("  floors_count=0 -> ValidationError ✓")
    else:
        raise AssertionError("floors_count=0 应触发 ValidationError")

    # 合法参数 (position 可空)
    req = BlockModelRequest(
        length_m=100, width_m=50, height_m=20, floors_count=5,
        position_x=None, position_y=None,
    )
    assert req.position_x is None
    assert req.position_y is None
    print(f"  position_x/y=None 合法 ✓")


def main() -> None:
    init_pool()
    try:
        tenant_id = get_demo_tenant_id()
        site_id = get_demo_site_id(tenant_id)
        building_id = get_building_id_by_code(TEST_BUILDING_CODE, tenant_id)
        logger.info("demo tenant_id={} site_id={} building_id={} ({})",
                    tenant_id[:8], site_id[:8], building_id[:8], TEST_BUILDING_CODE)

        # 单元测试 (不需要 DB)
        test_compute_color_level_unit()
        test_estimate_dimensions_unit()
        test_block_model_request_validation()

        # 集成测试 (走 DB)
        model_id = test_submit_block_model(tenant_id, building_id)
        test_get_building_visual_model(tenant_id, building_id, model_id)
        test_list_site_scene(tenant_id, site_id, building_id)
        test_auto_estimate_dimensions(tenant_id, site_id)
        test_color_metric_levels(tenant_id, site_id)
        test_delete_visual_model(tenant_id, building_id, model_id)

        # 清场: 把 test 提交的所有 visual_model 删了, 不留 demo 残留
        cleanup_visual_models(tenant_id, building_id)
        print("\n=== 清场: 已清理所有 test visual_model ===")

        print("\n=== ALL TESTS PASSED ===")
    finally:
        close_pool()


if __name__ == "__main__":
    main()
