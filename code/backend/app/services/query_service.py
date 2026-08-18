"""
查询与聚合服务：6 个核心业务查询。

  get_park_overview              园区总览：建筑数、总能耗、EUI 均值、异常数、同比/环比
  list_buildings                 建筑列表：按 total_kwh / eui / anomaly_count 排序
  get_building_timeseries        单楼时序：15min/hour/day/month 粒度切换
  compare_buildings              多楼对比：最多 10 栋楼，每栋时序 + 统计摘要
  get_building_weather           天气关联：返回 building 所属 site 的天气数据
  get_building_energy_composition 能源构成：各 energy_type 占比

数据来源：
  园区总览 / 建筑列表 / 时序 day/month / 能源构成 -> mart.building_daily_energy
  时序 15min/hour -> fact.point_reading（更细粒度，mart 没这么细）
  天气 -> fact.weather_reading（按 site_id 查，building 通过 site_id 关联）

tenant_id 隔离：所有 SQL 都带 WHERE tenant_id = %s，不依赖连接层做隔离。
时间范围：所有查询支持 start/end ISO8601，默认最近 30 天。BDG2 demo 数据是 2017 年，
默认范围会返空，前端调 demo 时要显式传 2017 年的 start/end。

anomaly_count：mart.anomaly_event 表存在但 Step 08 还没做异常检测，目前返 0；
后续 Step 08 跑完会自动反映真实异常数，不用改这里的 SQL。
"""
from datetime import datetime, timedelta, timezone
from typing import Any

from loguru import logger

from app.db.session import get_conn


DEFAULT_RANGE_DAYS = 30
MAX_TIMESERIES_POINTS = 10000
MAX_COMPARE_BUILDINGS = 10

GRANULARITIES = ("15min", "hour", "day", "month")
SORT_COLUMNS = {"total_kwh", "eui_kwh_per_m2", "anomaly_count", "building_code", "display_name"}


class NotFoundError(ValueError):
    """资源不存在或租户越权访问。API 层捕获后返 404，区别于普通参数校验失败的 400。"""


def _default_time_range(tenant_id: str) -> tuple[str, str]:
    """
    默认时间范围：自动查 fact.point_reading 该租户实际数据的 min/max ts。

    demo（BDG2 2017）会自动落到 2017-01-01 ~ 2017-12-31，不用调用方显式传时间范围。
    真实客户接入后会自动落到他们数据范围。

    没数据时回退到最近 30 天（查询会返空，让调用方知道没数据，而不是返一个看起来正常
    但其实没数据的"2017 全年"）。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT MIN(ts), MAX(ts)
                FROM fact.point_reading
                WHERE tenant_id = %s
            """, (tenant_id,))
            min_ts, max_ts = cur.fetchone()
    if min_ts is None or max_ts is None:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=DEFAULT_RANGE_DAYS)
        return (
            start.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
            end.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        )
    return (
        min_ts.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        max_ts.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
    )


def _resolve_range(start: str | None, end: str | None, tenant_id: str) -> tuple[str, str]:
    """start/end 都给就用给的，否则按租户实际数据范围自动定。"""
    if start and end:
        return start, end
    return _default_time_range(tenant_id)


