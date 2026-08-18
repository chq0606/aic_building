"""
上传路由:文件上传 / 列映射 / 校验 / commit / 列表。

三个上传档位共享同一套后续接口:
- A 档 POST /uploads/single: 单 CSV,后端自动猜映射,直接走完整流程到 validate
- B 档 POST /uploads/single + 手动调 mapping-suggest/mapping: CSV + 向导调映射
- C 档 POST /uploads/multi: 多文件批量,每个文件各自走 mapping/validate/commit

session 状态机:UPLOADED -> MAPPED -> VALIDATED -> COMMITTED
任何阶段失败置 FAILED,前端拿到后让用户改映射或换文件重来。

校验放在 service 层,路由只管参数解析、状态流转和错误码翻译。
"""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.responses import Response
from loguru import logger

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user, require_write_access
from app.core.response import error, success
from app.db.session import get_db
from app.models.upload import (
    CommitResponse,
    SaveMappingRequest,
    UploadSessionOut,
)
from app.services.mapping_service import suggest_mapping
from app.services.template_service import TEMPLATE_TYPES, get_template_csv
from app.services.upload_service import (
    commit_buildings,
    commit_floors,
    commit_to_staging,
    create_session,
    read_as_long,
    save_photo_file,
    save_upload_file,
    validate_long_df,
)


router = APIRouter(prefix="/uploads", tags=["uploads"])


# 允许的文件后缀。其他后缀直接拒,避免有人传 .exe 之类奇怪的东西。
ALLOWED_SUFFIXES = {".csv", ".xlsx", ".xls"}
# 单文件 50MB 上限。读数文件一般不会超过这个,真超了多半是合错了表。
MAX_FILE_BYTES = 50 * 1024 * 1024

# 照片后缀 + mime 双白名单 (Step 12)。后缀防有人改后缀传 .exe,
# mime 防有人真的改了后缀但 Content-Type 还是 application/octet-stream。
PHOTO_ALLOWED_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
PHOTO_ALLOWED_MIMES = {"image/jpeg", "image/jpg", "image/png", "image/webp"}


def _check_suffix(filename: str) -> None:
    """后缀白名单校验,不让前端传奇怪文件进来。"""
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=f"不支持的文件类型: {suffix}, 仅支持 {sorted(ALLOWED_SUFFIXES)}", code=400),
        )


def _check_photo(filename: str, content_type: str | None) -> None:
    """照片后缀 + mime 双校验。两个都过才放行。"""
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in PHOTO_ALLOWED_SUFFIXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(
                message=f"不支持的图片类型: {suffix}, 仅支持 {sorted(PHOTO_ALLOWED_SUFFIXES)}",
                code=400,
            ),
        )
    if content_type and content_type not in PHOTO_ALLOWED_MIMES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=f"不支持的 mime: {content_type}", code=400),
        )


def _load_session(cur, session_id: str, tenant_id: str) -> dict:
    """
    拉一条 upload_session,带 tenant_id 校验,避免跨租户读别人的会话。
    返回 dict 形式的字段,业务层用着顺手。
    """
    cur.execute(
        """
        SELECT s.id, s.tenant_id, s.upload_file_id, s.target_type, s.status,
               s.mapping_json, s.timezone, s.timestamp_format,
               s.row_count_total, s.row_count_valid, s.row_count_error,
               s.error_summary, s.committed_batch_id,
               s.created_at, s.updated_at,
               f.original_filename, f.storage_path
        FROM ingest.upload_session s
        JOIN ingest.upload_file f ON f.id = s.upload_file_id
        WHERE s.id = %s AND s.tenant_id = %s
        """,
        (session_id, tenant_id),
    )
    row = cur.fetchone()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message="上传会话不存在或不属于当前租户", code=404),
        )
    return {
        "id": str(row[0]),
        "tenant_id": str(row[1]),
        "upload_file_id": str(row[2]),
        "target_type": row[3],
        "status": row[4],
        "mapping_json": row[5],
        "timezone": row[6],
        "timestamp_format": row[7],
        "row_count_total": row[8],
        "row_count_valid": row[9],
        "row_count_error": row[10],
        "error_summary": row[11],
        "committed_batch_id": str(row[12]) if row[12] else None,
        "created_at": row[13],
        "updated_at": row[14],
        "original_filename": row[15],
        "storage_path": row[16],
    }


