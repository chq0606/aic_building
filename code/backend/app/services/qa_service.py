"""
问答主流程: SSE 流式生成器, 串联 agent 决策 -> 工具执行 -> 最终回答 -> 持久化。

SSE 事件协议 (每个事件 yield 一条 "data: {json}\\n\\n"):
  - session:        会话 ID, 第一个事件, 前端建 chat session 用
  - tool_calls:     LLM 决定调哪些工具, 前端展开工具调用卡片
  - tool_result:    单个工具执行结果, 前端更新对应卡片状态
  - delta:          LLM 流式回答的增量文本, 前端逐字 append
  - optimization_plan: 节能优化场景的四段结构化 JSON, 前端渲染卡片
  - done:           回答完成, 含 message_id 持久化 ID
  - error:          错误, 含 message + code (key_invalid / rate_limit / glm_error / internal)

流程分支:
  - 节能优化场景: 工具阶段 + 非流式 JSON 输出 (response_format=json_object) +
    重试 + 降级为流式散文
  - 其他场景: 工具阶段 + 流式 chat_stream 逐字 delta

同步生成器 (不用 async): 工具内部是同步 DB 操作, async 没收益;
StreamingResponse 接受 sync generator, FastAPI 用 anyio 线程跑, 不阻塞事件循环。

持久化: 一条 user 消息 + 一条 assistant 消息 (含 tool_calls / citations /
referenced_chunk_ids / optimization_plan) 落 knowledge.assistant_message。
"""
from __future__ import annotations

import json
import queue
import threading
import time
from datetime import datetime, timezone
from typing import Any, Iterator

from loguru import logger

from app.db.session import get_conn
from app.services import agent_service, llm_service
from app.services.agent_service import (
    SCENARIO_ANOMALY,
    SCENARIO_DATA,
    SCENARIO_OPTIMIZATION,
    SCENARIO_STANDARD,
    build_tool_result_messages,
    decide_scenario,
)
from app.services.llm_service import (
    GlmApiError,
    GlmApiKeyError,
    GlmRateLimitError,
)
from app.tools import execute_tool


# ---------------------------------------------------------------------------
# SSE 事件封装
# ---------------------------------------------------------------------------


def _sse(event_type: str, **kwargs: Any) -> str:
    """封装一条 SSE 事件: data: {json}\n\n。"""
    payload = {"type": event_type, **kwargs}
    return f"data: {json.dumps(payload, ensure_ascii=False, default=str)}\n\n"


# ---------------------------------------------------------------------------
# 节能优化 JSON Schema 校验
# ---------------------------------------------------------------------------


_REQUIRED_TOP_KEYS = ("summary", "problems", "measures", "priorities")

# evidence_ref / summary 里禁止出现的"臆想词", 出现就拒答重试
# 不禁单字"约"(会误伤"约 100 栋楼"这种合法精度表达), 只禁明确表示凭记忆估算的词
_HALLUCINATION_WORDS = ("估算", "行业通用", "行业一般", "预估", "推测", "大约", "凭经验", "一般认为")

# CO2 减排系数: 中国电网平均 0.581 kg CO2/kWh (生态环境部 2022 公布值)
# 后端用这个强制重算 saved_co2, 不依赖 LLM 算对
_CO2_FACTOR_KG_PER_KWH = 0.581

# 节能优化场景强制重试时注入的指令 (原因见 stream_answer 阶段 1):
# tool_choice=auto 时 GLM 偶发不调工具直接写散文, 方案没有数据支撑后端必须拒收
_OPTIMIZATION_FORCE_TOOLS_HINT = (
    "提醒: 你上一轮的回答没有调用任何工具, 已被系统拒绝。"
    "节能优化场景必须先调用工具获取真实数据: "
    "query_building_energy (能耗时序), query_energy_composition (分项占比), "
    "query_anomalies (异常事件), search_knowledge (国标条款)。"
    "请从调用工具开始重新按工作流程执行, 不要直接输出文字方案。"
)


