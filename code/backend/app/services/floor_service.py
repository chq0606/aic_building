"""
楼层分析服务: 8 个查询接口 + 1 个写接口, 全部只读除 auto_split_floors。

  list_floors                 楼层列表 + 每层摘要 (total_kwh / EUI / 设备数 / 故障数)
  get_floor_detail            单楼层详情 + 设备列表
  get_floor_timeseries        楼层时序 (hour/day/month 粒度, 按能源分组)
  compare_floors              楼层对比 (同楼不同层 × 能源)
  get_floor_composition       楼层能源构成 (饼图, 全楼或单层)
  list_floor_devices          设备状态列表 (可按 floor_id / status 过滤)
  list_floor_anomalies        楼层异常事件 (复用 mart.anomaly_event)
  check_floor_consistency     加总约束自检 (Σ floor = building)
  auto_split_floors           写接口: 自动按 floors_count 拆楼层 + 生成 SENSOR 读数

数据来源:
  楼层摘要 / 时序 day / 对比 / 构成 -> mart.floor_daily_energy
  时序 hour -> fact.point_reading (mart 没这么细)
  设备状态 -> mart.point_status_snapshot
  异常 -> mart.anomaly_event (Step 08 跑完后有数据, 目前可能为空)

tenant_id 隔离: 所有 SQL 都带 WHERE tenant_id = %s, 不依赖连接层。
时间范围: 复用 query_service._resolve_range, demo 自动落到 2017 全年。

跟 query_service 区别:
  query_service 处理楼栋级 (core.point point_kind='METER', floor_id=NULL)
  floor_service 处理楼层级 (core.point point_kind='SENSOR', floor_id 指向 core.floor)
  两套服务的 building_id 校验都走 _verify_building, 楼层服务多一层 _verify_floor
"""
from typing import Any

from loguru import logger

from app.db.session import get_conn
from app.seed.floor_synthetic import _default_floor_plan, process_building_floors
from app.services.query_service import (
    NotFoundError,
    _resolve_range,
    _to_utc_date_str,
    _verify_building,
)


GRANULARITIES = ("hour", "day", "month")
CONSISTENCY_THRESHOLD_PCT = 0.001  # 加总约束允许的浮点误差上限 (0.001%)


def _verify_floor(cur, floor_id: str, building_id: str, tenant_id: str) -> dict:
    """
    校验 floor 属于指定 building 和 tenant, 返回楼层基础信息。
    不存在或越权抛 NotFoundError (API 层捕获返 404)。

    返回 dict 而不是 tuple, 因为字段多 (8 个), tuple 顺序记不住容易调错。
    """
    cur.execute("""
        SELECT id, floor_number, floor_name, floor_type, area_sqm, is_rooftop, metadata
        FROM core.floor
        WHERE id = %s AND building_id = %s AND tenant_id = %s
    """, (floor_id, building_id, tenant_id))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError("楼层不存在或不属于当前楼栋")
    return {
        "id": str(row[0]),
        "floor_number": row[1],
        "floor_name": row[2],
        "floor_type": row[3],
        "area_sqm": float(row[4]) if row[4] is not None else None,
        "is_rooftop": row[5],
        "metadata": row[6] if row[6] else {},
    }


