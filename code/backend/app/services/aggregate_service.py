"""
mart.building_daily_energy 重算。

fact.point_reading 是增量 upsert 进来的，日聚合表 mart.building_daily_energy
要同步更新。聚合时不限 source_batch_id（一个日聚合行可能由多个 batch 贡献数据），
只按受影响范围过滤：找出本 batch 写入的 (building_id, energy_type, date) 集合，
把这些日期的聚合行先删后插，重新从 fact 全量算。

source_batch_id 字段在 mart 里记"最后修改这个日聚合的 batch"，rollback 时不依赖
这个字段定位（rollback 先查受影响范围再删 fact，重算时 fact 已少了一部分，
聚合自然反映新数据）。

step 03 seed 里有一段类似的聚合 SQL，但 WHERE 限了 pr.source_batch_id = %s
（因为 reset 把 fact 表清空了）。step 05 增量场景不能这么写，要全量聚合。
"""
from loguru import logger

from app.db.session import get_conn


def _rebuild_range(cur, tenant_id: str, affected_subquery: str, params: list, batch_id: str | None) -> int:
    """
    按 affected_subquery 给出的 (building_id, energy_type, date) 集合重算日聚合。
    affected_subquery 应返回三列 (building_id, energy_type, date)。
    返回写入行数。

    重算逻辑：先删受影响范围的旧聚合行，再从 fact.point_reading 全量算这些日期的聚合。
    不按 source_batch_id 过滤 fact（不然其他 batch 贡献的数据会丢）。
    """
    cur.execute(f"""
        DELETE FROM mart.building_daily_energy m
        WHERE m.tenant_id = %s
          AND (m.building_id, m.energy_type, m.date) IN ({affected_subquery})
    """, [tenant_id] + params)
    deleted = cur.rowcount
    logger.info("  删旧日聚合: {} 行", deleted)

    cur.execute(f"""
        INSERT INTO mart.building_daily_energy
            (tenant_id, building_id, energy_type, date,
             total_kwh, max_kwh, min_kwh, avg_kwh, hour_count, eui_kwh_per_m2,
             source_batch_id)
        SELECT
            p.tenant_id, p.building_id, p.energy_type, pr.ts::date AS date,
            SUM(pr.value_num) AS total_kwh,
            MAX(pr.value_num) AS max_kwh,
            MIN(pr.value_num) AS min_kwh,
            AVG(pr.value_num) AS avg_kwh,
            COUNT(*) AS hour_count,
            ROUND(CAST(SUM(pr.value_num) / NULLIF(b.sqm, 0) AS numeric), 3) AS eui_kwh_per_m2,
            %s
        FROM fact.point_reading pr
        JOIN core.point p ON p.id = pr.point_id
        JOIN core.building b ON b.id = p.building_id
        WHERE p.tenant_id = %s
          AND p.measure_kind IN ('energy_kwh', 'energy_kwh_thermal')
          AND (p.building_id, p.energy_type, pr.ts::date) IN ({affected_subquery})
        GROUP BY p.tenant_id, p.building_id, p.energy_type, pr.ts::date, b.sqm
        ON CONFLICT (building_id, energy_type, date) DO UPDATE SET
            total_kwh      = EXCLUDED.total_kwh,
            max_kwh        = EXCLUDED.max_kwh,
            min_kwh        = EXCLUDED.min_kwh,
            avg_kwh        = EXCLUDED.avg_kwh,
            hour_count     = EXCLUDED.hour_count,
            eui_kwh_per_m2 = EXCLUDED.eui_kwh_per_m2,
            source_batch_id = EXCLUDED.source_batch_id
    """, [batch_id, tenant_id] + params)
    return cur.rowcount


def rebuild_after_merge(batch_id: str, tenant_id: str) -> int:
    """
    merge 完后调：找出本 batch 写入 fact 的 (building, energy, date) 集合，重算这些日期的日聚合。
    返回重算的日聚合行数。
    """
    affected_subquery = """
        SELECT p.building_id, p.energy_type, pr.ts::date AS date
        FROM fact.point_reading pr
        JOIN core.point p ON p.id = pr.point_id
        WHERE pr.source_batch_id = %s AND pr.tenant_id = %s
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            rows = _rebuild_range(cur, tenant_id, affected_subquery, [batch_id, tenant_id], batch_id)
        conn.commit()
    logger.info("聚合重算完成（merge 后）: batch={} rows={}", batch_id, rows)
    return rows


def rebuild_for_range(
    cur,
    tenant_id: str,
    affected_tuples: list[tuple],
    batch_id: str | None,
) -> int:
    """
    rollback 后调：fact 数据已删，按已知 (building_id, energy_type, date) 集合重算日聚合。
    注意要在调用方的事务里执行（cur 是同一事务的 cursor）。

    affected_tuples 是 (building_id, energy_type, date) 三元组列表，由 rollback
    在删 fact 之前查好。直接展开成 VALUES 子句做 IN 过滤。
    """
    if not affected_tuples:
        return 0

    # psycopg2 的 adapt 把 list of tuple 转成 PG 的复合类型 array
    # 用 unnest 三列展开做 IN 匹配
    affected_subquery = """
        SELECT * FROM unnest(
            %s::uuid[],
            %s::text[],
            %s::date[]
        ) AS t(building_id, energy_type, date)
    """
    building_ids = [t[0] for t in affected_tuples]
    energy_types = [t[1] for t in affected_tuples]
    dates = [t[2] for t in affected_tuples]
    params = [building_ids, energy_types, dates]
    return _rebuild_range(cur, tenant_id, affected_subquery, params, batch_id)
