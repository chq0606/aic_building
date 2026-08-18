"""
单图重建任务服务 (Step 12)。

五个对外函数:
  create_job            提交重建请求 -> PENDING job + 落 reconstruction_job 表
  get_job               查单个 job 详情
  list_jobs             列 job (可按 building_id / status 过滤)
  retry_job             手动重试 FAILED job (retry_count 重置为 0)
  delete_job            硬删 job + 删磁盘文件目录

写操作显式 conn.commit(): get_conn 异常时自动 rollback, 正常时不自动 commit,
写操作必须显式调 conn.commit() (见 db/session.py 注释)。

tenant_id 隔离: 所有 SQL 都带 WHERE tenant_id = %s, 不依赖连接层做隔离。

不抛 HTTPException: service 层返 None 表示"找不到", 由路由层转 404。
返 {"error": "..."} 表示业务校验失败 (如重试非 FAILED job), 路由层转 400。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any

from loguru import logger

from app.core.config import settings
from app.db.session import get_conn
from app.models.reconstruction import SinglePhotoRequest


def _photo_path_from_id(tenant_id: str, photo_id: str) -> Path:
    """从 photo_id 反查磁盘文件路径。

    照片上传时文件名规则: {tenant_id}/{photo_id}_{original_filename}。
    photo_id 是 uuid4, original_filename 不存 DB (没建表), 所以反查要走
    glob -- 找目录下以 photo_id 开头的文件。
    找不到返不存在的路径 (调用方决定怎么处理, 通常 job 失败重试)。
    """
    tenant_dir = settings.photo_upload_path / tenant_id
    matches = list(tenant_dir.glob(f"{photo_id}_*"))
    return matches[0] if matches else tenant_dir / f"{photo_id}_missing"


def _row_to_job_out(row: tuple) -> dict[str, Any]:
    """把 DB row 转成 JobOut dict。row 字段顺序见 SELECT 列。"""
    return {
        "id": str(row[0]),
        "building_id": str(row[1]),
        "status": row[2],
        "engine": row[3],
        "model_mode": row[4],
        "retry_count": row[5],
        "photo_upload_id": str(row[6]) if row[6] else None,
        "output_model_id": str(row[7]) if row[7] else None,
        "error_message": row[8],
        "started_at": row[9].isoformat() if row[9] else None,
        "finished_at": row[10].isoformat() if row[10] else None,
        "created_at": row[11].isoformat() if row[11] else "",
        "dimensions": {
            "length_m": float(row[12]) if row[12] is not None else None,
            "width_m": float(row[13]) if row[13] is not None else None,
            "height_m": float(row[14]) if row[14] is not None else None,
            "floors_count": row[15],
        },
        "position": {
            "x": float(row[16]) if row[16] is not None else None,
            "y": float(row[17]) if row[17] is not None else None,
        },
    }


_JOB_COLUMNS = """
    j.id, j.building_id, j.status, j.engine, j.model_mode,
    j.retry_count, j.input_upload_file_id, j.output_model_id,
    j.error_message, j.started_at, j.finished_at, j.created_at,
    j.input_length_m, j.input_width_m, j.input_height_m, j.input_floors_count,
    j.input_position_x, j.input_position_y