def list_floors(building_id: str, tenant_id: str, start: str, end: str) -> dict:
    """
    楼层列表 + 每层摘要。

    返回:
      building: {id, building_code, display_name, floors_count}
      floors: [
        {
          id, floor_number, floor_name, floor_type, area_sqm, is_rooftop,
          metadata (含 rooms 模板), total_kwh, eui_kwh_per_m2, device_count,
          fault_device_count, energy_types: [..]
        }
      ] (按 floor_number DESC, 顶层在前)

    EUI 算法: SUM(total_kwh) / area_sqm, 是所选时段的累计 EUI, 不是年均。
    demo 默认 2017 全年, 算出来就是年均 EUI。

    JOIN 防fan-out: mart.floor_daily_energy (~2000 行/层) 跟 core.point (~5-17 行/层)
    直接 JOIN 会笛卡尔积, SUM(m.total_kwh) 被放大 n_points 倍。改用子查询分别
    预聚合 m 和 p, 外层 LEFT JOIN 不产生倍数。
    """
    start_date = _to_utc_date_str(start)
    end_date = _to_utc_date_str(end)

    with get_conn() as conn:
        with conn.cursor() as cur:
            bcode, bname, _site = _verify_building(cur, building_id, tenant_id)

            cur.execute("""
                SELECT floors_count
                FROM core.building
                WHERE id = %s AND tenant_id = %s
            """, (building_id, tenant_id))
            floors_count = cur.fetchone()[0]

            # 拉楼层 source_dataset 聚合, 给前端 badge 用
            # 1 个 distinct 值 -> 直接返该值; 多个 -> 'mixed'; 0 -> None (空态)
            cur.execute("""
                SELECT DISTINCT source_dataset FROM core.floor
                WHERE building_id = %s AND tenant_id = %s
            """, (building_id, tenant_id))
            src_rows = [r[0] for r in cur.fetchall()]
            if len(src_rows) == 0:
                floor_source_dataset = None
            elif len(src_rows) == 1:
                floor_source_dataset = src_rows[0]
            else:
                floor_source_dataset = "mixed"

            cur.execute("""
                SELECT
                    f.id, f.floor_number, f.floor_name, f.floor_type,
                    f.area_sqm, f.is_rooftop, f.metadata,
                    COALESCE(m_sum.total_kwh, 0) AS total_kwh,
                    COALESCE(m_sum.total_kwh / NULLIF(f.area_sqm, 0), 0) AS eui_kwh_per_m2,
                    COALESCE(dev.device_count, 0) AS device_count,
                    COALESCE(dev.fault_device_count, 0) AS fault_device_count,
                    m_sum.energy_types
                FROM core.floor f
                LEFT JOIN (
                    SELECT
                        floor_id,
                        SUM(total_kwh) AS total_kwh,
                        ARRAY_AGG(DISTINCT energy_type) AS energy_types
                    FROM mart.floor_daily_energy
                    WHERE date BETWEEN %s AND %s
                    GROUP BY floor_id
                ) m_sum ON m_sum.floor_id = f.id
                LEFT JOIN (
                    SELECT
                        p.floor_id,
                        COUNT(*) AS device_count,
                        COUNT(*) FILTER (WHERE ps.status != 'ONLINE') AS fault_device_count
                    FROM core.point p
                    LEFT JOIN mart.point_status_snapshot ps ON ps.point_id = p.id
                    WHERE p.floor_id IS NOT NULL
                    GROUP BY p.floor_id
                ) dev ON dev.floor_id = f.id
                WHERE f.building_id = %s AND f.tenant_id = %s
                ORDER BY f.floor_number DESC
            """, (start_date, end_date, building_id, tenant_id))
            rows = cur.fetchall()

    floors = []
    for r in rows:
        floors.append({
            "id": str(r[0]),
            "floor_number": r[1],
            "floor_name": r[2],
            "floor_type": r[3],
            "area_sqm": float(r[4]) if r[4] is not None else None,
            "is_rooftop": r[5],
            "metadata": r[6] if r[6] else {},
            "total_kwh": float(r[7]),
            "eui_kwh_per_m2": float(r[8]) if r[8] is not None else None,
            "device_count": r[9],
            "fault_device_count": r[10] or 0,
            "energy_types": sorted(r[11]) if r[11] else [],
        })

    return {
        "building": {
            "id": building_id,
            "building_code": bcode,
            "display_name": bname,
            "floors_count": floors_count,
            "floor_source_dataset": floor_source_dataset,
        },
        "floors": floors,
        "time_range": {"start": start, "end": end},
    }


