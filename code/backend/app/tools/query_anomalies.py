"""
工具: query_anomalies - 查异常事件。

LLM 用这个工具拿异常事件列表 + 按类型/严重度的分组统计, 用于回答
'这栋楼异常原因' '最近有什么异常' 等问题。

入参支持两种粒度:
  - building_id 传: 查单楼异常列表 (返 events + by_type + by_severity)
  - site_id 传: 查园区异常概览 (返 total + by_type + by_severity + 每楼摘要)
  - 都不传: ValueError, 让 LLM 补参数
"""
from __future__ import annotations

from app.services.anomaly_service import (
    NotFoundError,
    get_site_anomaly_overview,
    list_anomalies_for_building,
)
from app.services.query_service import _resolve_range


TOOL_NAME = "query_anomalies"

TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "查询异常事件。支持两种粒度: 传 building_id 查单楼异常列表 (含事件类型/严重度/起止时间/观测值/基线值/证据); "
            "传 site_id 查园区异常概览 (含按类型/严重度分组 + 每栋楼异常数摘要)。"
            "用于回答 '这栋楼最近有什么异常' '异常原因' '园区异常分布' 等问题。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "building_id": {
                    "type": "string",
                    "description": "建筑 ID (UUID)。传了就查单楼, 不传就用 site_id 查园区。",
                },
                "site_id": {
                    "type": "string",
                    "description": "园区 ID (UUID)。building_id 没传时必填。",
                },
                "start": {"type": "string", "description": "ISO8601 起始时间, 可选。"},
                "end": {"type": "string", "description": "ISO8601 结束时间, 可选。"},
                "limit": {
                    "type": "integer",
                    "description": "单楼场景返回的最大事件数, 默认 20, 上限 100。",
                    "default": 20,
                },
            },
            "required": [],
        },
    },
}


# 异常事件 evidence 字段可能很长, 给 LLM 看截断到 200 字符
_MAX_EVIDENCE_CHARS = 200


def call(
    conn,
    tenant_id: str,
    building_id: str | None = None,
    site_id: str | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 20,
) -> dict:
    """查异常事件, building_id 优先单楼, 否则用 site_id 查园区。"""
    s, e = _resolve_range(start, end, tenant_id)

    if building_id:
        return _query_building_anomalies(building_id, tenant_id, s, e, limit)
    if site_id:
        return _query_site_anomalies(site_id, tenant_id, s, e)
    raise ValueError("必须传 building_id 或 site_id 之一")


def _query_building_anomalies(
    building_id: str, tenant_id: str, s: str, e: str, limit: int
) -> dict:
    try:
        result = list_anomalies_for_building(
            building_id=building_id,
            tenant_id=tenant_id,
            start=s,
            end=e,
            limit=limit,
        )
    except NotFoundError as ex:
        raise ValueError(str(ex))

    # 原始 service 返: total / items (list of event) / by_type / by_severity /
    # building_code / display_name / time_range
    items = []
    for ev in result.get("items", []):
        evidence = ev.get("evidence") or ""
        if isinstance(evidence, str) and len(evidence) > _MAX_EVIDENCE_CHARS:
            evidence = evidence[:_MAX_EVIDENCE_CHARS] + "..."
        items.append({
            "id": ev["id"],
            "event_type": ev["event_type"],
            "severity": ev["severity"],
            "point_code": ev.get("point_code"),
            "energy_type": ev.get("energy_type"),
            "start_ts": ev["start_ts"],
            "end_ts": ev["end_ts"],
            "observed_value": ev.get("observed_value"),
            "baseline_value": ev.get("baseline_value"),
            "evidence": evidence,
            "status": ev.get("status"),
        })

    return {
        "scope": "building",
        "building_id": building_id,
        "building_name": result.get("display_name") or result.get("building_code") or "",
        "time_range": {"start": s, "end": e},
        "total": result.get("total", 0),
        "by_type": result.get("by_type", {}),
        "by_severity": result.get("by_severity", {}),
        "items": items,
        "hint": _building_hint(result.get("display_name"), result.get("total", 0), result.get("by_severity", {})),
    }


def _query_site_anomalies(site_id: str, tenant_id: str, s: str, e: str) -> dict:
    try:
        result = get_site_anomaly_overview(
            site_id=site_id,
            tenant_id=tenant_id,
            start=s,
            end=e,
        )
    except NotFoundError as ex:
        raise ValueError(str(ex))
    return {
        "scope": "site",
        "site_id": site_id,
        "time_range": {"start": s, "end": e},
        **result,
    }


def _building_hint(name: str | None, total: int, by_severity: dict) -> str:
    """给 LLM 一句话摘要。"""
    if not name:
        name = "该建筑"
    if total == 0:
        return f"{name} 时间范围内无异常事件"
    sev_parts = [f"{k} {v} 条" for k, v in by_severity.items() if v > 0]
    return f"{name} 时间范围内共 {total} 条异常 ({', '.join(sev_parts)})"
