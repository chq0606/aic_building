"""
Step 18 AI 抽屉的请求/响应 Pydantic 模型。

跟其他 step 的 models 一样, 用 Pydantic v2 做请求体校验 + 文档自动生成。
响应结构走统一的 {code, message, data} envelope (response.success/error 包装),
这里只列请求体 + 必要的响应 data 子结构。
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    title: str | None = Field(None, description="会话标题, 不填默认按首条问题自动生成")
    context: dict[str, Any] | None = Field(
        None,
        description="会话上下文: {site_id, building_id, time_range, metric}",
    )


class SendMessageRequest(BaseModel):
    """发消息请求体。

    content: 用户输入的文本
    context: 当前上下文覆盖 (前端从 contextStore 拿, 后端拼进 system prompt)
    """
    content: str = Field(..., min_length=1, max_length=4000, description="用户消息内容")
    context: dict[str, Any] | None = Field(
        None,
        description="当前上下文覆盖, 不传则用 session.context",
    )


class SessionResponse(BaseModel):
    id: str
    title: str = ""
    context: dict[str, Any] = {}
    created_at: str
    updated_at: str | None = None
    message_count: int = 0


class MessageResponse(BaseModel):
    id: str
    session_id: str | None = None
    role: str
    content: str
    tool_calls: list[Any] = []
    citations: list[Any] = []
    referenced_chunk_ids: list[str] = []
    referenced_point_ids: list[str] = []
    optimization_plan: dict[str, Any] | None = None
    tokens_used: int | None = None
    latency_ms: int | None = None
    created_at: str


class OptimizationPlanResponse(BaseModel):
    """节能优化结构化数据响应。"""
    message_id: str
    has_plan: bool
    plan: dict[str, Any] | None = None