def get_floor_detail(
    building_id: str, floor_id: str, tenant_id: str, start: str, end: str
) -> dict:
    """
    单楼层详情 + 设备列表。

    返回:
      floor: {id, floor_number, floor_name, floor_type, area_sqm, is_rooftop, metadata}
      summary: {total_kwh, eui_kwh_per_m2, max_kwh, min_kwh, hour_count}
      devices: [{id, point_code, point_name, energy_type, measure_kind, unit_code,
                 status, last_reading_ts, last_reading_val, completeness_pct, fault_reason}]
    """
    start_date = _to_utc_date_str(start)
    end_date = _to_utc_date_str(end)

    with get_conn() as conn:
        with conn.cursor() as cur:
            _verify_building(cur, building_id, tenant_id)
            floor = _verify_floor(cur, floor_id, building_id, tenant_id)

            # 摘要单独查, 不跟设备列表 JOIN (否则 m × p 笛卡尔积放大 total_kwh)
            cur.execute("""
                SELECT
                    COALESCE(SUM(m.total_kwh), 0) AS total_kwh,
                    COALESCE(SUM(m.total_kwh) / NULLIF(f.area_sqm, 0), 0) AS eui_kwh_per_m2,
                    COALESCE(MAX(m.max_kwh), 0) AS max_kwh,
                    COALESCE(MIN(m.min_kwh), 0) AS min_kwh,
                    COALESCE(SUM(m.hour_count), 0) AS hour_count
                FROM core.floor f
                LEFT JOIN mart.floor_daily_energy m
                       ON m.floor_id = f.id
                      AND m.date BETWEEN %s AND %s
                WHERE f.id = %s AND f.tenant_id = %s
                GROUP BY f.id, f.area_sqm
            """, (start_date, end_date, floor_id, tenant_id))
            s = cur.fetchone()

            cur.execute("""
                SELECT
                    p.id, p.point_code, p.point_name, p.energy_type,
                    p.measure_kind, p.unit_code,
                    ps.status, ps.last_reading_ts, ps.last_reading_val,
                    ps.completeness_pct, ps.fault_reason
                FROM core.point p
                LEFT JOIN mart.point_status_snapshot ps ON ps.point_id = p.id
                WHERE p.floor_id = %s
                ORDER BY p.energy_type, p.point_code
            """, (floor_id,))
            dev_rows = cur.fetchall()

    devices = []
    for r in dev_rows:
        devices.append({
            "id": str(r[0]),
            "point_code": r[1],
            "point_name": r[2],
            "energy_type": r[3],
            "measure_kind": r[4],
            "unit_code": r[5],
            "status": r[6],
            "last_reading_ts": r[7].isoformat() if r[7] else None,
            "last_reading_val": float(r[8]) if r[8] is not None else None,
            "completeness_pct": float(r[9]) if r[9] is not None else None,
            "fault_reason": r[10],
        })

    return {
        "floor": floor,
        "summary": {
            "total_kwh": float(s[0]),
            "eui_kwh_per_m2": float(s[1]) if s[1] is not None else None,
            "max_kwh": float(s[2]) if s[2] is not None else None,
            "min_kwh": float(s[3]) if s[3] is not None else None,
            "hour_count": s[4],
        },
        "devices": devices,
        "time_range": {"start": start, "end": end},
    }


def get_floor_timeseries(
    building_id: str,
    floor_id: str,
    tenant_id: str,
    start: str,
    end: str,
    granularity: str = "day",
    energy_type: str | None = None,
) -> dict:
    """
    楼层时序, 按能源分组。

    granularity:
      hour -> fact.point_reading (最细, 8760 点/年)
      day  -> mart.floor_daily_energy (365 点/年)
      month -> 从 day 聚合 (12 点/年)

    energy_type: 不传则返回所有能源, 传则只返回该能源。

    返回:
      series: [{energy_type, points: [{ts, value}]}]
    """
    if granularity not in GRANULARITIES:
        raise ValueError(f"granularity 只支持 {GRANULARITIES}")

    start_date = _to_utc_date_str(start)
    end_date = _to_utc_date_str(end)

    with get_conn() as conn:
        with conn.cursor() as cur:
            _verify_building(cur, building_id, tenant_id)
            _verify_floor(cur, floor_id, building_id, tenant_id)

            if granularity == "hour":
                sql = """
                    SELECT
                        pr.ts,
                        p.energy_type,
                        SUM(pr.value_num) AS value
                    FROM fact.point_reading pr
                    JOIN core.point p ON p.id = pr.point_id
                    WHERE p.floor_id = %s
                      AND pr.tenant_id = %s
                      AND pr.ts BETWEEN %s AND %s
                """
                params: list[Any] = [floor_id, tenant_id, start, end]
                if energy_type:
                    sql += " AND p.energy_type = %s"
                    params.append(energy_type)
                sql += " GROUP BY pr.ts, p.energy_type ORDER BY pr.ts, p.energy_type"
                cur.execute(sql, params)

            elif granularity == "day":
                sql = """
                    SELECT
                        m.date AS ts,
                        m.energy_type,
                        m.total_kwh AS value
                    FROM mart.floor_daily_energy m
                    WHERE m.floor_id = %s
                      AND m.tenant_id = %s
                      AND m.date BETWEEN %s AND %s
                """
                params = [floor_id, tenant_id, start_date, end_date]
                if energy_type:
                    sql += " AND m.energy_type = %s"
                    params.append(energy_type)
                sql += " ORDER BY m.date, m.energy_type"
                cur.execute(sql, params)

            else:  # month
                sql = """
                    SELECT
                        date_trunc('month', m.date)::date AS ts,
                        m.energy_type,
                        SUM(m.total_kwh) AS value
                    FROM mart.floor_daily_energy m
                    WHERE m.floor_id = %s
                      AND m.tenant_id = %s
                      AND m.date BETWEEN %s AND %s
                """
                params = [floor_id, tenant_id, start_date, end_date]
                if energy_type:
                    sql += " AND m.energy_type = %s"
                    params.append(energy_type)
                sql += """
                    GROUP BY date_trunc('month', m.date), m.energy_type
                    ORDER BY ts, m.energy_type
                """
                cur.execute(sql, params)

            rows = cur.fetchall()

    # 按 energy_type 分组
    grouped: dict[str, list[dict]] = {}
    for ts, et, val in rows:
        grouped.setdefault(et, []).append({
            "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "value": float(val) if val is not None else 0.0,
        })

    series = [{"energy_type": et, "points": pts} for et, pts in sorted(grouped.items())]

    return {
        "floor_id": floor_id,
        "granularity": granularity,
        "energy_type": energy_type,
        "series": series,
        "time_range": {"start": start, "end": end},
    }


