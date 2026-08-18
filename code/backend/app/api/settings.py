"""
Step 17 系统设置路由。

6 个 endpoint:
  GET  /api/v1/settings/glm-api-key      返当前用户 GLM Key 脱敏状态
  PUT  /api/v1/settings/glm-api-key      保存 GLM Key (加密入库)
  POST /api/v1/settings/test-glm          测试 GLM 连接 (用 body 里的临时 Key 或已保存的 Key)
  GET  /api/v1/settings/bge-info          返 BGE 当前状态 (只读, BGE 走 .env 全局配置)
  POST /api/v1/settings/test-bge          测试 BGE embedding 是否可用
  GET  /api/v1/settings/data-source       返当前租户数据源状态 + 最近一次 seed 时间

认证策略:
  所有 endpoint 都要登录 (get_current_user)。GLM Key 配置允许 demo 用户调
  (用户原话: "demo 体验账号也要自己配 GLM API Key"), 走 get_current_user 不走
  require_write_access。重新 seed 的 demo 拦截在 seed.py 改, 不在这里管。
"""
from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from app.core.deps import CurrentUser, get_current_user
from app.core.response import success, error
from app.db.session import get_db
from app.models.settings import GlmApiKeyRequest
from app.services import settings_service


router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("/glm-api-key")
def get_glm_key_status(
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
):
    """查当前用户的 GLM API Key 配置状态 (脱敏)。

    返 {configured, hint, updated_at}, 不返明文。
    """
    status_obj = settings_service.get_glm_key_status(conn, user.user_id)
    return success(data={
        "configured": status_obj.configured,
        "hint": status_obj.hint,
        "updated_at": status_obj.updated_at.isoformat() if status_obj.updated_at else None,
    })


@router.put("/glm-api-key")
def save_glm_key(
    req: GlmApiKeyRequest,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
):
    """保存 GLM API Key (AES-GCM 加密后入库)。

    demo 用户允许调 (用户级配置, 不算业务数据写)。
    """
    status_obj = settings_service.set_glm_api_key(conn, user.user_id, req.api_key)
    logger.info("用户 {} 保存了 GLM API Key hint={}", user.username, status_obj.hint)
    return success(
        data={
            "configured": status_obj.configured,
            "hint": status_obj.hint,
            "updated_at": status_obj.updated_at.isoformat() if status_obj.updated_at else None,
        },
        message="GLM API Key 已保存",
    )


@router.post("/test-glm")
def test_glm(
    req: GlmApiKeyRequest,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
):
    """测试 GLM API Key 是否可用。

    优先用 body 里的 api_key (用户在输入框输入但还没保存, 先测一下)。
    body 里没传 api_key 就用当前用户已保存的 Key 测。
    会发一条 max_tokens=5 的 ping 到智谱 v4 endpoint, 耗时 ~1-3s。
    """
    api_key = req.api_key.strip() if req.api_key else None
    if not api_key:
        # body 没传, 用已保存的 Key
        api_key = settings_service.get_glm_api_key(conn, user.user_id)
        if not api_key:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error(message="未配置 GLM API Key, 请先输入或保存", code=400),
            )

    result = settings_service.test_glm_connection(api_key)
    logger.info(
        "GLM 连接测试 user={} ok={} msg={}",
        user.username, result.ok, result.message,
    )
    return success(data={
        "ok": result.ok,
        "message": result.message,
        "detail": result.detail,
    })


@router.get("/bge-info")
def get_bge_info(
    user: CurrentUser = Depends(get_current_user),
):
    """查 BGE 模型当前状态 (只读, 不触发加载)。

    BGE 是后端启动时 singleton 加载, 走 .env 全局配置 (settings.bge_model_path),
    不能按用户切换。这里只返状态让前端展示: 配置路径 / 维度 / 是否已加载 / device。
    """
    s = settings_service.get_bge_status()
    return success(data={
        "configured": s.configured,
        "model_path": s.model_path,
        "expected_dim": s.expected_dim,
        "actual_dim": s.actual_dim,
        "loaded": s.loaded,
        "device": s.device,
    })


@router.post("/test-bge")
def test_bge(
    user: CurrentUser = Depends(get_current_user),
):
    """测试 BGE embedding 是否可用 (embed 一条测试文本)。

    会触发模型加载 (如果还没加载), 首次测试可能等 5-10s。
    """
    result = settings_service.test_bge_connection()
    logger.info(
        "BGE 连接测试 user={} ok={} msg={}",
        user.username, result.ok, result.message,
    )
    return success(data={
        "ok": result.ok,
        "message": result.message,
        "detail": result.detail,
    })


@router.get("/data-source")
def get_data_source_status(
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
):
    """查当前租户的数据源状态 + 最近一次 seed 任务结果。

    前端 DataSourceConfig 展示用, demo 用户也能看 (只是看, 不能重新 seed)。
    """
    s = settings_service.get_data_source_status(conn, user.tenant_id)
    return success(data={
        "building_count": s.building_count,
        "point_count": s.point_count,
        "reading_count": s.reading_count,
        "anomaly_count": s.anomaly_count,
        "last_seed_at": s.last_seed_at.isoformat() if s.last_seed_at else None,
        "last_seed_batch_id": s.last_seed_batch_id,
        "last_seed_status": s.last_seed_status,
    })
