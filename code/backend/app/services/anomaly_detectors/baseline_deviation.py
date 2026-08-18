"""
BASELINE_DEVIATION: 横向对比异常检测 (building-level)。

算法: 当前 building 的 EUI 高于同 primary_use 楼群均值 + 1σ 算异常。
      EUI = 时间范围内总能耗 / sqm, 只统计能耗类 (排除 solar)。

排除项 (边界处理):
  - solar: 装机容量差异大,无对比意义
  - 非运行季冷热负荷: 分母低值不可比 (一期简化,直接用全年总能耗,运行季过滤留给后续)

样本不足降级 (决策点 #8):
  - 同 primary_use 楼群 >= 3 栋时用同类均值 + 1σ
  - < 3 栋时降级用全租户所有 building 均值 + 1σ
  - BDG2 demo 6 栋楼的 primary_use 分布:
      Education: 3 栋 (Alissa/Dylan/Seth) - 能算同类基准
      Entertainment/public assembly: 1 栋 (Franklin) - 降级
      Technology/science: 1 栋 (Tammy) - 降级
      Public services: 1 栋 (Angie) - 降级

检测粒度:
  - building 级, ctx.point_id 为 None
  - 每个 building 跑一次, 1 个事件 (整个时间范围)
  - 偏离 1σ LOW, 2σ MEDIUM, 3σ HIGH

baseline 取均值 + 1σ 做异常阈值 (提示词要求 "EUI 高于同 primary_use 建筑均值 + 1σ"):
  observed_eui - baseline_mean > 1 * baseline_std 才算异常
"""
from psycopg2.extensions import cursor as Cursor

from . import DetectorContext


# 样本数阈值, < 此值降级到全租户基准
PEER_MIN_SAMPLE = 3

# sigma 倍数阈值
SIGMA_LOW = 1.0    # > 1σ LOW
SIGMA_MEDIUM = 2.0  # > 2σ MEDIUM
SIGMA_HIGH = 3.0   # > 3σ HIGH

# 排除的 energy_type (装机容量差异大, 无对比意义)
EXCLUDED_ENERGY_TYPES = {"solar"}