def compare_floors(
    building_id: str,
    tenant_id: str,
    start: str,
    end: str,
    energy_type: str | None = None,
) -> dict:
    """
    楼层对比 (同楼不同层 × 能源)。

    返回:
      building: {id, building_code, display_name}
      floors: [
        {
          floor_id, floor_number, floor_name, floor_type, area_sqm,
          energy_breakdown: [{energy_type, total_kwh, eui_kwh_per_m2}],
          total_kwh (所有能源求和), eui_kwh_per_m2 (total_kwh / area_sqm)
        }
      ] (按 floor_number DESC)
    """
    start_date = _to_utc_date_str(start)
    end_date = _to_utc_date_str(end)

    with get_conn() as conn:
        with conn.cursor() as cur:
            bcode, bname, _site = _verify_building(cur, building_id, tenant_id)

            sql = """
                SELECT
                    f.id, f.floor_number, f.floor_name, f.floor_type, f.area_sqm,
                    m.energy_type,
                    SUM(m.total_kwh) AS total_kwh,
                    SUM(m.total_kwh) / NULLIF(f.area_sqm, 0) AS eui_kwh_per_m2
                FROM core.floor f
                JOIN mart.floor_daily_energy m ON m.floor_id = f.id
                WHERE f.building_id = %s
                  AND f.tenant_id = %s
                  AND m.date BETWEEN %s AND %s
            """
            params: list[Any] = [building_id, tenant_id, start_date, end_date]
            if energy_type:
                sql += " AND m.energy_type = %s"
                params.append(energy_type)
            sql += """
                GROUP BY f.id, f.floor_number, f.floor_name, f.floor_type,
                         f.area_sqm, m.energy_type
                ORDER BY f.floor_number DESC, m.energy_type
            """
            cur.execute(sql, params)
            rows = cur.fetchall()

    # 按楼层分组
    floors_map: dict[str, dict] = {}
    for r in rows:
        fid = str(r[0])
        if fid not in floors_map:
            floors_map[fid] = {
                "floor_id": fid,
                "floor_number": r[1],
                "floor_name": r[2],
                "floor_type": r[3],
                "area_sqm": float(r[4]) if r[4] is not None else None,
                "energy_breakdown": [],
                "total_kwh": 0.0,
                "eui_kwh_per_m2": None,
            }
        floor = floors_map[fid]
        et_total = float(r[6]) if r[6] is not None else 0.0
        floor["energy_breakdown"].append({
            "energy_type": r[5],
            "total_kwh": et_total,
            "eui_kwh_per_m2": float(r[7]) if r[7] is not None else None,
        })
        floor["total_kwh"] += et_total

    # 算每层总 EUI
    for floor in floors_map.values():
        area = floor["area_sqm"]
        if area and area > 0:
            floor["eui_kwh_per_m2"] = round(floor["total_kwh"] / area, 3)
        floor["total_kwh"] = round(floor["total_kwh"], 3)

    return {
        "building": {
            "id": building_id,
            "building_code": bcode,
            "display_name": bname,
        },
        "floors": list(floors_map.values()),
        "energy_type": energy_type,
        "time_range": {"start": start, "end": end},
    }