"""


def create_job(tenant_id: str, building_id: str, req: SinglePhotoRequest) -> dict[str, Any]:
    """创建 PENDING 重建 job。

    路由层已校验 building 存在 + 属于当前 tenant。这里只兜底校验 photo
    文件存在 (Path.exists), 不存在抛 FileNotFoundError, 路由层转 400。

    不校验 photo 内容是否真为建筑正脸 -- 照片不对 TripoSplat 会输出
    垃圾 splat 但不报错。用户传错尺寸也不校验, 接受用户输入。
    """
    photo_path = _photo_path_from_id(tenant_id, req.photo_upload_id)
    if not photo_path.exists():
        raise FileNotFoundError(f"照片文件不存在: photo_id={req.photo_upload_id}")

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO core.reconstruction_job
                    (tenant_id, building_id, model_mode, engine, status,
                     input_upload_file_id, input_photo_count,
                     input_length_m, input_width_m, input_height_m,
                     input_floors_count, input_position_x, input_position_y,
                     retry_count, created_at)
                VALUES (%s::uuid, %s::uuid, 'PHOTO_SINGLE', 'TRIPOSPLAT', 'PENDING',
                        %s::uuid, 1,
                        %s, %s, %s, %s, %s, %s,
                        0, now())
                RETURNING id, created_at
            """, (
                tenant_id, building_id, req.photo_upload_id,
                req.length_m, req.width_m, req.height_m,
                req.floors_count, req.position_x, req.position_y,
            ))
            job_id, created_at = cur.fetchone()
        conn.commit()

    logger.info(
        "create_job tenant={} building={} job={} photo={}",
        tenant_id[:8], building_id[:8], str(job_id)[:8], req.photo_upload_id[:8],
    )
    return {
        "id": str(job_id),
        "building_id": building_id,
        "status": "PENDING",
        "engine": "TRIPOSPLAT",
        "model_mode": "PHOTO_SINGLE",
        "retry_count": 0,
        "photo_upload_id": req.photo_upload_id,
        "output_model_id": None,
        "error_message": None,
        "started_at": None,
        "finished_at": None,
        "created_at": created_at.isoformat() if created_at else "",
        "dimensions": {
            "length_m": req.length_m,
            "width_m": req.width_m,
            "height_m": req.height_m,
            "floors_count": req.floors_count,
        },
        "position": {"x": req.position_x, "y": req.position_y},
    }


