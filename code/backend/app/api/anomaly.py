"""
异常检测 API 路由。

4 个接口:
  POST /anomalies/detect                      触发检测
  GET  /anomalies/buildings/{building_id}     单楼异常列表 (只读)
  GET  /anomalies/sites/{site_id}/overview    site 异常概览 (只读)
  GET  /anomalies/{anomaly_id}/evidence        单条异常证据链 (只读, AI 助手用)

路由声明顺序: /buildings/{id} / /sites/{id}/overview 必须在 /{anomaly_id}/evidence
之前, 避免 'buildings' 或 'sites' 被当成 anomaly_id 解析。

异常检测不改源能耗数据, 只写 anomaly 事件表 (分析结果), demo 账号也可触发 -
让 demo 用户能体验"改阈值重跑检测"等完整流程。源数据相关 (upload/imports/seed)
才拦 demo。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import CurrentUser, get_current_user
from app.core.response import error, success
from app.services.anomaly_service import (
    NotFoundError,
    detect_anomalies,
    get_anomaly_evidence,
    get_site_anomaly_overview,
    list_anomalies_for_building,
)
from app.services.query_service import NotFoundError as QueryNotFoundError


router = APIRouter(prefix="/anomalies", tags=["anomalies"])

ALL_EVENT_TYPES = ("SPIKE", "DRIFT", "PROLONGED_ZERO", "MISSING_GAP", "SCHEDULE_VIOLATION", "BASELINE_DEVIATION", "ML_OUTLIER")


@router.post("/detect")
def trigger_detect(
    building_id: str | None = Query(None, description="不传则跑整个 site"),
    site_id: str | None = Query(None, description="building_id 为空时必传"),
    start: str | None = Query(None, description="ISO8601, 不传按租户实际数据范围"),
    end: str | None = Query(None, description="ISO8601, 不传按租户实际数据范围"),
    event_types: str | None = Query(
        None,
        description="逗号分隔的检测类型, 不传全部 7 类。如: SPIKE,DRIFT",
    ),
    user: CurrentUser = Depends(get_current_user),
):
    """触发异常检测。同步执行, 返回检测汇总。"""
    types_list = None
    if event_types:
        types_list = [t.strip() for t in event_types.split(",") if t.strip()]

    try:
        result = detect_anomalies(
            tenant_id=user.tenant_id,
            building_id=building_id,
            site_id=site_id,
            start=start,
            end=end,
            event_types=types_list,
        )
    except QueryNotFoundError as ex:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=str(ex), code=404),
        )
    except ValueError as ex:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=str(ex), code=400),
        )
    return success(data=result)


@router.get("/buildings/{building_id}")
def list_building_anomalies(
    building_id: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    event_type: str | None = Query(None, description=f"可选: {ALL_EVENT_TYPES}"),
    anomaly_status: str | None = Query(None, description="可选: open / ack / closed"),
    limit: int = Query(100, ge=1, le=500),
    user: CurrentUser = Depends(get_current_user),
):
    """查 building 的异常列表。"""
    try:
        result = list_anomalies_for_building(
            building_id=building_id,
            tenant_id=user.tenant_id,
            start=start,
            end=end,
            event_type=event_type,
            status=anomaly_status,
            limit=limit,
        )
    except NotFoundError as ex:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=str(ex), code=404),
        )
    except ValueError as ex:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=str(ex), code=400),
        )
    return success(data=result)


@router.get("/sites/{site_id}/overview")
def site_overview(
    site_id: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """site 异常概览: 总数 + 按类型/严重度分组 + 每栋楼摘要。"""
    try:
        result = get_site_anomaly_overview(
            site_id=site_id,
            tenant_id=user.tenant_id,
            start=start,
            end=end,
        )
    except NotFoundError as ex:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=str(ex), code=404),
        )
    except ValueError as ex:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=str(ex), code=400),
        )
    return success(data=result)


@router.get("/{anomaly_id}/evidence")
def anomaly_evidence(
    anomaly_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """查单条异常的证据链。AI 助手按异常 ID 检索时用。"""
    try:
        result = get_anomaly_evidence(anomaly_id, user.tenant_id)
    except NotFoundError as ex:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=str(ex), code=404),
        )
    return success(data=result)
