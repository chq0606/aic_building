"""
SPIKE: 瞬时尖峰检测。

算法: 单点值 > 近 7 天均值 + N*σ,N 按 energy_type 分:
  - electricity / hotwater / chilledwater / steam: 3σ
  - gas: 4σ (燃气供暖启停尖峰正常,容忍度高)
  - water: 5σ (用水波动大,容忍度最高)
  - solar: 不算 SPIKE (白天发电高峰是正常,只检测夜间非 0 才算异常,夜间非 0 归 PROLONGED_ZERO 反向)

季节约束: chilledwater 只在制冷季(5-9 月)算,hotwater 只在供暖季(10-4 月)算。
非运行季数据稀疏,σ 偏小容易误报--比如 chilledwater 12 月只有零星读数,均值偏低,
即使有合理读数也会被判成尖峰。这些月份跳过 SPIKE 检测。

baseline 用 RANGE INTERVAL 窗口函数算滚动 7 天均值和标准差,每个观察点都有
"自己之前 7 天"的 baseline,符合因果性(不用整个时段的均值当 baseline,避免循环论证)。
观察点本身不参与 baseline 计算 (RANGE ... AND INTERVAL '1 hour' PRECEDING,排除当前点)。
"""
from psycopg2.extensions import cursor as Cursor

from . import DetectorContext, sample_points_from_rows, severity_from_threshold, stats_from_rows


# 按 energy_type 分阈值 (sigma_threshold, severity_low_sigma, severity_medium_sigma, severity_high_sigma)
# solar 不在表里,直接 return [] 不检测
SPIKE_THRESHOLDS: dict[str, tuple[float, float, float, float]] = {
    "electricity":   (3.0, 3.0, 5.0, 7.0),
    "hotwater":      (3.0, 3.0, 5.0, 7.0),
    "chilledwater":  (3.0, 3.0, 5.0, 7.0),
    "steam":         (3.0, 3.0, 5.0, 7.0),
    "gas":           (4.0, 4.0, 6.0, 8.0),
    "water":         (5.0, 5.0, 7.0, 9.0),
}

# 运行季约束,只在这些月份检测 (避免非运行季 σ 偏小误报)
SEASONAL_MONTHS: dict[str, list[int]] = {
    "chilledwater": [5, 6, 7, 8, 9],
    "hotwater":     [10, 11, 12, 1, 2, 3, 4],
}


def detect(cur: Cursor, ctx: DetectorContext) -> list[dict]:
    """检测 SPIKE 异常。"""
    if ctx.energy_type is None or ctx.point_id is None:
        return []

    # solar 不算 SPIKE
    if ctx.energy_type not in SPIKE_THRESHOLDS:
        return []

    sigma_thr, low_sigma, med_sigma, high_sigma = SPIKE_THRESHOLDS[ctx.energy_type]

    # 季节约束: 非运行季跳过 (start_ts 在非运行季里就不算,简单按 start 月份判)
    # BDG2 demo 是 2017 全年,如果用户传 2017-01-01~2017-12-31 跨季,
    # 用 SQL 的 EXTRACT(MONTH FROM ts) 过滤,不直接跳过整个调用
    seasonal_months = SEASONAL_MONTHS.get(ctx.energy_type)

    # 提前 7 天取 baseline,让窗口函数有充足前置数据
    # 范围是 [start - 7d, end),窗口函数在范围内算 rolling baseline
    sql = """
        WITH rolling AS (
            SELECT
                pr.ts,
                pr.value_num,
                AVG(pr.value_num) OVER (
                    ORDER BY pr.ts
                    RANGE BETWEEN INTERVAL '7 days' PRECEDING AND INTERVAL '1 hour' PRECEDING
                ) AS baseline_mean,
                STDDEV(pr.value_num) OVER (
                    ORDER BY pr.ts
                    RANGE BETWEEN INTERVAL '7 days' PRECEDING AND INTERVAL '1 hour' PRECEDING
                ) AS baseline_std
            FROM fact.point_reading pr
            WHERE pr.point_id = %s
              AND pr.ts >= %s::timestamptz - INTERVAL '7 days'
              AND pr.ts < %s::timestamptz
              AND pr.value_num >= 0
        )
        SELECT ts, value_num, baseline_mean, baseline_std
        FROM rolling
        WHERE ts >= %s::timestamptz AND ts < %s::timestamptz
          AND baseline_std IS NOT NULL AND baseline_std > 0
          AND value_num > baseline_mean + %s * baseline_std
    """
    params = [ctx.point_id, ctx.start_ts, ctx.end_ts, ctx.start_ts, ctx.end_ts, sigma_thr]

    # 加季节过滤
    if seasonal_months:
        months_csv = ",".join(str(m) for m in seasonal_months)
        sql += f" AND EXTRACT(MONTH FROM ts) IN ({months_csv})"

    sql += " ORDER BY ts"

    cur.execute(sql, params)
    rows = cur.fetchall()

    results: list[dict] = []
    for r in rows:
        ts, value, baseline_mean, baseline_std = r
        # sigma 倍数: 实际偏离 baseline 的标准差倍数
        sigma_multiple = (value - baseline_mean) / baseline_std if baseline_std > 0 else 0
        severity = severity_from_threshold(sigma_multiple, low_sigma, med_sigma, high_sigma)

        results.append({
            "event_type": "SPIKE",
            "severity": severity,
            "metric_code": f"{ctx.energy_type}_interval_kwh",
            "start_ts": ts,
            "end_ts": ts,  # 单点尖峰,start=end=该点 ts
            "observed_value": float(value),
            "baseline_value": float(baseline_mean),
            "evidence": {
                "baseline_method": "zscore_rolling_7d",
                "baseline_window": "近 7 天滚动均值 ± Nσ",
                "observed_window": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "stats": {
                    "mean": round(float(baseline_mean), 4),
                    "std": round(float(baseline_std), 4),
                    "sigma_multiple": round(float(sigma_multiple), 2),
                },
                "sample_points": [
                    {"ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                     "value": float(value)}
                ],
                "extra": {
                    "sigma_threshold": sigma_thr,
                    "energy_type": ctx.energy_type,
                },
            },
        })

    return results
