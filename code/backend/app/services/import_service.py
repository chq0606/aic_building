"""
import 编排：batch 生命周期 + 后台 merge 线程 + rollback。

import_batch 状态机：
  step 04 commit 完置 LOADING（staging 已灌，未 merge）
  -> /imports/{id}/run 触发：原子 UPDATE ... WHERE status='LOADING' 抢锁 -> MERGING
  -> 后台线程跑 upsert + aggregate -> SUCCEEDED / FAILED
  -> /imports/{id}/rollback 删 fact 数据 + 重算受影响日聚合 -> FAILED（数据已不在 fact）

并发保护：用 batch.status 做乐观锁，UPDATE ... WHERE status='LOADING' AND tenant_id=X。
抢到锁（rowcount=1）才启动后台线程。多进程部署也能工作（PG 行级锁兜底）。
同一 batch_id 重复触发 run 会拿到 status=MERGING，返回 409。
"""
import threading
from datetime import datetime, timezone

from loguru import logger

from app.db.session import get_conn
from app.services.aggregate_service import rebuild_after_merge, rebuild_for_range
from app.services.upsert_service import delete_fact_by_batch, upsert_staging_to_fact


def trigger_merge(batch_id: str, tenant_id: str) -> bool:
    """
    原子抢锁：UPDATE import_batch.status 从 LOADING 转 MERGING。
    返回 True 表示抢到锁，False 表示状态不对（已被抢或不属于本租户）。
    抢到锁后立即启动后台线程跑 merge。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE ingest.import_batch
                SET status = 'MERGING', started_at = COALESCE(started_at, now())
                WHERE id = %s AND tenant_id = %s AND status = 'LOADING'
            """, (batch_id, tenant_id))
            grabbed = cur.rowcount == 1
        conn.commit()

    if not grabbed:
        return False

    # 启动后台线程跑 upsert + aggregate
    t = threading.Thread(
        target=_run_merge_in_background,
        args=(batch_id, tenant_id),
        daemon=True,
    )
    t.start()
    logger.info("merge 后台线程已启动: batch={}", batch_id)
    return True


def _run_merge_in_background(batch_id: str, tenant_id: str) -> None:
    """后台线程函数：跑 upsert + aggregate，更新 batch 状态。"""
    try:
        affected = upsert_staging_to_fact(batch_id, tenant_id)
        daily_rows = rebuild_after_merge(batch_id, tenant_id)

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
                    affected,
                    affected,
                    f"merge ok, daily_energy_rebuilt={daily_rows}",
                    batch_id,
                ))
            conn.commit()
        logger.info(
            "merge 任务完成 batch={} affected={} daily_rows={}",
            batch_id, affected, daily_rows,
        )
    except Exception as e:
        logger.exception("merge 任务失败 batch={}: {}", batch_id, e)
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
            logger.exception("更新 FAILED 状态也失败了 batch={}", batch_id)


def get_batch_status(batch_id: str, tenant_id: str) -> dict | None:
    """查 batch 当前状态 + 统计。None 表示不存在或不属于本租户。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id, status, dataset_source, target_type,
                       row_count_total, row_count_success, row_count_error,
                       error_summary, started_at, finished_at, created_at
                FROM ingest.import_batch
                WHERE id = %s AND tenant_id = %s
            """, (batch_id, tenant_id))
            r = cur.fetchone()
    if r is None:
        return None
    return {
        "batch_id": str(r[0]),
        "status": r[1],
        "dataset_source": r[2],
        "target_type": r[3],
        "row_count_total": r[4],
        "row_count_success": r[5],
        "row_count_error": r[6],
        "error_summary": r[7],
        "started_at": r[8].isoformat() if r[8] else None,
        "finished_at": r[9].isoformat() if r[9] else None,
        "created_at": r[10].isoformat() if r[10] else None,
    }


