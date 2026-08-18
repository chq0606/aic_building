"""
mapping_profile 的请求/响应模型。

mapping_profile 是租户级的可复用映射模板。客户第一次上传某类文件时
配置好映射，保存成 profile，下次同类文件直接套用。

一个租户可以有多个 profile，按 template_type（POINT/WEATHER/BUILDING）分类。
"""
from pydantic import BaseModel, Field

from app.models.upload import MappingConfig


class MappingProfileCreate(BaseModel):
    """创建 mapping_profile。"""
    profile_name: str = Field(..., examples=["BDG2 长表格式"])
    template_type: str = Field(..., examples=["POINT"])
    mapping: MappingConfig
    unit_rules: dict = Field(
        default_factory=dict,
        examples=[{"kWh": "energy_kwh", "L": "volume_liter"}],
        description="单位字符串到 measure_kind 的映射",
    )


class MappingProfileOut(BaseModel):
    """mapping_profile 对外结构。"""
    id: str
    profile_name: str
    template_type: str
    mapping: MappingConfig
    unit_rules: dict
    created_at: str


class MappingProfileList(BaseModel):
    """mapping_profile 列表项。"""
    id: str
    profile_name: str
    template_type: str
    created_at: str
