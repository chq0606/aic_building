"""
工具: query_park_overview - 查园区总览。

LLM 用这个工具拿园区维度的 6 个核心指标 (建筑数 / 总能耗 / 平均 EUI /
异常数 / 同比 / 环比), 用于回答"园区整体用电怎么样"这类问题。

实现直接调 query_service.get_park_overview, 不走 HTTP API。
"""
from __future__ import annotations

from app.services.query_service import (
    NotFoundError,
    _resolve_range,
    get_park_overview,
)


TOOL_NAME = "query_park_overview"

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "查询园区总览指标: 建筑数 / 总能耗 / 平均 EUI / 异常事件数 / 同比 / 环比。"
            "用于回答 '园区整体用电怎么样' '园区能耗水平' 等宏观问题。"
            "如果用户当前在查看某栋楼, 调用 query_building_energy 而不是这个工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "site_id": {
                    "type": "string",
                    "description": "园区 ID (UUID)。如果用户上下文里有 site_id, 直接用。",
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
            "required": ["site_id"],
        },
    },
}


def call(conn, tenant_id: str, site_id: str, start: str | None = None, end: str | None = None) -> dict:
    """查园区总览。

    复用 query_service._resolve_range 把 None 时间补成租户实际数据范围,
    demo 默认落到 2017 全年。
    """
    s, e = _resolve_range(start, end, tenant_id)
    try:
        result = get_park_overview(site_id, tenant_id, s, e)
    except NotFoundError as ex:
        # site 不存在 / 不属于当前租户, 让 LLM 看到能换个参数
        raise ValueError(str(ex))
    # 把 UUID / 数字都转 JSON 友好, _resolve_range 出来的 s/e 是 ISO 字符串
    return {
        "site_id": result["site_id"],
        "time_range": result["time_range"],
        "building_count": result["building_count"],
        "total_kwh": result["total_kwh"],
        "avg_eui": result["avg_eui"],
        "anomaly_count": result["anomaly_count"],
        "yoy_pct": result["yoy_pct"],
        "mom_pct": result["mom_pct"],
        "hint": (
            f"园区共 {result['building_count']} 栋楼, 时间范围总能耗 "
            f"{result['total_kwh']:.1f} kWh, 平均 EUI "
            f"{result['avg_eui']:.2f} kWh/m²" if result["avg_eui"] is not None
            else "平均 EUI 数据不足 (楼面积 sqm 未配置)"
        ),
    }