def get_site_data_range(site_id: str, tenant_id: str) -> dict:
    """
    园区数据时间范围：返回该园区下 fact.point_reading 实际数据的 min/max ts + 读数总数。

    前端拿到后用来初始化默认时间范围：
      - earliest_ts/latest_ts 都非空 -> 切到 custom 覆盖整个数据周期
        (demo BDG2 数据是 2017 全年, 默认 '本月' 会查到空, 必须切到 2017)
      - 都空 -> 该园区没数据, 前端保持默认 '本月' 范围, 让用户看到"暂无数据"提示

    跟 _default_time_range 区别:
      _default_time_range 走 tenant 维度 (所有 site 数据混合), 给后端内部 _resolve_range 用
      get_site_data_range 走 site 维度, 给前端按当前 site 自动设定范围用
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM core.site WHERE id = %s AND tenant_id = %s
            """, (site_id, tenant_id))
            if cur.fetchone() is None:
                raise NotFoundError("site 不存在或不属于当前租户")

            # 走 fact.point_reading 而不是 mart.building_daily_energy, 因为:
            #   1. fact 是源数据, mart 是聚合后; fact 的范围一定 >= mart 的范围
            #   2. 新接入数据还没 aggregate 时 mart 是空的, 但 fact 已经有读数了
            cur.execute("""
                SELECT MIN(pr.ts), MAX(pr.ts), COUNT(*)
                FROM fact.point_reading pr
                JOIN core.point p ON p.id = pr.point_id
                JOIN core.building b ON b.id = p.building_id
                WHERE b.site_id = %s AND pr.tenant_id = %s
            """, (site_id, tenant_id))
            min_ts, max_ts, count = cur.fetchone()

    logger.info(
        "site data range: tenant={} site={} points={} range={}~{}",
        tenant_id, site_id, count, min_ts, max_ts,
    )
    # isoformat 保留 tzinfo (timestamptz 列默认 UTC, 输出形如 2017-01-01T00:00:00+00:00)
    # 不用 strftime(...+"+00:00") 硬编码, 防止 DB 时区非 UTC 时偏移错误
    return {
        "site_id": site_id,
        "earliest_ts": min_ts.isoformat() if min_ts else None,
        "latest_ts": max_ts.isoformat() if max_ts else None,
        "point_count": count,
    }


def _shift_days(ts: str, days: int) -> str:
    """把 ISO 时间字符串平移 N 天，返回同格式字符串。"""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    new_dt = dt - timedelta(days=days)
    return new_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _shift_year_back(ts: str) -> str:
    """把 ISO 时间字符串平移 1 年，处理 2/29 闰年边界。

    用 year-1 替换而不是 timedelta(days=365)，否则在非闰年会偏 1 天
    （2017-12-31 - 365 天会落到 2017-01-01 而不是 2016-12-31）。
    """
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    try:
        new_dt = dt.replace(year=dt.year - 1)
    except ValueError:
        # 2/29 在非闰年不存在，回退到 2/28
        new_dt = dt.replace(year=dt.year - 1, day=28)
    return new_dt.strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _previous_period_endpoints(start_ts: str, end_ts: str, gap_days: int) -> tuple[str, str]:
    """算上一同等长度时段的 (start, end)。

    上一时段终点 = 当期起点 - gap_days 天，起点 = 终点 - 时段长度。
    用"上期终点 = 当期起点 - gap_days 天"而不是"上期终点 = 当期终点 - 时段长度"，
    避免上期终点恰好落在当期起点上（gap_days=1 时是环比紧邻上一时段，
    gap_days 大时是同比更早的对照时段）。
    """
    start = datetime.fromisoformat(start_ts.replace("Z", "+00:00"))
    end = datetime.fromisoformat(end_ts.replace("Z", "+00:00"))
    period_length = end - start
    prev_end = start - timedelta(days=gap_days)
    prev_start = prev_end - period_length
    return (
        prev_start.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
        prev_end.strftime("%Y-%m-%dT%H:%M:%S+00:00"),
    )


def _to_utc_date_str(ts: str) -> str:
    """把 ISO 时间字符串转成 UTC 日期字符串 YYYY-MM-DD。

    PG 把 timestamptz cast 成 date 时会用会话时区，导致跨时区时日期偏移。
    比如 +08:00 session 下 '2016-12-31T23:00:00+00:00'::date 会被解释成 '2017-01-01'，
    算环比时把当期数据当成上期。统一在 Python 里先转 UTC 再取日期。
    """
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d")


def _verify_building(cur, building_id: str, tenant_id: str) -> tuple[str, str, str]:
    """校验 building 属于本租户，返回 (building_code, display_name, site_id)。不存在抛 NotFoundError。"""
    cur.execute("""
        SELECT building_code, display_name, site_id
        FROM core.building
        WHERE id = %s AND tenant_id = %s
    """, (building_id, tenant_id))
    row = cur.fetchone()
    if row is None:
        raise NotFoundError("building 不存在或不属于当前租户")
    return row[0], row[1], str(row[2])