def get_floor_composition(
    building_id: str,
    tenant_id: str,
    start: str,
    end: str,
    floor_id: str | None = None,
) -> dict:
    """
    楼层能源构成 (饼图)。

    floor_id 不传: 返回全楼各能源占比 + 按楼层细分
    floor_id 传: 返回该楼层各能源占比

    返回:
      overall: [{energy_type, total_kwh, pct}]
      by_floor: [{floor_id, floor_number, floor_name, composition: [{energy_type, total_kwh, pct}]}]
    """
    start_date = _to_utc_date_str(start)
    end_date = _to_utc_date_str(end)

    with get_conn() as conn:
        with conn.cursor() as cur:
            bcode, bname, _site = _verify_building(cur, building_id, tenant_id)

            if floor_id:
                _verify_floor(cur, floor_id, building_id, tenant_id)

            # 全楼各能源汇总
            sql = """
                SELECT m.energy_type, SUM(m.total_kwh) AS total_kwh
                FROM mart.floor_daily_energy m
                JOIN core.floor f ON f.id = m.floor_id
                WHERE f.building_id = %s AND f.tenant_id = %s
                  AND m.date BETWEEN %s AND %s
            """
            params: list[Any] = [building_id, tenant_id, start_date, end_date]
            if floor_id:
                sql += " AND m.floor_id = %s"
                params.append(floor_id)
            sql += " GROUP BY m.energy_type ORDER BY total_kwh DESC"
            cur.execute(sql, params)
            overall_rows = cur.fetchall()

            # 按楼层细分 (floor_id 不传时才查)
            by_floor_rows = []
            if not floor_id:
                cur.execute("""
                    SELECT
                        f.id, f.floor_number, f.floor_name,
                        m.energy_type, SUM(m.total_kwh) AS total_kwh
                    FROM mart.floor_daily_energy m
                    JOIN core.floor f ON f.id = m.floor_id
                    WHERE f.building_id = %s AND f.tenant_id = %s
                      AND m.date BETWEEN %s AND %s
                    GROUP BY f.id, f.floor_number, f.floor_name, m.energy_type
                    ORDER BY f.floor_number DESC, m.energy_type
                """, (building_id, tenant_id, start_date, end_date))
                by_floor_rows = cur.fetchall()

    overall_total = sum(float(r[1] or 0) for r in overall_rows)
    overall = []
    for r in overall_rows:
        total = float(r[1] or 0)
        overall.append({
            "energy_type": r[0],
            "total_kwh": round(total, 3),
            "pct": round(total / overall_total * 100, 2) if overall_total > 0 else 0.0,
        })

    by_floor: dict[str, dict] = {}
    for r in by_floor_rows:
        fid = str(r[0])
        if fid not in by_floor:
            by_floor[fid] = {
                "floor_id": fid,
                "floor_number": r[1],
                "floor_name": r[2],
                "composition": [],
                "_total": 0.0,
            }
        total = float(r[4] or 0)
        by_floor[fid]["composition"].append({"energy_type": r[3], "total_kwh": total})
        by_floor[fid]["_total"] += total

    # 算每层各能源 pct
    for floor in by_floor.values():
        ft = floor.pop("_total")
        for c in floor["composition"]:
            c["pct"] = round(c["total_kwh"] / ft * 100, 2) if ft > 0 else 0.0
            c["total_kwh"] = round(c["total_kwh"], 3)

    return {
        "building": {
            "id": building_id,
            "building_code": bcode,
            "display_name": bname,
        },
        "floor_id": floor_id,
        "overall": overall,
        "by_floor": list(by_floor.values()),
        "time_range": {"start": start, "end": end},
    }


