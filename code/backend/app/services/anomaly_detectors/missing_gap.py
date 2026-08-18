"""
MISSING_GAP: 缺测时段检测。

算法: 相邻读数间隔 > 3 小时算缺测 (BDG2 小时粒度, 1 小时间隔正常, > 3 小时
      意味着至少缺 2 个连续点)。

分级:
  - 1 小时内不算 (gap_hours < 1.5): 可能是数据上传延迟, 不报异常
  - gap 1.5-6 小时: LOW
  - gap 6-12 小时: MEDIUM
  - gap > 12 小时: HIGH

BDG2 demo 数据中 Bobcat_assembly_Franklin 楼 electricity 在 2017-01 有严重缺测
(Step 06 验证完整度只有 69%), 本检测器应能检出该缺测段。
"""
from psycopg2.extensions import cursor as Cursor

from . import DetectorContext


# 阈值 (gap_hours)
GAP_THRESHOLD_HOURS = 3.0  # 超过 3 小时算异常
GAP_LOW_THRESHOLD = 3.0    # 3-6 小时 LOW
GAP_MEDIUM_THRESHOLD = 6.0  # 6-12 小时 MEDIUM
GAP_HIGH_THRESHOLD = 12.0   # > 12 小时 HIGH


def detect(cur: Cursor, ctx: DetectorContext) -> list[dict]:
    """检测 MISSING_GAP 异常。"""
    if ctx.point_id is None:
        return []

    sql = """
        WITH gaps AS (
            SELECT
                ts,
                LAG(ts) OVER (ORDER BY ts) AS prev_ts,
                ts - LAG(ts) OVER (ORDER BY ts) AS gap
            FROM fact.point_reading
            WHERE point_id = %s
              AND ts >= %s::timestamptz AND ts < %s::timestamptz
        )
        SELECT prev_ts, ts, EXTRACT(EPOCH FROM (ts - prev_ts))/3600 AS gap_hours
        FROM gaps
        WHERE gap IS NOT NULL AND gap > INTERVAL '3 hours'
        ORDER BY prev_ts
    """
    cur.execute(sql, (ctx.point_id, ctx.start_ts, ctx.end_ts))
    rows = cur.fetchall()

    results: list[dict] = []
    for prev_ts, ts, gap_hours in rows:
        gap_h = float(gap_hours)

        if gap_h >= GAP_HIGH_THRESHOLD:
            severity = "HIGH"
        elif gap_h >= GAP_MEDIUM_THRESHOLD:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        # 缺测前后的边界读数作为 sample (缺测段内无读数)
        cur.execute("""
            SELECT ts, value_num
            FROM fact.point_reading
            WHERE point_id = %s AND ts IN (%s, %s)
            ORDER BY ts
        """, (ctx.point_id, prev_ts, ts))
        sample_rows = cur.fetchall()
        sample_points = [
            {"ts": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
             "value": float(r[1]) if r[1] is not None else None}
            for r in sample_rows
        ]

        results.append({
            "event_type": "MISSING_GAP",
            "severity": severity,
            "metric_code": f"reading_gap_hours",
            "start_ts": prev_ts,
            "end_ts": ts,
            "observed_value": gap_h,
            "baseline_value": 1.0,  # 期望 1 小时间隔
            "evidence": {
                "baseline_method": "lag_gap_threshold",
                "baseline_window": "N/A (期望 1 小时间隔)",
                "observed_window": f"{prev_ts.isoformat() if hasattr(prev_ts, 'isoformat') else str(prev_ts)} ~ {ts.isoformat() if hasattr(ts, 'isoformat') else str(ts)}",
                "stats": {
                    "gap_hours": round(gap_h, 2),
                    "missing_points_estimate": int(gap_h - 1),  # 估计缺失的点数
                },
                "sample_points": sample_points,
                "extra": {
                    "expected_interval_hours": 1,
                    "energy_type": ctx.energy_type,
                },
            },
        })

    return results