def _validate_optimization_plan(
    data: Any,
    tool_results: list[dict] | None = None,
) -> tuple[bool, dict | None, str]:
    """校验节能优化四段结构是否合法。

    返 (ok, parsed_dict, error_msg):
      - ok=True, parsed_dict=数据: 校验通过
      - ok=False, parsed_dict=None, error_msg=原因: 校验失败

    校验项:
      - 顶层 4 个 key 必须都存在
      - summary 是非空字符串, 不能含臆想词
      - problems / measures / priorities 是数组
      - measures 至少 3 条 (提示词第 13 条要求)
      - 每个 measure 含 saved_kwh (number) / clause_ref (string)
      - evidence_ref / summary 不能含臆想词 (估算/行业通用/大约/约/预估/推测)
      - clause_ref 必须在 search_knowledge 工具返回的条款列表里 (如果传了 tool_results)

    tool_results 是阶段 2 的工具执行结果, 用来校验 clause_ref 是否有真实条款支撑。
    不传 (None) 就跳过 clause_ref 校验 (向后兼容)。
    """
    if not isinstance(data, dict):
        return False, None, "顶层不是 JSON 对象"
    for k in _REQUIRED_TOP_KEYS:
        if k not in data:
            return False, None, f"缺字段: {k}"
    if not isinstance(data["summary"], str) or not data["summary"].strip():
        return False, None, "summary 必须是非空字符串"
    if not isinstance(data["problems"], list):
        return False, None, "problems 必须是数组"
    if not isinstance(data["measures"], list):
        return False, None, "measures 必须是数组"
    if not isinstance(data["priorities"], list):
        return False, None, "priorities 必须是数组"
    if len(data["measures"]) < 3:
        return False, None, f"measures 至少 3 条, 实际 {len(data['measures'])}"

    # 臆想词检查: summary + problems[].evidence_ref + problems[].problem_desc
    err = _check_hallucination(data["summary"], "summary")
    if err:
        return False, None, err
    for i, p in enumerate(data["problems"]):
        if not isinstance(p, dict):
            return False, None, f"problems[{i}] 不是对象"
        for field in ("problem_desc", "evidence_ref"):
            val = p.get(field, "")
            if not isinstance(val, str):
                return False, None, f"problems[{i}].{field} 必须是字符串"
            err = _check_hallucination(val, f"problems[{i}].{field}")
            if err:
                return False, None, err

    # 从 tool_results 抽 search_knowledge 返回的条款列表 (用于 clause_ref 校验)
    valid_clauses: list[tuple[str, str]] = []  # [(standard_no, section_title), ...]
    if tool_results:
        for tr in tool_results:
            if tr.get("name") == "search_knowledge":
                result = tr.get("result", {}) or {}
                if result.get("ok", False):
                    for item in result.get("items", []):
                        sn = item.get("standard_no", "")
                        st = item.get("section_title", "")
                        if sn and st:
                            valid_clauses.append((sn, st))

    # measures 校验
    for i, m in enumerate(data["measures"]):
        if not isinstance(m, dict):
            return False, None, f"measures[{i}] 不是对象"
        if "measure_name" not in m or not isinstance(m["measure_name"], str):
            return False, None, f"measures[{i}] 缺 measure_name"
        if "saved_kwh" not in m or not isinstance(m["saved_kwh"], (int, float)):
            return False, None, f"measures[{i}] 缺 saved_kwh 或非数字"
        if "clause_ref" not in m or not isinstance(m["clause_ref"], str):
            return False, None, f"measures[{i}] 缺 clause_ref"

        # clause_ref 必须在 search_knowledge 返回的条款里 (如果调过 search_knowledge)
        if valid_clauses and m["clause_ref"]:
            if not _clause_in_results(m["clause_ref"], valid_clauses):
                return False, None, (
                    f"measures[{i}].clause_ref '{m['clause_ref']}' 不在 search_knowledge 返回的条款里, "
                    f"不能凭记忆写条款号。可用条款: {valid_clauses[:3]}"
                )

    return True, data, ""


def _check_hallucination(text: str, field_name: str) -> str | None:
    """检查文本是否含臆想词, 含了就返错误字符串, 不含返 None。"""
    for word in _HALLUCINATION_WORDS:
        if word in text:
            return f"{field_name} 含臆想词 '{word}', 数据必须有工具支撑不要估算"
    return None


