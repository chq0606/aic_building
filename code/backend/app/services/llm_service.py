"""
智谱 GLM-4.5 封装: 非流式 chat + 流式 chat_stream + tools function calling。

设计:
  - 用 requests 直调智谱 v4 endpoint, 不引 zhipuai SDK (Step 17 已说明原因)
  - 非流式 chat: 一次性返完整响应, 用于工具决策阶段 (要拿到完整 tool_calls)
  - 流式 chat_stream: SSE 逐 chunk 返 content delta, 给前端逐字显示
  - tools 参数走 OpenAI 兼容格式, GLM v4 支持
  - JSON 模式: response_format={"type": "json_object"}, 节能优化场景用

Key 取数优先级:
  1. 调用方显式传 api_key (测试用)
  2. 调用方传 user_id, 从 settings_service.get_glm_api_key 拿用户级 Key
  3. settings.glm_api_key (.env 兜底, 一般不用)

流式 SSE 解析:
  智谱返 "data: {json}\\n\\n" 格式, 最后一个 chunk 是 "data: [DONE]\\n\\n"。
  每个 chunk 的 choices[0].delta.content 是增量文本, tool_calls 是增量工具调用。
  tool_calls 增量合并: 每个 chunk 可能只返 tool_call 的一部分 (id/name/arguments
  分多次来), 要按 index 累积拼起来。

错误处理:
  - 401: Key 无效 -> GlmApiKeyError
  - 429: 限流 -> GlmRateLimitError
  - 其他 HTTP 非 200: GlmApiError + 状态码
  - 网络超时/连接失败: GlmApiError + detail
  - tool_calls 格式异常 (缺 id/name/arguments): GlmToolCallError, 上层重试

不用 httpx 而用 requests: 项目里 requests 已是 Step 17 依赖, httpx 虽然
支持 async 但本项目 SSE 端点用 sync generator + StreamingResponse 包,
不需要 async client。
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests
from loguru import logger

from app.core.config import settings


# ---------------------------------------------------------------------------
# 异常
# ---------------------------------------------------------------------------


class GlmApiError(Exception):
    """GLM API 调用异常, 带状态码和诊断 detail。"""

    def __init__(self, message: str, status_code: int | None = None, detail: str = ""):
        super().__init__(message)
        self.status_code = status_code
        self.detail = detail


class GlmApiKeyError(GlmApiError):
    """Key 无效 / 未配置, 上层应该提示用户去设置页配 Key。"""


class GlmRateLimitError(GlmApiError):
    """请求被智谱限流, 上层应该提示稍后重试。"""


class GlmToolCallError(GlmApiError):
    """LLM 返了 tool_calls 但格式异常 (缺 id/name/arguments 或 JSON 解析失败)。"""


# ---------------------------------------------------------------------------
# 响应数据结构
# ---------------------------------------------------------------------------


@dataclass
class GlmResponse:
    """非流式调用的解析结果。

    content 是 LLM 自然语言回答 (流式场景由调用方累积 delta, 不用这个字段)。
    tool_calls 是解析后的工具调用列表, 每项 {id, name, arguments(dict)}。
    finish_reason: stop (正常结束) / tool_calls (要调工具) / length (截断)。
    """
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"
    usage: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


@dataclass
class StreamEvent:
    """chat_stream_with_tools 的事件。

    type='delta': 流式 content 增量文本, 给前端逐字显示
                  (LLM 第一轮直接回答时也有 delta, 上层直接当前端回答增量转发)
    type='final': 流结束, 含组装好的 tool_calls (空列表表示 LLM 直接回答)
                  raw_content 是这一轮累积的完整文本, 上层判断走不走第二阶段用
    """
    type: str  # 'delta' | 'final'
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"
    raw_content: str = ""
    usage: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Key 解析
# ---------------------------------------------------------------------------


def resolve_api_key(api_key: str | None = None, user_id: str | None = None) -> str:
    """按优先级解析 GLM Key: 显式传 > 用户级 DB > .env 兜底。

    调用方一般传 user_id, 由 settings_service 拿用户自己配的 Key。
    测试场景直接传 api_key 绕过 DB。
    """
    if api_key:
        return api_key
    if user_id:
        from app.db.session import get_conn
        from app.services.settings_service import get_glm_api_key
        with get_conn() as conn:
            key = get_glm_api_key(conn, user_id)
        if key:
            return key
    if settings.glm_api_key:
        return settings.glm_api_key
    raise GlmApiKeyError(
        "未配置 GLM API Key, 请到系统设置页配置后再使用 AI 助手",
        status_code=None,
    )


# ---------------------------------------------------------------------------
# 请求构造
# ---------------------------------------------------------------------------


def _build_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _build_body(
    messages: list[dict],
    tools: list[dict] | None = None,
    response_format: dict | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    stream: bool = False,
) -> dict:
    body: dict[str, Any] = {
        "model": settings.glm_model,
        "messages": messages,
        "stream": stream,
    }
    if tools:
        body["tools"] = tools
        # tool_choice="auto" 让 LLM 自己决定调不调工具, 不强制
        body["tool_choice"] = "auto"
    if response_format:
        body["response_format"] = response_format
        # JSON 模式关掉思维链。GLM-4.5 思考模型 reasoning_content 也算在
        # max_tokens 输出预算里, 长上下文 + 复杂 schema 时思维链能把 2000 token
        # 预算耗光, content 返空串 ("JSON 解析失败: char 0")。关掉后输出直接
        # 是 JSON, 还省 10-20s 的思考时间
        body["thinking"] = {"type": "disabled"}
    body["max_tokens"] = max_tokens or settings.glm_max_tokens
    body["temperature"] = (
        temperature if temperature is not None else settings.glm_temperature
    )
    # 限频相关, 让 LLM 知道当前会话的一些元信息 (可选)
    body["stream_options"] = {"include_usage": True}
    return body


# ---------------------------------------------------------------------------
# HTTP 调用
# ---------------------------------------------------------------------------


def _post(url: str, headers: dict, body: dict, stream: bool = False) -> requests.Response:
    """统一 POST, 处理超时/网络错误。stream=True 时返 Response 让上层 iter。

    timeout 用 (connect, read) 元组:
      - connect: TCP 握手 + TLS 建立超时, 智谱 API 偶尔卡握手, 10s 没建连就报错
      - read: 建连后等响应的超时。非流式是"发送完请求到收到响应"的总时间;
              流式是"两次 chunk 之间"的间隔超时 (不是总时间)
    这样分离避免连接阶段卡死时白等 60s, 同时允许 LLM 推理慢 (120s/180s)。
    """
    connect_timeout = settings.glm_connect_timeout_seconds
    read_timeout = settings.glm_stream_timeout_seconds if stream else settings.glm_request_timeout_seconds
    timeout = (connect_timeout, read_timeout)
    try:
        return requests.post(url, headers=headers, json=body, stream=stream, timeout=timeout)
    except requests.exceptions.Timeout as e:
        raise GlmApiError(
            f"GLM API 请求超时 (connect={connect_timeout}s, read={read_timeout}s)",
            status_code=None,
            detail=str(e)[:200],
        )
    except requests.exceptions.ConnectionError as e:
        raise GlmApiError(
            "无法连接智谱 API, 检查网络或服务状态",
            status_code=None,
            detail=str(e)[:200],
        )


def _check_http_error(resp: requests.Response) -> None:
    """HTTP 非 200 转 GlmApiError 子类, 带诊断 detail。"""
    if resp.status_code == 200:
        return
    detail = ""
    try:
        err_body = resp.json()
        detail = json.dumps(err_body, ensure_ascii=False)[:300]
    except Exception:
        detail = (resp.text or "")[:300]

    if resp.status_code == 401:
        raise GlmApiKeyError("GLM API Key 无效或已过期", resp.status_code, detail)
    if resp.status_code == 403:
        raise GlmApiKeyError("GLM API Key 无权限调用此模型", resp.status_code, detail)
    if resp.status_code == 429:
        raise GlmRateLimitError("GLM API 请求被限流, 稍后重试", resp.status_code, detail)
    raise GlmApiError(
        f"GLM API 返 HTTP {resp.status_code}",
        resp.status_code,
        detail,
    )


# ---------------------------------------------------------------------------
# 非流式: chat
# ---------------------------------------------------------------------------


def chat(
    messages: list[dict],
    tools: list[dict] | None = None,
    response_format: dict | None = None,
    max_tokens: int | None = None,
    temperature: float | None = None,
    api_key: str | None = None,
    user_id: str | None = None,
) -> GlmResponse:
    """非流式调用 GLM, 一次性返完整响应。

    工具决策阶段用这个 (要拿完整 tool_calls), 节能优化 JSON 模式也用这个
    (response_format=json_object 模式不支持流式, GLM 会一次性返完整 JSON)。
    """
    key = resolve_api_key(api_key, user_id)
    headers = _build_headers(key)
    body = _build_body(
        messages,
        tools=tools,
        response_format=response_format,
        max_tokens=max_tokens,
        temperature=temperature,
        stream=False,
    )

    started = time.monotonic()
    resp = _post(settings.glm_api_url, headers, body, stream=False)
    _check_http_error(resp)

    try:
        data = resp.json()
    except Exception as e:
        raise GlmApiError(
            "GLM 响应 JSON 解析失败",
            status_code=resp.status_code,
            detail=str(e)[:200],
        )

    elapsed_ms = int((time.monotonic() - started) * 1000)
    return _parse_response(data, elapsed_ms)


def _parse_response(data: dict, elapsed_ms: int) -> GlmResponse:
    """解析非流式响应, 提取 content / tool_calls / finish_reason / usage。"""
    choices = data.get("choices") or []
    if not choices:
        raise GlmApiError(
            "GLM 响应缺少 choices 字段",
            detail=json.dumps(data, ensure_ascii=False)[:300],
        )
    choice = choices[0]
    msg = choice.get("message", {}) or {}

    content = msg.get("content") or ""
    finish_reason = choice.get("finish_reason", "stop")
    tool_calls = _parse_tool_calls(msg.get("tool_calls") or [])

    usage = data.get("usage") or {}
    logger.info(
        "GLM chat ok: finish={} tools={} tokens={} elapsed_ms={}",
        finish_reason, len(tool_calls), usage.get("total_tokens"), elapsed_ms,
    )
    return GlmResponse(
        content=content,
        tool_calls=tool_calls,
        finish_reason=finish_reason,
        usage=usage,
        raw=data,
    )


def _parse_tool_calls(raw_tool_calls: list[dict]) -> list[dict]:
    """解析 GLM 返的 tool_calls 数组, 每项转成 {id, name, arguments(dict)}。

    GLM 兼容 OpenAI 格式: tool_calls=[{id, type="function", function:{name, arguments(JSON str)}}]。
    arguments 是 JSON 字符串, 解析成 dict 给后端工具用。解析失败抛 GlmToolCallError,
    上层重试一次。
    """
    parsed: list[dict] = []
    for raw in raw_tool_calls:
        func = raw.get("function") or {}
        name = func.get("name")
        if not name:
            raise GlmToolCallError(
                f"tool_call 缺 name 字段: {raw}",
                detail=json.dumps(raw, ensure_ascii=False)[:200],
            )
        args_json = func.get("arguments") or "{}"
        try:
            args = json.loads(args_json) if isinstance(args_json, str) else args_json
        except json.JSONDecodeError as e:
            raise GlmToolCallError(
                f"tool_call {name} 的 arguments JSON 解析失败: {e}",
                detail=args_json[:200] if isinstance(args_json, str) else "",
            )
        parsed.append({
            "id": raw.get("id") or f"call_{name}_{len(parsed)}",
            "name": name,
            "arguments": args,
        })
    return parsed


# ---------------------------------------------------------------------------
# 流式: chat_stream
# ---------------------------------------------------------------------------


def chat_stream(
    messages: list[dict],
    tools: list[dict] | None = None,
    api_key: str | None = None,
    user_id: str | None = None,
) -> Iterator[str]:
    """流式调用 GLM, yield content delta 字符串。

    工具调用阶段不用流式 (要拿完整 tool_calls), 调 chat() 即可。
    流式只用于最终回答阶段, 前端逐字显示。

    yield 出来的就是纯文本增量, SSE 包装由调用方 (qa_service) 负责。
    流式中途出错抛 GlmApiError, 调用方捕获后转 SSE error 事件。
    """
    key = resolve_api_key(api_key, user_id)
    headers = _build_headers(key)
    body = _build_body(
        messages,
        tools=tools,
        response_format=None,  # 流式不支持 JSON 模式
        stream=True,
    )

    resp = _post(settings.glm_api_url, headers, body, stream=True)
    _check_http_error(resp)

    # SSE 逐行解析, "data: {json}\\n\\n" 或 "data: [DONE]\\n\\n"
    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("GLM SSE chunk 解析失败, 跳过: {!r}", payload[:100])
            continue
        choices = chunk.get("choices") or []
        if not choices:
            continue
        delta = choices[0].get("delta", {}) or {}
        text = delta.get("content")
        if text:
            yield text


# ---------------------------------------------------------------------------
# 流式 + tools: chat_stream_with_tools
# ---------------------------------------------------------------------------


def chat_stream_with_tools(
    messages: list[dict],
    tools: list[dict] | None = None,
    api_key: str | None = None,
    user_id: str | None = None,
) -> Iterator[StreamEvent]:
    """流式调用 GLM, 同时处理 content delta 和 tool_calls 增量合并。

    用于 agent_service.plan_stream: plan 阶段也用流式, 前端能看到 LLM 思考过程的
    增量文本 (如果 LLM 第一轮直接回答) 或 tool_calls 决策完成后立刻进入工具阶段,
    不再像非流式那样整个 plan 期间前端只看到静态 "思考中"。

    yield 两种 StreamEvent:
      - type='delta': content 增量文本。LLM 决定调工具时通常无 delta; LLM 直接回答时
                      delta 拼起来就是完整回答, 上层直接当最终回答增量转发给前端。
      - type='final': 流结束。tool_calls 是组装好的列表 (空表示 LLM 直接回答, 上层
                      用 raw_content 作为最终回答, 不再调第二阶段)。

    tool_calls 增量合并: GLM 兼容 OpenAI 流式格式, 每个 chunk 的 delta.tool_calls
    里每项含 index, 同 index 的 id/function.name 在第一个 chunk 出现, function.arguments
    跨多个 chunk 分片返回 (字符串拼接)。流结束时把 arguments 字符串 json.loads 成 dict。
    """
    key = resolve_api_key(api_key, user_id)
    headers = _build_headers(key)
    body = _build_body(
        messages,
        tools=tools,
        response_format=None,  # 流式不支持 JSON 模式
        stream=True,
    )

    resp = _post(settings.glm_api_url, headers, body, stream=True)
    _check_http_error(resp)

    # 按 index 累积 tool_call, 同 index 的 id/name 在首个 chunk, arguments 分片拼
    # 用 dict 不用 list 是因为 index 可能不连续 (理论上), 按 index 排序输出
    tool_call_acc: dict[int, dict] = {}
    accumulated_content = ""
    finish_reason = "stop"
    usage: dict = {}

    for line in resp.iter_lines(decode_unicode=True):
        if not line:
            continue
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            logger.warning("GLM SSE chunk 解析失败, 跳过: {!r}", payload[:100])
            continue

        # usage 在最后一个 chunk (stream_options.include_usage=True 时)
        if chunk.get("usage"):
            usage = chunk["usage"]

        choices = chunk.get("choices") or []
        if not choices:
            continue
        choice = choices[0]
        delta = choice.get("delta", {}) or {}

        # 1. content 增量: yield 给上层
        text = delta.get("content")
        if text:
            accumulated_content += text
            yield StreamEvent(type="delta", text=text)

        # 2. tool_calls 增量: 按 index 累积, 不 yield (上层只关心最终组装结果)
        raw_tcs = delta.get("tool_calls") or []
        for raw in raw_tcs:
            idx = raw.get("index", 0)
            if idx not in tool_call_acc:
                tool_call_acc[idx] = {
                    "id": "",
                    "name": "",
                    "arguments_parts": [],
                }
            acc = tool_call_acc[idx]
            if raw.get("id"):
                acc["id"] = raw["id"]
            func = raw.get("function") or {}
            if func.get("name"):
                acc["name"] = func["name"]
            if func.get("arguments"):
                # arguments 是分片字符串, 拼起来
                acc["arguments_parts"].append(func["arguments"])

        # 3. finish_reason (最后一个 chunk 才有)
        fr = choice.get("finish_reason")
        if fr:
            finish_reason = fr

    # 组装最终 tool_calls: 按 index 排序, arguments 字符串拼起来 json.loads
    final_tool_calls: list[dict] = []
    for idx in sorted(tool_call_acc.keys()):
        acc = tool_call_acc[idx]
        args_str = "".join(acc["arguments_parts"])
        try:
            args = json.loads(args_str) if args_str else {}
        except json.JSONDecodeError as e:
            raise GlmToolCallError(
                f"流式 tool_call {acc['name']} arguments JSON 解析失败: {e}",
                detail=args_str[:200],
            )
        if not acc["name"]:
            raise GlmToolCallError(
                f"流式 tool_call 缺 name 字段: index={idx}",
                detail=f"id={acc['id']} args={args_str[:200]}",
            )
        final_tool_calls.append({
            "id": acc["id"] or f"call_{acc['name']}_{idx}",
            "name": acc["name"],
            "arguments": args,
        })

    logger.info(
        "GLM stream+tools ok: finish={} tools={} content_len={}",
        finish_reason, len(final_tool_calls), len(accumulated_content),
    )

    yield StreamEvent(
        type="final",
        tool_calls=final_tool_calls,
        finish_reason=finish_reason,
        raw_content=accumulated_content,
        usage=usage,
    )
