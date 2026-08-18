"""
统一响应结构。

全局约束要求所有 API 返回 {code, message, data} 或 {code, message, errors}。
这里提供两个工具函数，路由层用 success(...) / error(...) 包装，避免每个
路由手写 dict。

code 用业务码，0=成功，非 0=失败。HTTP 状态码由 FastAPI 异常机制管，
和这里的 code 解耦——这样前端可以同时拿 HTTP 状态做兜底，也用业务码
做细分错误处理。
"""
from typing import Any


def success(data: Any = None, message: str = "ok") -> dict:
    return {"code": 0, "message": message, "data": data}


def error(message: str, code: int = -1, errors: Any = None) -> dict:
    return {"code": code, "message": message, "errors": errors}