def _require_status(session: dict, *allowed: str) -> None:
    """状态机校验:session 必须在允许的状态集合里,否则 409。"""
    if session["status"] not in allowed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=error(
                message=f"当前状态 {session['status']} 不允许此操作,期望 {list(allowed)}",
                code=409,
            ),
        )


# ── 上传文件 ────────────────────────────────────────────────

@router.post("/photo", status_code=201)
def upload_photo(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    """通用照片上传接口 (Step 12)。返回 photo_id 给 reconstruction API 用。

    跟 /uploads/single (CSV/XLSX) 分开, 不走 mapping/validate/commit 流程,
    照片不需要解析。worker 拿 photo_id 后从磁盘读文件直接喂 TripoSplat。

    权限: 跟 reconstruction.py 同属"非源数据写操作", demo 也可用。
    /uploads/single /multi /mapping /validate /commit 仍挂 require_write_access
    拦 demo, 那些会改 fact/staging 表属于源数据。照片只写磁盘 + ingest.upload_file,
    不动能耗数据, 跟知识库 PDF / 体块模型 / 异常检测同级。

    mime + 后缀双校验: 防有人改后缀传 .exe。大小上限 photo_max_bytes (10MB),
    比读数文件小 (建筑正脸照片一般 1-5MB 够了)。
    """
    filename = file.filename or "photo.jpg"
    _check_photo(filename, file.content_type)

    content = file.file.read()
    if len(content) > settings.photo_max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=error(
                message=f"照片超过 {settings.photo_max_bytes // 1024 // 1024}MB 限制",
                code=413,
            ),
        )

    photo_id, storage_path = save_photo_file(
        tenant_id=user.tenant_id,
        original_filename=filename,
        file_content=content,
        mime_type=file.content_type,
    )
    logger.info(
        "照片上传成功: tenant={} photo_id={} file={} size={}",
        user.tenant_id, photo_id[:8], filename, len(content),
    )
    return success(
        data={
            "photo_id": photo_id,
            "file_path": str(storage_path),
            "file_size": len(content),
            "mime_type": file.content_type,
        },
        message="照片上传成功, 可用于重建任务",
    )


@router.post("/single", status_code=201)
def upload_single(
    file: UploadFile = File(...),
    target_type: str = "POINT",
    user: CurrentUser = Depends(require_write_access),
    conn=Depends(get_db),
):
    """
    单文件上传(A/B 档共用入口)。
    target_type: POINT / WEATHER / BUILDING,默认 POINT。
    返回 session_id,前端拿到后调 mapping-suggest 走向导。
    """
    _check_suffix(file.filename or "")
    # 先读到内存里:既要算大小上限,save_upload_file 也是从流读的,
    # 直接传 file.file 会让它读不到内容(指针已被我们用过)。用 BytesIO 包一下传进去。
    from io import BytesIO
    content = file.file.read()
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=error(message=f"文件超过 {MAX_FILE_BYTES // 1024 // 1024}MB 限制", code=413),
        )

    upload_file_id, storage_path = save_upload_file(
        tenant_id=user.tenant_id,
        original_filename=file.filename or "upload.csv",
        file_content=BytesIO(content),
        mime_type=file.content_type,
    )

    session_id = create_session(
        tenant_id=user.tenant_id,
        upload_file_id=upload_file_id,
        target_type=target_type,
    )
    logger.info(
        "单文件上传成功: tenant={} session={} file={} size={}",
        user.tenant_id, session_id, file.filename, len(content),
    )
    return success(
        data={"session_id": session_id, "upload_file_id": upload_file_id},
        message="上传成功,请继续配置列映射",
    )


