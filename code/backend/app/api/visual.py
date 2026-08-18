"""
体块模式后端 API 路由 (Step 11)。

6 个接口:
  POST   /buildings/{id}/visual-models/block       提交体块参数 (写, demo 拦)
  GET    /sites/{site_id}/scene                    园区 3D 场景元数据 (读)
  GET    /buildings/{id}/visual-model              查 building 最新 active 模型 (读)
  DELETE /buildings/{id}/visual-models/{model_id}  删模型 (写, demo 拦)
  PATCH  /buildings/{id}/visual-models/yaw         更新朝向 (写)
  PATCH  /buildings/{id}/use                       更新用途 (写)

路由薄壳: 业务逻辑在 visual_model_service, 路由层只做:
  - 参数校验 (Pydantic body 已校验, 路由层补 building 存在性)
  - 404 处理 (building/model 不存在或租户越权)
  - 响应包装 (success/error)

跟 api/knowledge.py 风格对齐: NotFound 用 HTTPException 404 + error 包装,
业务异常用 400。service 层返 None 表示"没找到", 路由层转 404。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse, Response
from loguru import logger
from pathlib import Path

from app.core.deps import CurrentUser, get_current_user
from app.core.response import error, success
from app.db.session import get_conn
from app.models.visual import BlockModelRequest, BuildingUseUpdateRequest, YawUpdateRequest
from app.services.query_service import NotFoundError
from app.services.visual_model_service import (
    delete_visual_model,
    get_building_visual_model,
    list_site_scene,
    update_building_use,
    update_building_yaw,
    upsert_block_model,
)


router = APIRouter(tags=["visual"])


def _verify_building_access(building_id: str, tenant_id: str) -> None:
    """校验 building 属于当前租户, 不存在抛 404。

    路由层校验而不是 service 层, 因为 HTTP 404 比 service 抛 ValueError 更标准,
    前端拿到的 HTTP 状态码语义更清晰。
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
                    detail=error(message=f"building 不存在: {building_id}", code=404),
                )


@router.post("/buildings/{building_id}/visual-models/block", status_code=201)
def submit_block_model(
    building_id: str,
    req: BlockModelRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """提交体块参数。写操作, demo 账号 403。

    body:
        {
          "length_m": 120.0, "width_m": 80.0, "height_m": 7.0,
          "floors_count": 2,
          "position_x": 0.0, "position_y": 0.0
        }

    返回 {"model_id": str, "building_id": str, "is_active": true}。
    """
    _verify_building_access(building_id, user.tenant_id)
    result = upsert_block_model(user.tenant_id, building_id, req)
    return success(data=result, message="体块参数已保存")


@router.get("/sites/{site_id}/scene")
def get_site_scene(
    site_id: str,
    metric: str = Query(
        "eui",
        pattern=r"^(eui|total_kwh|anomaly_count)$",
        description="体块着色用的指标: eui/total_kwh/anomaly_count, 默认 eui",
    ),
    start: str | None = Query(None, description="ISO8601, 不传按租户实际数据范围"),
    end: str | None = Query(None, description="ISO8601, 不传按租户实际数据范围"),
    user: CurrentUser = Depends(get_current_user),
):
    """园区 3D 场景元数据。一次返整个园区所有 building 的:
      - dimensions (length/width/height/floors_count, 没 visual_model 走自动估算)
      - position (x/y, 没设过则 None)
      - color_metric ({metric, value, level} 按 metric 算着色等级)
      - anomaly_status ({has_anomaly, severity_max, count, by_severity})
      - energy_composition (各能源类型占比)

    demo 用户可用 (只读)。
    """
    try:
        result = list_site_scene(
            tenant_id=user.tenant_id,
            site_id=site_id,
            metric=metric,
            start=start,
            end=end,
        )
    except NotFoundError as ex:
        # 跨租户 / 不存在的 site_id (如 localStorage 泄漏了上一个账号的园区) -> 404
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=str(ex), code=404),
        )
    return success(data=result)


