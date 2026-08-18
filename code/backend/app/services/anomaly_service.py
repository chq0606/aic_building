"""
异常检测编排服务。

编排流程:
  1. 解析时间范围 (复用 query_service._resolve_range, 不传则用租户实际数据范围)
  2. 解析 building 列表 (传 building_id 跑单楼, 传 site_id 跑整个 site)
  3. 每个 building 跑:
     - building-level 检测: BASELINE_DEVIATION (1 次)
     - point-level 检测: SPIKE/DRIFT/PROLONGED_ZERO/MISSING_GAP/SCHEDULE_VIOLATION
       对该 building 所有 point 各跑一遍 (5 类 × N 个 point)
  4. 写入前 DELETE 该 building 该时段 + event_type 的旧异常 (决策点 #4)
     再 INSERT 新检测出的异常, UNIQUE 约束兜底防重 (决策点 #5)
  5. 一次事务提交, 返回汇总统计

同步执行 (决策点 #2): 6 栋楼 × 5 类 × 28 个 point 总计 ~150 次检测,
demo 数据 16.9 万行读数, 实测 < 5 秒能跑完, 不需要异步任务。等真实客户
数据量上来后 (> 100 栋楼) 再考虑改异步。
"""
import time
from datetime import datetime, timezone

from loguru import logger

from app.db.session import get_conn
from app.services.query_service import NotFoundError, _resolve_range
from .anomaly_detectors import (
    DetectorContext,
    baseline_deviation,
    drift,
    isolation_forest,
    missing_gap,
    prolonged_zero,
    schedule_violation,
    spike,
)


# 全部 7 类异常类型
ALL_EVENT_TYPES = (
    "SPIKE",
    "DRIFT",
    "PROLONGED_ZERO",
    "MISSING_GAP",
    "SCHEDULE_VIOLATION",
    "BASELINE_DEVIATION",
    "ML_OUTLIER",
)

# point-level 检测器映射
POINT_LEVEL_DETECTORS = {
    "SPIKE": spike,
    "DRIFT": drift,
    "PROLONGED_ZERO": prolonged_zero,
    "MISSING_GAP": missing_gap,
    "SCHEDULE_VIOLATION": schedule_violation,
}

# building-level 检测器映射 (每楼跑一遍, point_id IS NULL)
BUILDING_LEVEL_DETECTORS = {
    "BASELINE_DEVIATION": baseline_deviation,
    "ML_OUTLIER": isolation_forest,
}


def _verify_site(cur, site_id: str, tenant_id: str) -> None:
    """校验 site 属于租户, 不存在抛 NotFoundError。"""
    cur.execute("""
        SELECT id FROM core.site WHERE id = %s AND tenant_id = %s
    """, (site_id, tenant_id))
    if cur.fetchone() is None:
        raise NotFoundError("site 不存在或不属于当前租户")


def _verify_building(cur, building_id: str, tenant_id: str) -> tuple[str, str | None, float | None, str | None, str | None]:
    """
    校验 building 属于租户, 返回 (building_code, primary_use, sqm, site_timezone, site_latitude)。
    site 信息一起 join 出来, 用于时区判定 (SCHEDULE_VIOLATION 需要)。
    """
    cur.execute("""
        SELECT
            b.building_code,
            b.primary_use,
            b.sqm,
            s.timezone,
            s.latitude
        FROM core.building b
        JOIN core.site s ON s.id = b.site_id
        WHERE b.id = %s AND b.tenant_id = %s
    """, (building_id, tenant_id))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError("building 不存在或不属于当前租户")
    return row[0], row[1], row[2], row[3], row[4]


def _list_buildings_in_site(cur, site_id: str, tenant_id: str) -> list[str]:
    """列出 site 下所有 building_id。"""
    cur.execute("""
        SELECT id FROM core.building
        WHERE site_id = %s AND tenant_id = %s
        ORDER BY building_code
    """, (site_id, tenant_id))
    return [str(r[0]) for r in cur.fetchall()]


