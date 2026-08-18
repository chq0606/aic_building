"""
Step 17 系统设置 - 请求/响应模型。

GLM Key 测试和保存接口共用 GlmApiKeyRequest body:
  - 测试时只填 api_key (不写入 DB, 临时测一下)
  - 保存时 api_key 必填

为什么不拆成 SaveGlmKeyRequest 和 TestGlmKeyRequest 两个 model:
  两边字段重叠 (都是 api_key), 拆开后续加字段 (e.g. model_name) 要改两处。
  测试和保存语义不同靠 endpoint 区分 (POST /test-glm vs PUT /glm-api-key),
  body model 共用没歧义。
"""
from pydantic import BaseModel, Field, field_validator


class GlmApiKeyRequest(BaseModel):
    """GLM API Key 请求体。api_key 必填非空。"""
    api_key: str = Field(..., description="智谱 GLM API Key, 形如 xxx.xxxxxx")

    @field_validator("api_key")
    @classmethod
    def _check_api_key(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("api_key 不能为空")
        return v.strip()


class GlmKeyStatusResponse(BaseModel):
    """GLM Key 配置状态 (脱敏), 前端展示用。"""
    configured: bool
    hint: str | None = None
    updated_at: str | None = None  # ISO8601 字符串, 前端 dayjs 解析


class TestResultResponse(BaseModel):
    """通用测试结果。GLM 和 BGE 共用。"""
    ok: bool
    message: str
    detail: str | None = None


class BgeStatusResponse(BaseModel):
    """BGE 模型当前状态。"""
    configured: bool
    model_path: str
    expected_dim: int
    actual_dim: int | None = None
    loaded: bool
    device: str | None = None


class DataSourceStatusResponse(BaseModel):
    """demo 数据源状态 (给 DataSourceConfig 展示用)。"""
    building_count: int
    point_count: int
    reading_count: int
    anomaly_count: int
    last_seed_at: str | None = None       # ISO8601
    last_seed_batch_id: str | None = None
    last_seed_status: str | None = None
