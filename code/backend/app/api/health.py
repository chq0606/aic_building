"""
健康检查端点。

/health：进程存活就返回 200，不查任何依赖。给 k8s liveness / 负载均衡用。
/ready：查数据库连通性，连不上返回 503。给 k8s readiness 用——
        DB 没起来时不要让流量进来，避免请求 500 满天飞。
"""
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import settings
from app.core.response import success, error
from app.db.session import ping


router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    """存活探针。只要进程能响应就算活。"""
    return success(data={
        "status": "up",
        "version": settings.app_version,
        "env": settings.env,
        "app_name": settings.app_name,
        "repo_url": settings.app_repo_url,
        "license": settings.app_license,
    })


@router.get("/ready")
def ready():
    """就绪探针。数据库连得上才算就绪。"""
    if ping():
        return success(data={"status": "ready", "db": "ok"})
    # 503 让上游负载均衡把这台实例摘掉
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=error(message="database unavailable", code=503),
    )
