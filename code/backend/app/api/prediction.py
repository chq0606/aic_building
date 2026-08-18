"""
能耗预测路由。

三个接口:
  POST /prediction/buildings/{id}/forecast   提交预测 job (202, demo 例外可调)
  GET  /prediction/jobs/{id}                  查 job 详情 (读)
  GET  /prediction/buildings/{id}/latest      取最近一次 SUCCEEDED 预测 (读)

路由薄壳: 业务在 prediction_service。路由层只做:
  - building 存在性校验 (404)
  - horizon_days / model_type 参数校验 (Pydantic 在请求体层就做了)
  - job 不存在/跨租户转 404
  - 响应包装 (success/error)

submit_forecast 挂 get_current_user 而非 require_write_access: 预测是异步只读
任务, 不写 fact 表只写 mart.prediction_job (带 tenant_id 隔离), demo 也能体验.
其他写接口 (上传/重建/回滚等) 仍挂 require_write_access 拦 demo.
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger

from app.core.deps import CurrentUser, get_current_user
from app.core.response import error, success
from app.db.session import get_conn
from app.models.prediction import ForecastRequest
from app.services.prediction_service import (
    create_job,
    get_job,
    get_latest,
)


router = APIRouter(tags=["prediction"])


def _verify_building_access(building_id: str, tenant_id: str) -> None:
    """校验 building 属于当前租户, 不存在抛 404。

    跟 api/reconstruction.py 的同名函数一致, 一期没抽公共模块
    (后续如果跨模块重复变多再考虑抽 app/api/_common.py)。
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
    "/prediction/buildings/{building_id}/forecast",
    status_code=status.HTTP_202_ACCEPTED,
)
def submit_forecast(
    building_id: str,
    req: ForecastRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """提交预测 job。202 Accepted (异步任务, prediction_worker 进程消费)。

    body:
      horizon_days: 1-90
      model_type: prophet / lstm / linear

    worker 启动方式 (单独进程):
      cd backend
      python -m worker.prediction_worker

    前端拿到 job_id 后轮询 GET /prediction/jobs/{id} 直到 SUCCEEDED/FAILED。

    demo 例外: 这里挂 get_current_user 而不是 require_write_access, 让 demo
    账号也能体验预测. 理由: 预测是异步只读任务, 不写 fact 表只写
    mart.prediction_job (该表带 tenant_id 隔离, demo 跑的 job 只属于 demo
    租户, 不会污染其他租户). 其他写接口 (上传/重建/回滚等) 仍挂
    require_write_access 拦 demo, 那些会改 fact 数据或文件系统.
    """
    _verify_building_access(building_id, user.tenant_id)

    job_id = create_job(
        tenant_id=user.tenant_id,
        building_id=building_id,
        model_type=req.model_type,
        horizon_days=req.horizon_days,
    )

    logger.info(
        "submit_forecast tenant={} building={} job={} model={} horizon={}",
        user.tenant_id[:8], building_id[:8], job_id[:8],
        req.model_type, req.horizon_days,
    )
    return success(
        data={"job_id": job_id, "status": "PENDING"},
        message="预测任务已提交, 等待 worker 消费",
    )


@router.get("/prediction/jobs/{job_id}")
def get_job_status(
    job_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """查 job 状态。前端轮询直到 SUCCEEDED/FAILED。

    返回 result_jsonb (含 history + forecast + mape + warning)。
    """
    result = get_job(user.tenant_id, job_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=f"job 不存在或租户越权: {job_id}", code=404),
        )
    return success(data=result)


@router.get("/prediction/buildings/{building_id}/latest")
def get_latest_forecast(
    building_id: str,
    model_type: str | None = Query(
        None,
        pattern="^(prophet|lstm|linear|gbm)$",
        description="按模型类型过滤, 不传则取任意模型的最近预测",
    ),
    user: CurrentUser = Depends(get_current_user),
):
    """取最近一次 SUCCEEDED 的预测结果。model_type 可选过滤。

    前端默认进入 PredictionPanel 时调这个, 有最近预测直接显示避免重新跑。
    没有则返 200 + data=null (前端显示空状态, 提示用户点预测按钮)。

    "没跑过预测"是合法初始态而非"资源不存在", 返 200 + null 让前端按空状态处理,
    避免浏览器 Network 面板对 4xx 默认打红字干扰调试。跟 visual-model 接口
    设计对齐: 没设过 visual_model 也返 200 + null。
    """
    _verify_building_access(building_id, user.tenant_id)

    result = get_latest(
        tenant_id=user.tenant_id,
        building_id=building_id,
        model_type=model_type,
    )
    return success(data=result)