def get_park_overview(site_id: str, tenant_id: str, start_ts: str, end_ts: str) -> dict:
    """
    园区总览：6 个核心指标。

    building_count  site 下的建筑数
    total_kwh       时间范围内所有楼所有能耗类型的总用电量（kWh）
    avg_eui         各楼 EUI 的算术平均（kWh/m²），按"每栋楼时间范围总能耗 / sqm"算完后再平均
    anomaly_count   时间范围内所有楼的异常事件数（Step 08 没做时返 0）
    yoy_pct         同比：本期总能耗 vs 去年同期总能耗的百分比变化
    mom_pct         环比：本期总能耗 vs 上一同等长度时段总能耗的百分比变化
    """
    # 同比：去年同期，直接年份 -1（不用 timedelta(days=365) 避免 2/29 闰年偏 1 天）
    yoy_start = _shift_year_back(start_ts)
    yoy_end = _shift_year_back(end_ts)
    # 环比：上一同等长度时段，prev_end = 当期起点 - 1 天，紧邻不重叠
    mom_start, mom_end = _previous_period_endpoints(start_ts, end_ts, gap_days=1)

    # 转成 UTC 日期字符串再传 SQL，避免 timestamptz::date 受会话时区影响
    start_date = _to_utc_date_str(start_ts)
    end_date = _to_utc_date_str(end_ts)
    yoy_start_date = _to_utc_date_str(yoy_start)
    yoy_end_date = _to_utc_date_str(yoy_end)
    mom_start_date = _to_utc_date_str(mom_start)
    mom_end_date = _to_utc_date_str(mom_end)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM core.site WHERE id = %s AND tenant_id = %s
            """, (site_id, tenant_id))
            if cur.fetchone() is None:
                raise NotFoundError("site 不存在或不属于当前租户")

            # 建筑数 + 总能耗 + 各楼 EUI 平均
            # EUI 按"每栋楼时间范围总能耗 / sqm"先按楼算，再取算术平均
            # sqm 为 0 或 NULL 的楼跳过 EUI 但仍计入建筑数
            cur.execute("""
                WITH building_stats AS (
                    SELECT
                        b.id,
                        b.sqm,
                        COALESCE(SUM(d.total_kwh), 0) AS total_kwh
                    FROM core.building b
                    LEFT JOIN mart.building_daily_energy d
                        ON d.building_id = b.id
                        AND d.tenant_id = b.tenant_id
                        AND d.date >= %s AND d.date <= %s
                    WHERE b.site_id = %s AND b.tenant_id = %s
                    GROUP BY b.id, b.sqm
                )
                SELECT
                    COUNT(*) AS building_count,
                    COALESCE(SUM(total_kwh), 0) AS total_kwh,
                    AVG(CASE WHEN sqm > 0 THEN total_kwh / sqm ELSE NULL END) AS avg_eui
                FROM building_stats
            """, (start_date, end_date, site_id, tenant_id))
            building_count, total_kwh, avg_eui = cur.fetchone()

            # 异常事件数（Step 08 没做时返 0，SQL 仍然查 mart.anomaly_event 保留扩展性）
            cur.execute("""
                SELECT COUNT(*)
                FROM mart.anomaly_event ae
                JOIN core.building b ON b.id = ae.building_id
                WHERE b.site_id = %s AND ae.tenant_id = %s
                  AND ae.created_at >= %s AND ae.created_at < %s
            """, (site_id, tenant_id, start_ts, end_ts))
            anomaly_count = cur.fetchone()[0]

            # 同比：去年同期总能耗
            cur.execute("""
                SELECT COALESCE(SUM(d.total_kwh), 0)
                FROM mart.building_daily_energy d
                JOIN core.building b ON b.id = d.building_id
                WHERE b.site_id = %s AND d.tenant_id = %s
                  AND d.date >= %s AND d.date <= %s
            """, (site_id, tenant_id, yoy_start_date, yoy_end_date))
            yoy_total = cur.fetchone()[0]

            # 环比：上一同等长度时段总能耗
            cur.execute("""
                SELECT COALESCE(SUM(d.total_kwh), 0)
                FROM mart.building_daily_energy d
                JOIN core.building b ON b.id = d.building_id
                WHERE b.site_id = %s AND d.tenant_id = %s
                  AND d.date >= %s AND d.date <= %s
            """, (site_id, tenant_id, mom_start_date, mom_end_date))
            mom_total = cur.fetchone()[0]

    # 百分比变化 = (本期 - 上期) / 上期 * 100，上期为 0 时返 None 避免除零
    yoy_pct = round((total_kwh - yoy_total) / yoy_total * 100, 2) if yoy_total > 0 else None
    mom_pct = round((total_kwh - mom_total) / mom_total * 100, 2) if mom_total > 0 else None

    logger.info(
        "park overview: tenant={} site={} buildings={} total_kwh={} avg_eui={}",
        tenant_id, site_id, building_count, total_kwh, avg_eui,
    )
    return {
        "site_id": site_id,
        "time_range": {"start": start_ts, "end": end_ts},
        "building_count": building_count,
        "total_kwh": float(total_kwh or 0),
        "avg_eui": float(avg_eui) if avg_eui is not None else None,
        "anomaly_count": anomaly_count,
        "yoy_pct": yoy_pct,
        "mom_pct": mom_pct,
    }


def list_buildings(
    site_id: str,
    tenant_id: str,
    start_ts: str,
    end_ts: str,
    sort: str = "total_kwh",
    limit: int = 100,
) -> dict:
    """
    建筑列表：每栋楼一行摘要 + 排序。

    sort 可选：total_kwh / eui_kwh_per_m2 / anomaly_count / building_code / display_name
    limit 默认 100，最大 500（防止拉爆响应）。
    """
    if sort not in SORT_COLUMNS:
        raise ValueError(f"不支持的排序字段: {sort}，可选 {SORT_COLUMNS}")
    limit = max(1, min(int(limit), 500))

    start_date = _to_utc_date_str(start_ts)
    end_date = _to_utc_date_str(end_ts)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM core.site WHERE id = %s AND tenant_id = %s
            """, (site_id, tenant_id))
            if cur.fetchone() is None:
                raise NotFoundError("site 不存在或不属于当前租户")

            # LEFT JOIN anomaly 子查询拿到每栋楼的异常数（Step 08 没做时全 0）
            # 主聚合表 LEFT JOIN 的时间范围在 ON 子句里，不放过没能耗数据的楼
            cur.execute(f"""
                SELECT
                    b.id,
                    b.building_code,
                    b.display_name,
                    b.primary_use,
                    b.sub_use,
                    b.sqm,
                    COALESCE(SUM(d.total_kwh), 0) AS total_kwh,
                    CASE
                        WHEN b.sqm IS NOT NULL AND b.sqm > 0
                        THEN ROUND(COALESCE(SUM(d.total_kwh), 0) / b.sqm, 3)
                        ELSE NULL
                    END AS eui_kwh_per_m2,
                    COALESCE(ae.cnt, 0) AS anomaly_count
                FROM core.building b
                LEFT JOIN mart.building_daily_energy d
                    ON d.building_id = b.id
                    AND d.tenant_id = b.tenant_id
                    AND d.date >= %s AND d.date <= %s
                LEFT JOIN (
                    SELECT building_id, COUNT(*) AS cnt
                    FROM mart.anomaly_event
                    WHERE tenant_id = %s
                    GROUP BY building_id
                ) ae ON ae.building_id = b.id
                WHERE b.site_id = %s AND b.tenant_id = %s
                GROUP BY b.id, b.building_code, b.display_name, b.primary_use, b.sub_use, b.sqm, ae.cnt
                ORDER BY {sort} DESC NULLS LAST
                LIMIT %s
            """, (start_date, end_date, tenant_id, site_id, tenant_id, limit))
            rows = cur.fetchall()

    buildings = [
        {
            "building_id": str(r[0]),
            "building_code": r[1],
            "display_name": r[2],
            "primary_use": r[3],
            "sub_use": r[4],
            "sqm": float(r[5]) if r[5] is not None else None,
            "total_kwh": float(r[6]),
            "eui_kwh_per_m2": float(r[7]) if r[7] is not None else None,
            "anomaly_count": r[8],
        }
        for r in rows
    ]
    logger.info("list buildings: tenant={} site={} sort={} limit={} returned={}",
                tenant_id, site_id, sort, limit, len(buildings))
    return {
        "site_id": site_id,
        "time_range": {"start": start_ts, "end": end_ts},
        "sort": sort,
        "buildings": buildings,
    }


