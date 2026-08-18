"""
数据质量报告路由。

3 个接口：
  GET /data-quality/buildings/{building_id}        按 building + time_range 查
  GET /data-quality/batches/{batch_id}              按 batch 查
  GET /data-quality/sites/{site_id}/overview        site 概览（列各 building 摘要）

building / site 接口支持 start / end query 参数指定时间范围，默认最近 7 天。
batch 接口不需要时间范围（一个 batch 本身就界定了数据范围）。

所有接口挂 get_current_user 依赖（demo 用户也能看，质量报告是只读分析）。
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import CurrentUser, get_current_user
from app.core.response import error, success
from app.services.data_quality_service import (
    get_batch_quality,
    get_building_quality,
    get_site_overview,
)


router = APIRouter(prefix="/data-quality", tags=["data-quality"])


def _default_time_range() -> tuple[str, str]:
    """默认时间范围：最近 7 天（UTC）。返回 (start, end) ISO 字符串。"""
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=7)
    return start.strftime("%Y-%m-%dT%H:%M:%S+00:00"), end.strftime("%Y-%m-%dT%H:%M:%S+00:00")


@router.get("/buildings/{building_id}")
def building_quality(
    building_id: str,
    start: str | None = Query(None, description="ISO 时间，默认最近 7 天起点"),
    end: str | None = Query(None, description="ISO 时间，默认最近 7 天终点"),
    user: CurrentUser = Depends(get_current_user),
):
    """按 building + time_range 查 6 个质量指标。"""
    s, e = (start, end) if start and end else _default_time_range()
    try:
        result = get_building_quality(building_id, user.tenant_id, s, e)
    except ValueError as ex:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=str(ex), code=404),
        )
    return success(data=result)


@router.get("/batches/{batch_id}")
def batch_quality(
    batch_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """按 batch 查 6 个质量指标。"""
    try:
        result = get_batch_quality(batch_id, user.tenant_id)
    except ValueError as ex:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=str(ex), code=404),
        )
    return success(data=result)


@router.get("/sites/{site_id}/overview")
def site_overview(
    site_id: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """site 概览：列每个 building 的 completeness + missing_gap_count 摘要。"""
    s, e = (start, end) if start and end else _default_time_range()
    try:
        result = get_site_overview(site_id, user.tenant_id, s, e)
    except ValueError as ex:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=str(ex), code=404),
        )
    return success(data=result)