@router.post("/multi", status_code=201)
def upload_multi(
    files: list[UploadFile] = File(...),
    target_type: str = "POINT",
    user: CurrentUser = Depends(require_write_access),
    conn=Depends(get_db),
):
    """
    多文件批量上传(C 档)。
    每个文件各建一个 session,前端拿到 session 列表后逐个配置映射并 commit。
    部分文件失败不影响其他文件,失败的会在 result.errors 里列出。
    """
    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message="未提供任何文件", code=400),
        )

    from io import BytesIO

    results: list[dict] = []
    errors: list[dict] = []
    for f in files:
        try:
            _check_suffix(f.filename or "")
            content = f.file.read()
            if len(content) > MAX_FILE_BYTES:
                errors.append({"filename": f.filename, "reason": "文件过大"})
                continue
            upload_file_id, storage_path = save_upload_file(
                tenant_id=user.tenant_id,
                original_filename=f.filename or "upload.csv",
                file_content=BytesIO(content),
                mime_type=f.content_type,
            )
            session_id = create_session(
                tenant_id=user.tenant_id,
                upload_file_id=upload_file_id,
                target_type=target_type,
            )
            results.append({
                "filename": f.filename,
                "session_id": session_id,
                "upload_file_id": upload_file_id,
            })
        except HTTPException as e:
            errors.append({"filename": f.filename, "reason": e.detail.get("message") if isinstance(e.detail, dict) else str(e.detail)})
        except Exception as e:
            logger.exception("多文件上传单文件失败: {}", f.filename)
            errors.append({"filename": f.filename, "reason": str(e)})

    return success(
        data={"sessions": results, "errors": errors},
        message=f"批量上传完成: 成功 {len(results)} 个, 失败 {len(errors)} 个",
    )


# ── 列映射 ──────────────────────────────────────────────────

@router.get("/{session_id}/mapping-suggest")
def mapping_suggest(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
):
    """
    读文件 header + 前 5 行,猜列映射。
    返回原始列名 + 猜出来的 MappingConfig + 前 5 行样例。
    猜不出来就留 None,让用户在向导里手选。
    """
    with conn.cursor() as cur:
        session = _load_session(cur, session_id, user.tenant_id)

    from pathlib import Path
    try:
        columns, suggested, sample = suggest_mapping(Path(session["storage_path"]))
    except Exception as e:
        logger.exception("列名猜测失败: session={}", session_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=f"读文件失败: {e}", code=400),
        )

    return success(data={
        "columns": columns,
        "suggested": suggested.model_dump(),
        "sample_rows": sample,
    })


@router.post("/{session_id}/mapping")
def save_mapping(
    session_id: str,
    req: SaveMappingRequest,
    user: CurrentUser = Depends(require_write_access),
    conn=Depends(get_db),
):
    """
    保存用户确认/调整后的映射。状态 UPLOADED -> MAPPED。
    profile_name 非空时同时存一份到 mapping_profile,下次同类文件直接套用。
    """
    with conn.cursor() as cur:
        session = _load_session(cur, session_id, user.tenant_id)
        _require_status(session, "UPLOADED", "MAPPED", "VALIDATED", "FAILED")

        import json
        mapping_json = json.dumps(req.mapping.model_dump(), ensure_ascii=False)
        cur.execute(
            """
            UPDATE ingest.upload_session
            SET mapping_json = %s,
                timezone = %s,
                timestamp_format = %s,
                status = 'MAPPED',
                row_count_total = 0,
                row_count_valid = 0,
                row_count_error = 0,
                error_summary = NULL
            WHERE id = %s
            """,
            (mapping_json, req.mapping.timezone, req.mapping.timestamp_format, session_id),
        )

        profile_id = None
        if req.profile_name:
            from app.services.mapping_service import save_profile
            profile_id = save_profile(
                tenant_id=user.tenant_id,
                profile_name=req.profile_name,
                template_type=session["target_type"],
                mapping=req.mapping,
                unit_rules={},
            )
            cur.execute(
                "UPDATE ingest.upload_session SET mapping_profile_id = %s WHERE id = %s",
                (profile_id, session_id),
            )
        conn.commit()

    logger.info("映射已保存: session={} profile={}", session_id, profile_id)
    return success(data={"session_id": session_id, "profile_id": profile_id}, message="映射已保存")


# ── 校验 ────────────────────────────────────────────────────