def get_building_timeseries(
    building_id: str,
    tenant_id: str,
    metric: str,
    granularity: str,
    start_ts: str,
    end_ts: str,
) -> dict:
    """
    单楼时序：按 granularity 分桶返回 [{ts, value, unit}]。

    metric 目前只支持 interval_energy_kwh（建筑总能耗 kWh）。
    granularity=15min/hour 走 fact.point_reading（细粒度原始读数）
    granularity=day/month 走 mart.building_daily_energy（预聚合快）

    桶内对 measure_kind IN ('energy_kwh','energy_kwh_thermal') 的所有 point 读数求和，
    所以是"整栋楼总能耗"而不是单个 point。

    返回点数超过 MAX_TIMESERIES_POINTS 抛错，前端要换更粗粒度或缩时间范围。
    """
    if granularity not in GRANULARITIES:
        raise ValueError(f"不支持的粒度: {granularity}，可选 {GRANULARITIES}")
    if metric != "interval_energy_kwh":
        raise ValueError(f"暂只支持 metric=interval_energy_kwh，收到 {metric}")

    start_date = _to_utc_date_str(start_ts)
    end_date = _to_utc_date_str(end_ts)

    with get_conn() as conn:
        with conn.cursor() as cur:
            building_code, display_name, _ = _verify_building(cur, building_id, tenant_id)

            if granularity == "15min":
                # 按 15 分钟分桶：先把 ts 截到小时，再加 15min * floor(minute/15)
                # BDG2 数据本身是小时粒度，分桶后所有点都落在 0-14min 那个桶里
                cur.execute("""
                    SELECT
                        date_trunc('hour', pr.ts)
                            + INTERVAL '15 minutes' * FLOOR(EXTRACT(MINUTE FROM pr.ts) / 15) AS bucket,
                        SUM(pr.value_num) AS value
                    FROM fact.point_reading pr
                    JOIN core.point p ON p.id = pr.point_id
                    WHERE p.building_id = %s
                      AND p.tenant_id = %s
                      AND p.measure_kind IN ('energy_kwh', 'energy_kwh_thermal')
                      AND pr.ts >= %s AND pr.ts < %s
                    GROUP BY bucket
                    ORDER BY bucket
                """, (building_id, tenant_id, start_ts, end_ts))
            elif granularity == "hour":
                cur.execute("""
                    SELECT
                        date_trunc('hour', pr.ts) AS bucket,
                        SUM(pr.value_num) AS value
                    FROM fact.point_reading pr
                    JOIN core.point p ON p.id = pr.point_id
                    WHERE p.building_id = %s
                      AND p.tenant_id = %s
                      AND p.measure_kind IN ('energy_kwh', 'energy_kwh_thermal')
                      AND pr.ts >= %s AND pr.ts < %s
                    GROUP BY bucket
                    ORDER BY bucket
                """, (building_id, tenant_id, start_ts, end_ts))
            elif granularity == "day":
                cur.execute("""
                    SELECT
                        d.date AS bucket,
                        SUM(d.total_kwh) AS value
                    FROM mart.building_daily_energy d
                    WHERE d.building_id = %s
                      AND d.tenant_id = %s
                      AND d.date >= %s AND d.date <= %s
                    GROUP BY d.date
                    ORDER BY d.date
                """, (building_id, tenant_id, start_date, end_date))
            else:  # month
                cur.execute("""
                    SELECT
                        DATE_TRUNC('month', d.date) AS bucket,
                        SUM(d.total_kwh) AS value
                    FROM mart.building_daily_energy d
                    WHERE d.building_id = %s
                      AND d.tenant_id = %s
                      AND d.date >= %s AND d.date <= %s
                    GROUP BY bucket
                    ORDER BY bucket
                """, (building_id, tenant_id, start_date, end_date))

            rows = cur.fetchall()

    if len(rows) > MAX_TIMESERIES_POINTS:
        raise ValueError(
            f"时序返回 {len(rows)} 个点超过上限 {MAX_TIMESERIES_POINTS}，请换更粗粒度或缩时间范围"
        )

    points = [
        {"ts": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
         "value": float(r[1]) if r[1] is not None else None,
         "unit": "kWh"}
        for r in rows
    ]

    logger.info(
        "building timeseries: tenant={} building={} granularity={} points={}",
        tenant_id, building_code, granularity, len(points),
    )
    return {
        "building_id": building_id,
        "building_code": building_code,
        "display_name": display_name,
        "metric": metric,
        "granularity": granularity,
        "time_range": {"start": start_ts, "end": end_ts},
        "points": points,
    }