@router.get("/buildings/{building_id}/visual-model")
def get_visual_model(
    building_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """查 building 最新 active visual_model。没设过返 200 + data=null。

    同 building 多条 active 记录时按优先级返: splat > block
    (PHOTO_SINGLE/PHOTO_MULTI > BLOCK)。

    "没设过"是合法初始态而非"资源不存在", 返 200 + null 让前端按空表单处理,
    避免浏览器 Network 面板对 4xx 默认打红字干扰调试。同 scene 接口设计:
    scene 没数据返空集合, 这里没模型返 null, "楼没有 visual_model" 等同
    "楼没有 eui" 那种子属性缺失, 不是 RESTful 资源 404。
    """
    _verify_building_access(building_id, user.tenant_id)
    result = get_building_visual_model(user.tenant_id, building_id)
    return success(data=result)


@router.delete("/buildings/{building_id}/visual-models/{model_id}")
def delete_model(
    building_id: str,
    model_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """删除 visual_model。写操作, demo 账号 403。

    路径里带 building_id 是为了 RESTful 语义清晰 (虽然 model_id 全局唯一),
    DELETE /buildings/{bid}/visual-models/{mid} 比 /visual-models/{mid} 更明确
    表达"删这栋楼的这个模型"。删前校验 building 属于租户, 但不校验 model
    属于该 building (service 层 DELETE WHERE tenant_id 已隔离, model_id 在
    别的 building 名下也只会被删一次)。
    """
    _verify_building_access(building_id, user.tenant_id)
    result = delete_visual_model(user.tenant_id, model_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=f"visual_model 不存在: {model_id}", code=404),
        )
    logger.info("visual_model deleted: tenant={} building={} model={}",
                user.tenant_id[:8], building_id[:8], model_id[:8])
    return success(data=result, message="已删除")


@router.patch("/buildings/{building_id}/visual-models/yaw")
def update_yaw(
    building_id: str,
    req: YawUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """更新 building 当前 active visual_model 的水平旋转角度 (Step 13)。

    写操作, demo 账号 403。前端 BuildingDetailDrawer 滑块拖动 debounce 300ms
    后调一次, 比走 POST /block 全量 upsert 轻。

    body:
        {"yaw_deg": 45.0}

    返 {"building_id": str, "yaw_deg": float, "updated_count": int}。
    building 没 active visual_model (没设过体块 / 没传过照片) 时返 404,
    让前端提示用户先创建模型。
    """
    _verify_building_access(building_id, user.tenant_id)
    result = update_building_yaw(user.tenant_id, building_id, req.yaw_deg)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(
                message=f"building 还没有 visual_model, 无法更新 yaw: {building_id}",
                code=404,
            ),
        )
    return success(data=result, message="朝向已更新")


@router.patch("/buildings/{building_id}/use")
def update_use(
    building_id: str,
    req: BuildingUseUpdateRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """更新 building 用途字段 (primary_use / sub_use)。

    3D 建模属于 demo 开放功能 (跟体块提交一致, 用 get_current_user 不拦 demo)。
    前端体块表单选"建筑用途"后调这里, 决定 3D 页程序化表皮 + 屋顶类型,
    也喂给异常检测的同类楼群对比。

    body:
        {"primary_use": "Commercial", "sub_use": "Retail"}

    返 {"building_id": str, "primary_use": str|null, "sub_use": str|null}。
    """
    _verify_building_access(building_id, user.tenant_id)
    result = update_building_use(
        user.tenant_id, building_id, req.primary_use, req.sub_use
    )
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=f"building 不存在: {building_id}", code=404),
        )
    return success(data=result, message="用途已更新")


# splat 模型类型 (照片重建产物), 这些 model_mode 才有 .ply 文件可下
_SPLAT_MODEL_MODES = ("PHOTO_SINGLE", "PHOTO_MULTI")