def _clause_in_results(clause_ref: str, valid_clauses: list[tuple[str, str]]) -> bool:
    """检查 LLM 写的 clause_ref 是否在 search_knowledge 返回的条款里。

    clause_ref 形如 "GB 55015-2021 第 3.3.1 条", valid_clauses 是 [(standard_no, section_title)]
    匹配规则: standard_no 完全包含 + section_title 的条款号包含在 clause_ref 里
    (section_title 可能是 "3.3.1 强制性条文" 这种, 取数字部分匹配)。

    比较前先把两边空格全去掉: 知识库 standard_no 是 'GB55015-2021' (PDF 解析产物,
    无空格), LLM 按提示词示例写 'GB 55015-2021' (带空格), 直接子串匹配永远对不上,
    校验会拒掉本来正确的引用
    """
    import re
    ref_norm = clause_ref.replace(" ", "").upper()
    for sn, st in valid_clauses:
        sn_norm = (sn or "").replace(" ", "").upper()
        if sn_norm and sn_norm in ref_norm:
            # 从 section_title 提取条款号 (如 "3.3.1 强制性条文" -> "3.3.1")
            clause_num_match = re.search(r"(\d+\.\d+\.\d+)", st)
            if clause_num_match:
                clause_num = clause_num_match.group(1)
                if clause_num in clause_ref:
                    return True
            # section_title 没有标准条款号格式, 只要 standard_no 匹配就算过 (宽松)
            else:
                return True
    return False


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def stream_answer(
    session_id: str,
    user_msg: str,
    context: dict | None,
    history: list[dict],
    tenant_id: str,
    user_id: str | None = None,
    api_key: str | None = None,
) -> Iterator[str]:
    """SSE 流式主流程生成器。

    yield 字符串, 每条是 "data: {json}\\n\\n"。StreamingResponse 直接吐给前端。
    所有异常都被转成 error 事件, 不让 SSE 流中途断 (前端 EventSource 看到断流会重连,
    但 POST 流不能重连, 必须一次性走完)。

    plan 阶段 (调 LLM 决定调哪些工具) 用 daemon 线程 + queue 跑 plan_stream,
    主线程每 2s 轮询一次事件队列, 队列空时 yield 一次 thinking 心跳。LLM 第一轮
    如果有 content delta (直接回答场景), 实时 yield 给前端, 不再像非流式 plan
    那样整段 60s 静默。
    """
    started_at = time.monotonic()
    scenario = decide_scenario(user_msg, context)
    logger.info("qa_stream start session={} scenario={}", session_id[:8], scenario)

    # 持久化用的累积数据
    accumulated_tool_calls: list[dict] = []
    accumulated_tool_results: list[dict] = []
    accumulated_text = ""
    optimization_plan: dict | None = None

    try:
        # 阶段 1: 流式决策调哪些工具 (带心跳)
        # plan_stream 在子线程跑, 主线程轮询事件队列, 2s 没事件就发 thinking 心跳
        # 让用户知道在动。LLM 第一轮如果有 content delta (直接回答场景), 实时 yield
        # 给前端, 不再像非流式 plan 那样整段 60s 静默。
        #
        # 封装成 _plan_phase 生成器是因为节能优化场景要跑两轮: GLM 跳过工具直接
        # 写散文时, 注入强制指令重跑 (tool_choice=auto 模型可自由决定, 偶发偷懒)。
        plan_result: dict = {}

        def _plan_phase(extra_hint: str | None = None) -> Iterator[str]:
            """跑一轮 plan_stream, yield thinking/delta SSE, plan_done 写进 plan_result。"""
            # delta 累积要写外层的 accumulated_text (直接回答路径持久化用), 没有
            # nonlocal 的话 += 会把它变成本生成器的局部变量, 心跳读时 UnboundLocalError
            nonlocal accumulated_text
            yield _sse("thinking", text="正在分析问题...")
            plan_started = time.monotonic()
            event_q: queue.Queue = queue.Queue()

            def _run_plan_stream():
                try:
                    for evt in agent_service.plan_stream(
                        user_msg=user_msg,
                        context=context,
                        history=history,
                        user_id=user_id,
                        api_key=api_key,
                        extra_hint=extra_hint,
                    ):
                        event_q.put(evt)
                except Exception as e:
                    event_q.put(e)
                finally:
                    event_q.put(None)  # sentinel: 流结束

            plan_thread = threading.Thread(target=_run_plan_stream, daemon=True)
            plan_thread.start()

            while True:
                try:
                    item = event_q.get(timeout=2)
                except queue.Empty:
                    # 2s 没事件, 发心跳带已耗时秒数, 让用户知道在动
                    # 已开始流式回答 (accumulated_text 非空) 就不发心跳, 避免覆盖正在 append 的回答
                    if accumulated_text == "":
                        elapsed = int(time.monotonic() - plan_started)
                        yield _sse("thinking", text=f"正在分析问题... ({elapsed}s)")
                    continue

                if item is None:
                    break  # plan_stream 结束
                if isinstance(item, Exception):
                    # plan_stream 抛了 GlmApiKeyError / GlmRateLimitError / GlmApiError 等,
                    # 直接向上抛, 由外层 try/except 转 SSE error
                    raise item

                if item["type"] == "plan_delta":
                    # LLM 第一轮的 content 增量。LLM 决定调工具时通常无 delta;
                    # LLM 直接回答时 delta 拼起来就是完整回答, 当前端回答增量转发。
                    # 节能优化场景例外: 直接回答一定会被后端拒 (强制重试或降级),
                    # 这段散文不会成为最终答案, 不转发不累积, 免得用户先看到
                    # 一份马上要被扔掉的方案
                    if scenario == SCENARIO_OPTIMIZATION:
                        continue
                    yield _sse("delta", text=item["text"])
                    accumulated_text += item["text"]
                elif item["type"] == "plan_done":
                    plan_result["plan_done"] = item

        for sse in _plan_phase():
            yield sse
        plan_done_event = plan_result.get("plan_done")
        if plan_done_event is None:
            raise GlmApiError("plan_stream 未返 plan_done 事件")

        tool_calls = plan_done_event.get("tool_calls", []) or []
        raw_plan_content = plan_done_event.get("raw_content", "") or ""

        # 节能优化场景: 第一轮没调到工具 -> 注入强制调工具指令重跑一轮,
        # 仍不调才走下面的降级提示 (不用 LLM 编的散文)
        if scenario == SCENARIO_OPTIMIZATION and not tool_calls and raw_plan_content:
            logger.warning(
                "qa_stream optimization 直接回答被拦截, 强制重试 session={} first_content_len={}",
                session_id[:8], len(raw_plan_content),
            )
            yield _sse("thinking", text="AI 跳过了数据查询, 正在强制重试...")
            for sse in _plan_phase(_OPTIMIZATION_FORCE_TOOLS_HINT):
                yield sse
            plan_done_event = plan_result.get("plan_done")
            if plan_done_event is None:
                raise GlmApiError("plan_stream 强制重试未返 plan_done 事件")
            tool_calls = plan_done_event.get("tool_calls", []) or []
            raw_plan_content = plan_done_event.get("raw_content", "") or ""

        accumulated_tool_calls = tool_calls

        # 分支: LLM 第一轮直接回答 (tool_calls 空 + raw_content 非空)
        # accumulated_text 已经在 plan_delta 里 yield 给前端了, 不再调第二阶段 LLM
        # 直接跳到持久化。
        if not tool_calls and raw_plan_content:
            if scenario == SCENARIO_OPTIMIZATION:
                # 节能优化场景禁止直接回答, 必须调工具拿真实数据
                # LLM 跳过工具流程 -> 走降级提示, 不用 LLM 编的散文
                logger.warning(
                    "qa_stream optimization 直接回答被拦截 session={} content_len={}",
                    session_id[:8], len(raw_plan_content),
                )
                # 清掉之前 yield 给前端的 plan_delta (LLM 编的散文), 用降级提示覆盖
                accumulated_text = (
                    "⚠️ 节能优化场景需要先调用工具拿真实数据 (能耗查询 / 能源构成 / 知识库检索), "
                    "但本轮 AI 没调到工具, 拒绝生成可能含臆想数据的方案。\n\n"
                    "可能原因:\n"
                    "- 知识库未上传相关节能标准文档 (如 GB 55015-2021 / GB 50034)\n"
                    "- 问题表述不够明确, AI 不知道该调哪些工具\n\n"
                    "建议:\n"
                    "- 在知识库页上传国标 PDF (节能优化必须基于真实条款)\n"
                    "- 换个问法, 如 '基于异常情况给节能建议' 或 '这栋楼有什么节能空间'"
                )
                yield _sse("delta", text="\n\n" + accumulated_text)
            else:
                # 其他场景直接回答 OK
                logger.info("qa_stream plan 直接回答, 跳过工具 + 第二阶段 session={}", session_id[:8])
            message_id, _ = _persist_messages(
                session_id=session_id,
                tenant_id=tenant_id,
                user_id=user_id,
                user_msg=user_msg,
                assistant_text=accumulated_text,
                tool_calls=accumulated_tool_calls,
                tool_results=accumulated_tool_results,
                optimization_plan=optimization_plan,
            )
            elapsed_ms = int((time.monotonic() - started_at) * 1000)
            yield _sse("done", message_id=message_id, session_id=session_id, elapsed_ms=elapsed_ms)
            logger.info("qa_stream done (direct answer) session={} elapsed_ms={}", session_id[:8], elapsed_ms)
            return

        # 走工具 + 第二阶段回答
        if tool_calls:
            yield _sse("thinking", text=f"已决定调用 {len(tool_calls)} 个工具, 正在执行...")
        else:
            yield _sse("thinking", text="正在生成回答...")

        # 阶段 2: 执行工具
        if tool_calls:
            yield _sse(
                "tool_calls",
                items=[
                    {"id": tc["id"], "name": tc["name"], "arguments": tc["arguments"]}
                    for tc in tool_calls
                ],
            )
            with get_conn() as conn:
                for tc in tool_calls:
                    result = execute_tool(tc["name"], conn, tenant_id, tc["arguments"])
                    accumulated_tool_results.append({"name": tc["name"], "arguments": tc["arguments"], "result": result})
                    yield _sse(
                        "tool_result",
                        tool_call_id=tc["id"],
                        name=tc["name"],
                        data=result,
                        ok=bool(result.get("ok", False)),
                    )

        # 阶段 3: 生成最终回答
        messages = build_tool_result_messages(
            user_msg=user_msg,
            context=context,
            history=history,
            tool_calls=tool_calls,
            tool_results=accumulated_tool_results,
            scenario=scenario,
        )

        if scenario == SCENARIO_OPTIMIZATION:
            # 节能优化: 非流式 JSON 输出 + 重试 + 降级
            plan_data, full_text, ok = _generate_optimization_json(
                messages,
                tool_results=accumulated_tool_results,
                user_id=user_id,
                api_key=api_key,
            )
            if ok and plan_data:
                optimization_plan = plan_data
                # 把 summary 作为流式 delta 发给前端, 让聊天区显示一句话
                summary = plan_data.get("summary", "")
                yield _sse("delta", text=summary)
                accumulated_text += summary
                yield _sse("optimization_plan", data=plan_data)
            else:
                # 降级: 流式返完整诊断文本
                yield _sse("delta", text="⚠️ 节能优化方案生成失败, 降级为散文回答:\n\n")
                fallback_text = full_text or "无法生成节能方案, 请稍后重试或换个问法"
                yield _sse("delta", text=fallback_text)
                accumulated_text += "⚠️ 节能优化方案生成失败\n\n" + fallback_text
        else:
            # 普通场景: 流式逐字回答
            # 用 daemon 线程 + queue 模式, 2s 没 delta 就发 thinking 心跳
            # 避免输出停顿 (LLM 思考 / prefill) 时前端只看到小光标, 用户以为卡死
            yield _sse("thinking", text="正在生成回答...")
            stream_q: queue.Queue = queue.Queue()
            last_delta_time = time.monotonic()

            def _run_chat_stream():
                try:
                    for delta in llm_service.chat_stream(messages, user_id=user_id, api_key=api_key):
                        stream_q.put(delta)
                except Exception as e:
                    stream_q.put(e)
                finally:
                    stream_q.put(None)

            stream_thread = threading.Thread(target=_run_chat_stream, daemon=True)
            stream_thread.start()

            while True:
                try:
                    item = stream_q.get(timeout=2)
                except queue.Empty:
                    elapsed = int(time.monotonic() - last_delta_time)
                    if accumulated_text == "":
                        yield _sse("thinking", text=f"正在生成回答... ({elapsed}s)")
                    else:
                        yield _sse("thinking", text=f"正在继续生成回答... ({elapsed}s)")
                    continue
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                accumulated_text += item
                yield _sse("delta", text=item)
                last_delta_time = time.monotonic()

        # 阶段 4: 持久化
        message_id, _ = _persist_messages(
            session_id=session_id,
            tenant_id=tenant_id,
            user_id=user_id,
            user_msg=user_msg,
            assistant_text=accumulated_text,
            tool_calls=accumulated_tool_calls,
            tool_results=accumulated_tool_results,
            optimization_plan=optimization_plan,
        )

        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        yield _sse("done", message_id=message_id, session_id=session_id, elapsed_ms=elapsed_ms)
        logger.info("qa_stream done session={} elapsed_ms={}", session_id[:8], elapsed_ms)

    except GlmApiKeyError as e:
        logger.warning("qa_stream key 错: {}", e)
        yield _sse("error", message=str(e), code="key_invalid")
    except GlmRateLimitError as e:
        logger.warning("qa_stream 限流: {}", e)
        yield _sse("error", message=str(e), code="rate_limit")
    except GlmApiError as e:
        logger.warning("qa_stream GLM 错: {}", e)
        yield _sse("error", message=str(e), code="glm_error", detail=getattr(e, "detail", ""))
    except Exception as e:
        logger.exception("qa_stream 未预期异常")
        yield _sse("error", message=f"AI 助手内部错误: {e}", code="internal")