def compare_buildings(
    building_ids: list[str],
    tenant_id: str,
    metric: str,
    start_ts: str,
    end_ts: str,
) -> dict:
    """
    多楼对比：每栋楼的日粒度时序 + 总/均/最大/最小统计。

    对比粒度固定 day（多楼时序走 fact 太慢，day 走 mart 预聚合）。
    building_ids 最多 10 栋，超出抛错。
    """
    if len(building_ids) == 0:
        raise ValueError("至少传一个 building_id")
    if len(building_ids) > MAX_COMPARE_BUILDINGS:
        raise ValueError(f"最多对比 {MAX_COMPARE_BUILDINGS} 栋楼，收到 {len(building_ids)}")
    if metric != "interval_energy_kwh":
        raise ValueError(f"暂只支持 metric=interval_energy_kwh，收到 {metric}")

    start_date = _to_utc_date_str(start_ts)
    end_date = _to_utc_date_str(end_ts)

    results: list[dict] = []
    with get_conn() as conn:
        with conn.cursor() as cur:
            for bid in building_ids:
                building_code, display_name, _ = _verify_building(cur, bid, tenant_id)

                cur.execute("""
                    SELECT
                        d.date AS bucket,
                        SUM(d.total_kwh) AS value
                    FROM mart.building_daily_energy d
                    WHERE d.building_id = %s
                      AND d.tenant_id = %s
                      AND d.date >= %s AND d.date <= %s
                    GROUP BY d.date
                    ORDER BY d.date
                """, (bid, tenant_id, start_date, end_date))
                rows = cur.fetchall()

                values = [float(r[1]) for r in rows if r[1] is not None]
                points = [
                    {"ts": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
                     "value": float(r[1]) if r[1] is not None else None,
                     "unit": "kWh"}
                    for r in rows
                ]
                results.append({
                    "building_id": bid,
                    "building_code": building_code,
                    "display_name": display_name,
                    "points": points,
                    "stats": {
                        "total_kwh": round(sum(values), 3) if values else 0.0,
                        "avg_kwh": round(sum(values) / len(values), 3) if values else 0.0,
                        "max_kwh": round(max(values), 3) if values else 0.0,
                        "min_kwh": round(min(values), 3) if values else 0.0,
                    },
                })

    logger.info(
        "compare buildings: tenant={} buildings={} range={}~{}",
        tenant_id, [r["building_code"] for r in results], start_ts, end_ts,
    )
    return {
        "metric": metric,
        "granularity": "day",
        "time_range": {"start": start_ts, "end": end_ts},
        "buildings": results,
    }