def get_job(tenant_id: str, job_id: str) -> dict[str, Any] | None:
    """查单个 job 详情。不存在或跨租户返 None (路由层转 404)。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(f"""
                SELECT {_JOB_COLUMNS}
                FROM core.reconstruction_job j
                WHERE j.id = %s::uuid AND j.tenant_id = %s::uuid
            """, (job_id, tenant_id))
            row = cur.fetchone()
    if row is None:
        return None
    return _row_to_job_out(row)


def list_jobs(
    tenant_id: str,
    building_id: str | None = None,
    status: str | None = None,
    limit: int = 20,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """列 job, 可按 building_id + status 过滤。created_at 倒序。"""
    where_clauses = ["j.tenant_id = %s::uuid"]
    params: list[Any] = [tenant_id]
    if building_id:
        where_clauses.append("j.building_id = %s::uuid")
        params.append(building_id)
    if status:
        where_clauses.append("j.status = %s")
        params.append(status)
    where_sql = " AND ".join(where_clauses)

    # 列表场景只取核心字段, dimensions/position 不返 (前端列表用不到)
    sql = f"""
        SELECT j.id, j.building_id, j.status, j.retry_count,
               j.output_model_id, j.error_message,
               j.created_at, j.finished_at
        FROM core.reconstruction_job j
        WHERE {where_sql}
        ORDER BY j.created_at DESC
        LIMIT %s OFFSET %s
    """
    params.extend([limit, offset])

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()

    return [
        {
            "id": str(r[0]),
            "building_id": str(r[1]),
            "status": r[2],
            "retry_count": r[3],
            "output_model_id": str(r[4]) if r[4] else None,
            "error_message": r[5],
            "created_at": r[6].isoformat() if r[6] else "",
            "finished_at": r[7].isoformat() if r[7] else None,
        }
        for r in rows
    ]


def retry_job(tenant_id: str, job_id: str) -> dict[str, Any] | None:
    """手动重试 FAILED job。

    行为: 状态置 PENDING, retry_count 重置为 0, error_message 清空,
    last_retry_at=NULL, started_at/finished_at 也清。重置 retry_count 是因为
    自动重试已耗尽才进 FAILED, 手动 retry 是用户给的机会, 重新给 3 次
    自动重试额度。

    级联清旧 visual_model: 如果上次 job 跑到生成 model 后才失败, 旧 model 记录
    留着会变孤儿 (重试生成新 model_id, 旧的没人引用)。这里跟 delete_job 一样
    显式删 model + 删磁盘目录, 让重试从干净状态开始。

    返 None: job 不存在或跨租户 (路由层 404)。
    返 {"error": "..."}: job 状态不是 FAILED (路由层 400)。
    返 {"id": ..., "status": "PENDING"}: 重试成功。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status, output_model_id FROM core.reconstruction_job
                WHERE id = %s::uuid AND tenant_id = %s::uuid
            """, (job_id, tenant_id))
            row = cur.fetchone()
            if row is None:
                return None
            if row[0] != "FAILED":
                return {"error": f"仅 FAILED 状态的 job 可重试, 当前状态: {row[0]}"}
            old_model_id = row[1]

            # 先删旧 model 记录 (避免 retry 后 output_model_id 设 NULL, 旧 model 变孤儿)
            if old_model_id is not None:
                cur.execute("""
                    DELETE FROM core.building_visual_model
                    WHERE id = %s::uuid AND tenant_id = %s::uuid
                """, (old_model_id, tenant_id))
                logger.info("retry_job 清旧 visual_model: model_id={} deleted={}",
                            old_model_id, cur.rowcount)

            cur.execute("""
                UPDATE core.reconstruction_job
                SET status = 'PENDING',
                    retry_count = 0,
                    error_message = NULL,
                    last_retry_at = NULL,
                    started_at = NULL,
                    finished_at = NULL,
                    output_model_id = NULL
                WHERE id = %s::uuid AND tenant_id = %s::uuid
                RETURNING id
            """, (job_id, tenant_id))
            updated = cur.fetchone()
        conn.commit()

    if updated is None:
        return None

    # 旧产物目录也清掉, worker 重跑时 _ensure_output_dir 会重新建
    if old_model_id is not None:
        job_dir = settings.reconstruction_storage_path / job_id
        if job_dir.exists():
            shutil.rmtree(job_dir, ignore_errors=True)
            logger.info("retry_job 清旧磁盘目录: {}", job_dir)

    logger.info("retry_job tenant={} job={} (manual, retry_count reset)",
                tenant_id[:8], job_id[:8])
    return {"id": str(updated[0]), "status": "PENDING", "retry_count": 0}


def delete_job(tenant_id: str, job_id: str) -> dict[str, Any] | None:
    """硬删 job + 级联删生成的 visual_model + 删磁盘文件目录。

    visual_model 是 job 的产物 (job.output_model_id 指向它), job 删了 model 就是孤儿,
    所以这里一起删。FK 是 SET NULL 不会自动级联, 显式 DELETE 防 7-19 那次 bug 复现
    (job 删了 model 留着, storage_path 指向不存在的 .ply, /splat.ply 接口 404)。

    磁盘文件: backend/storage/reconstruction/{job_id}/ 整目录删, ignore_errors
    防御目录不存在的情况 (已经手动清过 storage 的情况)。

    返 None: job 不存在或跨租户 (路由层 404)。
    返 {"id": ..., "deleted": True}: 删成功。
    """
    with get_conn() as conn:
        with conn.cursor() as cur:
            # 先拿到 output_model_id (FK SET NULL 不会删, 需要显式删 model)
            cur.execute("""
                SELECT output_model_id FROM core.reconstruction_job
                WHERE id = %s::uuid AND tenant_id = %s::uuid
            """, (job_id, tenant_id))
            row = cur.fetchone()
            if row is None:
                return None
            output_model_id = row[0]

            # 删 job 记录
            cur.execute("""
                DELETE FROM core.reconstruction_job
                WHERE id = %s::uuid AND tenant_id = %s::uuid
                RETURNING id
            """, (job_id, tenant_id))
            deleted = cur.fetchone()

            # 级联删生成的 visual_model (如果 job 跑成功并生成了 model)
            if output_model_id is not None:
                cur.execute("""
                    DELETE FROM core.building_visual_model
                    WHERE id = %s::uuid AND tenant_id = %s::uuid
                """, (output_model_id, tenant_id))
                model_deleted = cur.rowcount
                logger.info("delete_job 级联删 visual_model: model_id={} deleted={}",
                            output_model_id, model_deleted)
        conn.commit()

    if deleted is None:
        return None

    job_dir = settings.reconstruction_storage_path / job_id
    if job_dir.exists():
        shutil.rmtree(job_dir, ignore_errors=True)
        logger.info("delete_job 删磁盘目录: {}", job_dir)

    logger.info("delete_job tenant={} job={}", tenant_id[:8], job_id[:8])
    return {"id": str(deleted[0]), "deleted": True}


def resolve_photo_path(tenant_id: str, photo_upload_id: str) -> Path:
    """给 worker 用的辅助: 从 photo_upload_id 解析磁盘路径。

    worker 在 _process_job 启动时调, 找不到文件抛 FileNotFoundError,
    由 worker 主循环捕获走重试机制。
    """
    path = _photo_path_from_id(tenant_id, photo_upload_id)
    if not path.exists():
        raise FileNotFoundError(f"照片文件不存在: photo_id={photo_upload_id}, path={path}")
    return path
