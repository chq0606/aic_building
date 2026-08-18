"""
检索 API 的请求模型。

POST /api/v1/knowledge/search 的 body 走 SearchRequest:
  {
    "query": "公共建筑 EUI 限值",
    "top_k": 10,
    "filters": {
      "standard_no": "GB 55015-2021",
      "page_range": [1, 50]
    }
  }

字段命名走 snake_case (跟其他模块一致), 前端 axios 直接用。

不写 ChunkResult 响应模型: 路由层挂 response_model 会和统一响应结构
{code, message, data} 冲突 (历史踩过的坑), chunks 直接当 list[dict] 返,
字段在 retrieval_service.hybrid_search 注释里列清楚。
"""
from pydantic import BaseModel, Field, field_validator


class SearchFilters(BaseModel):
    """检索 metadata 过滤条件 (全部可选, AND 关系)。

    section_type 字段 step09 没写入 chunk.metadata (一期保留接口不实现),
    传了会被 retrieval_service 忽略, 不报错。
    """
    standard_no: str | None = Field(None, examples=["GB 55015-2021"])
    section_type: str | None = None
    page_range: list[int] | None = Field(None, description="[min, max] 闭区间页码范围", examples=[[1, 50]])

    @field_validator("page_range")
    @classmethod
    def _check_page_range(cls, v: list[int] | None) -> list[int] | None:
        if v is None:
            return None
        if len(v) != 2:
            raise ValueError("page_range 必须是 [min, max] 两个元素")
        if v[0] < 1 or v[1] < 1:
            raise ValueError("page_range 页码从 1 开始, 不能小于 1")
        if v[0] > v[1]:
            raise ValueError("page_range min 不能大于 max")
        return v


class SearchRequest(BaseModel):
    """检索请求体。query 必填非空, top_k 和 filters 可选。"""
    query: str = Field(..., examples=["公共建筑 EUI 限值"])
    top_k: int | None = Field(None, description="不传走 settings.retrieval_top_k_default, 最大 retrieval_top_k_max", examples=[10])
    filters: SearchFilters | None = None

    @field_validator("query")
    @classmethod
    def _check_query(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("query 不能为空")
        return v.strip()

    @field_validator("top_k")
    @classmethod
    def _check_top_k(cls, v: int | None) -> int | None:
        if v is not None and v < 1:
            raise ValueError("top_k 必须 >= 1")
        return v
