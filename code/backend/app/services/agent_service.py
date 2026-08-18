"""
Agent 编排服务: 单轮 function call + 场景识别 + system prompt 拼装。

单轮 function call 含义:
  1. 调 LLM 一次 (带 tools), 拿 tool_calls
  2. 并行执行 tool_calls, 拿 results
  3. 把 results 塞进 messages, 再调 LLM 流式生成最终回答

不在后端做多轮 ReAct (用户决策选了单轮), 因为:
  - 多轮 ReAct 实现复杂 (循环退出/超时/最大轮数), 容易失控
  - GLM 多轮调用 token 消耗大, 一次问答可能烧 5k+ token
  - 单轮够用: 大部分问题 LLM 一次就能决定调哪几个工具

场景识别 (decide_scenario) 用关键词匹配, 不用 LLM 分类 (省钱):
  - optimization: 节能 / 优化 / 节能空间 / 节能方案 / 节能措施 / 节能建议
  - anomaly: 异常 / 报警 / 故障 / 偏差 / 偏离
  - standard: 限值 / 标准 / 国标 / 规范 / 条款 / 强条
  - data: 默认场景, 用电 / 能耗 / EUI 等数据查询

system prompt 按场景不同给不同指令, 比如 optimization 场景会要求
"最终回答必须用 JSON 输出四段结构, 用 response_format 模式"。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from app.core.config import settings
from app.services import llm_service
from app.services.llm_service import GlmApiError, GlmResponse, GlmToolCallError
from app.tools import TOOL_SCHEMAS


SCENARIO_OPTIMIZATION = "optimization"
SCENARIO_ANOMALY = "anomaly"
SCENARIO_STANDARD = "standard"
SCENARIO_DATA = "data"

ALL_SCENARIOS = (SCENARIO_OPTIMIZATION, SCENARIO_ANOMALY, SCENARIO_STANDARD, SCENARIO_DATA)


@dataclass
class AgentPlan:
    """单轮 function call 第一阶段结果。

    tool_calls 为空表示 LLM 决定不调工具直接回答 (走 final_answer 阶段
    时 messages 里就没 tool_results, 但仍调 LLM 流式生成)。
    """
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"
    raw_content: str = ""  # LLM 第一轮可能直接返了文本, 留着降级用
    usage: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# 场景识别 (关键词匹配)
# ---------------------------------------------------------------------------


_SCENARIO_KEYWORDS: dict[str, tuple[str, ...]] = {
    SCENARIO_OPTIMIZATION: (
        "节能", "优化", "节能空间", "节能方案", "节能措施", "节能建议",
        "省电", "降低能耗", "能效改进", "改造",
    ),
    SCENARIO_ANOMALY: (
        "异常", "报警", "故障", "偏差", "偏离", "异常原因", "异常分析",
        "为什么高", "为什么低", "为啥",
    ),
    SCENARIO_STANDARD: (
        "限值", "标准", "国标", "规范", "条款", "强条", "GB", "JGJ",
        "GB/T", "要求", "规定",
    ),
}


def decide_scenario(user_msg: str, context: dict | None = None) -> str:
    """关键词匹配决定场景。命中多个时按 optimization > anomaly > standard > data 优先级。

    优先级原因: 节能优化是项目主推场景 (提示词第 13 条深化), 命中就优先;
    异常诊断也常带"节能"字眼但优先级低; 标准查询独立; 默认数据问答。
    """
    msg = user_msg.lower()
    for scenario in (SCENARIO_OPTIMIZATION, SCENARIO_ANOMALY, SCENARIO_STANDARD):
        keywords = _SCENARIO_KEYWORDS[scenario]
        if any(kw.lower() in msg for kw in keywords):
            return scenario
    return SCENARIO_DATA


# ---------------------------------------------------------------------------
# System prompt 拼装
# ---------------------------------------------------------------------------


_BASE_SYSTEM = """你是建筑能耗分析与节能优化平台的 AI 助手, 服务于多租户建筑群场景。

