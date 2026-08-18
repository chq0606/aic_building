"""
楼层分析 API 路由。

8 个 GET 接口 (只读, demo 用户可读) + 1 个 POST 接口 (写, demo 拦截):
  GET  /buildings/{building_id}/floors                         楼层列表 + 摘要
  GET  /buildings/{building_id}/floors/compare                 楼层对比
  GET  /buildings/{building_id}/floors/composition             能源构成 (饼图)
  GET  /buildings/{building_id}/floors/devices                 设备状态列表
  GET  /buildings/{building_id}/floors/anomalies               异常事件
  GET  /buildings/{building_id}/floors/consistency-check       加总约束自检
  POST /buildings/{building_id}/floors/auto-split              自动拆分楼层 (写)
  GET  /buildings/{building_id}/floors/{floor_id}              单楼层详情
  GET  /buildings/{building_id}/floors/{floor_id}/timeseries   楼层时序

路由声明顺序: 静态路径 (compare / composition / devices / anomalies /
consistency-check / auto-split) 必须在 {floor_id} 之前, 否则 FastAPI 会把
"compare" 当 floor_id 解析, 报 "楼层不存在"。动态路径 {floor_id} 放最后。

时间范围: start / end 都可选, 不传按租户实际数据范围自动定
(demo 自动落到 2017 全年, 复用 query_service._resolve_range)。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.deps import CurrentUser, get_current_user, require_write_access
from app.core.response import error, success
from app.services.floor_service import (
    GRANULARITIES,
    auto_split_floors,
    check_floor_consistency,
    compare_floors,
    get_floor_composition,
    get_floor_detail,
    get_floor_timeseries,
    list_floor_anomalies,
    list_floor_devices,
    list_floors,
)
from app.services.query_service import NotFoundError, _resolve_range


router = APIRouter(tags=["floor"])


def _handle_service_errors(ex: Exception) -> None:
    """统一异常转 HTTP, 避免每个路由重复 try/except。"""
    if isinstance(ex, NotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=str(ex), code=404),
        )
    if isinstance(ex, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=str(ex), code=400),
        )
    raise ex


# ============================================================================
# 静态路径 (必须在 {floor_id} 之前)
# ============================================================================

@router.get("/buildings/{building_id}/floors")
def floors_list(
    building_id: str,
    start: str | None = Query(None, description="ISO 时间, 不传按租户数据范围"),
    end: str | None = Query(None, description="ISO 时间, 不传按租户数据范围"),
    user: CurrentUser = Depends(get_current_user),
):
    """楼层列表 + 每层摘要 (total_kwh / EUI / 设备数 / 故障数)。"""
    s, e = _resolve_range(start, end, user.tenant_id)
    try:
        result = list_floors(building_id, user.tenant_id, s, e)
    except Exception as ex:
        _handle_service_errors(ex)
    return success(data=result)


@router.get("/buildings/{building_id}/floors/compare")
def floors_compare(
    building_id: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    energy_type: str | None = Query(None, description="只看某能源, 不传则全部能源"),
    user: CurrentUser = Depends(get_current_user),
):
    """楼层对比 (同楼不同层 × 能源), 柱状图用。"""
    s, e = _resolve_range(start, end, user.tenant_id)
    try:
        result = compare_floors(building_id, user.tenant_id, s, e, energy_type)
    except Exception as ex:
        _handle_service_errors(ex)
    return success(data=result)


@router.get("/buildings/{building_id}/floors/composition")
def floors_composition(
    building_id: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    floor_id: str | None = Query(None, description="只看某层, 不传则全楼汇总 + 按层细分"),
    user: CurrentUser = Depends(get_current_user),
):
    """楼层能源构成 (饼图), 支持全楼或单层。"""
    s, e = _resolve_range(start, end, user.tenant_id)
    try:
        result = get_floor_composition(building_id, user.tenant_id, s, e, floor_id)
    except Exception as ex:
        _handle_service_errors(ex)
    return success(data=result)


@router.get("/buildings/{building_id}/floors/devices")
def floors_devices(
    building_id: str,
    floor_id: str | None = Query(None, description="过滤楼层"),
    status_filter: str | None = Query(None, alias="status",
                                      description="过滤状态: ONLINE/OFFLINE/FAULT/STALE"),
    user: CurrentUser = Depends(get_current_user),
):
    """设备状态列表, 可按 floor_id / status 过滤。"""
    try:
        result = list_floor_devices(building_id, user.tenant_id, floor_id, status_filter)
    except Exception as ex:
        _handle_service_errors(ex)
    return success(data=result)


@router.get("/buildings/{building_id}/floors/anomalies")
def floors_anomalies(
    building_id: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    floor_id: str | None = Query(None, description="过滤楼层"),
    limit: int = Query(100, ge=1, le=500, description="返回条数上限"),
    user: CurrentUser = Depends(get_current_user),
):
    """楼层异常事件 (Step 08 跑完才有数据, 目前可能为空)。"""
    s, e = _resolve_range(start, end, user.tenant_id)
    try:
        result = list_floor_anomalies(building_id, user.tenant_id, s, e, floor_id, limit)
    except Exception as ex:
        _handle_service_errors(ex)
    return success(data=result)


@router.get("/buildings/{building_id}/floors/consistency-check")
def floors_consistency_check(
    building_id: str,
    energy_type: str | None = Query(None, description="只检某能源, 不传则全部"),
    user: CurrentUser = Depends(get_current_user),
):
    """加总约束自检: Σ floor = building METER (逐小时)。"""
    try:
        result = check_floor_consistency(building_id, user.tenant_id, energy_type)
    except Exception as ex:
        _handle_service_errors(ex)
    return success(data=result)


@router.post("/buildings/{building_id}/floors/auto-split")
def floors_auto_split(
    building_id: str,
    user: CurrentUser = Depends(require_write_access),
):
    """
    自动按 building.floors_count 拆楼层 + 生成 SENSOR 读数。

    demo 用户 403 (require_write_access 拦)。普通用户在楼层分析页点"自动拆分"
    按钮调这个接口, 后端按 floors_count 生成默认楼层 (1F LOBBY / 中间 OFFICE
    / 顶 MECHANICAL+rooftop), 复用 floor_synthetic 算法生成 SENSOR 读数,
    加总约束严格成立 (Σ floor = building METER)。

    前置条件:
      - 楼栋已上传 METER 读数 (有 ground truth 才能拆)
      - 楼栋没有已有楼层 (避免覆盖)

    返回统计: floors_created / floor_points_created / floor_readings_inserted 等。
    """
    try:
        result = auto_split_floors(building_id, user.tenant_id)
    except Exception as ex:
        _handle_service_errors(ex)
    return success(data=result)


# ============================================================================
# 动态路径 {floor_id} (放最后)
# ============================================================================

@router.get("/buildings/{building_id}/floors/{floor_id}")
def floor_detail(
    building_id: str,
    floor_id: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    user: CurrentUser = Depends(get_current_user),
):
    """单楼层详情 + 设备列表。"""
    s, e = _resolve_range(start, end, user.tenant_id)
    try:
        result = get_floor_detail(building_id, floor_id, user.tenant_id, s, e)
    except Exception as ex:
        _handle_service_errors(ex)
    return success(data=result)


@router.get("/buildings/{building_id}/floors/{floor_id}/timeseries")
def floor_timeseries(
    building_id: str,
    floor_id: str,
    start: str | None = Query(None),
    end: str | None = Query(None),
    granularity: str = Query("day", description=f"粒度: {GRANULARITIES}"),
    energy_type: str | None = Query(None, description="只看某能源"),
    user: CurrentUser = Depends(get_current_user),
):
    """楼层时序 (hour/day/month 粒度, 按能源分组)。"""
    s, e = _resolve_range(start, end, user.tenant_id)
    try:
        result = get_floor_timeseries(
            building_id, floor_id, user.tenant_id, s, e, granularity, energy_type
        )
    except Exception as ex:
        _handle_service_errors(ex)
    return success(data=result)
