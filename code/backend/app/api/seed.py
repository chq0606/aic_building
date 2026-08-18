"""
seed-demo API。

POST /api/v1/admin/seed-demo:
    任何登录用户都能调。后台线程跑 BDG2 seed,立即返回 batch_id。
    前端用 GET /api/v1/admin/seed-demo/{batch_id}/status 轮询状态。

状态记录在 ingest.import_batch 表,用 dataset_source='bdg2_seed' 区分
普通客户上传任务。target_type 复用 'POINT'(seed 主要是测点数据),
status 用 LOADING(进行中)/SUCCEEDED/FAILED。

并发保护:同一时间只允许一个 seed 任务跑。用 import_batch 表里
dataset_source='bdg2_seed' AND status='LOADING' 是否存在来判断。
"""
import threading
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from loguru import logger

from app.core.deps import get_current_user, CurrentUser, require_write_access
from app.core.response import success, error
from app.db.session import get_conn, get_db
from app.seed.bdg2 import run_bdg2_seed


router = APIRouter(prefix="/admin", tags=["admin"])

# 全局锁:同一进程内只允许一个 seed 任务跑。多进程部署时再用 PG 行锁,
# 一期单进程够用
_seed_lock = threading.Lock()
_running_batch_id: str | None = None


def _run_seed_in_background(batch_id: str, reset: bool) -> None:
    """后台线程函数。seed 跑完后更新 import_batch 状态。"""
    global _running_batch_id
    try:
        stats = run_bdg2_seed(reset=reset)
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    UPDATE ingest.import_batch
                    SET status = 'SUCCEEDED',
                        row_count_total = %s,
                        row_count_success = %s,
                        error_summary = %s,
                        finished_at = now()
                    WHERE id = %s
                """, (
                    stats["readings"],
                    stats["readings"],
                    f"buildings={stats['buildings']}, points={stats['points']}, "
                    f"weather={stats['weather']}, daily_energy={stats['daily_energy']}, "
                    f"ivfflat_rebuilt={stats['ivfflat_rebuilt']}",
                    batch_id,
                ))
            conn.commit()
        logger.info("seed 任务完成 batch_id={} stats={}", batch_id, stats)
    except Exception as e:
        logger.exception("seed 任务失败 batch_id={}: {}", batch_id, e)
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        UPDATE ingest.import_batch
                        SET status = 'FAILED', error_summary = %s, finished_at = now()
                        WHERE id = %s
                    """, (str(e)[:500], batch_id))
                conn.commit()
        except Exception:
            logger.exception("更新 FAILED 状态也失败了 batch_id={}", batch_id)
    finally:
        _running_batch_id = None
        _seed_lock.release()


@router.post("/seed-demo")
def trigger_seed_demo(
    reset: bool = False,
    user: CurrentUser = Depends(require_write_access),
):
    """
    触发 BDG2 demo seed。
    ?reset=true 先清 demo 租户业务数据再灌,默认 false。

    demo 用户不能调: 重新 seed 是运维级写操作, demo 只读原则不能破。
    用 require_write_access 而不是 get_current_user, demo 调到直接 403。
    """
    global _running_batch_id

    if not _seed_lock.acquire(blocking=False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error(message="已有 seed 任务在跑,请等当前任务完成", code=409),
        )

    # 在 demo 租户名下建 import_batch 记录,dataset_source 标 bdg2_seed
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM core.tenant WHERE tenant_code = 'demo'")
            row = cur.fetchone()
            if row is None:
                _seed_lock.release()
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=error(message="demo 租户不存在,无法启动 seed", code=500),
                )
            demo_tenant_id = row[0]

            cur.execute("""
                INSERT INTO ingest.import_batch
                    (tenant_id, dataset_source, target_type, status,
                     row_count_total, row_count_success, row_count_error,
                     started_at)
                VALUES (%s, 'bdg2_seed', 'POINT', 'LOADING', 0, 0, 0, now())
                RETURNING id
            """, (demo_tenant_id,))
            batch_id = str(cur.fetchone()[0])
        conn.commit()

    _running_batch_id = batch_id
    logger.info("用户 {} 触发 seed 任务 batch_id={} reset={}", user.username, batch_id, reset)

    # 启动后台线程,把锁一起传过去(线程结束时 release)
    t = threading.Thread(
        target=_run_seed_in_background,
        args=(batch_id, reset),
        daemon=True,
    )
    t.start()

    return success(data={"batch_id": batch_id, "status": "LOADING"}, message="seed 任务已启动")


@router.get("/seed-demo/{batch_id}/status")
def get_seed_status(
    batch_id: str,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
):
    """查 seed 任务状态。前端轮询这个接口。"""
    with conn.cursor() as cur:
        cur.execute("""
            SELECT id, status, row_count_total, row_count_success,
                   row_count_error, error_summary, started_at, finished_at, created_at
            FROM ingest.import_batch
            WHERE id = %s AND dataset_source = 'bdg2_seed'
        """, (batch_id,))
        row = cur.fetchone()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message="batch 不存在或不是 seed 任务", code=404),
        )

    return success(data={
        "batch_id": str(row[0]),
        "status": row[1],
        "row_count_total": row[2],
        "row_count_success": row[3],
        "row_count_error": row[4],
        "error_summary": row[5],
        "started_at": row[6].isoformat() if row[6] else None,
        "finished_at": row[7].isoformat() if row[7] else None,
        "created_at": row[8].isoformat() if row[8] else None,
    })
