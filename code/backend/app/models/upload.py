"""
上传模块的请求/响应模型。

核心抽象是 MappingConfig：一个上传文件的列映射配置。
长表和宽表都用这个表达，宽表时多填 wide_melt 字段。

长表示例（一行一个读数）：
  timestamp, building_id, energy_type, value, unit
  2017-01-01 00:00, B1, electricity, 12.5, kWh

宽表示例（每列一个测点）：
  timestamp, B1__electricity, B1__water, B2__electricity
  2017-01-01 00:00, 12.5, 3.2, 10.1

宽表要走 melt：id_cols=[timestamp], value_cols=[B1__electricity, B1__water, B2__electricity]，
parse_rules 告诉后端怎么从列名拆出 (building, energy)。
"""
from pydantic import BaseModel, Field


class WideMeltRule(BaseModel):
    """
    宽表转长表的拆解规则。

    id_cols: 作为标识的列，通常是 [timestamp]，也可以包含 [timestamp, building_id]
             （如果宽表已经按 building 分了多个 block）
    value_cols: 哪些列是 value 列，每列一个测点
    column_parse: 列名拆解规则
      - "underscore_2": 列名用下划线分两段，前段 building 后段 energy（B1__electricity）
      - "underscore_3": 三段，前两段合并为 building，第三段 energy
      - "fixed_building": 所有 value 列都属于同一个 building，building 从外部指定
      - "regex": 用正则拆（高级，一期可不实现）
    fixed_building: column_parse=fixed_building 时用，指定 building_id
    """
    id_cols: list[str] = Field(..., examples=[["timestamp"]])
    value_cols: list[str] = Field(..., examples=[["B1__electricity", "B1__water"]])
    column_parse: str = Field("underscore_2", examples=["underscore_2"])
    fixed_building: str | None = None


class MappingConfig(BaseModel):
    """
    列映射配置。长表和宽表统一用这个。

    长表：填 timestamp_col / building_col / energy_col / value_col / unit_col
    宽表：填 timestamp_col（=wide_melt.id_cols[0]）+ wide_melt
    """
    timestamp_col: str = Field(..., examples=["timestamp"])
    building_col: str | None = Field(None, examples=["building_id"])
    energy_col: str | None = Field(None, examples=["energy_type"])
    value_col: str | None = Field(None, examples=["value"])
    unit_col: str | None = Field(None, examples=["unit"])
    quality_col: str | None = None
    # 楼层列 (Step 11a-6): 不填 = 楼栋级 METER, 填了 = 楼层级 SENSOR
    # 用户上传真实楼层数据时, CSV 里有 floor_number 列, 这里映射到 "floor_number"
    floor_col: str | None = Field(None, examples=["floor_number"])

    # 宽表专用。None 表示长表
    wide_melt: WideMeltRule | None = None

    # 默认值（列缺失时用）
    default_energy_type: str | None = Field(None, examples=["electricity"])
    default_unit: str | None = Field(None, examples=["kWh"])

    # 时区 + 时间格式
    timezone: str = Field("UTC", examples=["UTC", "Asia/Shanghai", "US/Mountain"])
    timestamp_format: str | None = Field(
        None,
        examples=[None, "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S%z"],
        description="None 表示让 pandas 自动猜",
    )


class MappingSuggestResponse(BaseModel):
    """GET /uploads/{id}/mapping-suggest 的响应。后端猜出来的列映射 + 原始列名列表。"""
    columns: list[str]
    suggested: MappingConfig
    sample_rows: list[dict]  # 前 5 行样例，帮助用户确认


class SaveMappingRequest(BaseModel):
    """POST /uploads/{id}/mapping 的请求体。用户确认或调整后的映射。"""
    mapping: MappingConfig
    profile_name: str | None = Field(
        None,
        description="非空则同时保存为 mapping_profile 供下次复用",
    )


class ValidateResponse(BaseModel):
    """POST /uploads/{id}/validate 的响应。"""
    row_count_total: int
    row_count_valid: int
    row_count_error: int
    errors: list[dict]  # 前 20 条错误详情，避免响应过大
    can_commit: bool


class CommitResponse(BaseModel):
    """POST /uploads/{id}/commit 的响应。"""
    session_id: str
    batch_id: str  # commit 后生成的 import_batch id，Step 05 merge 用
    row_count_inserted: int


class UploadSessionOut(BaseModel):
    """上传会话的对外结构。"""
    id: str
    status: str
    target_type: str
    original_filename: str
    row_count_total: int
    row_count_valid: int
    row_count_error: int
    error_summary: str | None
    committed_batch_id: str | None
    created_at: str
    updated_at: str
