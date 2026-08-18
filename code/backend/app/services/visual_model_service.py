"""
体块模式后端服务 (Step 11)。

五个对外函数:
  upsert_block_model         提交体块参数 -> 落 building_visual_model 记录
  get_building_visual_model   查 building 最新 active 的 visual_model (splat 优先)
  list_site_scene            组装园区 3D 场景元数据 (核心 API, 拼 5 个数据源)
  delete_visual_model        硬删 visual_model 记录
  update_building_yaw        只更新当前 active visual_model 的 yaw_deg (Step 13)

数据来源 (list_site_scene 拼装):
  1. core.building 表: building 基础信息 (code/name/sqm/floors_count/site_id)
  2. core.building_visual_model 表: 已设过的 visual_model (is_active=true)
  3. query_service.list_buildings: 能耗聚合 (total_kwh/eui/anomaly_count)
  4. anomaly_service.get_site_anomaly_overview: 异常 by_severity 摘要
  5. query_service.get_building_energy_composition: 每楼能源构成 (逐楼查, N 次)

第 5 项是 N 次查询 (N = building 数), demo 6 栋楼没问题, 后续 site 楼多了
再改 batch 查询。每楼 composition 在 100ms 内, 6 栋楼 600ms 可接受。

is_active 语义: 同 building 同 mode 提交新记录时, 把旧记录 is_active=false,
新记录 is_active=true。保留历史, 前端按 is_active=true 取最新。这不是
真正的 upsert (不用 UNIQUE 约束 + ON CONFLICT), 因为业务上要保留历史
(便于回溯), 不是合并。

写操作显式 conn.commit(): get_conn 异常时自动 rollback, 正常时不自动 commit,
写操作必须显式调 conn.commit() (见 db/session.py 注释)。

tenant_id 隔离: 所有 SQL 都带 WHERE tenant_id = %s, 不依赖连接层做隔离。
"""
from __future__ import annotations

import math
from typing import Any

from loguru import logger

from app.core.config import settings
from app.db.session import get_conn
from app.models.visual import BlockModelRequest, Dimensions, Position
from app.services.anomaly_service import get_site_anomaly_overview
from app.services.query_service import (
    _resolve_range,
    get_building_energy_composition,
    list_buildings,
)


# model_mode -> 渲染优先级 (越小越优先)。同 building 有多条 active 记录时,
# scene API 按这个排序取第一条返给前端。splat (照片重建) 比 block (手动体块)
# 优先, 因为 splat 是真实建模, 视觉更准。
_MODEL_MODE_PRIORITY = {
    "PHOTO_SINGLE": 1,
    "PHOTO_MULTI": 2,
    "MANUAL_GLTF": 3,
    "BLOCK": 4,
}


def _estimate_dimensions(sqm: float | None, floors_count: int | None) -> Dimensions:
    """从 sqm + floors_count 估体块尺寸。

    层高 3.5m, 长宽比 1.5:1。sqm 当总建筑面积 (BDG2 数据集的 sqm 量级 10000-17000
    配 floors_count 1-6, 按总建筑面积算 footprint = sqm / floors, 每层占地合理;
    之前按 footprint 算导致大平层配矮层高, 比例失真像棚子)。

    floors_count NULL 时默认 1 层 (避免 NULL * 3.5 = NULL 把 height 算没)。
    sqm NULL 或 0 时用最小默认尺寸 10m x 15m, 不让前端渲染时尺寸为 0。
    """
    floors = max(1, floors_count or 1)
    height = floors * settings.visual_block_floor_height
    if sqm and sqm > 0:
        # sqm 是总建筑面积, footprint = sqm / floors
        footprint = sqm / floors
        # footprint = length * width, length = ratio * width
        # => footprint = ratio * width^2 => width = sqrt(footprint / ratio)
        width = math.sqrt(footprint / settings.visual_block_aspect_ratio)
        length = settings.visual_block_aspect_ratio * width
    else:
        width, length = 10.0, 15.0
    return Dimensions(
        length_m=round(length, 2),
        width_m=round(width, 2),
        height_m=round(height, 2),
        floors_count=floors,
    )


