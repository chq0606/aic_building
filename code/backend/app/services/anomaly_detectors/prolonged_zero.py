"""
PROLONGED_ZERO: 持续为零检测。

算法: 连续 ≥ 6 小时值为 0 (BDG2 小时粒度, = 6 个连续读数都是 0)。

边界处理 (立于实际,用户强调"太阳能就是 0 嘛"):
  solar 直接跳过, 不参与 PROLONGED_ZERO 检测。solar 是发电侧不是负荷侧,
  0 值的物理含义很多 (夜间/阴天/积雪/传感器离线/季节性停测), 简单按
  "时长阈值" 判异常会误报一片。真有面板故障会反映在 SPIKE/DRIFT 或
  MISSING_GAP 上, PROLONGED_ZERO 不重复管。

  其他 energy_type 用 NORMAL_ZERO_RULES 白名单:
    - chilledwater: 供暖季 (11-3 月) 为 0 正常 (无冷负荷)
    - hotwater: 制冷季 (6-9 月) 为 0 正常 (无热负荷)
    - irrigation: 冬季 (11-3 月) 为 0 正常 (雨季需天气数据,一期简化为冬季)
    - steam: 按 primary_use 判定,Education/Public/Tech 类夏季为 0 正常 (无蒸汽需求)

  electricity / gas / water 没在白名单里,这些 energy_type 任何时段为 0 都是异常
  (没电没水没法运行,燃气锅炉停了说明有问题)。

severity:
  6-12 小时 LOW
  12-24 小时 MEDIUM
  > 24 小时 HIGH
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from psycopg2.extensions import cursor as Cursor

from . import DetectorContext


# 白名单规则。命中规则的连续 0 段不算异常。
# type 含义:
#   "seasonal":              时段所在月份都在 zero_months 里才算正常
#   "primary_use_seasonal":  seasonal 的增强版,还要 primary_use 在 primary_use_filter 里才生效
#
# hour/month 都按 site 本地时区判定 (NOT session timezone).
# PG 取出来的 timestamptz tzinfo 是 session timezone (服务器配 UTC+8 中国时区),
# 但夜里的"18:00-7:00"是相对 site 本地时区 (Bobcat US/Mountain) 说的,
# 不转一下 hour 就对不上 night_hours。
#
# solar 不在这里: 整个 energy_type 跳过 PROLONGED_ZERO, 见 detect() 早返。
NORMAL_ZERO_RULES: dict[str, dict] = {
    "chilledwater": {
        "type": "seasonal",
        # 供暖季+过渡月 (10-4 月)。BDG2 数据显示 10 月底和 4 月初 chilledwater
        # 经常 0 (制冷季还没开始/已经结束), 自然过渡, 算正常不算异常。
        "zero_months": {10, 11, 12, 1, 2, 3, 4},
    },
    "hotwater": {
        "type": "seasonal",
        # 制冷季+过渡月 (5-10 月)。5 月初和 10 月底 hotwater 经常 0 (供暖季
        # 还没开始/已经结束), 自然过渡, 算正常不算异常。
        "zero_months": {5, 6, 7, 8, 9, 10},
    },
    "irrigation": {
        "type": "seasonal",
        "zero_months": {11, 12, 1, 2, 3},
    },
    "steam": {
        "type": "primary_use_seasonal",
        "primary_use_filter": {
            "Education",
            "Public services",
            "Entertainment/public assembly",
            "Technology/science",
        },
        "zero_months": {6, 7, 8, 9},
    },
}


# 单段最长正常 0 时长 (天)。超过此值即使 start.month 在 zero_months 也算异常,
# 避免漏报"系统从冬季一直关到夏季"这种真异常 (正常 zero_months 最长 6 个月,
# 180 天正好覆盖,超过就是跨季了)。
NORMAL_ZERO_MAX_DURATION_DAYS = 180


def _to_local_dt(dt, site_timezone: str | None) -> datetime:
    """把 timestamptz (session tz, 可能是 UTC+8) 转成 site 本地时区的 naive datetime,
    方便后续取 .hour / .month 跟 night_hours / zero_months 对齐。"""
    if not isinstance(dt, datetime):
        dt = datetime.fromisoformat(str(dt).replace("Z", "+00:00"))
    if site_timezone:
        try:
            return dt.astimezone(ZoneInfo(site_timezone))
        except Exception:
            return dt.astimezone(timezone.utc)
    return dt


def _is_normal_zero(
    rule: dict,
    start_ts,
    end_ts,
    primary_use: str | None,
    site_timezone: str | None = None,
) -> bool:
    """检查连续 0 段是否命中白名单规则。hour/month 按 site 本地时区判定。

    seasonal 判定: start.month 在 zero_months 里 AND 时长 ≤ 180 天。
    用 start 而非 end, 因为"系统关停"是从 start 那刻开始的 (e.g. 8 月关到 11 月初,
    系统是因为 8 月不需要 hotwater 才关的, 11 月回来是 heating 季到了);
    180 天兜底防漏报跨季节长 0 段。
    """
    if rule is None:
        return False

    rule_type = rule["type"]
    start_dt = _to_local_dt(start_ts, site_timezone)
    end_dt = _to_local_dt(end_ts, site_timezone)

    duration_days = (end_dt - start_dt).total_seconds() / 86400 + 1  # +1 含两端
    if duration_days > NORMAL_ZERO_MAX_DURATION_DAYS:
        return False

    if rule_type == "seasonal":
        zero_months = rule["zero_months"]
        return start_dt.month in zero_months

    if rule_type == "primary_use_seasonal":
        if primary_use not in rule["primary_use_filter"]:
            return False
        zero_months = rule["zero_months"]
        return start_dt.month in zero_months

    return False


def detect(cur: Cursor, ctx: DetectorContext) -> list[dict]:
    """检测 PROLONGED_ZERO 异常。"""
    if ctx.energy_type is None or ctx.point_id is None:
        return []

    # solar 直接跳过 (见模块 docstring)
    if ctx.energy_type == "solar":
        return []

    rule = NORMAL_ZERO_RULES.get(ctx.energy_type)

    # 用 LAG + 累计分组找连续 0 段
    sql = """
        WITH zero_readings AS (
            SELECT
                ts,
                value_num,
                ts - LAG(ts) OVER (ORDER BY ts) AS gap_to_prev
            FROM fact.point_reading
            WHERE point_id = %s
              AND ts >= %s::timestamptz AND ts < %s::timestamptz
              AND value_num = 0
        ),
        groups AS (
            SELECT
                ts,
                value_num,
                gap_to_prev,
                SUM(CASE
                    WHEN gap_to_prev > INTERVAL '1 hour' OR gap_to_prev IS NULL THEN 1
                    ELSE 0
                END) OVER (ORDER BY ts) AS group_id
            FROM zero_readings
        )
        SELECT
            MIN(ts) AS start_ts,
            MAX(ts) AS end_ts,
            COUNT(*) AS zero_count,
            EXTRACT(EPOCH FROM (MAX(ts) - MIN(ts)))/3600 + 1 AS zero_hours
        FROM groups
        GROUP BY group_id
        HAVING COUNT(*) >= 6
        ORDER BY start_ts
    """
    cur.execute(sql, (ctx.point_id, ctx.start_ts, ctx.end_ts))
    rows = cur.fetchall()

    results: list[dict] = []
    for start_ts, end_ts, zero_count, zero_hours in rows:
        # 命中白名单的段跳过
        if _is_normal_zero(rule, start_ts, end_ts, ctx.primary_use, ctx.site_timezone):
            continue

        hours = float(zero_hours)
        if hours >= 24:
            severity = "HIGH"
        elif hours >= 12:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        # 拿这个时段的前 20 条读数做 sample
        cur.execute("""
            SELECT ts, value_num
            FROM fact.point_reading
            WHERE point_id = %s
              AND ts >= %s AND ts <= %s
            ORDER BY ts
            LIMIT 20
        """, (ctx.point_id, start_ts, end_ts))
        sample_rows = cur.fetchall()
        sample_points = [
            {"ts": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
             "value": float(r[1]) if r[1] is not None else None}
            for r in sample_rows
        ]

        results.append({
            "event_type": "PROLONGED_ZERO",
            "severity": severity,
            "metric_code": f"{ctx.energy_type}_interval_kwh",
            "start_ts": start_ts,
            "end_ts": end_ts,
            "observed_value": 0.0,
            "baseline_value": None,
            "evidence": {
                "baseline_method": "continuous_zero_threshold",
                "baseline_window": "N/A (无基准值)",
                "observed_window": f"{start_ts.isoformat() if hasattr(start_ts, 'isoformat') else str(start_ts)} ~ {end_ts.isoformat() if hasattr(end_ts, 'isoformat') else str(end_ts)}",
                "stats": {
                    "zero_count": int(zero_count),
                    "zero_hours": round(hours, 2),
                },
                "sample_points": sample_points,
                "extra": {
                    "energy_type": ctx.energy_type,
                    "normal_zero_rule_applied": rule is not None,
                    "rule_type": rule["type"] if rule else None,
                },
            },
        })

    return results