def _list_points_in_building(cur, building_id: str, tenant_id: str) -> list[tuple]:
    """列出 building 下所有 point, 返回 (point_id, point_code, energy_type, measure_kind, unit_code)。"""
    cur.execute("""
        SELECT id, point_code, energy_type, measure_kind, unit_code
        FROM core.point
        WHERE building_id = %s AND tenant_id = %s
        ORDER BY energy_type
    """, (building_id, tenant_id))
    return [(str(r[0]), r[1], r[2], r[3], r[4]) for r in cur.fetchall()]


def _delete_old_events(
    cur,
    tenant_id: str,
    building_id: str,
    point_id: str | None,
    event_type: str,
    start_ts: str,
    end_ts: str,
) -> int:
    """
    删除旧异常 (决策点 #4)。

    必须按 point_id 过滤:
      - point-level (SPIKE/DRIFT/...): DELETE 该 (building, point, event_type) 时段内的旧事件
      - building-level (BASELINE_DEVIATION, point_id IS NULL): DELETE 该 (building, event_type) 且 point_id IS NULL 的旧事件

    不加 point_id 过滤会出大 bug: 同一 building 的多个 point 顺序处理时,
    后一个 point 的 DELETE 会把前一个 point 刚 INSERT 的事件全删掉,
    最终只剩 "每个 (building, event_type) 最后一个 point" 的事件存活。
    """
    if point_id is None:
        cur.execute("""
            DELETE FROM mart.anomaly_event
            WHERE tenant_id = %s
              AND building_id = %s
              AND point_id IS NULL
              AND event_type = %s
              AND start_ts >= %s::timestamptz
              AND end_ts <= %s::timestamptz
        """, (tenant_id, building_id, event_type, start_ts, end_ts))
    else:
        cur.execute("""
            DELETE FROM mart.anomaly_event
            WHERE tenant_id = %s
              AND building_id = %s
              AND point_id = %s
              AND event_type = %s
              AND start_ts >= %s::timestamptz
              AND end_ts <= %s::timestamptz
        """, (tenant_id, building_id, point_id, event_type, start_ts, end_ts))
    return cur.rowcount


def _insert_event(cur, tenant_id: str, building_id: str, point_id: str | None, event: dict) -> None:
    """插入一条异常事件。"""
    cur.execute("""
        INSERT INTO mart.anomaly_event
            (tenant_id, building_id, point_id, event_type, severity,
             metric_code, start_ts, end_ts, observed_value, baseline_value,
             evidence, status)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'open')
    """, (
        tenant_id, building_id, point_id,
        event["event_type"], event["severity"],
        event["metric_code"], event["start_ts"], event["end_ts"],
        event["observed_value"], event["baseline_value"],
        _to_jsonb(event["evidence"]),
    ))


def _to_jsonb(value):
    """把 dict 转 psycopg2 的 Json 适配器, 让 jsonb 列正确接收。"""
    from psycopg2.extras import Json
    return Json(value)


def _run_point_level_detectors(
    cur,
    ctx: DetectorContext,
    event_types: list[str],
    start_ts: str,
    end_ts: str,
) -> tuple[int, int, dict[str, int], dict[str, int]]:
    """
    对一个 point 跑 5 类 point-level 检测器。

    返回 (inserted, deleted, by_type, by_severity)。
    """
    inserted = 0
    deleted = 0
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}

    for event_type in event_types:
        if event_type not in POINT_LEVEL_DETECTORS:
            continue
        detector = POINT_LEVEL_DETECTORS[event_type]

        # 先删旧 (该 point 该 event_type 该时段, 不动其他 point)
        deleted += _delete_old_events(
            cur, ctx.tenant_id, ctx.building_id, ctx.point_id, event_type, start_ts, end_ts,
        )

        # 跑检测
        events = detector.detect(cur, ctx)

        # 插新
        for ev in events:
            _insert_event(cur, ctx.tenant_id, ctx.building_id, ctx.point_id, ev)
            inserted += 1
            by_type[event_type] = by_type.get(event_type, 0) + 1
            sev = ev["severity"]
            by_severity[sev] = by_severity.get(sev, 0) + 1

    return inserted, deleted, by_type, by_severity


