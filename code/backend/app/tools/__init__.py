"""
LLM 可调用的工具集 (Step 18)。

四个工具, 每个模块导出:
  - TOOL_NAME: 工具名 (LLM 看到的)
  - TOOL_SCHEMA: OpenAI tools 格式的 schema 描述 (给 LLM 决策用)
  - call(conn, tenant_id, **args) -> dict: 实际执行函数

本模块聚合:
  - TOOL_REGISTRY: {name -> (call, schema)} 映射, agent_service 用它分发执行
  - TOOL_SCHEMAS: list[schema], 直接传给 llm_service.chat(tools=...)
  - execute_tool(name, conn, tenant_id, args) -> dict: 统一入口,
    捕获工具异常转成 {ok: False, error: "..."} 返给 LLM, 不让工具崩整个回答

工具实现原则:
  - 入参从 LLM tool_call.arguments 拿 (已经是 dict)
  - 直接调 service 层 (query_service / anomaly_service / retrieval_service),
    不走 HTTP API (避免 JWT/序列化开销)
  - 出参只返 LLM 需要的核心字段, 大表 (e.g. timeseries 1000 个点) 做截断
  - 不返图片 / base64 (全局约束第 10 条: GLM 纯文本模型)
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger

from app.tools.query_anomalies import call as _query_anomalies, TOOL_SCHEMA as _anomalies_schema
from app.tools.query_building_energy import call as _query_building_energy, TOOL_SCHEMA as _building_energy_schema
from app.tools.query_energy_composition import call as _query_energy_composition, TOOL_SCHEMA as _energy_composition_schema
from app.tools.query_energy_drivers import call as _query_energy_drivers, TOOL_SCHEMA as _energy_drivers_schema
from app.tools.query_park_overview import call as _query_park_overview, TOOL_SCHEMA as _park_overview_schema
from app.tools.search_knowledge import call as _search_knowledge, TOOL_SCHEMA as _search_knowledge_schema


# 工具注册表: name -> (call, schema)。call 签名统一 (conn, tenant_id, **args) -> dict。
TOOL_REGISTRY: dict[str, tuple] = {
    "query_park_overview": (_query_park_overview, _park_overview_schema),
    "query_building_energy": (_query_building_energy, _building_energy_schema),
    "query_energy_composition": (_query_energy_composition, _energy_composition_schema),
    "query_energy_drivers": (_query_energy_drivers, _energy_drivers_schema),
    "query_anomalies": (_query_anomalies, _anomalies_schema),
    "search_knowledge": (_search_knowledge, _search_knowledge_schema),
}


# 直接给 llm_service.chat(tools=...) 用的 schema 列表
TOOL_SCHEMAS: list[dict] = [item[1] for item in TOOL_REGISTRY.values()]


def execute_tool(name: str, conn, tenant_id: str, args: dict) -> dict:
    """统一执行入口, 捕获异常返 {ok, data|error} 给 LLM。

    工具内部抛 ValueError (业务错) / Exception (系统错) 都在这里被吞掉,
    转成 ok=False + error 文字。LLM 看到 error 后能自行决定是换个参数重试
    还是直接告诉用户。
    """
    if name not in TOOL_REGISTRY:
        return {
            "ok": False,
            "error": f"工具 {name} 不存在, 可用工具: {list(TOOL_REGISTRY.keys())}",
        }
    call_fn = TOOL_REGISTRY[name][0]
    try:
        result = call_fn(conn, tenant_id, **(args or {}))
        # 工具内部返的 dict 已经是 LLM 友好的结构, 这里只补 ok 字段
        if isinstance(result, dict) and "ok" not in result:
            result["ok"] = True
        logger.info("tool {} ok tenant={} args={}", name, tenant_id[:8], _truncate_args(args))
        return result
    except ValueError as e:
        # 业务错: 楼不存在 / 时间范围错等, LLM 看到能补救
        logger.warning("tool {} 业务错: {}", name, e)
        return {"ok": False, "error": f"业务参数错误: {e}"}
    except Exception as e:
        # 系统错: DB 异常 / SQL 错, 上层 LLM 看到也只能告诉用户
        logger.exception("tool {} 系统错", name)
        return {"ok": False, "error": f"工具执行异常: {e}"}


def _truncate_args(args: dict) -> str:
    """日志里 args 截断, 避免 query 文本太长刷屏。"""
    s = json.dumps(args, ensure_ascii=False, default=str)
    return s[:120] + "..." if len(s) > 120 else s


__all__ = ["TOOL_REGISTRY", "TOOL_SCHEMAS", "execute_tool"]