回答原则:
1. 优先调用工具拿真实数据, 不要凭空编造数字 (EUI / 总能耗 / 异常数 / 分项占比 必须来自工具)
2. 工具调用失败时, 友好告诉用户失败原因, 不要继续编造数据
3. 数据证据要清晰: 引用工具返回的具体数字, 标注数据来源类型即可。说人话, 不要在回答里提工具内部名。
   - 正确示例: "根据该园区能耗查询数据, 7 月总能耗 12345 kWh"
   - 正确示例: "根据建筑能耗时序数据, Bobcat 楼最近 7 天日均 450 kWh"
   - 错误示例: "根据 query_building_energy 工具返回..." (不要用工具内部名)
   - 错误示例: "调用 query_park_overview 工具拿到..." (不要用工具内部名)
4. 文档引用要标明标准号 + 条款号 (如 "GB 55015-2021 第 3.3.1 条"), 但条款号必须来自 search_knowledge 工具返回的 section_title, 不要凭记忆写
5. 回答使用中文, 简洁专业, 避免冗长散文

数据真实性约束 (重要, 违反会导致回答被后端拒答):
- 分项能耗占比 (如"照明占 25% 暖通占 40%") 必须来自 query_energy_composition 工具, 不要凭"行业通用比例"估算。如果没调这个工具, evidence_ref 里不要写具体占比数字, 改写"建议进一步分项计量"
- evidence_ref 字段不要出现"估算 / 行业通用比例 / 大约 / 约 / 一般约"等表示臆想的词。每个数字必须有工具数据支撑
- 国标条款号 (clause_ref) 必须来自 search_knowledge 工具返回的 standard_no + section_title, 不要凭记忆写条款号 (记错标准号会误导用户)
- CO₂ 减排系数固定用 0.581 kg CO₂/kWh (中国电网平均值 2022), saved_co2 字段后端会用 saved_kwh × 0.581 强制重算覆盖你写的值, 你写的 saved_co2 不会被采用, 但 saved_kwh 必须合理

参数约束 (重要, 违反会导致工具报错):
- site_id / building_id 必须是 UUID 格式 (形如 8-4-4-4-12 的十六进制串, 如 19fee5d5-2d31-4b4a-b74d-7b544664f4e8)。
- 如果用户当前上下文里没有 site_id / building_id, **不要编造占位符** (如 site-001 / building-1 这种都不是 UUID)。
  此时应该: 园区问题就告诉用户"请在顶部选择园区后再问"; 建筑问题就告诉用户"请先在园区里选中一栋楼"。
- 不要把 building_id 当 site_id 传, 也不要反过来。园区级问题用 site_id 调 query_park_overview, 单楼问题用 building_id 调 query_building_energy。

可用工具:
- query_park_overview: 查园区总览 (建筑数 / 总能耗 / 平均 EUI / 异常数 / 同比环比)
- query_building_energy: 查单楼能耗时序 (日 / 周 / 月粒度)
- query_energy_composition: 查能源构成 (各 energy_type 占比 + 绝对值), 节能优化场景必调
- query_energy_drivers: 查楼栋能耗驱动因子 (GBM 特征重要性, 回答"什么在驱动能耗"), 节能优化场景可选调
- query_anomalies: 查异常事件 (单楼列表 / 园区概览)
- search_knowledge: 三路混检知识库 (国标 / 行业标准 / 技术文档), 节能优化场景必调拿条款
"""


_OPTIMIZATION_INSTRUCTION = """

【当前场景: 节能优化】
用户问的是节能优化相关问题。你必须在最终回答阶段用 JSON 格式输出四段结构化数据:

字段:
  summary: string, 一句话总结建筑能耗整体评价
  problems: array, 2-3 个最显著问题, 每个含 problem_desc / evidence_ref / severity (high|medium|low)
  measures: array, 节能措施清单, 每个含 measure_name / saved_kwh (number) / saved_co2 (number) /
            payback_months (number) / difficulty (easy|medium|hard) / clause_ref (国标条款引用)
  priorities: array, top 3 优先级建议, 每个含 measure_name / reason

工作流程 (必须按顺序, 跳步会导致回答被后端拒答重试):
1. 调 query_park_overview (园区级) 或 query_building_energy (单楼级) 拿总能耗 + EUI
2. 调 query_energy_composition 拿真实分项能耗占比 (electricity/gas/hotwater/solar/chilledwater 等)
   - evidence_ref 里的分项占比必须引用这个工具的返回, 不要凭"行业通用比例"估算
   - 真实数据按能源载体分, 不是按用途分 (没有"照明""暖通"细分, 只有 electricity/gas/hotwater 等)
