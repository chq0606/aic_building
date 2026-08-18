"""
异常检测器集合。

6 类异常各一个文件,每个文件暴露 detect(cur, ctx) -> list[dict] 协议:
  - ctx: DetectorContext,包含 tenant_id / building_id / point_id / energy_type 等
  - 返回: 异常事件 dict 列表,每个 dict 包含 event_type / severity / metric_code /
          start_ts / end_ts / observed_value / baseline_value / evidence 字段
  - service 层负责把 building_id / point_id / tenant_id / status 等元信息补上后 INSERT

检测粒度按决策点 #1:
  - SPIKE / DRIFT / PROLONGED_ZERO / MISSING_GAP / SCHEDULE_VIOLATION 在 point 级
    (ctx.point_id 必填,每个 point 跑一遍)
  - BASELINE_DEVIATION 在 building 级
    (ctx.point_id 为 None,每个 building 跑一遍,跨 point 聚合 EUI)

边界处理立于实际(用户强调"太阳能就是 0 嘛"):
  - PROLONGED_ZERO 白名单见 prolonged_zero.NORMAL_ZERO_RULES
  - SPIKE 阈值按 energy_type 分,见 spike.SPIKE_THRESHOLDS
  - SCHEDULE_VIOLATION 不适用 solar / irrigation,见 schedule_violation.APPLICABLE_ENERGY_TYPES
  - DRIFT 季风切换窗口阈值提高,见 drift.SEASONAL_TRANSITION_MONTHS
  - BASELINE_DEVIATION 排除 solar,样本 <3 时降级,见 baseline_deviation

evidence 字段结构见 app/models/anomaly.py 的 AnomalyEvidence。
"""
from dataclasses import dataclass


@dataclass
class DetectorContext:
    """
    检测器上下文。

    point-level 检测(SPIKE 等)时 point_id 必填,energy_type / measure_kind 也填。
    building-level 检测(BASELINE_DEVIATION)时 point_id 为 None,energy_type 也是 None。
    """

    tenant_id: str
    building_id: str
    point_id: str | None
    point_code: str | None
    energy_type: str | None
    measure_kind: str | None
    unit_code: str | None
    building_code: str | None
    primary_use: str | None
    sqm: float | None
    start_ts: str  # ISO8601
    end_ts: str  # ISO8601
    site_timezone: str | None
    site_latitude: float | None


def severity_from_threshold(value: float, low: float, medium: float, high: float) -> str:
    """
    按数值大小判定 severity。

    阈值含义: 超过 low 是 LOW,超过 medium 是 MEDIUM,超过 high 是 HIGH。
    用于 SPIKE 的 σ 倍数、DRIFT 的偏离百分比等场景。
    """
    if value >= high:
        return "HIGH"
    if value >= medium:
        return "MEDIUM"
    if value >= low:
        return "LOW"
    return "LOW"


def stats_from_rows(rows: list, value_idx: int = 1) -> dict:
    """
    从数据库返回的 rows 算 mean/median/std/max/min。

    rows 是 psycopg2 fetchall 的结果,value_idx 指向值列。
    None 值过滤掉。空列表返空 dict。

    PG 自己能算这些,但 detector 里多次查表会重复扫数据,统一在 Python 里算更省一次 SQL。
    数据量小(point 一周 168 条),Python 算无性能影响。
    """
    values = [float(r[value_idx]) for r in rows if r[value_idx] is not None]
    if not values:
        return {}
    n = len(values)
    mean = sum(values) / n
    sorted_v = sorted(values)
    # 中位数:偶数个取中间两个均值,奇数个取中间
    if n % 2 == 1:
        median = sorted_v[n // 2]
    else:
        median = (sorted_v[n // 2 - 1] + sorted_v[n // 2]) / 2
    # 样本标准差(n-1 分母),n=1 时返 0 避免除零
    if n > 1:
        var = sum((v - mean) ** 2 for v in values) / (n - 1)
        std = var ** 0.5
    else:
        std = 0.0
    return {
        "mean": round(mean, 4),
        "median": round(median, 4),
        "std": round(std, 4),
        "max": round(max(values), 4),
        "min": round(min(values), 4),
    }


def sample_points_from_rows(rows: list, ts_idx: int = 0, value_idx: int = 1, limit: int = 20) -> list[dict]:
    """从数据库返回的 rows 取前 N 条作为 sample_points。"""
    points = []
    for r in rows[:limit]:
        ts = r[ts_idx]
        val = r[value_idx]
        points.append({
            "ts": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
            "value": float(val) if val is not None else None,
        })
    return points
