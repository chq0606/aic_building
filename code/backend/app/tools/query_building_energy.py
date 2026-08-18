"""
工具: query_building_energy - 查单楼能耗时序。

LLM 用这个工具拿单楼的能耗时序数据 (日/周/月粒度), 用于回答
'这栋楼上个月用电情况' 这类问题。返回摘要 + 截断后的时序点, 不返
全部 365 个点 (省 token, LLM 看摘要 + 趋势够用)。
"""
from __future__ import annotations

from app.services.query_service import (
    GRANULARITIES,
    NotFoundError,
    _resolve_range,
    get_building_timeseries,
)


TOOL_NAME = "query_building_energy"

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "查询单栋建筑的能耗时序数据, 含日/周/月粒度的时序点 + 统计摘要 (总能耗 / 峰值 / 谷值 / 平均)。"
            "用于回答 '这栋楼上个月用电情况' 'Bobcat 这栋楼最近 7 天能耗' 等具体楼问题。"
            "如果用户问的是整个园区, 调用 query_park_overview 而不是这个工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "building_id": {
                    "type": "string",
                    "description": "建筑 ID (UUID)。如果用户上下文里有 building_id, 直接用。",
                },
                "metric": {
                    "type": "string",
                    "description": "对比指标, 目前只支持 interval_energy_kwh (区间能耗 kWh)。",
                    "default": "interval_energy_kwh",
                    "enum": ["interval_energy_kwh"],
                },
                "granularity": {
                    "type": "string",
                    "description": "时间粒度: day (日) / week (周) / month (月)。",
                    "default": "day",
                    "enum": ["day", "week", "month"],
                },
                "start": {
                    "type": "string",
                    "description": "ISO8601 起始时间, 可选。不传按租户实际数据范围。",
                },
                "end": {
                    "type": "string",
                    "description": "ISO8601 结束时间, 可选。不传按租户实际数据范围。",
                },
            },
            "required": ["building_id"],
        },
    },
}


# 时序点超过这个数就截断, 给 LLM 看摘要 + 头尾样本比给全量更省 token。
_MAX_SERIES_POINTS = 30


def call(
    conn,
    tenant_id: str,
    building_id: str,
    metric: str = "interval_energy_kwh",
    granularity: str = "day",
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """查单楼能耗时序。

    复用 query_service.get_building_timeseries, 拿到完整时序后做截断:
    时序点 <= _MAX_SERIES_POINTS 全返, 超过的返头 10 + 尾 10 + 中间抽样,
    并附 truncated=True 标记。

    service 返回的字段是 points (list of {ts, value, unit}), 这里改名叫
    series_sample 走 LLM 视角; series_count 用原始点数 (没截断前)。
    service 不返 stats, 这里从 points 自己算 total/peak/trough/avg。
    """
    if granularity not in GRANULARITIES:
        raise ValueError(f"粒度非法, 支持: {GRANULARITIES}")
    s, e = _resolve_range(start, end, tenant_id)
    try:
        result = get_building_timeseries(building_id, tenant_id, metric, granularity, s, e)
    except NotFoundError as ex:
        raise ValueError(str(ex))

    full_series = result.get("points") or []
    stats = _compute_stats(full_series)
    truncated = False
    if len(full_series) > _MAX_SERIES_POINTS:
        truncated = True
        head = full_series[:10]
        tail = full_series[-10:]
        mid_start = len(full_series) // 2 - 5
        mid = full_series[mid_start:mid_start + 10] if mid_start > 10 else []
        sample = head + mid + tail
    else:
        sample = full_series

    return {
        "building_id": result.get("building_id", building_id),
        "building_name": result.get("display_name", ""),
        "metric": result.get("metric", metric),
        "granularity": result.get("granularity", granularity),
        "time_range": result.get("time_range", {"start": s, "end": e}),
        "series_count": len(full_series),
        "truncated": truncated,
        "series_sample": sample,
        "stats": stats,
        "hint": _build_hint(result.get("display_name", ""), stats, granularity),
    }


def _compute_stats(series: list[dict]) -> dict:
    """从时序点自己算 total/peak/trough/avg, service 不返这个字段。

    None 值跳过 (零值和 None 不一样, 不能算进 avg)。
    """
    values = [p["value"] for p in series if p.get("value") is not None]
    if not values:
        return {}
    return {
        "total_kwh": sum(values),
        "peak_kwh": max(values),
        "trough_kwh": min(values),
        "avg_kwh": sum(values) / len(values),
    }


def _build_hint(building_name: str, stats: dict, granularity: str) -> str:
    """给 LLM 一句话摘要, 让它不读 series 就能大致回答。"""
    if not stats:
        return f"{building_name} 无统计数据"
    total = stats.get("total_kwh")
    peak = stats.get("peak_kwh")
    avg = stats.get("avg_kwh")
    parts = [f"{building_name}"]
    if total is not None:
        parts.append(f"总能耗 {total:.1f} kWh")
    if peak is not None:
        parts.append(f"峰值 {peak:.1f} kWh")
    if avg is not None:
        parts.append(f"日均 {avg:.1f} kWh")
    parts.append(f"粒度 {granularity}")
    return ", ".join(parts)