def _compute_color_level(metric: str, value: float | None) -> str:
    """按 metric 算 level (low/mid/high/critical)。

    阈值在 settings 里, 可调。value None 时返 'low' (前端显示中性色,
    不让没数据的楼看着像 critical 一样红)。
    """
    if value is None:
        return "low"
    if metric == "eui":
        if value < settings.visual_threshold_eui_low:
            return "low"
        if value < settings.visual_threshold_eui_mid:
            return "mid"
        if value < settings.visual_threshold_eui_high:
            return "high"
        return "critical"
    if metric == "total_kwh":
        if value < settings.visual_threshold_kwh_low:
            return "low"
        if value < settings.visual_threshold_kwh_mid:
            return "mid"
        if value < settings.visual_threshold_kwh_high:
            return "high"
        return "critical"
    if metric == "anomaly_count":
        if value < settings.visual_threshold_anomaly_mid:
            return "low"
        if value < settings.visual_threshold_anomaly_high:
            return "mid"
        if value < settings.visual_threshold_anomaly_critical:
            return "high"
        return "critical"
    return "low"


def _severity_max(high: int, medium: int, low: int) -> str | None:
    """算最高严重度。HIGH > MEDIUM > LOW, 全 0 返 None。"""
    if high > 0:
        return "HIGH"
    if medium > 0:
        return "MEDIUM"
    if low > 0:
        return "LOW"
    return None


def _row_to_visual_model_out(row: tuple) -> dict[str, Any]:
    """SQL 行转 VisualModelOut dict。

    SELECT 字段顺序:
      0 id | 1 building_id | 2 model_mode | 3 render_format | 4 storage_path
      | 5 preview_image_path | 6 length_m | 7 width_m | 8 height_m | 9 floors_count
      | 10 position_x | 11 position_y | 12 is_active | 13 created_at | 14 tiles_path
      | 15 yaw_deg
    """
    return {
        "id": str(row[0]),
        "building_id": str(row[1]),
        "model_mode": row[2],
        "render_format": row[3],
        "storage_path": row[4],
        "preview_image_path": row[5],
        "dimensions": Dimensions(
            length_m=float(row[6]) if row[6] is not None else None,
            width_m=float(row[7]) if row[7] is not None else None,
            height_m=float(row[8]) if row[8] is not None else None,
            floors_count=row[9],
        ).model_dump(),
        "position": Position(
            x=float(row[10]) if row[10] is not None else None,
            y=float(row[11]) if row[11] is not None else None,
            yaw_deg=float(row[15]) if row[15] is not None else 0.0,
        ).model_dump(),
        "is_active": row[12],
        "created_at": row[13].isoformat() if hasattr(row[13], "isoformat") else str(row[13]),
        "tiles_path": row[14],
    }


