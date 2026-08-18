"""
import 路由：触发 merge / 查状态 / 列表 / 回滚。

接口形态（异步）：
  POST /imports/{batch_id}/run   立即返回 status=MERGING，后台线程跑 upsert + aggregate
  GET  /imports/{batch_id}/status 前端轮询查状态
  GET  /imports                  batch 列表
  POST /imports/{batch_id}/rollback 删 fact 数据 + 重算日聚合

并发保护靠 import_batch.status 字段做乐观锁（UPDATE WHERE status='LOADING'），
抢到锁才启动后台线程。重复触发同一 batch 会拿到 status=MERGING 返回 409。

demo 用户能调 /run 和 /rollback 吗？/run 是写 fact，/rollback 是删 fact，
两者都是写操作。挂 require_write_access 让 demo 拦掉。GET 接口只挂 get_current_user。
"""
from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from app.core.deps import CurrentUser, get_current_user, require_write_access
from app.core.response import error, success
from app.services.import_service import (
    get_batch_status,
    list_batches,
    rollback_batch,
    trigger_merge,
)


router = APIRouter(prefix="/imports", tags=["imports"])


@router.post("/{batch_id}/run")
def run_merge(
    batch_id: str,
    user: CurrentUser = Depends(require_write_access),
):
    """
    触发 staging -> fact 的 merge。
    原子 UPDATE ... WHERE status='LOADING' 抢锁，抢到后启动后台线程。
    立即返回 status=MERGING，前端轮询 /status 接口拿结果。
    """
    grabbed = trigger_merge(batch_id, user.tenant_id)
    if not grabbed:
        # 抢锁失败可能是：batch 不存在/不属于本租户、状态不是 LOADING（已被抢或已 SUCCEEDED）
        batch = get_batch_status(batch_id, user.tenant_id)
        if batch is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=error(message="batch 不存在或不属于当前租户", code=404),
            )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error(
                message=f"batch 当前状态 {batch['status']}, 不允许触发 merge (期望 LOADING)",
                code=409,
            ),
        )

    logger.info("用户 {} 触发 merge: batch={}", user.username, batch_id)
    return success(data={"batch_id": batch_id, "status": "MERGING"}, message="merge 已启动,请轮询 status")


@router.get("/{batch_id}/status")
def get_status(
    batch_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """查 batch 状态。前端轮询这个接口。"""
    batch = get_batch_status(batch_id, user.tenant_id)
    if batch is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message="batch 不存在或不属于当前租户", code=404),
        )
    return success(data=batch)


@router.get("")
def list_user_batches(
    status_filter: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    """列出租户的所有 batch。可按 status 过滤。"""
    batches = list_batches(user.tenant_id, status=status_filter)
    return success(data=batches)


@router.post("/{batch_id}/rollback")
def rollback(
    batch_id: str,
    user: CurrentUser = Depends(require_write_access),
):
    """
    回滚：删 fact.point_reading WHERE source_batch_id=batch_id，重算受影响日聚合。
    前置：batch 状态必须是 SUCCEEDED。
    """
    try:
        result = rollback_batch(batch_id, user.tenant_id)
    except ValueError as e:
        # batch 不存在或状态不对
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=str(e), code=400),
        )

    logger.info("用户 {} 回滚 batch={}: {}", user.username, batch_id, result)
    return success(
        data={"batch_id": batch_id, **result, "status": "FAILED"},
        message="回滚完成,fact 数据已删,日聚合已重算",
    )