3. 调 query_anomalies 拿异常事件 (BASELINE_DEVIATION 类型最相关)
4. (单楼级) 调 query_energy_drivers 拿能耗驱动因子 (GBM 特征重要性, 如"气温占 40%"), 让节能措施针对主要驱动因子
   - 如果该楼没跑过 gbm 预测 (has_prediction=false), 跳过这步, 不要编造驱动因子
5. 调 search_knowledge 检索 GB 55015-2021 / GB 50189 / GB 50034 等节能设计标准
   - clause_ref 字段必须用 search_knowledge 返回的 standard_no + section_title 拼接
   - 不要凭记忆写条款号 (GB 55015 是强条规范, GB 50034 是照明设计标准, 搞混会误导用户)
6. 基于真实数据 + 异常 + 驱动因子 + 国标条款, 给出可落地的节能措施

字段约束 (重要):
- summary: 必须含具体数字 (总能耗 / EUI 等), 数字来自工具返回
- problems[].evidence_ref: 必须引用工具返回的具体数字, 禁止出现"估算/行业通用/大约/约"等词
- measures[].saved_kwh: 必须基于 query_energy_composition 返回的分项数据 × 合理节能率 (5%-30%)
- measures[].saved_co2: 后端会用 saved_kwh × 0.581 强制重算, 你写多少都会被覆盖, 写个占位值即可
- measures[].clause_ref: 必须来自 search_knowledge 返回的 standard_no + section_title, 格式如 "GB 55015-2021 第 X.X.X 条"
- measures[].payback_months: 给合理估值 (LED 12-36 月 / 暖通 24-60 月 / 智能控制 36-72 月)
- measures 至少 3 条, priorities 必须按 "节能率 ÷ 实施难度" 排序

如果用户当前上下文没有 site_id 也没有 building_id, 不要生成节能方案, 直接告诉用户"请先选择园区或建筑"。
"""


_ANOMALY_INSTRUCTION = """

【当前场景: 异常诊断】
用户问的是异常相关问题。工作流程:
1. 调 query_anomalies 拿异常事件列表 (含 event_type / severity / observed_value / baseline_value)
2. 对 BASELINE_DEVIATION 类异常, 调 search_knowledge 检索相关标准条款作为参考
3. 综合分析异常原因 (设备故障 / 调度异常 / 用能模式变化 / 外部天气等)
4. 给出排查建议 (先查什么 / 再查什么)

回答要包含具体事件 ID 和观测值 / 基线值对比, 让用户能定位到具体异常。
"""


_STANDARD_INSTRUCTION = """

【当前场景: 标准查询】
用户问的是标准 / 规范相关内容。必须调 search_knowledge 工具检索知识库, 不要凭记忆回答
(国标条款记忆可能过时或记错, 必须以工具检索结果为准)。

回答格式:
1. 引用标准号 (如 "GB 55015-2021")
2. 引用条款号 (如 "第 3.3.1 条")
3. 引用原文片段 (用引号包裹)
4. 必要时给一句通俗解释
"""


_DATA_INSTRUCTION = """

【当前场景: 数据问答】
用户问的是能耗 / 用电数据。工作流程:
1. 调 query_park_overview (园区问题) 或 query_building_energy (单楼问题) 拿数据
2. 必要时调 query_anomalies 补充异常信息
3. 用工具返回的真实数字回答, 标注时间范围