@router.post("/{session_id}/validate", response_model=None)
def validate(
    session_id: str,
    user: CurrentUser = Depends(require_write_access),
    conn=Depends(get_db),
):
    """
    按当前 mapping 读文件转长表并校验。状态 MAPPED -> VALIDATED/FAILED。
    校验错误不进 staging,等用户改映射或改文件后重 validate。
    """
    from pathlib import Path
    from app.models.upload import MappingConfig

    with conn.cursor() as cur:
        session = _load_session(cur, session_id, user.tenant_id)
        _require_status(session, "MAPPED", "VALIDATED", "FAILED")

        if not session["mapping_json"]:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=error(message="尚未配置映射,请先调 mapping-suggest / mapping", code=400),
            )
        mapping = MappingConfig(**session["mapping_json"])

    try:
        long_df = read_as_long(Path(session["storage_path"]), mapping)
    except Exception as e:
        logger.exception("读文件转长表失败: session={}", session_id)
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE ingest.upload_session
                SET status = 'FAILED', error_summary = %s
                WHERE id = %s
                """,
                (str(e)[:500], session_id),
            )
            conn.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=f"读文件失败: {e}", code=400),
        )

    result = validate_long_df(long_df, mapping)

    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE ingest.upload_session
            SET status = %s,
                row_count_total = %s,
                row_count_valid = %s,
                row_count_error = %s,
                error_summary = %s
            WHERE id = %s
            """,
            (
                "VALIDATED" if result["can_commit"] else "FAILED",
                result["row_count_total"],
                result["row_count_valid"],
                result["row_count_error"],
                None if result["can_commit"] else f"{result['row_count_error']} 行校验失败",
                session_id,
            ),
        )
        conn.commit()

    return success(data=result, message="校验完成" if result["can_commit"] else "校验未通过,请修正后重试")


# ── commit ──────────────────────────────────────────────────

@router.post("/{session_id}/commit", response_model=None)
def commit(
    session_id: str,
    user: CurrentUser = Depends(require_write_access),
    conn=Depends(get_db),
):
    """
    commit: 按 target_type 分支。
      - FLOOR: 直接读 CSV 写 core.floor (不需要 mapping, UPLOADED 状态可调)
      - POINT/WEATHER/BUILDING: 写 staging_reading, 等 merge 到 fact (需 VALIDATED)
    """
    from pathlib import Path
    from app.models.upload import MappingConfig

    with conn.cursor() as cur:
        session = _load_session(cur, session_id, user.tenant_id)
        target_type = session["target_type"]

        if target_type in ("FLOOR", "BUILDING"):
            # FLOOR / BUILDING 不走 mapping/validate, UPLOADED 即可 commit
            _require_status(session, "UPLOADED", "COMMITTED")
        else:
            _require_status(session, "VALIDATED")
            if not session["mapping_json"]:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error(message="映射丢失,请重新配置", code=400),
                )

    try:
        if target_type == "FLOOR":
            batch_id, row_count = commit_floors(
                session_id=session_id,
                tenant_id=user.tenant_id,
                file_path=Path(session["storage_path"]),
            )
        elif target_type == "BUILDING":
            batch_id, row_count = commit_buildings(
                session_id=session_id,
                tenant_id=user.tenant_id,
                file_path=Path(session["storage_path"]),
            )
        else:
            mapping = MappingConfig(**session["mapping_json"])
            batch_id, row_count = commit_to_staging(
                session_id=session_id,
                tenant_id=user.tenant_id,
                file_path=Path(session["storage_path"]),
                mapping=mapping,
            )
    except ValueError as e:
        # 用户输入错误 (非法 floor_type / 缺列 / 楼栋不存在等) 返 400, 不是 500
        logger.warning("commit 校验失败: session={} err={}", session_id, e)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ingest.upload_session SET status = 'FAILED', error_summary = %s WHERE id = %s",
                (str(e)[:500], session_id),
            )
            conn.commit()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=f"commit 校验失败: {e}", code=400),
        )
    except Exception as e:
        logger.exception("commit 失败: session={}", session_id)
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE ingest.upload_session SET status = 'FAILED', error_summary = %s WHERE id = %s",
                (str(e)[:500], session_id),
            )
            conn.commit()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=error(message=f"commit 失败: {e}", code=500),
        )

    return success(
        data=CommitResponse(
            session_id=session_id,
            batch_id=batch_id,
            row_count_inserted=row_count,
        ).model_dump(),
        message="commit 成功"
        + (", 楼层已写入" if target_type == "FLOOR"
           else (", 建筑信息已写入" if target_type == "BUILDING"
                 else ", 数据已进入 staging, 等待 merge 到 fact 表")),
    )