def list_floor_devices(
    building_id: str,
    tenant_id: str,
    floor_id: str | None = None,
    status: str | None = None,
) -> dict:
    """
    设备状态列表 (可按 floor_id / status 过滤)。

    返回:
      building: {id, building_code, display_name}
      summary: {total, online, offline, fault, stale}
      devices: [{id, point_code, point_name, energy_type, measure_kind, unit_code,
                 floor_id, floor_number, floor_name, floor_type,
                 status, last_reading_ts, last_reading_val, completeness_pct, fault_reason}]
    """
    if status and status not in ("ONLINE", "OFFLINE", "FAULT", "STALE"):
        raise ValueError("status 只支持 ONLINE / OFFLINE / FAULT / STALE")

    with get_conn() as conn:
        with conn.cursor() as cur:
            bcode, bname, _site = _verify_building(cur, building_id, tenant_id)

            if floor_id:
                _verify_floor(cur, floor_id, building_id, tenant_id)

            sql = """
                SELECT
                    p.id, p.point_code, p.point_name, p.energy_type,
                    p.measure_kind, p.unit_code,
                    f.id, f.floor_number, f.floor_name, f.floor_type,
                    ps.status, ps.last_reading_ts, ps.last_reading_val,
                    ps.completeness_pct, ps.fault_reason
                FROM core.point p
                JOIN core.floor f ON f.id = p.floor_id
                LEFT JOIN mart.point_status_snapshot ps ON ps.point_id = p.id
                WHERE p.building_id = %s
                  AND p.tenant_id = %s
                  AND p.floor_id IS NOT NULL
            """
            params: list[Any] = [building_id, tenant_id]
            if floor_id:
                sql += " AND p.floor_id = %s"
                params.append(floor_id)
            if status:
                sql += " AND ps.status = %s"
                params.append(status)
            sql += " ORDER BY f.floor_number DESC, p.energy_type, p.point_code"
            cur.execute(sql, params)
            rows = cur.fetchall()

    devices = []
    summary = {"total": 0, "online": 0, "offline": 0, "fault": 0, "stale": 0}
    for r in rows:
        st = r[10] or "ONLINE"
        summary["total"] += 1
        if st == "ONLINE":
            summary["online"] += 1
        elif st == "OFFLINE":
            summary["offline"] += 1
        elif st == "FAULT":
            summary["fault"] += 1
        elif st == "STALE":
            summary["stale"] += 1

        devices.append({
            "id": str(r[0]),
            "point_code": r[1],
            "point_name": r[2],
            "energy_type": r[3],
            "measure_kind": r[4],
            "unit_code": r[5],
            "floor_id": str(r[6]) if r[6] else None,
            "floor_number": r[7],
            "floor_name": r[8],
            "floor_type": r[9],
            "status": st,
            "last_reading_ts": r[11].isoformat() if r[11] else None,
            "last_reading_val": float(r[12]) if r[12] is not None else None,
            "completeness_pct": float(r[13]) if r[13] is not None else None,
            "fault_reason": r[14],
        })

    return {
        "building": {
            "id": building_id,
            "building_code": bcode,
            "display_name": bname,
        },
        "summary": summary,
        "devices": devices,
        "filter": {"floor_id": floor_id, "status": status},
    }