def upsert_block_model(tenant_id: str, building_id: str, req: BlockModelRequest) -> dict[str, Any]:
    """提交体块参数, 生成 visual_model 记录。

    同 building 旧 BLOCK 记录 is_active=false (保留历史), 新记录 is_active=true。
    不校验 building 是否存在 -- 校验放在路由层做 (路由层 404 比 service 抛错更标准)。

    返回 {"model_id": str, "building_id": str, "is_active": True}。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 步骤 1: 把旧 BLOCK 记录置 inactive (不删, 保留历史)
            cur.execute("""
                UPDATE core.building_visual_model
                SET is_active = false
                WHERE building_id = %s::uuid
                  AND tenant_id = %s::uuid
                  AND model_mode = 'BLOCK'
                  AND is_active = true
            """, (building_id, tenant_id))
            deactivated = cur.rowcount

            # 步骤 2: INSERT 新记录
            cur.execute("""
                INSERT INTO core.building_visual_model
                    (tenant_id, building_id, model_mode, render_format,
                     length_m, width_m, height_m, floors_count,
                     position_x, position_y, yaw_deg, is_active, created_at)
                VALUES (%s::uuid, %s::uuid, 'BLOCK', 'BOX',
                        %s, %s, %s, %s, %s, %s, %s, true, now())
                RETURNING id, created_at
            """, (
                tenant_id, building_id,
                req.length_m, req.width_m, req.height_m, req.floors_count,
                req.position_x, req.position_y,
                req.yaw_deg if req.yaw_deg is not None else 0.0,
            ))
            new_id, created_at = cur.fetchone()
        # 写操作显式 commit (get_conn 不会自动 commit)
        conn.commit()

    logger.info(
        "upsert_block_model tenant={} building={} deactivated_old={} new_id={}",
        tenant_id[:8], building_id[:8], deactivated, str(new_id)[:8],
    )
    return {
        "model_id": str(new_id),
        "building_id": building_id,
        "is_active": True,
    }


def get_building_visual_model(
    tenant_id: str,
    building_id: str,
    model_kind: str | None = None,
) -> dict[str, Any] | None:
    """查 building 最新 active 的 visual_model。

    model_kind 优先级: splat > block (PHOTO_SINGLE/PHOTO_MULTI > BLOCK)。
    传 model_kind 参数 ('block' 或 'splat') 可指定只查某一种。
    没设过返 None, 路由层转 404。
    """
    if model_kind == "block":
        model_modes = ("BLOCK",)
    elif model_kind == "splat":
        model_modes = ("PHOTO_SINGLE", "PHOTO_MULTI")
    else:
        model_modes = ("PHOTO_SINGLE", "PHOTO_MULTI", "MANUAL_GLTF", "BLOCK")

    # 用 CASE 把 model_mode 映射成优先级数字, 按 priority ASC 排序,
    # 取第一条 = 最高优先级的 active 记录
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, building_id, model_mode, render_format,
                       storage_path, preview_image_path,
                       length_m, width_m, height_m, floors_count,
                       position_x, position_y, is_active, created_at, tiles_path,
                       yaw_deg
                FROM core.building_visual_model
                WHERE building_id = %s::uuid
                  AND tenant_id = %s::uuid
                  AND is_active = true
                  AND model_mode = ANY(%s)
                ORDER BY CASE model_mode
                    WHEN 'PHOTO_SINGLE' THEN 1
                    WHEN 'PHOTO_MULTI'  THEN 2
                    WHEN 'MANUAL_GLTF'  THEN 3
                    WHEN 'BLOCK'         THEN 4
                END ASC
                LIMIT 1
            """, (building_id, tenant_id, list(model_modes)))
            row = cur.fetchone()

    if row is None:
        return None
    return _row_to_visual_model_out(row)


