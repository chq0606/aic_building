"""
查询与聚合 API 路由。

6 个 GET 接口，全部挂 get_current_user 依赖（demo 用户可读，查询是只读分析）：
  GET /query/park/overview                  园区总览
  GET /query/buildings                      建筑列表
  GET /query/buildings/compare              多楼对比
  GET /query/buildings/{building_id}/timeseries      单楼时序
  GET /query/buildings/{building_id}/weather         天气关联
  GET /query/buildings/{building_id}/energy-composition 能源构成

路由声明顺序：/buildings/compare 在 /buildings/{id}/... 之前，避免 compare 被
当成 building_id 解析（虽然二者路径段数不同，但显式顺序更清晰，避免后续加路由踩坑）。

所有接口支持 start / end ISO8601 时间范围参数。不传时按租户实际数据范围自动定
（demo 自动落到 2017 全年）。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import CurrentUser, get_current_user
from app.core.response import error, success
from app.services.query_service import (
    GRANULARITIES,
    NotFoundError,
    _resolve_range,
    compare_buildings,
    get_building_energy_composition,
    get_building_timeseries,
    get_building_weather,
    get_park_overview,
    get_site_data_range,
    list_buildings,
)


router = APIRouter(prefix="/query", tags=["query"])


@router.get("/sites/{site_id}/data-range")
def site_data_range(
    site_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """园区数据时间范围 (前端用来初始化默认时间范围, 覆盖 '本月' 兜底空数据)."""
    try:
        result = get_site_data_range(site_id, user.tenant_id)
    except NotFoundError as ex:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=str(ex), code=404),
        )
    return success(data=result)


@router.get("/park/overview")
def park_overview(
    site_id: str = Query(..., description="园区 ID"),
    start: str | None = Query(None, description="ISO 时间，不传按租户实际数据范围"),
    end: str | None = Query(None, description="ISO 时间，不传按租户实际数据范围"),
    user: CurrentUser = Depends(get_current_user),
):
    """园区总览：建筑数、总能耗、EUI 均值、异常数、同比/环比。"""
    s, e = _resolve_range(start, end, user.tenant_id)
    try:
        result = get_park_overview(site_id, user.tenant_id, s, e)
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


@router.get("/buildings")
def buildings_list(
    site_id: str = Query(..., description="园区 ID"),
    sort: str = Query("total_kwh", description="排序字段：total_kwh / eui_kwh_per_m2 / anomaly_count / building_code / display_name"),
    limit: int = Query(100, ge=1, le=500, description="返回条数，1-500"),
    start: str | None = Query(None),
    end: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """建筑列表，按指定字段排序。"""
    s, e = _resolve_range(start, end, user.tenant_id)
    try:
        result = list_buildings(site_id, user.tenant_id, s, e, sort, limit)
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


@router.get("/buildings/compare")
def buildings_compare(
    building_ids: str = Query(..., description="逗号分隔的 building_id 列表，最多 10 个"),
    metric: str = Query("interval_energy_kwh", description="对比指标，目前只支持 interval_energy_kwh"),
    start: str | None = Query(None),
    end: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """多楼对比：每栋楼日粒度时序 + 统计摘要。"""
    s, e = _resolve_range(start, end, user.tenant_id)
    ids = [bid.strip() for bid in building_ids.split(",") if bid.strip()]
    try:
        result = compare_buildings(ids, user.tenant_id, metric, s, e)
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


@router.get("/buildings/{building_id}/timeseries")
def building_timeseries(
    building_id: str,
    metric: str = Query("interval_energy_kwh", description="目前只支持 interval_energy_kwh"),
    granularity: str = Query("day", description=f"粒度：{GRANULARITIES}"),
    start: str | None = Query(None),
    end: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """单楼时序查询。"""
    s, e = _resolve_range(start, end, user.tenant_id)
    try:
        result = get_building_timeseries(building_id, user.tenant_id, metric, granularity, s, e)
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


@router.get("/buildings/{building_id}/weather")
def building_weather(
    building_id: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """单楼天气数据（按 building 的 site_id 查）。"""
    s, e = _resolve_range(start, end, user.tenant_id)
    try:
        result = get_building_weather(building_id, user.tenant_id, s, e)
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


@router.get("/buildings/{building_id}/energy-composition")
def building_energy_composition(
    building_id: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """单楼能源构成：各 energy_type 占比。"""
    s, e = _resolve_range(start, end, user.tenant_id)
    try:
        result = get_building_energy_composition(building_id, user.tenant_id, s, e)
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
