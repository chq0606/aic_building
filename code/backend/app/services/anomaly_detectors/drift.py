"""
DRIFT: 缓慢漂移检测。

算法: 7 天滑动均值(observed)偏离 30 天滑动均值(baseline)超过阈值。
  - baseline = 30 天滑动均值, 本身已经包含季节性变化 (随季节缓慢变化)
  - observed = 7 天滑动均值, 反映近期运行状态
  - 偏离 = (observed - baseline) / baseline
  - 这种"短期 vs 长期"对比避免了"整个时段均值当 baseline"的季节性误报
    (旧算法用整个时段均值, BDG2 demo 全年数据 chilledwater 1-3 月为 0 + 7-9 月高峰,
     rolling_mean 必然大幅偏离 baseline, 全年都报 DRIFT, 一年 365 个误报)

  - 季风切换月份(4-5 月供暖转制冷、10-11 月制冷转供暖)阈值提到 40%
    季风切换会让 7 天均值快速变化但 30 天均值跟不上, 阈值要放宽

baseline 选择:
  理想方案是同周同期对比(去年同期 ± 14 天的均值), 但 BDG2 demo 只有 2017 一年,
  没法做同周对比。30 天 vs 7 天是降级方案。等真实客户接入多年数据后
  这里可以改成同周对比, 改动只在本文件内, 接口不变。

severity:
  偏离超 20%(季风 40%) LOW
  偏离超 40%(季风 60%) MEDIUM
  偏离超 60%(季风 80%) HIGH

事件 start_ts / end_ts:
  用 observed 的 7 天滑窗范围 [day - 6d, day]
  转成 UTC datetime (避免 PG 把 DATE 转 timestamptz 时受会话时区影响,
  导致 DELETE 范围匹配不上)
"""
from datetime import datetime, timezone, timedelta
from psycopg2.extensions import cursor as Cursor

from . import DetectorContext


# 季风切换月份(北半球): 4-5 月供暖转制冷, 10-11 月制冷转供暖
SEASONAL_TRANSITION_MONTHS = {4, 5, 10, 11}

# (普通阈值, 季风阈值) - 偏离百分比超过此值算异常
DRIFT_THRESHOLD_NORMAL = 0.20    # 普通月份: 偏离 20%
DRIFT_THRESHOLD_TRANSITION = 0.40  # 季风月份: 偏离 40%

# severity 分档
SEVERITY_LOW_PCT = 0.20          # 偏离 20% LOW (季风 40%)
SEVERITY_MEDIUM_PCT = 0.40        # 偏离 40% MEDIUM (季风 60%)
SEVERITY_HIGH_PCT = 0.60          # 偏离 60% HIGH (季风 80%)

OBSERVED_WINDOW_DAYS = 7   # observed 滑窗
BASELINE_WINDOW_DAYS = 30  # baseline 滑窗


def detect(cur: Cursor, ctx: DetectorContext) -> list[dict]:
    """检测 DRIFT 异常。"""
    if ctx.energy_type is None or ctx.point_id is None:
        return []

    # 取 35 天前置数据 (30 天 baseline + 7 天 observed 重叠), 算 30 天和 7 天滑动均值
    # 用 ROWS BETWEEN N PRECEDING AND CURRENT ROW 算 N+1 天的滑动均值
    sql = """
        WITH daily AS (
            SELECT
                DATE_TRUNC('day', ts)::date AS day,
                SUM(value_num) AS daily_kwh
            FROM fact.point_reading
            WHERE point_id = %s
              AND ts >= %s::timestamptz - INTERVAL '35 days'
              AND ts < %s::timestamptz
              AND value_num >= 0
            GROUP BY day
        ),
        windows AS (
            SELECT
                day,
                daily_kwh,
                -- 30 天滑动均值 (含当前天往回 29 天)
                AVG(daily_kwh) OVER (
                    ORDER BY day
                    ROWS BETWEEN 29 PRECEDING AND CURRENT ROW
                ) AS baseline_mean,
                -- 7 天滑动均值 (含当前天往回 6 天)
                AVG(daily_kwh) OVER (
                    ORDER BY day
                    ROWS BETWEEN 6 PRECEDING AND CURRENT ROW
                ) AS observed_mean
            FROM daily
        )
        SELECT day, daily_kwh, observed_mean, baseline_mean
        FROM windows
        WHERE day >= (%s::timestamptz)::date
          AND day <= (%s::timestamptz)::date
          AND baseline_mean IS NOT NULL
          AND observed_mean IS NOT NULL
          AND baseline_mean > 0
        ORDER BY day
    """
    cur.execute(sql, (ctx.point_id, ctx.start_ts, ctx.end_ts, ctx.start_ts, ctx.end_ts))
    rows = cur.fetchall()

    results: list[dict] = []
    for day, daily_kwh, observed_mean, baseline_mean in rows:
        if observed_mean is None or baseline_mean is None or baseline_mean == 0:
            continue

        # 偏离百分比: (observed - baseline) / baseline
        deviation = (observed_mean - baseline_mean) / baseline_mean

        month = day.month
        is_transition = month in SEASONAL_TRANSITION_MONTHS
        threshold = DRIFT_THRESHOLD_TRANSITION if is_transition else DRIFT_THRESHOLD_NORMAL

        abs_dev = abs(deviation)
        if abs_dev < threshold:
            continue

        # severity (季风窗口阈值整体提高 20%)
        if is_transition:
            low_thr, med_thr, high_thr = (
                SEVERITY_LOW_PCT + 0.20,
                SEVERITY_MEDIUM_PCT + 0.20,
                SEVERITY_HIGH_PCT + 0.20,
            )
        else:
            low_thr, med_thr, high_thr = (
                SEVERITY_LOW_PCT,
                SEVERITY_MEDIUM_PCT,
                SEVERITY_HIGH_PCT,
            )

        if abs_dev >= high_thr:
            severity = "HIGH"
        elif abs_dev >= med_thr:
            severity = "MEDIUM"
        else:
            severity = "LOW"

        # 事件时段: observed 的 7 天滑窗 [day - 6d, day]
        # 用 Python datetime (UTC) 避免 PG 把 DATE 转 timestamptz 时受会话时区影响
        day_dt = datetime.combine(day, datetime.min.time(), tzinfo=timezone.utc)
        start_dt = day_dt - timedelta(days=OBSERVED_WINDOW_DAYS - 1)
        end_dt = day_dt

        results.append({
            "event_type": "DRIFT",
            "severity": severity,
            "metric_code": f"{ctx.energy_type}_daily_kwh_7d_vs_30d",
            "start_ts": start_dt,
            "end_ts": end_dt,
            "observed_value": float(observed_mean),
            "baseline_value": float(baseline_mean),
            "evidence": {
                "baseline_method": "rolling_mean_30d_vs_7d",
                "baseline_window": "30 天滑动均值 (含当前天往回 29 天)",
                "observed_window": f"7 天滑窗结束于 {day.isoformat()}",
                "stats": {
                    "daily_kwh": round(float(daily_kwh), 4) if daily_kwh is not None else None,
                    "observed_mean_7d": round(float(observed_mean), 4),
                    "baseline_mean_30d": round(float(baseline_mean), 4),
                    "deviation_pct": round(float(deviation * 100), 2),
                },
                "sample_points": [],
                "extra": {
                    "is_seasonal_transition_month": is_transition,
                    "threshold_used": "transition" if is_transition else "normal",
                    "threshold_value": round(threshold, 2),
                    "energy_type": ctx.energy_type,
                },
            },
        })

    return results