def _run_building_level_detectors(
    cur,
    ctx: DetectorContext,
    event_types: list[str],
    start_ts: str,
    end_ts: str,
) -> tuple[int, int, dict[str, int], dict[str, int]]:
    """
    对一个 building 跑 building-level 检测器 (BASELINE_DEVIATION + ML_OUTLIER)。
    """
    inserted = 0
    deleted = 0
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}

    for event_type in event_types:
        if event_type not in BUILDING_LEVEL_DETECTORS:
            continue
        detector = BUILDING_LEVEL_DETECTORS[event_type]

        # 先删旧 (building-level, point_id IS NULL)
        deleted += _delete_old_events(
            cur, ctx.tenant_id, ctx.building_id, None, event_type, start_ts, end_ts,
        )

        # 跑检测
        events = detector.detect(cur, ctx)

        for ev in events:
            _insert_event(cur, ctx.tenant_id, ctx.building_id, None, ev)
            inserted += 1
            by_type[event_type] = by_type.get(event_type, 0) + 1
            sev = ev["severity"]
            by_severity[sev] = by_severity.get(sev, 0) + 1

    return inserted, deleted, by_type, by_severity


def detect_anomalies(
    tenant_id: str,
    building_id: str | None,
    site_id: str | None,
    start: str | None,
    end: str | None,
    event_types: list[str] | None = None,
) -> dict:
    """
    触发异常检测。

    参数:
      building_id: 不传则跑整个 site (此时 site_id 必传)
      site_id: building_id 为空时必传
      start/end: ISO8601, 不传按租户实际数据范围
      event_types: 要跑的检测类型子集, 不传全部 6 类

    返回检测汇总。
    """
    if not building_id and not site_id:
        raise ValueError("必须传 building_id 或 site_id 之一")

    # 校验 event_types
    run_types = list(event_types) if event_types else list(ALL_EVENT_TYPES)
    invalid = [t for t in run_types if t not in ALL_EVENT_TYPES]
    if invalid:
        raise ValueError(f"不支持的 event_type: {invalid}, 可选 {list(ALL_EVENT_TYPES)}")

    # 时间范围
    s, e = _resolve_range(start, end, tenant_id)

    t0 = time.time()

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 确定要检测的 building 列表
            if building_id:
                building_code, _, _, _, _ = _verify_building(cur, building_id, tenant_id)
                building_ids = [building_id]
                logger.info("anomaly detect: tenant={} building={} range={}~{}", tenant_id, building_code, s, e)
            else:
                _verify_site(cur, site_id, tenant_id)
                building_ids = _list_buildings_in_site(cur, site_id, tenant_id)
                logger.info("anomaly detect: tenant={} site={} buildings={} range={}~{}",
                            tenant_id, site_id, len(building_ids), s, e)

            total_inserted = 0
            total_deleted = 0
            total_by_type: dict[str, int] = {}
            total_by_severity: dict[str, int] = {}
            point_count = 0

            for bid in building_ids:
                building_code, primary_use, sqm, site_tz, site_lat = _verify_building(cur, bid, tenant_id)

                # 累计本 building 的 inserted/deleted (含 building-level + 所有 point)
                building_inserted = 0
                building_deleted = 0

                # building-level 检测 (BASELINE_DEVIATION)
                building_ctx = DetectorContext(
                    tenant_id=tenant_id,
                    building_id=bid,
                    point_id=None,
                    point_code=None,
                    energy_type=None,
                    measure_kind=None,
                    unit_code=None,
                    building_code=building_code,
                    primary_use=primary_use,
                    sqm=float(sqm) if sqm is not None else None,
                    start_ts=s,
                    end_ts=e,
                    site_timezone=site_tz,
                    site_latitude=float(site_lat) if site_lat is not None else None,
                )
                ins, dels, bt, bs = _run_building_level_detectors(
                    cur, building_ctx, run_types, s, e,
                )
                total_inserted += ins
                total_deleted += dels
                building_inserted += ins
                building_deleted += dels
                for k, v in bt.items():
                    total_by_type[k] = total_by_type.get(k, 0) + v
                for k, v in bs.items():
                    total_by_severity[k] = total_by_severity.get(k, 0) + v

                # point-level 检测
                points = _list_points_in_building(cur, bid, tenant_id)
                point_count += len(points)
                for point_id, point_code, energy_type, measure_kind, unit_code in points:
                    point_ctx = DetectorContext(
                        tenant_id=tenant_id,
                        building_id=bid,
                        point_id=point_id,
                        point_code=point_code,
                        energy_type=energy_type,
                        measure_kind=measure_kind,
                        unit_code=unit_code,
                        building_code=building_code,
                        primary_use=primary_use,
                        sqm=float(sqm) if sqm is not None else None,
                        start_ts=s,
                        end_ts=e,
                        site_timezone=site_tz,
                        site_latitude=float(site_lat) if site_lat is not None else None,
                    )
                    ins, dels, bt, bs = _run_point_level_detectors(
                        cur, point_ctx, run_types, s, e,
                    )
                    total_inserted += ins
                    total_deleted += dels
                    building_inserted += ins
                    building_deleted += dels
                    for k, v in bt.items():
                        total_by_type[k] = total_by_type.get(k, 0) + v
                    for k, v in bs.items():
                        total_by_severity[k] = total_by_severity.get(k, 0) + v

                logger.info(
                    "  building={} points={} inserted={} deleted={}",
                    building_code, len(points), building_inserted, building_deleted,
                )

        # 写操作显式 commit (get_conn 不会自动 commit, 异常时才 rollback)
        conn.commit()

    duration_ms = int((time.time() - t0) * 1000)
    logger.info(
        "anomaly detect done: buildings={} points={} inserted={} deleted={} duration_ms={}",
        len(building_ids), point_count, total_inserted, total_deleted, duration_ms,
    )

    return {
        "building_count": len(building_ids),
        "point_count": point_count,
        "time_range": {"start": s, "end": e},
        "event_types_run": run_types,
        "anomalies_inserted": total_inserted,
        "anomalies_deleted": total_deleted,
        "by_type": total_by_type,
        "by_severity": total_by_severity,
        "duration_ms": duration_ms,
    }


