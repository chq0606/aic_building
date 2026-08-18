"""
知识库文档 API 路由。

5 个接口:
  POST   /knowledge/documents                  上传 PDF
  POST   /knowledge/documents/{doc_id}/parse   触发解析 (异步)
  POST   /knowledge/documents/{doc_id}/embed   触发 embedding (异步)
  GET    /knowledge/documents                  列表
  DELETE /knowledge/documents/{doc_id}         删除

知识库不属源数据 (能耗数据), demo 账号也可用 - 只挂 get_current_user 不挂
require_write_access。源数据相关 (upload/imports/seed) 才拦 demo。

parse 和 embed 用 FastAPI BackgroundTasks 异步跑, 接口立即返回 202。
任务失败时把状态写回 document 表 (status=failed, parse_error=...),
前端轮询 GET /documents 看进度。

文件大小上限 200MB (knowledge_service.MAX_FILE_SIZE)。
"""
from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    HTTPException,
    UploadFile,
    status,
)
from loguru import logger

from app.core.deps import CurrentUser, get_current_user
from app.core.response import error, success
from app.services.knowledge_service import (
    KnowledgeError,
    delete_document,
    embed_document,
    list_documents,
    parse_document,
    upload_document,
)


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.post("/documents", status_code=201)
def upload_doc(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(get_current_user),
):
    """上传 PDF, 返回 doc_id, status=pending。需再调 /parse 触发解析。"""
    content = file.file.read()
    try:
        result = upload_document(
            tenant_id=user.tenant_id,
            filename=file.filename or "upload.pdf",
            file_data=content,
            mime_type=file.content_type or "application/pdf",
        )
    except KnowledgeError as ex:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=str(ex), code=400),
        )
    return success(data=result, message="上传成功, 请调 /parse 触发解析")


@router.post("/documents/{doc_id}/parse", status_code=202)
def trigger_parse(
    doc_id: str,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
):
    """触发后台解析。立即返回, 解析结果异步写回 document 表。

    调用方需轮询 GET /knowledge/documents 看 status 是否变成 parsed/failed。
    """
    # 先在路由层校验文档存在 (同步), 没问题再起后台任务, 避免后台才发现 doc_id 不对
    # 让接口报 404 而不是 202 后台默默失败
    from app.db.session import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT id FROM knowledge.document
                WHERE id = %s AND tenant_id = %s
            """, (doc_id, user.tenant_id))
            if cur.fetchone() is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=error(message=f"文档不存在: {doc_id}", code=404),
                )

    background_tasks.add_task(_run_parse_safely, doc_id, user.tenant_id)
    return success(data={"doc_id": doc_id, "status": "parsing"}, message="解析任务已提交")


@router.post("/documents/{doc_id}/embed", status_code=202)
def trigger_embed(
    doc_id: str,
    background_tasks: BackgroundTasks,
    user: CurrentUser = Depends(get_current_user),
):
    """触发后台 embedding。要求文档已 parsed, 否则 400。"""
    from app.db.session import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT status FROM knowledge.document
                WHERE id = %s AND tenant_id = %s
            """, (doc_id, user.tenant_id))
            row = cur.fetchone()
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=error(message=f"文档不存在: {doc_id}", code=404),
                )
            if row[0] != "parsed":
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=error(message=f"文档状态 {row[0]}, 需先解析到 parsed", code=400),
                )

    background_tasks.add_task(_run_embed_safely, doc_id, user.tenant_id)
    return success(data={"doc_id": doc_id, "status": "embedding"}, message="embedding 任务已提交")


@router.get("/documents")
def list_docs(
    status_filter: str | None = None,
    user: CurrentUser = Depends(get_current_user),
):
    """列出租户所有文档。可按 status 过滤: pending/parsing/parsed/failed。"""
    try:
        result = list_documents(tenant_id=user.tenant_id, status=status_filter)
    except KnowledgeError as ex:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=str(ex), code=400),
        )
    return success(data=result)


@router.delete("/documents/{doc_id}")
def delete_doc(
    doc_id: str,
    user: CurrentUser = Depends(get_current_user),
):
    """删除文档: DB 记录 + chunk + 本地文件。"""
    try:
        result = delete_document(doc_id=doc_id, tenant_id=user.tenant_id)
    except KnowledgeError as ex:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=error(message=str(ex), code=404),
        )
    return success(data=result, message="文档已删除")


# 后台任务的安全包装: 吞掉异常只 log, 避免 BackgroundTasks 把异常喷到 uvicorn 日志
# 看着像崩了 (实际只是单文档解析失败, document 表里已经写了 failed 状态)。
def _run_parse_safely(doc_id: str, tenant_id: str) -> None:
    try:
        parse_document(doc_id, tenant_id)
    except Exception as e:
        logger.exception("后台解析失败 doc_id={}: {}", doc_id, e)


def _run_embed_safely(doc_id: str, tenant_id: str) -> None:
    try:
        embed_document(doc_id, tenant_id)
    except Exception as e:
        logger.exception("后台 embedding 失败 doc_id={}: {}", doc_id, e)