def detect(cur: Cursor, ctx: DetectorContext) -> list[dict]:
    """检测 BASELINE_DEVIATION 异常。building-level, ctx.point_id 应为 None。"""
    # 转成 UTC 日期字符串, 避免 timestamptz::date 用会话时区导致日期偏移
    # (Step 07 query_service.py 里 _to_utc_date_str 的同一坑)
    from datetime import datetime, timezone
    start_dt = datetime.fromisoformat(ctx.start_ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    end_dt = datetime.fromisoformat(ctx.end_ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    excluded_types = tuple(EXCLUDED_ENERGY_TYPES)

    sql = """
        WITH current_building AS (
            SELECT
                b.id,
                b.primary_use,
                b.sqm,
                COALESCE(SUM(d.total_kwh), 0) AS total_kwh,
                CASE WHEN b.sqm IS NOT NULL AND b.sqm > 0
                     THEN COALESCE(SUM(d.total_kwh), 0) / b.sqm
                     ELSE NULL END AS eui
            FROM core.building b
            LEFT JOIN mart.building_daily_energy d
                ON d.building_id = b.id AND d.tenant_id = b.tenant_id
                AND d.date >= %s AND d.date <= %s
                AND NOT (d.energy_type = ANY(%s))
            WHERE b.id = %s AND b.tenant_id = %s
            GROUP BY b.id, b.primary_use, b.sqm
        ),
        peer_group AS (
            SELECT
                b.id,
                CASE WHEN b.sqm IS NOT NULL AND b.sqm > 0
                     THEN COALESCE(SUM(d.total_kwh), 0) / b.sqm
                     ELSE NULL END AS eui
            FROM core.building b
            LEFT JOIN mart.building_daily_energy d
                ON d.building_id = b.id AND d.tenant_id = b.tenant_id
                AND d.date >= %s AND d.date <= %s
                AND NOT (d.energy_type = ANY(%s))
            WHERE b.tenant_id = %s
              AND b.primary_use IS NOT NULL
              AND b.primary_use = (SELECT primary_use FROM current_building)
              AND b.id != (SELECT id FROM current_building)
              AND b.sqm IS NOT NULL AND b.sqm > 0
            GROUP BY b.id, b.sqm
        ),
        all_buildings AS (
            SELECT
                b.id,
                CASE WHEN b.sqm IS NOT NULL AND b.sqm > 0
                     THEN COALESCE(SUM(d.total_kwh), 0) / b.sqm
                     ELSE NULL END AS eui
            FROM core.building b
            LEFT JOIN mart.building_daily_energy d
                ON d.building_id = b.id AND d.tenant_id = b.tenant_id
                AND d.date >= %s AND d.date <= %s
                AND NOT (d.energy_type = ANY(%s))
            WHERE b.tenant_id = %s
              AND b.id != (SELECT id FROM current_building)
              AND b.sqm IS NOT NULL AND b.sqm > 0
            GROUP BY b.id, b.sqm
        )
        SELECT
            cb.id,
            cb.primary_use,
            cb.sqm,
            cb.total_kwh,
            cb.eui AS current_eui,
            (SELECT COUNT(*) FROM peer_group) AS peer_count,
            (SELECT AVG(eui) FROM peer_group WHERE eui IS NOT NULL) AS peer_mean,
            (SELECT STDDEV(eui) FROM peer_group WHERE eui IS NOT NULL) AS peer_std,
            (SELECT COUNT(*) FROM all_buildings) AS all_count,
            (SELECT AVG(eui) FROM all_buildings WHERE eui IS NOT NULL) AS all_mean,
            (SELECT STDDEV(eui) FROM all_buildings WHERE eui IS NOT NULL) AS all_std
        FROM current_building cb
    """
    cur.execute(sql, (
        start_date, end_date, list(excluded_types),
        ctx.building_id, ctx.tenant_id,
        start_date, end_date, list(excluded_types), ctx.tenant_id,
        start_date, end_date, list(excluded_types), ctx.tenant_id,
    ))
    row = cur.fetchone()
    if row is None:
        return []

    (building_id, primary_use, sqm, total_kwh, current_eui,
     peer_count, peer_mean, peer_std,
     all_count, all_mean, all_std) = row

    if current_eui is None or sqm is None or sqm == 0:
        return []

    # 决定用 peer 还是 all
    use_peer = peer_count is not None and peer_count >= PEER_MIN_SAMPLE and peer_std is not None and peer_std > 0
    if use_peer:
        baseline_mean = float(peer_mean)
        baseline_std = float(peer_std)
        baseline_method = "peer_compare_same_primary_use"
        baseline_window = f"同 primary_use ({primary_use}) 楼群均值 + 1σ"
        peer_count_used = int(peer_count)
    else:
        # 降级: 用全租户所有 building
        if all_count is None or all_count < 2 or all_std is None or all_std == 0:
            return []  # 全租户样本不足, 不算
        baseline_mean = float(all_mean)
        baseline_std = float(all_std)
        baseline_method = "all_buildings_fallback"
        baseline_window = f"全租户所有 building 均值 + 1σ (同类样本不足 {peer_count}/{PEER_MIN_SAMPLE} 降级)"
        peer_count_used = int(all_count)

    # sigma 倍数: 当前 EUI 高于均值多少个标准差 (只看高于, 低于不算异常 - 节能优秀不算问题)
    sigma_deviation = (float(current_eui) - baseline_mean) / baseline_std if baseline_std > 0 else 0

    if sigma_deviation < SIGMA_LOW:
        return []  # 没超 1σ, 不算异常

    if sigma_deviation >= SIGMA_HIGH:
        severity = "HIGH"
    elif sigma_deviation >= SIGMA_MEDIUM:
        severity = "MEDIUM"
    else:
        severity = "LOW"

    # evidence 的 peer_group 字段: 列出对比池里其他 building 的 EUI
    cur.execute("""
        SELECT
            b.id,
            b.display_name,
            b.primary_use,
            CASE WHEN b.sqm IS NOT NULL AND b.sqm > 0
                 THEN COALESCE(SUM(d.total_kwh), 0) / b.sqm
                 ELSE NULL END AS eui
        FROM core.building b
        LEFT JOIN mart.building_daily_energy d
            ON d.building_id = b.id AND d.tenant_id = b.tenant_id
            AND d.date >= %s AND d.date <= %s
            AND NOT (d.energy_type = ANY(%s))
        WHERE b.tenant_id = %s
          AND b.id != %s
          AND b.sqm IS NOT NULL AND b.sqm > 0
          AND (
              (%s AND b.primary_use = %s)
              OR
              (NOT %s)
          )
        GROUP BY b.id, b.display_name, b.primary_use, b.sqm
        ORDER BY eui DESC
        LIMIT 10
    """, (
        start_date, end_date, list(excluded_types),
        ctx.tenant_id, ctx.building_id,
        use_peer, primary_use,
        use_peer,
    ))
    peer_rows = cur.fetchall()
    peer_group_list = [
        {
            "building_id": str(r[0]),
            "display_name": r[1],
            "primary_use": r[2],
            "eui": round(float(r[3]), 4) if r[3] is not None else None,
        }
        for r in peer_rows
    ]

    return [{
        "event_type": "BASELINE_DEVIATION",
        "severity": severity,
        "metric_code": "building_eui_kwh_per_m2",
        "start_ts": ctx.start_ts,
        "end_ts": ctx.end_ts,
        "observed_value": float(current_eui),
        "baseline_value": float(baseline_mean),
        "evidence": {
            "baseline_method": baseline_method,
            "baseline_window": baseline_window,
            "observed_window": f"{ctx.start_ts} ~ {ctx.end_ts}",
            "stats": {
                "current_eui": round(float(current_eui), 4),
                "baseline_mean": round(baseline_mean, 4),
                "baseline_std": round(baseline_std, 4),
                "sigma_deviation": round(float(sigma_deviation), 2),
                "total_kwh": round(float(total_kwh), 2) if total_kwh is not None else None,
                "sqm": round(float(sqm), 2) if sqm is not None else None,
            },
            "sample_points": [],
            "extra": {
                "primary_use": primary_use,
                "peer_count": peer_count_used,
                "use_peer_baseline": use_peer,
                "excluded_energy_types": list(EXCLUDED_ENERGY_TYPES),
                "peer_group": peer_group_list,
            },
        },
    }]
