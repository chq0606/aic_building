"""
SCHEDULE_VIOLATION: 作息违反检测。

算法: 非工作时段(夜间 22-6 点或周末)每小时读数 > 工作时段中位数的 50% 算违规。
      按违规日聚合,每天 1 个事件。

不适用范围(决策点 #1 + 边界处理):
  - solar: 不适用(本就靠天,无作息)
  - irrigation: 不适用(灌溉按需,无固定作息)

工作时段定义:
  - 小时: 8-17 (含 8, 含 17, 共 10 小时)
  - 星期: 周一-周五(DOW 1-5, 周日=0, 周六=6)
非工作时段: 上述之外(夜间 18-7 点 + 全天周末)

时区处理:
  ts 存的是 UTC, 工作时段判定要按本地时区。Bobcat 的 site_timezone='US/Mountain',
  UTC 8-17 点对应本地 1-10 点(冬令时 UTC-7), 那是夜里, 算法就错了。
  所以 SQL 里用 AT TIME ZONE 把 ts 转成本地 timestamp 后再取 HOUR/DOW。

baseline:
  - 近 30 天工作时段每小时读数中位数 (PERCENTILE_CONT 0.5)
  - 用中位数不用均值, 抗离群点(避免尖峰拉高 baseline 漏报)

severity:
  - 违规时段平均读数 > baseline * 0.5: LOW
  - > baseline * 1.0: MEDIUM (非工作时段和工作时段一样高, 严重)
  - > baseline * 1.5: HIGH (非工作时段比工作时段还高 50%, 极严重)
"""
from psycopg2.extensions import cursor as Cursor

from . import DetectorContext


# 不适用的 energy_type (本就无固定作息, 不算 SCHEDULE_VIOLATION)
APPLICABLE_ENERGY_TYPES = {"electricity", "hotwater", "chilledwater", "steam", "gas", "water"}

# 工作时段小时范围 (本地时区) 8-17 (含两端, 10 小时)
WORKING_HOUR_START = 8
WORKING_HOUR_END = 17
# 工作日 DOW: 周一=1, ..., 周五=5
WORKING_DOW_LIST = (1, 2, 3, 4, 5)

# baseline 倍数阈值
SCHEDULE_LOW_THRESHOLD = 0.5      # > baseline * 0.5 算 LOW
SCHEDULE_MEDIUM_THRESHOLD = 1.0   # > baseline * 1.0 算 MEDIUM
SCHEDULE_HIGH_THRESHOLD = 1.5     # > baseline * 1.5 算 HIGH


def detect(cur: Cursor, ctx: DetectorContext) -> list[dict]:
    """检测 SCHEDULE_VIOLATION 异常。"""
    if ctx.energy_type is None or ctx.point_id is None:
        return []
    if ctx.energy_type not in APPLICABLE_ENERGY_TYPES:
        return []

    # 时区: ctx.site_timezone 可能为 None, 用 UTC 兜底
    tz = ctx.site_timezone or "UTC"
    working_dows_tuple = tuple(WORKING_DOW_LIST)  # psycopg2 的 IN %s 需要 tuple 不是字符串

    # baseline: 工作时段每小时读数中位数
    sql_baseline = """
        SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY value_num) AS median
        FROM fact.point_reading
        WHERE point_id = %s
          AND ts >= %s::timestamptz AND ts < %s::timestamptz
          AND value_num >= 0
          AND EXTRACT(HOUR FROM ts AT TIME ZONE %s) BETWEEN %s AND %s
          AND EXTRACT(DOW FROM ts AT TIME ZONE %s) IN %s
    """
    cur.execute(sql_baseline, (
        ctx.point_id, ctx.start_ts, ctx.end_ts,
        tz, WORKING_HOUR_START, WORKING_HOUR_END,
        tz, working_dows_tuple,
    ))
    baseline_row = cur.fetchone()
    if baseline_row is None or baseline_row[0] is None:
        return []
    baseline_median = float(baseline_row[0])
    if baseline_median <= 0:
        return []

    # 违规: 非工作时段读数 > baseline * 0.5, 按违规日聚合
    sql_violations = """
        SELECT
            DATE_TRUNC('day', ts AT TIME ZONE %s) AS violation_day,
            MIN(ts) AS start_ts,
            MAX(ts) AS end_ts,
            COUNT(*) AS violation_count,
            AVG(value_num) AS avg_value,
            MAX(value_num) AS max_value
        FROM fact.point_reading
        WHERE point_id = %s
          AND ts >= %s::timestamptz AND ts < %s::timestamptz
          AND value_num >= 0
          AND value_num > %s
          AND (
              EXTRACT(HOUR FROM ts AT TIME ZONE %s) NOT BETWEEN %s AND %s
              OR EXTRACT(DOW FROM ts AT TIME ZONE %s) NOT IN %s
          )
        GROUP BY violation_day
        ORDER BY violation_day
    """
    threshold = baseline_median * SCHEDULE_LOW_THRESHOLD
    cur.execute(sql_violations, (
        tz,
        ctx.point_id, ctx.start_ts, ctx.end_ts,
        threshold,
        tz, WORKING_HOUR_START, WORKING_HOUR_END,
        tz, working_dows_tuple,
    ))
    rows = cur.fetchall()

    results: list[dict] = []
    for day, start_ts, end_ts, violation_count, avg_value, max_value in rows:
        avg_v = float(avg_value) if avg_value is not None else 0.0
        # ratio: 非工作时段均值 / 工作时段中位数
        ratio = avg_v / baseline_median if baseline_median > 0 else 0

        if ratio >= SCHEDULE_HIGH_THRESHOLD:
            severity = "HIGH"
        elif ratio >= SCHEDULE_MEDIUM_THRESHOLD:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        # 拿该日违规时段的前 20 条读数做 sample
        cur.execute("""
            SELECT ts, value_num
            FROM fact.point_reading
            WHERE point_id = %s
              AND ts >= %s AND ts <= %s
              AND value_num > %s
            ORDER BY ts
            LIMIT 20
        """, (ctx.point_id, start_ts, end_ts, threshold))
        sample_rows = cur.fetchall()
        sample_points = [
            {"ts": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
             "value": float(r[1]) if r[1] is not None else None}
            for r in sample_rows
        ]

        results.append({
            "event_type": "SCHEDULE_VIOLATION",
            "severity": severity,
            "metric_code": f"{ctx.energy_type}_interval_kwh",
            "start_ts": start_ts,
            "end_ts": end_ts,
            "observed_value": avg_v,
            "baseline_value": baseline_median,
            "evidence": {
                "baseline_method": "schedule_working_hours_median",
                "baseline_window": f"工作时段({WORKING_HOUR_START}-{WORKING_HOUR_END}, 工作日)中位数",
                "observed_window": f"{start_ts.isoformat() if hasattr(start_ts, 'isoformat') else str(start_ts)} ~ {end_ts.isoformat() if hasattr(end_ts, 'isoformat') else str(end_ts)}",
                "stats": {
                    "baseline_median": round(baseline_median, 4),
                    "violation_avg": round(avg_v, 4),
                    "violation_max": round(float(max_value), 4) if max_value is not None else None,
                    "violation_count": int(violation_count),
                    "ratio": round(float(ratio), 2),
                },
                "sample_points": sample_points,
                "extra": {
                    "energy_type": ctx.energy_type,
                    "timezone": tz,
                    "working_hours": f"{WORKING_HOUR_START}-{WORKING_HOUR_END} local",
                    "violation_threshold": threshold,
                },
            },
        })

    return results
