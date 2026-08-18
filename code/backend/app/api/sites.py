"""
园区列表 API 路由 (Step 14 + 新建园区)。

2 个接口:
  GET  /sites                  列出当前租户所有 site (读, demo 可用)
  POST /sites                  新建命名园区 (写, demo 拦)

为什么独立成 sites.py 而不是放 query.py:
  query.py 的路由前缀是 /query, 如果把 /sites 放进去会变成
  /api/v1/query/sites, 跟其他模块的 /api/v1/sites/{id}/scene (visual.py)
  路径前缀不一致。统一 /api/v1/sites/* 前缀, 后续 /sites/{id}/buildings
  等接口都放这里。

GET 只读用 get_current_user; POST 写操作挂 require_write_access 拦 demo。
"""
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.deps import CurrentUser, get_current_user, require_write_access
from app.core.response import error, success
from app.models.site import SiteCreateRequest
from app.services.site_service import create_site, list_sites


router = APIRouter(prefix="/sites", tags=["sites"])


@router.get("")
def get_sites(user: CurrentUser = Depends(get_current_user)):
    """列出当前租户所有 site。

    返回 [{site_id, site_code, site_name, latitude, longitude, building_count}]。
    demo 用户可用 (只读)。
    """
    sites = list_sites(user.tenant_id)
    return success(data=sites)


@router.post("", status_code=201)
def post_site(
    req: SiteCreateRequest,
    user: CurrentUser = Depends(require_write_access),
):
    """新建命名园区。

    body:
        {"site_name": "我的园区", "site_code": null, "timezone": "UTC"}

    site_code 空则自动生成。返 {site_id, site_code, site_name}。
    """
    try:
        result = create_site(user.tenant_id, req.site_name, req.site_code, req.timezone)
    except ValueError as ex:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=str(ex), code=400),
        )
    return success(data=result, message="园区已创建")