# ---------------------------------------------------------------------------
# 节能优化 JSON 生成 + 重试 + 降级
# ---------------------------------------------------------------------------


def _generate_optimization_json(
    messages: list[dict],
    tool_results: list[dict] | None = None,
    user_id: str | None = None,
    api_key: str | None = None,
) -> tuple[dict | None, str, bool]:
    """调 LLM 输出节能优化 JSON, 解析失败重试, 仍失败降级。

    返 (plan_data, fallback_text, ok):
      - ok=True: plan_data 是解析后的 dict (saved_co2 已用 saved_kwh × 0.581 重算),
                 fallback_text 是 summary 文本
      - ok=False: plan_data=None, fallback_text 是降级用的诊断文本

    校验流程:
      1. 解析 JSON
      2. _validate_optimization_plan 校验结构 + 臆想词 + clause_ref 在 search_knowledge 返回里
      3. CO2 重算: saved_co2 = saved_kwh × 0.581 (强制覆盖 LLM 写的值)
      4. 校验失败 -> 加压 prompt 重试, 最多 settings.glm_tool_call_retry 次
    """
    # 检查是否调过 search_knowledge (没调就拒答, 强制 LLM 调工具拿条款)
    has_search_knowledge = False
    if tool_results:
        for tr in tool_results:
            if tr.get("name") == "search_knowledge":
                has_search_knowledge = True
                break

    retry_count = settings_glm_json_retry()
    last_text = ""
    last_error = ""

    for attempt in range(retry_count + 1):
        try:
            # 第一次没调 search_knowledge -> 直接拒答让 LLM 重试调工具
            # (LLM 跳过工作流程第 4 步, 凭记忆写条款号)
            if not has_search_knowledge:
                last_error = "未调 search_knowledge 工具拿条款, 不能凭记忆写 clause_ref"
                logger.warning("optimization 拒答: {}", last_error)
                # 加压 prompt 让 LLM 调 search_knowledge
                retry_msg = {
                    "role": "user",
                    "content": (
                        "你上一轮没调 search_knowledge 工具就拿国标条款号, 这会记错标准号误导用户。"
                        "请先调 search_knowledge 检索相关节能标准 (如 'GB 55015 节能' 'GB 50034 照明'), "
                        "拿到真实条款后再生成 JSON。clause_ref 必须用工具返回的 standard_no + section_title。"
                    ),
                }
                # 这一轮不调 LLM 生成, 直接走重试逻辑 (上层 plan_stream 会重新决策调工具)
                # 但 _generate_optimization_json 是被动调 LLM, 没法触发工具调用
                # 所以这里直接返降级, 让用户看到错误提示
                continue

            # 第二次起, prompt 加压提示
            if attempt > 0:
                retry_msg = {
                    "role": "user",
                    "content": (
                        f"上次输出无法解析或校验失败: {last_error}\n"
                        f"上次输出: {last_text[:500]}\n"
                        "请严格按 JSON Schema 输出, 不要 markdown 代码块包裹, "
                        "不要在 JSON 前后加任何说明文字。直接输出 { 开头的 JSON。"
                        "注意: evidence_ref 不要出现'估算/行业通用/大约/约/预估/推测'等臆想词, "
                        "每个数字必须有工具数据支撑; clause_ref 必须来自 search_knowledge 返回。"
                    ),
                }
                msgs = messages + [retry_msg]
            else:
                msgs = messages

            resp = llm_service.chat(
                messages=msgs,
                response_format={"type": "json_object"},
                temperature=_settings_glm_temperature_json(),
                user_id=user_id,
                api_key=api_key,
            )
            last_text = resp.content
            # 先 json.loads 整体解析
            try:
                data = json.loads(resp.content)
            except json.JSONDecodeError as e:
                # 试着从 markdown 代码块里提取 (LLM 偶尔不听话加 ```)
                extracted = _extract_json_from_markdown(resp.content)
                if extracted:
                    try:
                        data = json.loads(extracted)
                    except json.JSONDecodeError:
                        last_error = f"JSON 解析失败: {e}"
                        logger.warning("optimization JSON 解析失败 attempt={}: {}", attempt, e)
                        continue
                else:
                    last_error = f"JSON 解析失败: {e}"
                    logger.warning("optimization JSON 解析失败 attempt={}: {}", attempt, e)
                    continue

            ok, parsed, err = _validate_optimization_plan(data, tool_results=tool_results)
            if ok:
                # CO2 强制重算: 用 saved_kwh × 0.581 覆盖 LLM 写的 saved_co2
                for m in parsed.get("measures", []):
                    saved_kwh = m.get("saved_kwh", 0)
                    if isinstance(saved_kwh, (int, float)):
                        m["saved_co2"] = round(saved_kwh * _CO2_FACTOR_KG_PER_KWH, 1)
                logger.info(
                    "optimization JSON 校验通过 attempt={} measures={} (CO2 已重算)",
                    attempt, len(parsed.get("measures", [])),
                )
                return parsed, parsed.get("summary", ""), True
            last_error = err
            logger.warning("optimization JSON 校验失败 attempt={}: {}", attempt, err)

        except (GlmApiKeyError, GlmRateLimitError):
            # 这两类错不重试, 直接返降级
            raise
        except Exception as e:
            last_error = f"GLM 调用异常: {e}"
            logger.warning("optimization GLM 调用失败 attempt={}: {}", attempt, e)

    # 重试用完仍失败, 降级
    logger.warning("optimization JSON 重试 {} 次仍失败, 降级为散文", retry_count)
    fallback = _fallback_text_response(last_text, last_error)
    return None, fallback, False