def list_batches(tenant_id: str, status: str | None = None) -> list[dict]:
    """列出租户的所有 batch。可按 status 过滤。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            if status:
                cur.execute("""
                    SELECT id, status, dataset_source, target_type,
                           row_count_total, row_count_success, row_count_error,
                           error_summary, started_at, finished_at, created_at
                    FROM ingest.import_batch
                    WHERE tenant_id = %s AND status = %s
                    ORDER BY created_at DESC
                """, (tenant_id, status))
            else:
                cur.execute("""
                    SELECT id, status, dataset_source, target_type,
                           row_count_total, row_count_success, row_count_error,
                           error_summary, started_at, finished_at, created_at
                    FROM ingest.import_batch
                    WHERE tenant_id = %s
                    ORDER BY created_at DESC
                """, (tenant_id,))
            rows = cur.fetchall()
    return [
        {
            "batch_id": str(r[0]),
            "status": r[1],
            "dataset_source": r[2],
            "target_type": r[3],
            "row_count_total": r[4],
            "row_count_success": r[5],
            "row_count_error": r[6],
            "error_summary": r[7],
            "started_at": r[8].isoformat() if r[8] else None,
            "finished_at": r[9].isoformat() if r[9] else None,
            "created_at": r[10].isoformat() if r[10] else None,
        }
        for r in rows
    ]


def rollback_batch(batch_id: str, tenant_id: str) -> dict:
    """
    回滚：删 fact.point_reading WHERE source_batch_id=batch_id，重算受影响日聚合。

    流程在一个事务里：
    1. 校验 batch 状态必须是 SUCCEEDED
    2. 查 fact 表里 source_batch_id=batch_id 的 (building_id, energy_type, ts::date) 集合
    3. 删 fact.point_reading WHERE source_batch_id=batch_id
    4. 调 aggregate_service.rebuild_for_range 重算这些 (building, energy, date) 的日聚合
    5. 把 batch 状态标 FAILED + error_summary='manually rolled back'

    返回 {fact_deleted, daily_rebuilt}。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 状态校验：只允许 rollback SUCCEEDED 的 batch
            cur.execute("""
                SELECT status FROM ingest.import_batch
                WHERE id = %s AND tenant_id = %s
            """, (batch_id, tenant_id))
            row = cur.fetchone()
            if row is None:
                raise ValueError("batch 不存在或不属于当前租户")
            if row[0] != "SUCCEEDED":
                raise ValueError(f"只允许回滚 SUCCEEDED 状态的 batch，当前状态 {row[0]}")

            # 1. 先查受影响范围（在删之前，否则查不到了）
            cur.execute("""
                SELECT DISTINCT p.building_id, p.energy_type, pr.ts::date
                FROM fact.point_reading pr
                JOIN core.point p ON p.id = pr.point_id
                WHERE pr.source_batch_id = %s AND pr.tenant_id = %s
            """, (batch_id, tenant_id))
            affected_tuples = [(str(r[0]), r[1], r[2]) for r in cur.fetchall()]
            logger.info("rollback 范围: batch={} affected_tuples={}", batch_id, len(affected_tuples))

            # 2. 删 fact.point_reading
            cur.execute("""
                DELETE FROM fact.point_reading
                WHERE source_batch_id = %s AND tenant_id = %s
            """, (batch_id, tenant_id))
            fact_deleted = cur.rowcount

            # 3. 重算受影响日聚合（在同一个事务里）
            daily_rebuilt = rebuild_for_range(cur, tenant_id, affected_tuples, batch_id)

            # 4. ANALYZE 让查询计划器更新统计
            cur.execute("ANALYZE fact.point_reading")
            cur.execute("ANALYZE mart.building_daily_energy")

            # 5. 更新 batch 状态：标 FAILED + error_summary 说明是 rollback
            cur.execute("""
                UPDATE ingest.import_batch
                SET status = 'FAILED',
                    error_summary = %s,
                    finished_at = now()
                WHERE id = %s
            """, (f"manually rolled back, fact_deleted={fact_deleted}, daily_rebuilt={daily_rebuilt}", batch_id))
        conn.commit()

    logger.info(
        "rollback 完成: batch={} fact_deleted={} daily_rebuilt={}",
        batch_id, fact_deleted, daily_rebuilt,
    )
    return {"fact_deleted": fact_deleted, "daily_rebuilt": daily_rebuilt}
