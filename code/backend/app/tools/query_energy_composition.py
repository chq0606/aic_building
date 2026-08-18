"""
工具: query_energy_composition - 查能源构成 (分项能耗占比)。

LLM 用这个工具拿真实的分项能耗数据, 用于节能优化场景:
  - 园区级问题 (site_id) -> 调 get_park_energy_composition
  - 单楼级问题 (building_id) -> 调 get_building_energy_composition

加这个工具是因为: 之前 LLM 拿不到分项数据, 凭记忆编"照明占 25% 暖通占 40%",
但 BDG2 demo 数据按能源载体分 (electricity/gas/hotwater/solar/chilledwater),
不是按用途分 (照明/暖通/动力)。LLM 编造的占比跟真实数据完全对不上, 节能措施
saved_kwh 也跟着错。这个工具让 LLM 拿到真实分项, evidence_ref 不再写"行业通用
比例估算"。
"""
from __future__ import annotations

from app.services.query_service import (
    NotFoundError,
    _resolve_range,
    get_building_energy_composition,
    get_park_energy_composition,
)


TOOL_NAME = "query_energy_composition"

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "查能源构成: 各 energy_type 的能耗占比 + 绝对值。"
            "用于节能优化场景, 拿真实分项数据写 evidence_ref, 不要凭记忆编占比。"
            "园区级问题传 site_id, 单楼级问题传 building_id (二选一)。"
            "返回 composition 数组, 每项含 type (能源类型) / kwh (绝对值) / pct (百分比)。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "site_id": {
                    "type": "string",
                    "description": "园区 ID (UUID)。园区级问题传这个。",
                },
                "building_id": {
                    "type": "string",
                    "description": "建筑 ID (UUID)。单楼级问题传这个。",
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
            # site_id / building_id 二选一, 不在 required 里强制 (LLM 可能只传一个)
        },
    },
}


def call(
    conn,
    tenant_id: str,
    site_id: str | None = None,
    building_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
) -> dict:
    """查能源构成。site_id 优先 (园区级), 否则用 building_id (单楼级)。

    两个都没传 -> ValueError, 让 LLM 看到错误换个参数重试。
    """
    s, e = _resolve_range(start, end, tenant_id)
    if site_id:
        try:
            result = get_park_energy_composition(site_id, tenant_id, s, e)
        except NotFoundError as ex:
            raise ValueError(str(ex))
        scope = "park"
    elif building_id:
        try:
            result = get_building_energy_composition(building_id, tenant_id, s, e)
        except NotFoundError as ex:
            raise ValueError(str(ex))
        scope = "building"
    else:
        raise ValueError("必须传 site_id 或 building_id 中的一个")

    # 给 LLM 一句话摘要, 让它不读 composition 数组就能大致回答
    comp = result.get("composition", [])
    if comp:
        top = comp[0]
        hint = (
            f"{'园区' if scope == 'park' else result.get('display_name', '该楼')} "
            f"总能耗 {result['total_kwh']:.1f} kWh, "
            f"最大分项 {top['type']} 占 {top['pct']:.1f}% ({top['kwh']:.1f} kWh), "
            f"共 {len(comp)} 种能源类型"
        )
    else:
        hint = "无能耗数据"

    return {
        "scope": scope,
        "site_id": result.get("site_id"),
        "building_id": result.get("building_id"),
        "building_name": result.get("display_name", ""),
        "time_range": result.get("time_range", {"start": s, "end": e}),
        "total_kwh": result.get("total_kwh", 0),
        "composition": comp,
        "hint": hint,
    }
