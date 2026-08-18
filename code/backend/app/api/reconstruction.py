"""
单图重建任务路由 (Step 12)。

六个接口:
  POST   /buildings/{id}/reconstruction/single-photo   创建 PENDING job (202, 写)
  GET    /reconstruction/jobs/{id}/status              查 job 详情 (读)
  GET    /reconstruction/jobs/{id}/preview             取 preview.png 静态图 (读, Step 16 加)
  GET    /reconstruction/jobs                          列 job (读)
  POST   /reconstruction/jobs/{id}/retry               重置 FAILED job 重试 (写)
  DELETE /reconstruction/jobs/{id}                     硬删 job (写)

路由薄壳: 业务在 reconstruction_service。路由层只做:
  - building 存在性校验 (404)
  - photo 文件存在性校验 (400)
  - job 不存在/跨租户转 404
  - service 返 {"error": ...} 转 400
  - 响应包装 (success/error)

5 个 endpoint 路径前缀不统一 (/buildings/... vs /reconstruction/...), 用不带
prefix 的 router + 每个路由写全路径, main.py 挂 prefix="/api/v1"。

preview 接口单独走 FileResponse (不走 success/error 包装), 前端 <img src> 直接用。
job 还在 PENDING/RUNNING 时 preview 文件不存在, 返 404 让前端等。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from loguru import logger
from pathlib import Path

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.core.response import error, success
from app.db.session import get_conn
from app.models.reconstruction import SinglePhotoRequest
from app.services.reconstruction_service import (
    create_job,
    delete_job,
    get_job,
    list_jobs,
    retry_job,
)


router = APIRouter(tags=["reconstruction"])


def _verify_building_access(building_id: str, tenant_id: str) -> None:
    """校验 building 属于当前租户, 不存在抛 404。

    跟 api/visual.py 的同名函数一致, 一期没抽公共模块 (后续如果 reconstruction
    接口越来越多再考虑抽 app/api/_common.py)。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM core.building
                WHERE id = %s::uuid AND tenant_id = %s::uuid
            """, (building_id, tenant_id))
            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=error(message=f"building 不存在或租户越权: {building_id}", code=404),
                )


@router.post(
    "/buildings/{building_id}/reconstruction/single-photo",
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_single_photo(
    building_id: str,
    req: SinglePhotoRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """提交单图重建 job。202 Accepted (异步任务, worker 进程消费)。

    body 字段:
      photo_upload_id: 上传照片返回的 uuid
      length_m / width_m / height_m: 建筑尺寸, 用于 splat 尺度校准
      floors_count: 楼层数
      position_x / position_y: 可空, 园区内坐标 (CesiumJS 渲染时用)
    """
    _verify_building_access(building_id, user.tenant_id)

    try:
        result = create_job(user.tenant_id, building_id, req)
    except FileNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=str(e), code=400),
        )

    logger.info(
        "submit_single_photo tenant={} building={} job={} (queued)",
        user.tenant_id[:8], building_id[:8], result["id"][:8],
    )
    return success(
        data={"job_id": result["id"], "status": "PENDING"},
        message="重建任务已提交, 等待 worker 消费",
    )


@router.get("/reconstruction/jobs/{job_id}/status")
def get_job_status(
    job_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """查 job 状态。前端轮询直到 SUCCEEDED / FAILED。"""
    result = get_job(user.tenant_id, job_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=f"job 不存在或租户越权: {job_id}", code=404),
        )
    return success(data=result)


@router.get("/reconstruction/jobs/{job_id}/preview")
def get_job_preview(
    job_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """取 preview.png 静态图 (Step 16 加)。

    前端 <img src="/api/v1/reconstruction/jobs/{id}/preview"> 直接用。
    走 FileResponse, 不走 success/error 包装, 避免前端拿到 base64 还要解码。

    路径: backend/storage/reconstruction/{job_id}/preview.png。
    job 还在 PENDING/RUNNING 时 preview 不存在, 返 404 让前端等。
    job 失败时也不存在, 同样 404。

    跨租户校验: 先 SELECT 1 FROM reconstruction_job WHERE id + tenant_id,
    拿不到行就 404, 不暴露 job 是否存在给其他租户。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT 1 FROM core.reconstruction_job
                WHERE id = %s::uuid AND tenant_id = %s::uuid
            """, (job_id, user.tenant_id))
            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=error(message=f"job 不存在或租户越权: {job_id}", code=404),
                )

    preview_path = settings.reconstruction_storage_path / job_id / "preview.png"
    if not preview_path.exists() or not preview_path.is_file():
        # job 还在跑或失败时 preview 没生成, 让前端继续轮询
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message="preview 尚未生成, 等待 job 完成后再试", code=404),
        )
    return FileResponse(
        path=str(preview_path),
        media_type="image/png",
        # filename 设了浏览器会触发下载而非内联显示, 这里要内联显示, 不设
    )


@router.get("/reconstruction/jobs")
def list_jobs_endpoint(
    building_id: str | None = Query(None, description="按 building 过滤"),
    status_filter: str | None = Query(
        None,
        alias="status",
        pattern="^(PENDING|RUNNING|SUCCEEDED|FAILED)$",
        description="按状态过滤",
    ),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    user: CurrentUser = Depends(get_current_user),
):
    """列 job。可按 building_id + status 过滤, created_at 倒序。"""
    result = list_jobs(
        tenant_id=user.tenant_id,
        building_id=building_id,
        status=status_filter,
        limit=limit,
        offset=offset,
    )
    return success(data=result)


@router.post("/reconstruction/jobs/{job_id}/retry")
def retry_job_endpoint(
    job_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """手动重试 FAILED job。retry_count 重置为 0, 给 3 次自动重试额度。

    只对 FAILED 状态的 job 有效。其他状态会返 400。
    """
    result = retry_job(user.tenant_id, job_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=f"job 不存在或租户越权: {job_id}", code=404),
        )
    if "error" in result:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=result["error"], code=400),
        )
    logger.info("retry_job_endpoint tenant={} job={} (manual)", user.tenant_id[:8], job_id[:8])
    return success(data=result, message="job 已重置, 重新进入队列")


@router.delete("/reconstruction/jobs/{job_id}")
def delete_job_endpoint(
    job_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """硬删 job + 删磁盘文件目录。

    不删 building_visual_model 记录 (visual_model 独立生命周期)。
    """
    result = delete_job(user.tenant_id, job_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=f"job 不存在或租户越权: {job_id}", code=404),
        )
    logger.info("delete_job_endpoint tenant={} job={}", user.tenant_id[:8], job_id[:8])
    return success(data=result, message="job 及磁盘文件已删除")