# ── 列表 / 详情 ────────────────────────────────────────────

@router.get("")
def list_sessions(
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
):
    """列出租户所有上传会话,按创建时间倒序。"""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT s.id, s.status, s.target_type, f.original_filename,
                   s.row_count_total, s.row_count_valid, s.row_count_error,
                   s.error_summary, s.committed_batch_id,
                   s.created_at, s.updated_at
            FROM ingest.upload_session s
            JOIN ingest.upload_file f ON f.id = s.upload_file_id
            WHERE s.tenant_id = %s
            ORDER BY s.created_at DESC
            """,
            (user.tenant_id,),
        )
        rows = cur.fetchall()

    return success(data=[
        UploadSessionOut(
            id=str(r[0]),
            status=r[1],
            target_type=r[2],
            original_filename=r[3],
            row_count_total=r[4] or 0,
            row_count_valid=r[5] or 0,
            row_count_error=r[6] or 0,
            error_summary=r[7],
            committed_batch_id=str(r[8]) if r[8] else None,
            created_at=r[9].isoformat() if r[9] else "",
            updated_at=r[10].isoformat() if r[10] else "",
        ).model_dump()
        for r in rows
    ])


# ── 模板下载 (Step 16) ────────────────────────────────────────
# 这两个路由必须放在 /{session_id} 之前声明, 否则 FastAPI 路由匹配时
# GET /uploads/templates 会被 /{session_id} 当成 session_id="templates"
# 捕获, 模板下载会 404。

@router.get("/templates")
def list_templates(
    user: CurrentUser = Depends(get_current_user),
):
    """列出可下载的模板类型 + 中文说明。前端模板下载卡片用这个渲染按钮列表。"""
    return success(data=[
        {"type": k, "label": v, "filename": f"{k}.csv"}
        for k, v in TEMPLATE_TYPES.items()
    ])


@router.get("/templates/{template_type}")
def download_template(
    template_type: str,
    user: CurrentUser = Depends(get_current_user),
):
    """下载 CSV 模板。返回 text/csv, 浏览器自动触发下载。

    三个模板:
      buildings -> 建筑基础信息 (target_type=BUILDING)
      readings  -> 能耗读数长表 (target_type=POINT)
      weather   -> 气象读数       (target_type=WEATHER)

    只读接口, demo 账号也能下 (下载模板不算写操作)。
    走 Response 直接返 text/csv, 不走 success/error 包装, 避免 <a download> 触发
    下载时拿到 JSON 反而无法被 Excel 解析。
    """
    filename, csv_content = get_template_csv(template_type)
    # utf-8-sig 带 BOM, Excel 双击打开不会乱码 (Excel 对纯 utf-8 中文 header
    # 有时会按 GBK 解析, BOM 是它认 utf-8 的信号)
    encoded = csv_content.encode("utf-8-sig")
    logger.info("download_template: user={} type={}", user.username, template_type)
    return Response(
        content=encoded,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
        },
    )


@router.get("/{session_id}")
def get_session(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    conn=Depends(get_db),
):
    """单个会话详情,含映射 JSON 给前端向导回填。"""
    with conn.cursor() as cur:
        session = _load_session(cur, session_id, user.tenant_id)
    return success(data={
        "id": session["id"],
        "status": session["status"],
        "target_type": session["target_type"],
        "original_filename": session["original_filename"],
        "mapping": session["mapping_json"],
        "timezone": session["timezone"],
        "timestamp_format": session["timestamp_format"],
        "row_count_total": session["row_count_total"],
        "row_count_valid": session["row_count_valid"],
        "row_count_error": session["row_count_error"],
        "error_summary": session["error_summary"],
        "committed_batch_id": session["committed_batch_id"],
        "created_at": session["created_at"].isoformat() if session["created_at"] else "",
        "updated_at": session["updated_at"].isoformat() if session["updated_at"] else "",
    })
