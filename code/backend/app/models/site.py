"""
园区创建请求模型 (Step 16 补充)。

POST /sites 的 body。site_code 可空, 后端自动生成 site_<8位hex>;
site_name 必填。timezone 默认 UTC (前端顶栏新建园区时基本用不到,
但预留字段跟 core.site.timezone 对齐)。
"""
from pydantic import BaseModel, Field


class SiteCreateRequest(BaseModel):
    site_name: str = Field(..., min_length=1, max_length=200, description="园区名称")
    site_code: str | None = Field(None, max_length=100, description="园区编码, 可空自动生成")
    timezone: str = Field("UTC", max_length=64, description="时区, 默认 UTC")