def _row_to_event(row) -> dict:
    """把 mart.anomaly_event 一行转成对外 dict, join 出 building/point 信息。"""
    return {
        "id": str(row[0]),
        "building_id": str(row[1]),
        "building_code": row[2],
        "display_name": row[3],
        "point_id": str(row[4]) if row[4] is not None else None,
        "point_code": row[5],
        "energy_type": row[6],
        "event_type": row[7],
        "severity": row[8],
        "metric_code": row[9],
        "start_ts": row[10].isoformat() if hasattr(row[10], "isoformat") else str(row[10]),
        "end_ts": row[11].isoformat() if hasattr(row[11], "isoformat") else str(row[11]),
        "observed_value": float(row[12]) if row[12] is not None else None,
        "baseline_value": float(row[13]) if row[13] is not None else None,
        "evidence": row[14],
        "status": row[15],
        "created_at": row[16].isoformat() if hasattr(row[16], "isoformat") else str(row[16]),
    }


_ANOMALY_JOIN_SELECT = """
    SELECT
        ae.id,
        ae.building_id,
        b.building_code,
        b.display_name,
        ae.point_id,
        p.point_code,
        p.energy_type,
        ae.event_type,
        ae.severity,
        ae.metric_code,
        ae.start_ts,
        ae.end_ts,
        ae.observed_value,
        ae.baseline_value,
        ae.evidence,
        ae.status,
        ae.created_at
    FROM mart.anomaly_event ae
    JOIN core.building b ON b.id = ae.building_id
    LEFT JOIN core.point p ON p.id = ae.point_id
"""