def _extract_json_from_markdown(text: str) -> str | None:
    """从 ```json ... ``` 代码块里提取 JSON 字符串。"""
    import re
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        return m.group(1)
    # 没找到代码块, 试着找第一个 { 到最后一个 }
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start:end + 1]
    return None


def _fallback_text_response(last_text: str, last_error: str) -> str:
    """节能优化 JSON 生成失败的降级文本。"""
    if last_text:
        return (
            f"未能生成结构化节能方案 ({last_error})。\n\n"
            f"LLM 原始输出:\n{last_text[:800]}"
        )
    return f"未能生成节能方案, 原因: {last_error}"


def settings_glm_json_retry() -> int:
    """从 settings 拿 JSON 重试次数, 兜底 1 次。"""
    from app.core.config import settings as _s
    return getattr(_s, "assistant_json_retry", 1)


def _settings_glm_temperature_json() -> float:
    """从 settings 拿 JSON 模式温度, 兜底 0.3。"""
    from app.core.config import settings as _s
    return getattr(_s, "glm_temperature_json", 0.3)


# ---------------------------------------------------------------------------
# 持久化
# ---------------------------------------------------------------------------


def _persist_messages(
    session_id: str,
    tenant_id: str,
    user_id: str | None,
    user_msg: str,
    assistant_text: str,
    tool_calls: list[dict],
    tool_results: list[dict],
    optimization_plan: dict | None,
) -> tuple[str, str]:
    """落库 user + assistant 两条消息, 返 (assistant_message_id, session_id)。

    user 消息: role=user, content=user_msg, tool_calls=[]
    assistant 消息: role=assistant, content=assistant_text,
                  tool_calls=tool_calls+results 摘要, citations=引用证据,
                  referenced_chunk_ids=从 tool_results 提取,
                  optimization_plan=节能优化场景的 JSON

    不在事务里包是因为: 即使持久化失败, 前端已经看到了流式回答, 用户不影响。
    持久化失败只打 warning, 不让整个流程崩。
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                # user 消息
                cur.execute(
                    """
                    INSERT INTO knowledge.assistant_message
                        (session_id, role, content, tool_calls, citations, referenced_chunk_ids, referenced_point_ids)
                    VALUES (%s, 'user', %s, '[]'::jsonb, '[]'::jsonb, '{}'::uuid[], '{}'::uuid[])
                    """,
                    (session_id, user_msg),
                )

                # 提取引用的 chunk_id 和 point_id (从 tool_results 里捞)
                chunk_ids, point_ids, citations = _extract_references(tool_results)

                # tool_calls 字段: 存执行摘要 (id/name/arguments/result_ok)
                tool_calls_summary = [
                    {
                        "id": tc["id"],
                        "name": tc["name"],
                        "arguments": tc["arguments"],
                        "ok": bool(tr["result"].get("ok", False)) if "result" in tr else False,
                    }
                    for tc, tr in zip(tool_calls, tool_results)
                ]

                # assistant 消息
                # referenced_chunk_ids / referenced_point_ids 是 NOT NULL, 空数组也得显式传
                # (传 NULL 会违反约束, 默认值 '{}'::uuid[] 只在不显式插该列时生效)。
                # ::uuid[] cast 必须加: _extract_references 返回的是 list[str], psycopg2
                # 适配成 text[], 直接插 uuid[] 列报 DatatypeMismatch, 消息整条丢
                cur.execute(
                    """
                    INSERT INTO knowledge.assistant_message
                        (session_id, role, content, tool_calls, citations,
                         referenced_chunk_ids, referenced_point_ids, optimization_plan)
                    VALUES (%s, 'assistant', %s, %s, %s, %s::uuid[], %s::uuid[], %s)
                    RETURNING id
                    """,
                    (
                        session_id,
                        assistant_text,
                        json.dumps(tool_calls_summary, ensure_ascii=False),
                        json.dumps(citations, ensure_ascii=False),
                        chunk_ids,
                        point_ids,
                        json.dumps(optimization_plan, ensure_ascii=False) if optimization_plan else None,
                    ),
                )
                message_id = str(cur.fetchone()[0])

                # 更新 session.updated_at
                cur.execute(
                    "UPDATE knowledge.assistant_session SET updated_at = now() WHERE id = %s",
                    (session_id,),
                )
            conn.commit()
        logger.info("persist ok session={} message_id={}", session_id[:8], message_id[:8])
        return message_id, session_id
    except Exception as e:
        logger.exception("persist 失败 (不影响已返回答)")
        return "", session_id


def _extract_references(tool_results: list[dict]) -> tuple[list[str], list[str], list[dict]]:
    """从工具返回结果里提取 referenced_chunk_ids / referenced_point_ids / citations。

    - search_knowledge 返回的 items 是 chunk 引用, 提取 chunk_id 加入 referenced_chunk_ids
    - query_anomalies 返回的 items 是异常事件, 加入 citations 作为数据证据
    - query_building_energy / query_park_overview 返回的统计数字, 加入 citations 作为数据证据
    """
    chunk_ids: list[str] = []
    point_ids: list[str] = []
    citations: list[dict] = []

    for tr in tool_results:
        name = tr.get("name", "")
        result = tr.get("result", {}) or {}
        if not result.get("ok", False):
            continue

        if name == "search_knowledge":
            for item in result.get("items", []):
                cid = item.get("chunk_id")
                if cid:
                    chunk_ids.append(cid)
                citations.append({
                    "type": "document",
                    "standard_no": item.get("standard_no", ""),
                    "section_title": item.get("section_title", ""),
                    "content_snippet": item.get("content_snippet", "")[:200],
                    "score": item.get("score_final", 0),
                })

        elif name == "query_anomalies":
            for item in result.get("items", []):
                citations.append({
                    "type": "anomaly",
                    "event_id": item.get("id"),
                    "event_type": item.get("event_type"),
                    "severity": item.get("severity"),
                    "observed_value": item.get("observed_value"),
                    "baseline_value": item.get("baseline_value"),
                    "evidence": item.get("evidence", "")[:200],
                })

        elif name == "query_building_energy":
            stats = result.get("stats", {}) or {}
            if stats:
                citations.append({
                    "type": "data",
                    "building_name": result.get("building_name"),
                    "metric": result.get("metric"),
                    "granularity": result.get("granularity"),
                    "stats": stats,
                })

        elif name == "query_park_overview":
            citations.append({
                "type": "data",
                "scope": "park",
                "building_count": result.get("building_count"),
                "total_kwh": result.get("total_kwh"),
                "avg_eui": result.get("avg_eui"),
                "anomaly_count": result.get("anomaly_count"),
            })

    return chunk_ids, point_ids, citations


# ---------------------------------------------------------------------------
# 会话管理
# ---------------------------------------------------------------------------


def create_session(tenant_id: str, user_id: str | None, title: str | None = None, context: dict | None = None) -> dict:
    """创建会话, 返 {id, title, context, created_at}。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO knowledge.assistant_session (tenant_id, user_id, title, context)
                VALUES (%s, %s, %s, %s)
                RETURNING id, title, context, created_at
                """,
                (
                    tenant_id,
                    user_id,
                    title,
                    json.dumps(context or {}, ensure_ascii=False),
                ),
            )
            row = cur.fetchone()
        conn.commit()
    return {
        "id": str(row[0]),
        "title": row[1] or "",
        "context": row[2] if isinstance(row[2], dict) else json.loads(row[2] or "{}"),
        "created_at": row[3].isoformat() if hasattr(row[3], "isoformat") else str(row[3]),
    }


def list_sessions(tenant_id: str, user_id: str | None, limit: int = 50) -> list[dict]:
    """列会话, 按更新时间倒序。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, title, context, created_at, updated_at,
                       (SELECT COUNT(*) FROM knowledge.assistant_message m WHERE m.session_id = s.id) AS message_count
                FROM knowledge.assistant_session s
                WHERE tenant_id = %s AND (user_id = %s OR user_id IS NULL)
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (tenant_id, user_id, limit),
            )
            rows = cur.fetchall()
    return [
        {
            "id": str(r[0]),
            "title": r[1] or "",
            "context": r[2] if isinstance(r[2], dict) else json.loads(r[2] or "{}"),
            "created_at": r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3]),
            "updated_at": r[4].isoformat() if hasattr(r[4], "isoformat") else str(r[4]),
            "message_count": r[5],
        }
        for r in rows
    ]


