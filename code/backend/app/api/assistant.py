"""
AI 抽屉 (Assistant) API 路由 - Step 18。

7 个端点:
  POST   /assistant/sessions              创建会话
  GET    /assistant/sessions              会话列表
  GET    /assistant/sessions/{id}         会话详情
  POST   /assistant/sessions/{id}/messages   发消息 (SSE 流式)
  GET    /assistant/sessions/{id}/messages   历史消息
  GET    /assistant/messages/{id}/optimization-plan  节能优化结构化数据
  POST   /assistant/messages/{id}/export-pdf        导出节能方案 PDF

权限:
  - 普通查询接口挂 get_current_user (demo 也能用, AI 抽屉是只读分析)
  - PDF 导出走 get_current_user (生成文件不算写操作, demo 允许)
  - 没挂 require_write_access: AI 抽屉本质是查询 + 推理, 不写业务数据

SSE 流式:
  POST /messages 返 StreamingResponse, media_type="text/event-stream"。
  FastAPI 的 StreamingResponse 接受 sync generator, 自动用 anyio 线程跑,
  不会阻塞事件循环。响应头加 Cache-Control / X-Accel-Buffering 让
  nginx 不缓冲 (本机开发无 nginx, 但生产可能有)。

历史 messages 转 LLM 上下文:
  list_messages 拿出 DB 里的消息, 转成 OpenAI messages 格式
  (role + content, 不带 tool_calls 字段, 因为 LLM 不需要看历史工具调用的
  OpenAI 格式, 看自然语言总结够了)。取最近 N 条 (settings.assistant_history_window)。
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from loguru import logger

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.core.response import error, success
from app.models.assistant import (
    CreateSessionRequest,
    SendMessageRequest,
)
from app.services import qa_service
from app.services.qa_service import (
    get_message,
    get_session,
    list_messages,
    list_sessions,
)


router = APIRouter(prefix="/assistant", tags=["assistant"])


# ---------------------------------------------------------------------------
# 会话管理
# ---------------------------------------------------------------------------


@router.post("/sessions")
def create_session(
    req: CreateSessionRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """创建新会话。"""
    session = qa_service.create_session(
        tenant_id=user.tenant_id,
        user_id=user.user_id,
        title=req.title,
        context=req.context,
    )
    return success(data=session)


@router.get("/sessions")
def get_sessions(
    limit: int = Query(50, ge=1, le=200),
    user: CurrentUser = Depends(get_current_user),
):
    """列当前用户的会话, 按更新时间倒序。"""
    sessions = list_sessions(user.tenant_id, user.user_id, limit=limit)
    return success(data={"items": sessions, "total": len(sessions)})


@router.get("/sessions/{session_id}")
def get_session_detail(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """查会话详情。"""
    session = get_session(session_id, user.tenant_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message="会话不存在或不属于当前租户", code=404),
        )
    return success(data=session)


# ---------------------------------------------------------------------------
# 消息发送 (SSE 流式)
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/messages")
def send_message(
    session_id: str,
    req: SendMessageRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """发消息, SSE 流式返回。

    流程:
      1. 校验 session 存在 + 属于当前租户
      2. 拿历史 messages 转成 LLM 上下文 (最近 N 条)
      3. 调 qa_service.stream_answer 生成 SSE 事件流
      4. StreamingResponse 吐给前端, media_type=text/event-stream

    响应头:
      Cache-Control: no-cache - 不缓存 SSE 流
      X-Accel-Buffering: no - nginx 不缓冲
      Connection: keep-alive - 长连接
    """
    # 校验 session 归属
    session = get_session(session_id, user.tenant_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message="会话不存在或不属于当前租户", code=404),
        )

    # 拿历史消息转 LLM 上下文
    history = _load_history_for_llm(session_id, user.tenant_id)

    # context 优先级: 请求体 > session.context
    context = req.context or session.get("context") or {}

    logger.info(
        "send_message session={} user={} history_len={} context_keys={}",
        session_id[:8], user.username, len(history), list(context.keys()),
    )

    # 生成 SSE 流
    sse_stream = qa_service.stream_answer(
        session_id=session_id,
        user_msg=req.content,
        context=context,
        history=history,
        tenant_id=user.tenant_id,
        user_id=user.user_id,
    )

    return StreamingResponse(
        sse_stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            # 前端用 fetch + ReadableStream 读, 不用 EventSource, 这个头是给浏览器看的
            "X-Accel-Charset": "utf-8",
        },
    )


def _load_history_for_llm(session_id: str, tenant_id: str) -> list[dict]:
    """从 DB 拿历史消息, 转成 LLM messages 格式 (role + content)。

    取最近 N 条 (settings.assistant_history_window), 中间用 user/assistant 配对。
    跳过 tool 角色消息 (历史工具调用结果对当前 LLM 决策没价值, 反而占 token)。
    """
    msgs = list_messages(session_id, tenant_id, limit=settings.assistant_history_window * 2)
    history: list[dict] = []
    for m in msgs:
        if m["role"] == "user":
            history.append({"role": "user", "content": m["content"]})
        elif m["role"] == "assistant":
            # assistant 历史只取 content (tool_calls 已经过去, LLM 看自然语言总结)
            history.append({"role": "assistant", "content": m["content"]})
    return history


# ---------------------------------------------------------------------------
# 历史消息
# ---------------------------------------------------------------------------


@router.get("/sessions/{session_id}/messages")
def get_messages(
    session_id: str,
    limit: int = Query(100, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
):
    """查会话历史消息, 按时间正序 (前端从上往下显示)。"""
    # 校验 session 归属
    session = get_session(session_id, user.tenant_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message="会话不存在或不属于当前租户", code=404),
        )
    msgs = list_messages(session_id, user.tenant_id, limit=limit)
    return success(data={"items": msgs, "total": len(msgs)})


# ---------------------------------------------------------------------------
# 节能优化结构化数据
# ---------------------------------------------------------------------------


@router.get("/messages/{message_id}/optimization-plan")
def get_optimization_plan(
    message_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """查单条消息的节能优化结构化数据。

    如果消息没有 optimization_plan 字段 (非节能优化场景或解析失败), 返 404
    让前端知道这条消息没有结构化方案, 不渲染四段卡片。
    """
    msg = get_message(message_id, user.tenant_id)
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message="消息不存在或不属于当前租户", code=404),
        )

    plan = msg.get("optimization_plan")
    if not plan:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message="此消息没有节能优化结构化数据", code=404),
        )
    return success(data={"message_id": message_id, "has_plan": True, "plan": plan})


# ---------------------------------------------------------------------------
# PDF 导出
# ---------------------------------------------------------------------------


@router.post("/messages/{message_id}/export-pdf")
def export_pdf(
    message_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """导出节能方案 PDF。

    流程:
      1. 校验消息存在 + 属于当前租户
      2. 拿 optimization_plan + tool_results (含查询数据)
      3. pdf_export_service 渲染 HTML + matplotlib 图表 -> weasyprint -> PDF bytes
      4. 返 StreamingResponse (application/pdf), 带文件名下载头

    demo 用户也能导出 (生成文件不算写操作), 但 PDF 内容是基于 demo
    数据生成的, 跟普通用户导出自己的数据没区别。
    """
    msg = get_message(message_id, user.tenant_id)
    if msg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message="消息不存在或不属于当前租户", code=404),
        )
    if not msg.get("optimization_plan"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message="此消息没有节能优化方案, 无法导出 PDF", code=400),
        )

    # 延迟 import, weasyprint 加载慢 + 要 DLL PATH 注入, 不用时别加载
    from app.services import pdf_export_service

    try:
        pdf_bytes, filename = pdf_export_service.export_optimization_pdf(
            message_id=message_id,
            message=msg,
            tenant_id=user.tenant_id,
        )
    except Exception as e:
        logger.exception("PDF 导出失败 message_id={}", message_id)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error(message=f"PDF 导出失败: {e}", code=500),
        )

    # 文件名 URL 编码 (RFC 5987), 中文文件名浏览器兼容
    from urllib.parse import quote
    encoded_filename = quote(filename)

    return StreamingResponse(
        iter([pdf_bytes]),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
            "Content-Length": str(len(pdf_bytes)),
        },
    )


__all__ = ["router"]
