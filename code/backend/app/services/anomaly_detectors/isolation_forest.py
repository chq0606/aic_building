"""
ML_OUTLIER: Isolation Forest 异常检测 (building-level, 机器学习)。

算法: 把楼栋日总能耗 + 时间上下文 (星期几 / 月份) 作为特征, 用 sklearn
IsolationForest 无监督学习找出"跟该楼自身历史模式不一致"的异常能耗日。
跟 SPIKE/DRIFT 等规则式检测器互补: 规则式抓已知形态 (尖峰/漂移), Isolation
Forest 抓"说不清但统计上突兀"的天 (比如某天能耗在当月明显反常)。

为什么加时间上下文特征: 纯 value 会让季节性高峰 (夏天制冷/冬天供暖) 被误判为
异常, 加了星期几 + 月份后模型学会"冬天本来就费能", 只标记"在冬天也异常"的天。

检测粒度: building-level (ctx.point_id 为 None), 每楼 1 个事件。
severity 按异常天数: >=5 HIGH, >=2 MEDIUM, else LOW。
样本 < 30 天不检测 (Isolation Forest 样本太少学不到分布)。
"""
from datetime import datetime, timezone

import numpy as np
from psycopg2.extensions import cursor as Cursor

from . import DetectorContext


MIN_SAMPLE_DAYS = 30
CONTAMINATION = 0.05  # 期望 ~5% 的天被判异常


def detect(cur: Cursor, ctx: DetectorContext) -> list[dict]:
    """检测 ML_OUTLIER。building-level, ctx.point_id 应为 None。"""
    from sklearn.ensemble import IsolationForest

    start_dt = datetime.fromisoformat(ctx.start_ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    end_dt = datetime.fromisoformat(ctx.end_ts.replace("Z", "+00:00")).astimezone(timezone.utc)
    start_date = start_dt.strftime("%Y-%m-%d")
    end_date = end_dt.strftime("%Y-%m-%d")

    cur.execute("""
        SELECT date, SUM(total_kwh) AS value
        FROM mart.building_daily_energy
        WHERE building_id = %s::uuid AND tenant_id = %s::uuid
          AND date >= %s AND date <= %s
        GROUP BY date
        ORDER BY date
    """, (ctx.building_id, ctx.tenant_id, start_date, end_date))
    rows = cur.fetchall()

    if len(rows) < MIN_SAMPLE_DAYS:
        return []

    # 特征: log1p(value) 压缩量级 + 星期几 + 月份
    X = []
    for d, v in rows:
        X.append([np.log1p(float(v)), d.weekday(), d.month])
    X = np.array(X)

    model = IsolationForest(contamination=CONTAMINATION, random_state=42)
    preds = model.fit_predict(X)  # 1=正常, -1=异常

    outlier_indices = [i for i, p in enumerate(preds) if p == -1]
    if not outlier_indices:
        return []

    # 用 score_samples 给每个异常天排序 (越负越异常), 取 top 5 做证据
    scores = model.score_samples(X)
    top = sorted(outlier_indices, key=lambda i: scores[i])[:5]
    outlier_days = [
        {
            "date": str(rows[i][0]),
            "value": round(float(rows[i][1]), 2),
            "anomaly_score": round(float(scores[i]), 3),
        }
        for i in top
    ]

    n = len(outlier_indices)
    if n >= 5:
        severity = "HIGH"
    elif n >= 2:
        severity = "MEDIUM"
    else:
        severity = "LOW"

    return [{
        "event_type": "ML_OUTLIER",
        "severity": severity,
        "metric_code": "building_daily_energy_kwh",
        "start_ts": ctx.start_ts,
        "end_ts": ctx.end_ts,
        "observed_value": round(float(np.mean([rows[i][1] for i in outlier_indices])), 2),
        "baseline_value": round(float(np.mean([rows[i][1] for i in range(len(rows))])), 2),
        "evidence": {
            "baseline_method": "isolation_forest",
            "baseline_window": f"Isolation Forest (contamination={CONTAMINATION})",
            "observed_window": f"{ctx.start_ts} ~ {ctx.end_ts}",
            "stats": {
                "outlier_count": n,
                "total_days": len(rows),
                "outlier_ratio": round(n / len(rows), 4),
            },
            "sample_points": [
                {"ts": d["date"], "value": d["value"]} for d in outlier_days
            ],
            "extra": {
                "top_outlier_days": outlier_days,
            },
        },
    }]