def list_floor_anomalies(
    building_id: str,
    tenant_id: str,
    start: str,
    end: str,
    floor_id: str | None = None,
    limit: int = 100,
) -> dict:
    """
    楼层异常事件 (复用 mart.anomaly_event, 通过 point_id JOIN 拿 floor_id)。

    Step 08 异常检测跑完后有数据, 目前可能为空 (返空列表, 前端显示"暂无异常")。

    返回:
      building: {id, building_code, display_name}
      anomalies: [{id, point_id, point_code, energy_type,
                   floor_id, floor_number, floor_name,
                   event_type, severity, metric_code, status,
                   start_ts, end_ts, observed_value, baseline_value, evidence}]
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            bcode, bname, _site = _verify_building(cur, building_id, tenant_id)

            if floor_id:
                _verify_floor(cur, floor_id, building_id, tenant_id)

            sql = """
                SELECT
                    ae.id, ae.point_id, p.point_code, p.energy_type,
                    f.id, f.floor_number, f.floor_name,
                    ae.event_type, ae.severity, ae.metric_code, ae.status,
                    ae.start_ts, ae.end_ts,
                    ae.observed_value, ae.baseline_value, ae.evidence
                FROM mart.anomaly_event ae
                JOIN core.point p ON p.id = ae.point_id
                LEFT JOIN core.floor f ON f.id = p.floor_id
                WHERE ae.building_id = %s
                  AND ae.tenant_id = %s
                  AND ae.start_ts BETWEEN %s AND %s
            """
            params: list[Any] = [building_id, tenant_id, start, end]
            if floor_id:
                sql += " AND p.floor_id = %s"
                params.append(floor_id)
            sql += " ORDER BY ae.start_ts DESC LIMIT %s"
            params.append(limit)
            cur.execute(sql, params)
            rows = cur.fetchall()

    anomalies = []
    for r in rows:
        anomalies.append({
            "id": str(r[0]),
            "point_id": str(r[1]) if r[1] else None,
            "point_code": r[2],
            "energy_type": r[3],
            "floor_id": str(r[4]) if r[4] else None,
            "floor_number": r[5],
            "floor_name": r[6],
            "event_type": r[7],
            "severity": r[8],
            "metric_code": r[9],
            "status": r[10],
            "start_ts": r[11].isoformat() if r[11] else None,
            "end_ts": r[12].isoformat() if r[12] else None,
            "observed_value": float(r[13]) if r[13] is not None else None,
            "baseline_value": float(r[14]) if r[14] is not None else None,
            "evidence": r[15] if r[15] else {},
        })

    return {
        "building": {
            "id": building_id,
            "building_code": bcode,
            "display_name": bname,
        },
        "anomalies": anomalies,
        "filter": {"floor_id": floor_id},
        "time_range": {"start": start, "end": end},
    }


def check_floor_consistency(
    building_id: str,
    tenant_id: str,
    energy_type: str | None = None,
) -> dict:
    """
    加总约束自检: Σ floor readings = building METER readings (逐小时)。

    开发期验证用, 前端"自检"按钮调用。passed=True 表示加总约束成立
    (max_diff_pct < 0.001%, 即浮点级误差)。

    返回:
      building: {id, building_code, display_name}
      energy_type: 用户筛选的能源 (None 表示全部)
      results: [{energy_type, n_hours, max_diff_pct, avg_diff_pct, passed}]
      passed: 全部能源都通过
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            bcode, bname, _site = _verify_building(cur, building_id, tenant_id)

            floor_sql = """
                SELECT p.energy_type, pr.ts, SUM(pr.value_num) AS floor_total
                FROM fact.point_reading pr
                JOIN core.point p ON p.id = pr.point_id
                WHERE p.building_id = %s
                  AND p.tenant_id = %s
                  AND p.floor_id IS NOT NULL
                  AND p.measure_kind IN ('energy_kwh', 'energy_kwh_thermal', 'volume_liter')
            """
            floor_params: list[Any] = [building_id, tenant_id]
            if energy_type:
                floor_sql += " AND p.energy_type = %s"
                floor_params.append(energy_type)
            floor_sql += " GROUP BY p.energy_type, pr.ts"

            building_sql = """
                SELECT p.energy_type, pr.ts, pr.value_num AS building_total
                FROM fact.point_reading pr
                JOIN core.point p ON p.id = pr.point_id
                WHERE p.building_id = %s
                  AND p.tenant_id = %s
                  AND p.point_kind = 'METER'
                  AND p.measure_kind IN ('energy_kwh', 'energy_kwh_thermal', 'volume_liter')
            """
            building_params: list[Any] = [building_id, tenant_id]
            if energy_type:
                building_sql += " AND p.energy_type = %s"
                building_params.append(energy_type)

            cur.execute(f"""
                WITH floor_sum AS ({floor_sql}),
                     building_total AS ({building_sql})
                SELECT
                    bs.energy_type,
                    COUNT(*) AS n_hours,
                    MAX(ABS(fs.floor_total - bs.building_total) /
                        NULLIF(ABS(bs.building_total), 0) * 100) AS max_diff_pct,
                    AVG(ABS(fs.floor_total - bs.building_total) /
                        NULLIF(ABS(bs.building_total), 0) * 100) AS avg_diff_pct
                FROM floor_sum fs
                JOIN building_total bs
                  ON bs.energy_type = fs.energy_type AND bs.ts = fs.ts
                GROUP BY bs.energy_type
                ORDER BY max_diff_pct DESC NULLS LAST
            """, floor_params + building_params)
            rows = cur.fetchall()

    results = []
    all_passed = True
    for r in rows:
        et, n_hours, max_d, avg_d = r
        # max_d 可能是 NULL (building_total 全 0, 无法算 pct), 视为通过
        max_d_val = float(max_d) if max_d is not None else 0.0
        avg_d_val = float(avg_d) if avg_d is not None else 0.0
        passed = max_d_val < CONSISTENCY_THRESHOLD_PCT
        if not passed:
            all_passed = False
        results.append({
            "energy_type": et,
            "n_hours": n_hours,
            "max_diff_pct": max_d_val,
            "avg_diff_pct": avg_d_val,
            "passed": passed,
        })

    return {
        "building": {
            "id": building_id,
            "building_code": bcode,
            "display_name": bname,
        },
        "energy_type": energy_type,
        "results": results,
        "passed": all_passed,
        "threshold_pct": CONSISTENCY_THRESHOLD_PCT,
    }