回答要包含: 时间范围 / 总能耗 / 峰值 / 异常数 (如果有) 等具体数字。
"""


def build_system_prompt(context: dict | None, scenario: str) -> str:
    """拼 system prompt: 基础指令 + 上下文 + 场景指令。"""
    parts = [_BASE_SYSTEM]

    # 上下文注入: 让 LLM 知道用户当前在看什么
    if context:
        ctx_lines = []
        # site_id 有就注入, site_name 没有就用 site_id 兜底 (前端漏传 name 也能让 LLM 看到 ID)
        if context.get("site_id"):
            ctx_lines.append(
                f"当前园区: {context.get('site_name') or context['site_id']} (site_id={context['site_id']})"
            )
        if context.get("building_id"):
            ctx_lines.append(
                f"当前建筑: {context.get('building_name') or context['building_id']} (building_id={context['building_id']})"
            )
        if context.get("time_range"):
            tr = context["time_range"]
            ctx_lines.append(f"时间范围: {tr.get('start', '?')} ~ {tr.get('end', '?')}")
        if context.get("metric"):
            ctx_lines.append(f"当前指标: {context['metric']}")
        if ctx_lines:
            parts.append("\n用户当前上下文 (你调工具时尽量用这些参数, 不要再问):\n" + "\n".join(ctx_lines))

    # 场景指令
    if scenario == SCENARIO_OPTIMIZATION:
        parts.append(_OPTIMIZATION_INSTRUCTION)
    elif scenario == SCENARIO_ANOMALY:
        parts.append(_ANOMALY_INSTRUCTION)
    elif scenario == SCENARIO_STANDARD:
        parts.append(_STANDARD_INSTRUCTION)
    else:
        parts.append(_DATA_INSTRUCTION)

    return "".join(parts)


# ---------------------------------------------------------------------------
# 单轮 function call 决策
# ---------------------------------------------------------------------------


def plan(
    user_msg: str,
    context: dict | None,
    history: list[dict],
    user_id: str | None = None,
    api_key: str | None = None,
) -> AgentPlan:
    """单轮 function call 决策: 调一次 LLM (带 tools), 拿 tool_calls。

    重试逻辑: GlmToolCallError (LLM 返了 tool_calls 但格式错) 时重试,
    最多 settings.glm_tool_call_retry 次。其他异常 (Key/限流/网络) 直接抛。
    """
    scenario = decide_scenario(user_msg, context)
    system = build_system_prompt(context, scenario)
    messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": user_msg}]

    retry = 0
    last_err: Exception | None = None
    while retry <= settings.glm_tool_call_retry:
        try:
            resp: GlmResponse = llm_service.chat(
                messages=messages,
                tools=TOOL_SCHEMAS,
                user_id=user_id,
                api_key=api_key,
            )
            logger.info(
                "agent plan ok scenario={} tools={} finish={}",
                scenario, len(resp.tool_calls), resp.finish_reason,
            )
            return AgentPlan(
                tool_calls=resp.tool_calls,
                finish_reason=resp.finish_reason,
                raw_content=resp.content,
                usage=resp.usage,
            )
        except GlmToolCallError as e:
            last_err = e
            retry += 1
            logger.warning("tool_call 解析失败, 重试 {}/{}: {}", retry, settings.glm_tool_call_retry, e)
        # GlmApiKeyError / GlmRateLimitError / GlmApiError 直接抛, 不重试

    raise GlmApiError(
        f"tool_call 决策重试 {settings.glm_tool_call_retry} 次仍失败: {last_err}",
        detail=str(last_err)[:200] if last_err else "",
    )


# ---------------------------------------------------------------------------
# 流式版单轮 function call 决策 (Step 重构: plan 阶段也流式)
# ---------------------------------------------------------------------------


def plan_stream(
    user_msg: str,
    context: dict | None,
    history: list[dict],
    user_id: str | None = None,
    api_key: str | None = None,
    extra_hint: str | None = None,
) -> Iterator[dict]:
    """流式版 plan: 调 chat_stream_with_tools, yield 事件给上层转发给前端。

    跟 plan() 的区别: plan() 是非流式, 整个 plan 期间 (GLM-4.5 + tools 通常 30-60s)
    前端只看到静态 "思考中", 体感很差。plan_stream 把 LLM 第一轮的 content delta
    实时 yield 出去, 前端能看到 LLM 在思考什么 (或 LLM 直接回答时就是最终回答)。

    extra_hint: 追加在 user 消息后的一条额外 user 消息。qa_service 在节能优化场景
    发现 LLM 跳过工具直接回答时, 用它注入强制调工具的指令重跑一轮。

    yield 事件:
      - {"type": "plan_delta", "text": "..."}: LLM 第一轮 content 增量。
        LLM 决定调工具时通常无 delta (GLM 工具决策阶段不吐 content);
        LLM 直接回答时 delta 拼起来就是完整回答。
      - {"type": "plan_done", "tool_calls": [...], "raw_content": "..."}:
        流结束。tool_calls 空 + raw_content 非空 -> LLM 直接回答了, 上层
        不再调第二阶段 LLM, 直接持久化 raw_content。

    重试逻辑跟 plan() 一致: GlmToolCallError (arguments JSON 解析失败) 重试,
    最多 settings.glm_tool_call_retry 次。
    """
    scenario = decide_scenario(user_msg, context)
    system = build_system_prompt(context, scenario)
    messages = [{"role": "system", "content": system}] + history + [{"role": "user", "content": user_msg}]
    if extra_hint:
        messages.append({"role": "user", "content": extra_hint})

    retry = 0
    last_err: Exception | None = None
    while retry <= settings.glm_tool_call_retry:
        try:
            final_event = None
            for event in llm_service.chat_stream_with_tools(
                messages=messages,
                tools=TOOL_SCHEMAS,
                user_id=user_id,
                api_key=api_key,
            ):
                if event.type == "delta":
                    yield {"type": "plan_delta", "text": event.text}
                elif event.type == "final":
                    final_event = event

            if final_event is None:
                raise GlmApiError("GLM 流式未返 final 事件, 无法继续")

            logger.info(
                "agent plan_stream ok scenario={} tools={} finish={} content_len={}",
                scenario, len(final_event.tool_calls),
                final_event.finish_reason, len(final_event.raw_content),
            )
            yield {
                "type": "plan_done",
                "tool_calls": final_event.tool_calls,
                "finish_reason": final_event.finish_reason,
                "raw_content": final_event.raw_content,
                "usage": final_event.usage,
            }
            return
        except GlmToolCallError as e:
            last_err = e
            retry += 1
            logger.warning("plan_stream tool_call 解析失败, 重试 {}/{}: {}", retry, settings.glm_tool_call_retry, e)
            # 重试时不发 plan_delta (上一轮的 partial delta 已经 yield 给前端了,
            # 重试是新一轮, 前端会看到内容继续 append, 这是已知小瑕疵, 不影响功能)

    raise GlmApiError(
        f"plan_stream 重试 {settings.glm_tool_call_retry} 次仍失败: {last_err}",
        detail=str(last_err)[:200] if last_err else "",
    )


# ---------------------------------------------------------------------------
# 工具调用结果 -> LLM messages
# ---------------------------------------------------------------------------


def build_tool_result_messages(
    user_msg: str,
    context: dict | None,
    history: list[dict],
    tool_calls: list[dict],
    tool_results: list[dict],
    scenario: str,
) -> list[dict]:
    """把第一阶段结果拼成第二阶段 messages, 用于调 LLM 生成最终回答。

    消息序列:
      [system, ...history, user_msg, assistant(tool_calls), tool(result_1), tool(result_2), ...]

    OpenAI 工具调用消息格式:
      - assistant 消息含 tool_calls 字段
      - 每个工具结果用 role=tool, tool_call_id 关联
    """
    system = build_system_prompt(context, scenario)
    messages: list[dict] = [{"role": "system", "content": system}] + history + [{"role": "user", "content": user_msg}]

    if tool_calls:
        # assistant 消息: 标记调了哪些工具
        messages.append({
            "role": "assistant",
            "content": "我将调用以下工具查询数据。",
            "tool_calls": [
                {
                    "id": tc["id"],
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": json.dumps(tc["arguments"], ensure_ascii=False),
                    },
                }
                for tc in tool_calls
            ],
        })
        # 每个工具结果用 role=tool 消息, 按 tool_call_id 关联
        for tc, result in zip(tool_calls, tool_results):
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": json.dumps(result, ensure_ascii=False, default=str),
            })

    return messages


__all__ = [
    "AgentPlan",
    "ALL_SCENARIOS",
    "SCENARIO_ANOMALY",
    "SCENARIO_DATA",
    "SCENARIO_OPTIMIZATION",
    "SCENARIO_STANDARD",
    "build_system_prompt",
    "build_tool_result_messages",
    "decide_scenario",
    "plan",
    "plan_stream",
]