def list_anomalies_for_building(
    building_id: str,
    tenant_id: str,
    start: str | None = None,
    end: str | None = None,
    event_type: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> dict:
    """查 building 的异常列表。"""
    s, e = _resolve_range(start, end, tenant_id)
    limit = max(1, min(int(limit), 500))

    clauses = ["ae.tenant_id = %s", "ae.building_id = %s", "ae.start_ts >= %s", "ae.end_ts <= %s"]
    params: list = [tenant_id, building_id, s, e]
    if event_type:
        clauses.append("ae.event_type = %s")
        params.append(event_type)
    if status:
        clauses.append("ae.status = %s")
        params.append(status)
    params.append(limit)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM core.building WHERE id = %s AND tenant_id = %s
            """, (building_id, tenant_id))
            if cur.fetchone() is None:
                raise NotFoundError("building 不存在或不属于当前租户")

            cur.execute(f"""
                {_ANOMALY_JOIN_SELECT}
                WHERE {' AND '.join(clauses)}
                ORDER BY ae.start_ts DESC
                LIMIT %s
            """, tuple(params))
            rows = cur.fetchall()

    anomalies = [_row_to_event(r) for r in rows]
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for a in anomalies:
        by_type[a["event_type"]] = by_type.get(a["event_type"], 0) + 1
        by_severity[a["severity"]] = by_severity.get(a["severity"], 0) + 1

    # 拿 building_code / display_name
    building_code = anomalies[0]["building_code"] if anomalies else None
    display_name = anomalies[0]["display_name"] if anomalies else None
    if building_code is None:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT building_code, display_name FROM core.building
                    WHERE id = %s AND tenant_id = %s
                """, (building_id, tenant_id))
                r = cur.fetchone()
                if r:
                    building_code, display_name = r[0], r[1]

    return {
        "building_id": building_id,
        "building_code": building_code,
        "display_name": display_name,
        "time_range": {"start": s, "end": e},
        "total": len(anomalies),
        "by_type": by_type,
        "by_severity": by_severity,
        "anomalies": anomalies,
    }


def get_site_anomaly_overview(
    site_id: str,
    tenant_id: str,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """site 异常概览: 总数 + 按类型/严重度分组 + 每栋楼摘要。"""
    s, e = _resolve_range(start, end, tenant_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM core.site WHERE id = %s AND tenant_id = %s", (site_id, tenant_id))
            if cur.fetchone() is None:
                raise NotFoundError("site 不存在或不属于当前租户")

            cur.execute(f"""
                {_ANOMALY_JOIN_SELECT}
                JOIN core.building b2 ON b2.id = ae.building_id
                WHERE ae.tenant_id = %s AND b2.site_id = %s
                  AND ae.start_ts >= %s AND ae.end_ts <= %s
                ORDER BY ae.start_ts DESC
            """, (tenant_id, site_id, s, e))
            rows = cur.fetchall()

            # 每栋楼摘要
            cur.execute("""
                SELECT b.id, b.building_code, b.display_name,
                       COUNT(ae.id) AS anomaly_count,
                       COUNT(ae.id) FILTER (WHERE ae.severity = 'HIGH') AS high_count,
                       COUNT(ae.id) FILTER (WHERE ae.severity = 'MEDIUM') AS medium_count,
                       COUNT(ae.id) FILTER (WHERE ae.severity = 'LOW') AS low_count
                FROM core.building b
                LEFT JOIN mart.anomaly_event ae
                    ON ae.building_id = b.id
                    AND ae.tenant_id = b.tenant_id
                    AND ae.start_ts >= %s AND ae.end_ts <= %s
                WHERE b.site_id = %s AND b.tenant_id = %s
                GROUP BY b.id, b.building_code, b.display_name
                ORDER BY anomaly_count DESC, b.building_code
            """, (s, e, site_id, tenant_id))
            building_rows = cur.fetchall()

    anomalies = [_row_to_event(r) for r in rows]
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    for a in anomalies:
        by_type[a["event_type"]] = by_type.get(a["event_type"], 0) + 1
        by_severity[a["severity"]] = by_severity.get(a["severity"], 0) + 1

    buildings = [
        {
            "building_id": str(r[0]),
            "building_code": r[1],
            "display_name": r[2],
            "anomaly_count": r[3],
            "high_count": r[4],
            "medium_count": r[5],
            "low_count": r[6],
        }
        for r in building_rows
    ]

    return {
        "site_id": site_id,
        "time_range": {"start": s, "end": e},
        "building_count": len(building_rows),
        "total_anomalies": len(anomalies),
        "by_type": by_type,
        "by_severity": by_severity,
        "buildings": buildings,
    }


def get_anomaly_evidence(anomaly_id: str, tenant_id: str) -> dict:
    """查单条异常的证据链。Step 18 AI 助手按异常 ID 检索时用。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                {_ANOMALY_JOIN_SELECT}
                WHERE ae.id = %s AND ae.tenant_id = %s
            """, (anomaly_id, tenant_id))
            row = cur.fetchone()

    if row is None:
        raise NotFoundError("anomaly 不存在或不属于当前租户")

    return _row_to_event(row)
