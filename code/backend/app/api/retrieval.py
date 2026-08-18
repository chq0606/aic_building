"""
知识库检索 API 路由。

POST /api/v1/knowledge/search 三路混检 (向量 + 关键词 + metadata 过滤),
返回 top-k chunk。

读操作, demo 用户可用 (不挂 require_write_access, 挂 get_current_user)。
强租户隔离: hybrid_search 内部 SQL JOIN document 拿 tenant_id 过滤。

返回结构: success(data={"query": ..., "top_k": ..., "weights": {...},
"chunks": [...]})。chunks 字段在 retrieval_service.hybrid_search 注释里
列清楚 (chunk_id / content / doc_id / page_no / section_title /
section_path / metadata / standard_no / score_vector / score_keyword /
score_vector_norm / score_keyword_norm / score_meta / score_final /
metadata_match)。

查不到匹配 chunk 时返 200 + chunks=[] (不是 404, 检索没结果是正常情况,
不是错误, 跟 SQL 查询返空 list 一个语义)。
"""
from fastapi import APIRouter, Depends, HTTPException, status

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.core.response import error, success
from app.models.retrieval import SearchRequest
from app.services.retrieval_service import RetrievalError, hybrid_search


router = APIRouter(prefix="/knowledge", tags=["retrieval"])


@router.post("/search")
def search(
    req: SearchRequest,
    user: CurrentUser = Depends(get_current_user),
):
    """三路混检: pgvector 向量 + tsvector 关键词 + jsonb metadata 过滤。"""
    # top_k 校验: 上限在 service 层用 settings.retrieval_top_k_max 兜底, 这里
    # 不重复校验, 避免双重限制 (用户传 100 时 service 层会自动截到 max=50)
    filters_dict = req.filters.model_dump() if req.filters else None
    try:
        chunks = hybrid_search(
            query=req.query,
            tenant_id=user.tenant_id,
            top_k=req.top_k,
            filters=filters_dict,
        )
    except RetrievalError as ex:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=error(message=str(ex), code=400),
        )

    return success(data={
        "query": req.query,
        "top_k": len(chunks),
        "weights": {
            "vector": settings.retrieval_weight_vector,
            "keyword": settings.retrieval_weight_keyword,
            "metadata": settings.retrieval_weight_metadata,
        },
        "chunks": chunks,
    })