# ============================================================================
# 写接口: 自动拆分楼层 (Step 11a)
# ----------------------------------------------------------------------------
# 用户上传楼栋 + 楼栋 METER 读数后, 楼层分析页没数据 (core.floor 为空)。
# 点"自动拆分"按钮按 building.floors_count 生成默认楼层 (1F LOBBY / 中间 OFFICE
# / 顶 MECHANICAL+rooftop), 复用 floor_synthetic 算法生成 SENSOR 读数, 加总
# 约束严格成立。
#
# 跟 seed 区别:
#   - source_dataset='auto_split' (seed 是 'synthetic_floor'), 便于一键清理
#   - 不写 FLOOR_PLAN 字典, 用 _default_floor_plan(floors_count) 自动生成
#   - 用户可重复点 (先清理旧数据再拆), seed 只能跑一次
#
# 校验:
#   - building 必须存在 (_verify_building 抛 NotFoundError)
#   - 楼栋不能已有楼层 (避免覆盖用户手动上传的 floors.csv 数据)
#   - 楼栋必须有 METER 读数 (process_building_floors 内部校验, 没 ground truth
#     拆不了)
# ============================================================================

# auto_split 默认楼层数: building.floors_count 为 0/NULL 时兜底
# (用户上传 buildings.csv 时没填 floors_count 的情况)
DEFAULT_FLOOR_COUNT_FOR_AUTO_SPLIT = 3


def auto_split_floors(building_id: str, tenant_id: str) -> dict:
    """
    自动按 building.floors_count 拆楼层 + 生成 SENSOR 读数。

    流程:
      1. 校验 building 存在
      2. 校验楼栋没有已有楼层 (避免覆盖)
      3. 拿 building_code / sqm / floors_count
      4. _default_floor_plan(floors_count) 生成楼层定义
      5. process_building_floors(source_dataset='auto_split') 跑算法
      6. ANALYZE + commit

    返回:
      {
        "building": {id, building_code, display_name, floors_count},
        "floors_created": int,
        "floor_points_created": int,
        "floor_readings_inserted": int,
        "floor_daily_rows": int,
        "device_status_rows": int,
        "source_dataset": "auto_split",
      }
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            bcode, bname, _site = _verify_building(cur, building_id, tenant_id)

            # 校验楼栋没有已有楼层 (任何 source_dataset 都算)
            cur.execute("""
                SELECT COUNT(*) FROM core.floor
                WHERE building_id = %s AND tenant_id = %s
            """, (building_id, tenant_id))
            existing = cur.fetchone()[0]
            if existing > 0:
                raise ValueError(
                    f"楼栋 {bcode} 已有 {existing} 层数据, "
                    "请先在楼层分析页清空再自动拆分 "
                    "(或检查是否已上传 floors.csv)"
                )

            # 拿 sqm + floors_count
            cur.execute("""
                SELECT sqm, floors_count FROM core.building
                WHERE id = %s AND tenant_id = %s
            """, (building_id, tenant_id))
            row = cur.fetchone()
            sqm = float(row[0]) if row[0] else 0.0
            floors_count = row[1] or DEFAULT_FLOOR_COUNT_FOR_AUTO_SPLIT

            if floors_count < 1:
                floors_count = DEFAULT_FLOOR_COUNT_FOR_AUTO_SPLIT

            # 生成默认楼层定义
            floor_defs = _default_floor_plan(floors_count)
            logger.info("楼栋 {} 自动拆分: {} 层 (sqm={})",
                        bcode, floors_count, sqm)

            # 跑算法 (不 commit, 这里统一 commit)
            stats = process_building_floors(
                cur, tenant_id, building_id, bcode, sqm, floors_count,
                floor_defs, source_dataset="auto_split",
            )

            # ANALYZE 这栋楼涉及的表 (auto_split 是单楼栋操作, 不批量)
            cur.execute("ANALYZE core.floor")
            cur.execute("ANALYZE core.point")
            cur.execute("ANALYZE fact.point_reading")
            cur.execute("ANALYZE mart.floor_daily_energy")
            cur.execute("ANALYZE mart.point_status_snapshot")

        conn.commit()

    return {
        "building": {
            "id": building_id,
            "building_code": bcode,
            "display_name": bname,
            "floors_count": floors_count,
        },
        "floors_created": stats["floors_created"],
        "floor_points_created": stats["floor_points_created"],
        "floor_readings_inserted": stats["floor_readings_inserted"],
        "floor_daily_rows": stats["floor_daily_rows"],
        "device_status_rows": stats["device_status_rows"],
        "source_dataset": "auto_split",
    }