def get_building_weather(building_id: str, tenant_id: str, start_ts: str, end_ts: str) -> dict:
    """天气关联：返回 building 所属 site 的天气读数，便于和能耗对比分析。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            building_code, display_name, site_id = _verify_building(cur, building_id, tenant_id)

            cur.execute("""
                SELECT
                    wr.ts,
                    wr.air_temp_c,
                    wr.cloud_cover_pct,
                    wr.dew_temp_c,
                    wr.precip_mm,
                    wr.precip_6hr_mm,
                    wr.sea_level_pressure_hpa,
                    wr.wind_direction_deg,
                    wr.wind_speed_mps
                FROM fact.weather_reading wr
                WHERE wr.site_id = %s
                  AND wr.tenant_id = %s
                  AND wr.ts >= %s AND wr.ts < %s
                ORDER BY wr.ts
            """, (site_id, tenant_id, start_ts, end_ts))
            rows = cur.fetchall()

    points = [
        {
            "ts": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
            "air_temp_c": r[1],
            "cloud_cover_pct": r[2],
            "dew_temp_c": r[3],
            "precip_mm": r[4],
            "precip_6hr_mm": r[5],
            "sea_level_pressure_hpa": r[6],
            "wind_direction_deg": r[7],
            "wind_speed_mps": r[8],
        }
        for r in rows
    ]

    logger.info(
        "building weather: tenant={} building={} site={} points={}",
        tenant_id, building_code, site_id, len(points),
    )
    return {
        "building_id": building_id,
        "building_code": building_code,
        "display_name": display_name,
        "site_id": site_id,
        "time_range": {"start": start_ts, "end": end_ts},
        "points": points,
    }


def get_building_energy_composition(building_id: str, tenant_id: str, start_ts: str, end_ts: str) -> dict:
    """
    能源构成：返回各 energy_type 的能耗占比。

    从 mart.building_daily_energy 按时间范围 + building 聚合到 energy_type 维度，
    再算每种能源类型占该楼总能耗的百分比。
    """
    start_date = _to_utc_date_str(start_ts)
    end_date = _to_utc_date_str(end_ts)

    with get_conn() as conn:
        with conn.cursor() as cur:
            building_code, display_name, _ = _verify_building(cur, building_id, tenant_id)

            cur.execute("""
                SELECT
                    d.energy_type,
                    SUM(d.total_kwh) AS kwh
                FROM mart.building_daily_energy d
                WHERE d.building_id = %s
                  AND d.tenant_id = %s
                  AND d.date >= %s AND d.date <= %s
                GROUP BY d.energy_type
                ORDER BY kwh DESC
            """, (building_id, tenant_id, start_date, end_date))
            rows = cur.fetchall()

    total_kwh = sum(float(r[1]) for r in rows if r[1] is not None)
    composition = [
        {
            "type": r[0],
            "kwh": float(r[1]) if r[1] is not None else 0.0,
            "pct": round(float(r[1]) / total_kwh * 100, 2) if total_kwh > 0 else 0.0,
        }
        for r in rows
    ]

    logger.info(
        "building energy composition: tenant={} building={} types={} total_kwh={}",
        tenant_id, building_code, len(composition), total_kwh,
    )
    return {
        "building_id": building_id,
        "building_code": building_code,
        "display_name": display_name,
        "time_range": {"start": start_ts, "end": end_ts},
        "total_kwh": round(total_kwh, 3),
        "composition": composition,
    }


def get_park_energy_composition(site_id: str, tenant_id: str, start_ts: str, end_ts: str) -> dict:
    """园区级能源构成: 各 energy_type 的能耗占比。

    跟 get_building_energy_composition 对偶, 区别是聚合维度从 building_id 换成 site_id
    (通过 JOIN core.building 拿 site_id)。节能优化场景 LLM 调这个工具拿真实分项数据,
    不要凭记忆编"照明占 25% 暖通占 40%" 这种臆想占比。
    """
    start_date = _to_utc_date_str(start_ts)
    end_date = _to_utc_date_str(end_ts)

    with get_conn() as conn:
        with conn.cursor() as cur:
            # 先校验 site 归属
            cur.execute(
                "SELECT 1 FROM core.site WHERE id = %s AND tenant_id = %s",
                (site_id, tenant_id),
            )
            if cur.fetchone() is None:
                raise NotFoundError("site 不存在或不属于当前租户")

            cur.execute("""
                SELECT
                    d.energy_type,
                    SUM(d.total_kwh) AS kwh
                FROM mart.building_daily_energy d
                JOIN core.building b ON b.id = d.building_id
                WHERE b.site_id = %s
                  AND d.tenant_id = %s
                  AND d.date >= %s AND d.date <= %s
                GROUP BY d.energy_type
                ORDER BY kwh DESC
            """, (site_id, tenant_id, start_date, end_date))
            rows = cur.fetchall()

    total_kwh = sum(float(r[1]) for r in rows if r[1] is not None)
    composition = [
        {
            "type": r[0],
            "kwh": float(r[1]) if r[1] is not None else 0.0,
            "pct": round(float(r[1]) / total_kwh * 100, 2) if total_kwh > 0 else 0.0,
        }
        for r in rows
    ]

    logger.info(
        "park energy composition: tenant={} site={} types={} total_kwh={}",
        tenant_id[:8], site_id[:8], len(composition), total_kwh,
    )
    return {
        "site_id": site_id,
        "time_range": {"start": start_ts, "end": end_ts},
        "total_kwh": round(total_kwh, 3),
        "composition": composition,
    }