@router.get("/buildings/{building_id}/visual-models/{model_id}/splat.ply")
def get_splat_ply(
    building_id: str,
    model_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """下载 splat 模型的 .ply 文件 (前端 BuildingSplat 用真 3DGS 库渲染)。

    鉴权: 只读接口, demo 也能用。三步校验:
      1. building 属于当前租户 (404 不存在/越权)
      2. model 属于该 building + 租户 (404)
      3. model_mode 是 PHOTO_SINGLE/PHOTO_MULTI (400, BLOCK 没有 splat .ply)

    返 FileResponse (application/octet-stream), 不走 success/error envelope,
    跟 /reconstruction/jobs/{id}/preview 同款模式 -- 前端 fetch + Authorization
    header 拿 blob URL 喂给 3DGS 库, 不走 axios 拦截器 (因为 <img src>/loader
    不带 Authorization)。

    文件不存在 (visual_model.storage_path 指向的 .ply 丢了) 返 404, 让前端
    BuildingSplat 显示错误态而不是空 splat。
    """
    _verify_building_access(building_id, user.tenant_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT model_mode, storage_path
                FROM core.building_visual_model
                WHERE id = %s::uuid
                  AND building_id = %s::uuid
                  AND tenant_id = %s::uuid
                  AND is_active = true
            """, (model_id, building_id, user.tenant_id))
            row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=f"visual_model 不存在或租户越权: {model_id}", code=404),
        )

    model_mode, storage_path = row
    if model_mode not in _SPLAT_MODEL_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(
                message=f"该模型类型 {model_mode} 无 splat .ply, 只有照片重建模型 (PHOTO_SINGLE/PHOTO_MULTI) 才有",
                code=400,
            ),
        )

    ply_path = Path(storage_path)
    if not ply_path.exists() or not ply_path.is_file():
        logger.error(
            "splat .ply 文件丢失: model_id={} path={}",
            model_id[:8], storage_path,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message="splat .ply 文件不存在 (可能被外部清理)", code=404),
        )

    logger.info(
        "splat.ply downloaded: tenant={} building={} model={} size={}MB",
        user.tenant_id[:8], building_id[:8], model_id[:8],
        ply_path.stat().st_size // 1024 // 1024,
    )
    return FileResponse(
        path=str(ply_path),
        media_type="application/octet-stream",
        # 不设 filename 避免触发下载 (前端要内联加载, 拿 blob URL 喂 3DGS 库)
    )


# Cesium 3D Tiles 文件白名单: tileset.json 主入口 + tiles/{level}/{x}.glb 子文件
# + build_summary.json 元数据。其他路径 (如 .ply 原文件) 不在白名单内, 拒绝。
# 安全: path:path 会捕获任意层级路径含 "..", 这里 resolve() 后校验仍在 tiles_dir
# 子树内, 防 path traversal 攻击。
_TILES_ALLOWED_FILES = ("tileset.json", "build_summary.json")
_TILES_ALLOWED_EXTS = (".glb", ".bin")


@router.get("/buildings/{building_id}/visual-models/{model_id}/splat-tiles/{file_path:path}")
def get_splat_tiles(
    building_id: str,
    model_id: str,
    file_path: str,
    user: CurrentUser = Depends(get_current_user),
):
    """下载 splat 模型的 3D Tiles 文件 (Cesium3DTileset 原生加载)。

    鉴权: 跟 splat.ply 同款三步校验 (building 属租户 / model 属 building+租户 /
    model_mode 是 splat 类型)。返 FileResponse 不走 envelope。

    URL 设计: {file_path:path} 捕获 tileset.json / tiles/0/0.glb / tiles/1/1.glb
    等多层级路径。前端 Cesium3DTileset.fromUrl(tileset_url) 加载 tileset.json,
    Cesium 内部自动按相对 URL 拉子 tile (tiles/{level}/{x}.glb), 每个请求都带
    Authorization header (前端用 Resource + headers 实现)。

    路径安全: file_path resolve 后必须在 tiles_dir 子树内, 防 ../../../etc/passwd
    这类 path traversal。同时白名单文件名 (tileset.json / build_summary.json /
    *.glb / *.bin), 进一步收紧攻击面。

    tiles_path 为 NULL (转换失败 / 老 visual_model 没转) 返 404, 前端降级走
    splat.ply 接口。
    """
    _verify_building_access(building_id, user.tenant_id)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT model_mode, tiles_path
                FROM core.building_visual_model
                WHERE id = %s::uuid
                  AND building_id = %s::uuid
                  AND tenant_id = %s::uuid
                  AND is_active = true
            """, (model_id, building_id, user.tenant_id))
            row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=f"visual_model 不存在或租户越权: {model_id}", code=404),
        )

    model_mode, tiles_path = row
    if model_mode not in _SPLAT_MODEL_MODES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(
                message=f"该模型类型 {model_mode} 无 3D Tiles, 只有照片重建模型 (PHOTO_SINGLE/PHOTO_MULTI) 才有",
                code=400,
            ),
        )
    if not tiles_path:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(
                message="该模型未生成 3D Tiles (转换失败或为老记录), 请走 splat.ply 接口降级渲染",
                code=404,
            ),
        )

    # tiles_path 指向 tileset.json 的绝对路径, tiles_dir 是其父目录
    tileset_path = Path(tiles_path)
    if not tileset_path.exists() or not tileset_path.is_file():
        logger.error(
            "tileset.json 丢失: model_id={} path={}",
            model_id[:8], tiles_path,
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message="tileset.json 不存在 (可能被外部清理)", code=404),
        )

    tiles_dir = tileset_path.parent
    # resolve() 解析 .., 符号链接等, 然后校验 requested_path 必须在 tiles_dir 子树内
    requested_path = (tiles_dir / file_path).resolve()
    try:
        requested_path.relative_to(tiles_dir.resolve())
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message="非法路径 (越出 tiles 目录)", code=400),
        )

    # 白名单: tileset.json / build_summary.json / *.glb / *.bin
    file_name = requested_path.name
    file_ext = requested_path.suffix.lower()
    if (file_name not in _TILES_ALLOWED_FILES
            and file_ext not in _TILES_ALLOWED_EXTS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(
                message=f"不允许的文件类型: {file_name}{file_ext}, 仅支持 tileset.json / *.glb / *.bin",
                code=400,
            ),
        )

    if not requested_path.exists() or not requested_path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=f"3D Tiles 子文件不存在: {file_path}", code=404),
        )

    # media_type: tileset.json 用 application/json, .glb 用 model/gltf-binary,
    # .bin 用 application/octet-stream。Cesium 不挑 media_type (它自己看文件头),
    # 但设正确 media_type 让浏览器 dev tools 显示更友好。
    if file_ext == ".json":
        media_type = "application/json"
    elif file_ext == ".glb":
        media_type = "model/gltf-binary"
    else:
        media_type = "application/octet-stream"

    return FileResponse(
        path=str(requested_path),
        media_type=media_type,
        # 不设 filename 让浏览器内联渲染而不是下载
    )
