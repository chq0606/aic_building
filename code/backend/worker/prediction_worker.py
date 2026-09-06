"""
能耗预测 worker (Step 19)。

跑法:
    cd backend
    python -m worker.prediction_worker

工作流 (复用 Step 12 reconstruction_worker 的模式):
1. 主循环每 prediction_poll_interval (5s) 轮询 DB:
   - SELECT FOR UPDATE SKIP LOCKED 拿一个 PENDING job
   - UPDATE status='RUNNING', started_at=now(), commit 释放锁
   - 调 prediction_service.run_prediction(job)
   - 成功: run_prediction 内部已 UPDATE status='SUCCEEDED', mape, result_jsonb
   - 异常: _handle_job_failure 置 status='FAILED', error_message

2. 失败策略 (与 Step 12 不同):
   - Step 12 triposplat 失败自动重试 3 次 (网络抖动 / GPU OOM 可恢复)
   - Step 19 失败立即 FAILED 不自动重试
   - 理由: Prophet/LSTM 失败通常是数据问题 (<30 天数据 / 全 NaN),
     重试也是同样结果, 浪费 worker 时间
   - 用户改数据后可调 API /prediction/jobs/{id}/retry 手动重试 (一期未实现,
     retry 直接新建 job 即可)

3. 不预加载模型: Prophet/LSTM 模型每次 job 独立训练 (不像 TripoSplatPipeline
   是固定权重可复用单例)。PyTorch + CmdStan 模型加载开销在训练里, 无单例优化空间。

4. SIGINT/SIGTERM 优雅退出: 处理完当前 job 后退出, 不中断进行中的预测。
   Windows 下 SIGTERM 不存在, 主靠 SIGINT (Ctrl+C)。
"""
import os
import signal
import sys
import time
from pathlib import Path

# 让 from app import ... 和 from worker import ... 能跑
BACKEND_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_ROOT))

# Windows 控制台 GBK 编码会让 logger 输出中文乱码 (UnicodeEncodeError), 切 UTF-8
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")

from loguru import logger

from app.core.config import settings
from app.core.logging import setup_logging
from app.db.session import close_pool, get_conn, init_pool
from app.services.prediction_service import run_prediction


def _claim_next_job():
    """SELECT FOR UPDATE SKIP LOCKED 拿一个 PENDING job 并置 RUNNING。

    事务内: SELECT 锁行 -> UPDATE status='RUNNING' -> commit 释放锁。
    返回 job dict 或 None (无 PENDING)。

    SQL 注意点:
      - FOR UPDATE SKIP LOCKED 跳过被其他 worker 锁住的行, 多 worker 并发安全
      - ORDER BY created_at 保证最早的 job 先处理 (FIFO)
      - 不像 Step 12 有 last_retry_at 字段做重试间隔 (Step 19 失败立即 FAILED)
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, tenant_id, building_id, model_type, horizon_days
                FROM mart.prediction_job
                WHERE status = 'PENDING'
                ORDER BY created_at
                LIMIT 1
                FOR UPDATE SKIP LOCKED
            """)
            row = cur.fetchone()
            if row is None:
                return None

            job_id, tenant_id, building_id, model_type, horizon_days = row

            cur.execute("""
                UPDATE mart.prediction_job
                SET status = 'RUNNING', started_at = now()
                WHERE id = %s::uuid
            """, (str(job_id),))
        conn.commit()

    return {
        "id": str(job_id),
        "tenant_id": str(tenant_id),
        "building_id": str(building_id),
        "model_type": model_type,
        "horizon_days": horizon_days,
    }


def _handle_job_failure(job: dict, exc: Exception):
    """job 失败处理: 立即置 FAILED。

    与 Step 12 不同, 不自动重试:
      - Step 12 失败原因通常是 GPU/网络抖动, 重试可恢复
      - Step 19 失败原因通常是数据问题 (<30 天 / 全 NaN), 重试无意义
    """
    job_id = job["id"]
    err_msg = str(exc)[:500]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE mart.prediction_job
                SET status = 'FAILED',
                    error_message = %s,
                    finished_at = now()
                WHERE id = %s::uuid
            """, (err_msg, job_id))
        conn.commit()

    logger.error(
        "job {} FAILED: {}",
        job_id[:8], err_msg[:200],
    )


def _process_job(job: dict):
    """处理单个 job。任何异常上抛由主循环捕获走 FAILED 逻辑。

    run_prediction 内部成功时已 UPDATE status='SUCCEEDED', 这里不需要再更新。
    """
    run_prediction(
        job_id=job["id"],
        building_id=job["building_id"],
        tenant_id=job["tenant_id"],
        model_type=job["model_type"],
        horizon=job["horizon_days"],
    )


_running = True


def _shutdown(signum, frame):
    """SIGINT/SIGTERM 优雅退出: 标记 _running=False, 主循环处理完当前 job 后退出。"""
    global _running
    logger.info("收到信号 {}, 处理完当前 job 后退出", signum)
    _running = False


def main():
    signal.signal(signal.SIGINT, _shutdown)
    # Windows 没 SIGTERM, try 一下避免 AttributeError
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except (AttributeError, ValueError):
        pass

    setup_logging()
    logger.info("prediction worker 启动 (pid={})", os.getpid())
    init_pool()

    # 预热: 验证 Prophet / torch / sklearn 都能 import (装包问题早暴露)
    try:
        from prophet import Prophet  # noqa: F401
        import torch  # noqa: F401
        import sklearn  # noqa: F401
        logger.info(
            "依赖检查通过: prophet + torch + sklearn 均可用 (torch version={})",
            torch.__version__,
        )
    except ImportError as e:
        logger.error("依赖缺失, worker 退出: {}", e)
        close_pool()
        return

    logger.info(
        "进入主循环 (poll_interval={}s)",
        settings.prediction_poll_interval,
    )

    while _running:
        try:
            job = _claim_next_job()
            if job is None:
                time.sleep(settings.prediction_poll_interval)
                continue

            logger.info(
                "picked job {} (building={}, model={}, horizon={})",
                job["id"][:8], job["building_id"][:8],
                job["model_type"], job["horizon_days"],
            )
            try:
                _process_job(job)
                logger.info("job {} SUCCEEDED", job["id"][:8])
            except Exception as e:
                logger.exception("job {} 失败", job["id"][:8])
                _handle_job_failure(job, e)
        except Exception as e:
            # 主循环异常 (DB 断连 / 网络抖动等), 不要让 worker 死
            logger.exception("worker 主循环异常: {}", e)
            time.sleep(5)

    logger.info("worker 退出, 关闭连接池")
    close_pool()


if __name__ == "__main__":
    main()