def get_session(session_id: str, tenant_id: str) -> dict | None:
    """查会话详情, 含权限校验 (必须属于当前租户)。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, tenant_id, user_id, title, context, created_at, updated_at
                FROM knowledge.assistant_session
                WHERE id = %s AND tenant_id = %s
                """,
                (session_id, tenant_id),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return {
        "id": str(row[0]),
        "tenant_id": str(row[1]),
        "user_id": row[2],
        "title": row[3] or "",
        "context": row[4] if isinstance(row[4], dict) else json.loads(row[4] or "{}"),
        "created_at": row[5].isoformat() if hasattr(row[5], "isoformat") else str(row[5]),
        "updated_at": row[6].isoformat() if hasattr(row[6], "isoformat") else str(row[6]),
    }


def list_messages(session_id: str, tenant_id: str, limit: int = 100) -> list[dict]:
    """查会话历史消息, 按时间正序 (前端从上往下显示)。

    权限校验: 先验 session 属于当前租户, 不属于直接返空 list。
    """
    session = get_session(session_id, tenant_id)
    if session is None:
        return []

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT id, session_id, role, content, tool_calls, citations,
                       referenced_chunk_ids, referenced_point_ids,
                       optimization_plan, tokens_used, latency_ms, created_at
                FROM knowledge.assistant_message
                WHERE session_id = %s
                ORDER BY created_at ASC
                LIMIT %s
                """,
                (session_id, limit),
            )
            rows = cur.fetchall()
    return [_row_to_message(r) for r in rows]


def get_message(message_id: str, tenant_id: str) -> dict | None:
    """查单条消息, 含租户校验 (通过 JOIN session 验证 tenant_id)。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT m.id, m.session_id, m.role, m.content, m.tool_calls,
                       m.citations, m.referenced_chunk_ids, m.referenced_point_ids,
                       m.optimization_plan, m.tokens_used, m.latency_ms, m.created_at
                FROM knowledge.assistant_message m
                JOIN knowledge.assistant_session s ON s.id = m.session_id
                WHERE m.id = %s AND s.tenant_id = %s
                """,
                (message_id, tenant_id),
            )
            row = cur.fetchone()
    if row is None:
        return None
    return _row_to_message(row)


def _row_to_message(row) -> dict:
    """SQL 行转 dict, jsonb 字段如果是字符串转 dict。"""
    def _parse(val):
        if val is None:
            return None
        if isinstance(val, (dict, list)):
            return val
        if isinstance(val, str):
            try:
                return json.loads(val)
            except (json.JSONDecodeError, TypeError):
                return val
        return val

    return {
        "id": str(row[0]),
        "session_id": str(row[1]) if len(row) > 1 and row[1] else (str(row[1]) if row[1] else None),
        "role": row[2],
        "content": row[3] or "",
        "tool_calls": _parse(row[4]) or [],
        "citations": _parse(row[5]) or [],
        "referenced_chunk_ids": [str(x) for x in row[6]] if row[6] else [],
        "referenced_point_ids": [str(x) for x in row[7]] if row[7] else [],
        "optimization_plan": _parse(row[8]),
        "tokens_used": row[9],
        "latency_ms": row[10],
        "created_at": row[11].isoformat() if hasattr(row[11], "isoformat") else str(row[11]),
    }