def list_site_scene(
    tenant_id: str,
    site_id: str,
    metric: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    """组装 scene API 响应。

    流程:
      1. _resolve_range 拿时间范围
      2. list_buildings 拿能耗数据 (total_kwh/eui/anomaly_count, 已聚合)
      3. get_site_anomaly_overview 拿异常摘要 (high/medium/low_count per building)
      4. SQL 拉 site 下所有 building + active visual_model (left join)
      5. 遍历 buildings, 按 building_id 拼装:
         - dimensions: 有 visual_model 用 DB 值, 没有走 _estimate_dimensions
         - position: 用 DB 值 (没设过则 NULL, 前端布局兜底)
         - color_metric: 按 metric 参数算 value + level
         - anomaly_status: 从 overview 拿
         - energy_composition: 逐楼查 get_building_energy_composition (N 次)
      6. 返回 SceneResponse dict

    性能: 6 栋楼 demo 数据 ~600ms 主要是 N 次查能源构成。后续楼多了改 batch。
    """
    metric = metric or settings.visual_scene_default_metric
    s, e = _resolve_range(start, end, tenant_id)

    # 数据源 2 + 3: 能耗聚合 + 异常摘要 (两个 service 各调一次)
    buildings_data = list_buildings(
        site_id=site_id, tenant_id=tenant_id,
        start_ts=s, end_ts=e,
        sort="building_code", limit=500,
    )
    overview = get_site_anomaly_overview(site_id, tenant_id, s, e)

    # 用 building_id 索引方便拼装
    energy_by_bid = {b["building_id"]: b for b in buildings_data["buildings"]}
    anomaly_by_bid = {b["building_id"]: b for b in overview["buildings"]}

    # 数据源 1 + 4: 拉 site 下所有 building + active visual_model (left join)
    # 没 visual_model 的 building 也返 (dimensions 走自动估算)
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    b.id, b.building_code, b.display_name,
                    b.sqm, b.floors_count,
                    vm.id, vm.model_mode, vm.length_m, vm.width_m,
                    vm.height_m, vm.floors_count AS vm_floors,
                    vm.position_x, vm.position_y, vm.tiles_path, vm.yaw_deg,
                    b.primary_use, b.sub_use
                FROM core.building b
                LEFT JOIN core.building_visual_model vm
                    ON vm.building_id = b.id
                    AND vm.tenant_id = b.tenant_id
                    AND vm.is_active = true
                WHERE b.site_id = %s::uuid AND b.tenant_id = %s::uuid
                ORDER BY b.building_code
            """, (site_id, tenant_id))
            rows = cur.fetchall()

            # 拿 site_name 给前端做园区标题展示 (Step 14 顶栏用)
            cur.execute("""
                SELECT site_name FROM core.site
                WHERE id = %s::uuid AND tenant_id = %s::uuid
            """, (site_id, tenant_id))
            site_row = cur.fetchone()
            site_name = site_row[0] if site_row else ""

    buildings_scene: list[dict[str, Any]] = []
    for r in rows:
        bid = str(r[0])
        building_code = r[1]
        display_name = r[2]
        sqm = float(r[3]) if r[3] is not None else None
        floors_count = r[4]
        vm_id = str(r[5]) if r[5] is not None else None
        vm_mode = r[6]
        vm_tiles_path = r[13]  # NULL 表示没转 3D Tiles (老记录 / 转换失败)
        vm_yaw_deg = float(r[14]) if r[14] is not None else 0.0  # 默认 0 不旋转
        primary_use = r[15]
        sub_use = r[16]

        # dimensions: 有 visual_model 用 DB 值, 没有走自动估算
        if vm_id is not None and vm_mode == "BLOCK":
            dimensions = Dimensions(
                length_m=float(r[7]) if r[7] is not None else None,
                width_m=float(r[8]) if r[8] is not None else None,
                height_m=float(r[9]) if r[9] is not None else None,
                floors_count=r[10],
            )
            model_kind = "block"
        elif vm_id is not None and vm_mode in ("PHOTO_SINGLE", "PHOTO_MULTI"):
            # splat 模式: 用户上传照片时填的 length/width/height 是建筑真实尺寸
            # (TripoSplat worker 用这些值做 ply 尺度校准, .ply 已经是真实米单位)。
            # 前端 BuildingSplat 渲染时直接 1:1 用, 不再放大。
            # vm_floors 取 visual_model 表的 floors_count (跟上传时填的一致),
            # 不取 building.floors_count (demo seed 可能跟实际重建的楼层数不符)。
            dimensions = Dimensions(
                length_m=float(r[7]) if r[7] is not None else None,
                width_m=float(r[8]) if r[8] is not None else None,
                height_m=float(r[9]) if r[9] is not None else None,
                floors_count=r[10],
            )
            model_kind = "splat"
        else:
            # 没 visual_model, 自动估算
            dimensions = _estimate_dimensions(sqm, floors_count)
            model_kind = "estimated"

        position = Position(
            x=float(r[11]) if r[11] is not None else None,
            y=float(r[12]) if r[12] is not None else None,
            yaw_deg=vm_yaw_deg,
        )

        # color_metric: 按 metric 参数从 energy_by_bid 拿对应字段
        energy = energy_by_bid.get(bid, {})
        if metric == "eui":
            metric_value = energy.get("eui_kwh_per_m2")
        elif metric == "total_kwh":
            metric_value = energy.get("total_kwh")
        else:  # anomaly_count
            metric_value = float(energy.get("anomaly_count", 0))
        # anomaly_count 字段可能是 int, 统一转 float 给 _compute_color_level
        if metric_value is not None and not isinstance(metric_value, float):
            metric_value = float(metric_value)
        level = _compute_color_level(metric, metric_value)

        # anomaly_status: 从 overview 拿 high/medium/low_count
        anomaly = anomaly_by_bid.get(bid, {})
        high = anomaly.get("high_count", 0) or 0
        medium = anomaly.get("medium_count", 0) or 0
        low = anomaly.get("low_count", 0) or 0
        anomaly_count = anomaly.get("anomaly_count", 0) or 0
        anomaly_status = {
            "has_anomaly": anomaly_count > 0,
            "severity_max": _severity_max(high, medium, low),
            "count": anomaly_count,
            "by_severity": {"LOW": low, "MEDIUM": medium, "HIGH": high},
        }

        # energy_composition: 逐楼查 (N 次, demo 数据 6 楼可接受)
        composition_data = get_building_energy_composition(bid, tenant_id, s, e)
        energy_composition = {"composition": composition_data.get("composition", [])}

        buildings_scene.append({
            "building_id": bid,
            "building_code": building_code,
            "display_name": display_name,
            "primary_use": primary_use,
            "sub_use": sub_use,
            "model_kind": model_kind,
            "dimensions": dimensions.model_dump(),
            "position": position.model_dump(),
            "color_metric": {
                "metric": metric,
                "value": metric_value,
                "level": level,
            },
            "anomaly_status": anomaly_status,
            "energy_composition": energy_composition,
            "model_id": vm_id,
            # has_tiles: 前端 BuildingSplat 用这个判断走 Cesium3DTileset (原生 3DGS)
            # 还是降级到 .ply 静态点云。tiles_path 为 NULL (老记录 / 转换失败 / BLOCK
            # 模式) 都返 False, 前端统一走 .ply。
            "has_tiles": vm_tiles_path is not None,
        })

    logger.info(
        "list_site_scene tenant={} site={} metric={} buildings={}",
        tenant_id[:8], site_id[:8], metric, len(buildings_scene),
    )
    return {
        "site_id": site_id,
        "site_name": site_name,
        "time_range": {"start": s, "end": e},
        "metric": metric,
        "buildings": buildings_scene,
    }


def delete_visual_model(tenant_id: str, model_id: str) -> dict[str, Any] | None:
    """硬删 visual_model 记录。

    ON DELETE CASCADE 兜底关联记录 (reconstruction_job.output_model_id 是
    ON DELETE SET NULL, 不会跟着删)。

    返 {"deleted_id": str} 找不到时返 None (路由层转 404)。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM core.building_visual_model
                WHERE id = %s::uuid AND tenant_id = %s::uuid
                RETURNING id
            """, (model_id, tenant_id))
            row = cur.fetchone()
        conn.commit()

    if row is None:
        return None
    logger.info("delete_visual_model tenant={} model_id={}", tenant_id[:8], model_id[:8])
    return {"deleted_id": str(row[0])}


def update_building_yaw(
    tenant_id: str, building_id: str, yaw_deg: float
) -> dict[str, Any] | None:
    """更新 building 当前 active visual_model 的 yaw_deg。

    只 UPDATE, 不 INSERT (没设过 visual_model 的楼应该先走 POST /block 或
    上传照片创建记录, 这里返 None 路由层转 404)。同 building 多条 active
    记录时全部更新 (理论上只有一条 active, 但兜底全改防数据不一致)。

    返 {"building_id": str, "yaw_deg": float, "updated_count": int}。
    找不到 active visual_model 时返 None。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE core.building_visual_model
                SET yaw_deg = %s
                WHERE building_id = %s::uuid
                  AND tenant_id = %s::uuid
                  AND is_active = true
            """, (yaw_deg, building_id, tenant_id))
            updated = cur.rowcount
        conn.commit()

    if updated == 0:
        return None
    logger.info(
        "update_building_yaw tenant={} building={} yaw={:.1f}° updated={}",
        tenant_id[:8], building_id[:8], yaw_deg, updated,
    )
    return {
        "building_id": building_id,
        "yaw_deg": yaw_deg,
        "updated_count": updated,
    }


def update_building_use(
    tenant_id: str,
    building_id: str,
    primary_use: str | None,
    sub_use: str | None,
) -> dict[str, Any] | None:
    """更新 building 的用途字段 (primary_use / sub_use)。

    前端体块表单选"建筑用途"后调这里, 决定 3D 页程序化表皮/屋顶, 也喂给异常
    检测的"同类楼群对比" (baseline_deviation 按 primary_use 分组)。传 None 的
    字段置 NULL (清空用途)。没找到 building 返 None (路由层转 404)。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE core.building
                SET primary_use = %s, sub_use = %s
                WHERE id = %s::uuid AND tenant_id = %s::uuid
                RETURNING id
            """, (primary_use, sub_use, building_id, tenant_id))
            row = cur.fetchone()
        conn.commit()

    if row is None:
        return None
    logger.info(
        "update_building_use tenant={} building={} primary={} sub={}",
        tenant_id[:8], building_id[:8], primary_use, sub_use,
    )
    return {
        "building_id": building_id,
        "primary_use": primary_use,
        "sub_use": sub_use,
    }
