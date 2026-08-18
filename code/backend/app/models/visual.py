"""
体块模式后端的请求/响应模型 (Step 11)。

四个对外接口对应四组 schema:
  POST /buildings/{id}/visual-models/block -> BlockModelRequest
  GET  /sites/{id}/scene                   -> SceneResponse
  GET  /buildings/{id}/visual-model         -> VisualModelOut
  DELETE /buildings/{id}/visual-models/{mid} (无 body)

不挂 response_model 到路由上: 项目统一响应 {code, message, data},
路由层挂 response_model 会和这个 wrapper 冲突 (见 retrieval.py 注释),
schema 只在 service 层组装时当结构定义用, 不挂到 FastAPI 上做自动序列化。

颜色 metric 字段约定:
  metric in {eui, total_kwh, anomaly_count}
  value 是原始值 (EUI kWh/m2 / 总能耗 kWh / 异常数), level 是分档后的字符串
  level in {low, mid, high, critical}

model_kind 优先级 splat > block, scene 返回每栋楼当前用的 kind:
  block     - 用户提交过体块参数 (DB 有 BLOCK 记录)
  splat     - 用户上传照片重建过 (DB 有 PHOTO_SINGLE / PHOTO_MULTI 记录)
  estimated - DB 没记录, service 层用 sqm + floors_count 自动估算
"""
from pydantic import BaseModel, Field, field_validator


class BlockModelRequest(BaseModel):
    """POST /buildings/{id}/visual-models/block 的 body。

    客户提交体块尺寸, 后端落 visual_model 记录 model_mode='BLOCK',
    render_format='BOX'。position 是园区内米制相对坐标, 可空 (没设过
    时前端布局算法兜底)。length/width/height 都必须 > 0, floors_count >= 1。
    yaw_deg 是水平旋转角度 (0-360 度), 可空 (默认 0 不旋转)。
    """

    length_m: float = Field(..., gt=0, le=100000, description="体块长度(米)")
    width_m: float = Field(..., gt=0, le=100000, description="体块宽度(米)")
    height_m: float = Field(..., gt=0, le=100000, description="体块总高(米)")
    floors_count: int = Field(..., ge=1, le=200, description="楼层数")
    position_x: float | None = Field(None, description="园区内 X 坐标(米), 可空")
    position_y: float | None = Field(None, description="园区内 Y 坐标(米), 可空")
    yaw_deg: float | None = Field(
        None, ge=0, lt=360, description="水平旋转角度(度, 0-360), 可空默认 0"
    )


class YawUpdateRequest(BaseModel):
    """PATCH /buildings/{id}/visual-models/yaw 的 body。

    只更新当前 active visual_model 的 yaw_deg, 不动其他字段。滑块拖动时
    前端 debounce 300ms 后调一次, 比走 POST /block 全量 upsert 轻。
    """

    yaw_deg: float = Field(..., ge=0, lt=360, description="水平旋转角度(度, 0-360)")


class BuildingUseUpdateRequest(BaseModel):
    """PATCH /buildings/{id}/use 的 body。

    更新 core.building 的用途字段, 决定 3D 页程序化表皮 + 屋顶类型。
    primary_use / sub_use 传 None 表示清空 (回到默认外观)。
    """

    primary_use: str | None = Field(None, description="主要用途 (如 Education / Commercial)")
    sub_use: str | None = Field(None, description="细分用途 (如 Academic / Retail / Residential)")


class Dimensions(BaseModel):
    """体块尺寸。统一给 scene API 和 building 详情接口用。"""

    length_m: float | None = None
    width_m: float | None = None
    height_m: float | None = None
    floors_count: int | None = None


class Position(BaseModel):
    """园区内相对坐标(米) + 水平旋转角度。"""

    x: float | None = None
    y: float | None = None
    yaw_deg: float | None = Field(
        None, description="水平旋转角度(度, 0-360), 默认 0 不旋转"
    )


class ColorMetric(BaseModel):
    """scene API 按 metric 算 value 和 level, 给前端 BoxGeometry 着色用。

    level 四档: low (绿) / mid (黄) / high (橙) / critical (红)。
    阈值在 config.py 里, 可调。
    """

    metric: str = Field(..., description="eui / total_kwh / anomaly_count")
    value: float | None = Field(None, description="metric 对应的原始值")
    level: str = Field(..., description="low / mid / high / critical")

    @field_validator("level")
    @classmethod
    def _check_level(cls, v: str) -> str:
        if v not in ("low", "mid", "high", "critical"):
            raise ValueError(f"level 取值非法: {v}")
        return v


class AnomalyStatus(BaseModel):
    """building 异常摘要, scene API 给前端在体块上叠角标用。

    severity_max 取 LOW/MEDIUM/HIGH 中最高的一档, 没异常时 None。
    by_severity 三档分别计数, 前端 tooltip 展开。
    """

    has_anomaly: bool
    severity_max: str | None = Field(None, description="LOW / MEDIUM / HIGH, 无异常 None")
    count: int = Field(0, description="异常总数")
    by_severity: dict[str, int] = Field(
        default_factory=lambda: {"LOW": 0, "MEDIUM": 0, "HIGH": 0},
        description="按严重度分组计数",
    )


class EnergyCompositionBadge(BaseModel):
    """building 能源构成小卡片。

    复用 query_service.get_building_energy_composition 的 composition 字段,
    结构 [{type, kwh, pct}], 给前端做小型堆叠条用。
    """

    composition: list[dict] = Field(default_factory=list)


class BuildingScene(BaseModel):
    """单栋楼在 scene API 里的完整元数据。

    model_kind 是场景渲染分流的关键字段:
      block     - 用 BoxGeometry 按 dimensions 渲染
      splat     - 用 splat loader 加载 .ply/.splat 文件 (storage_path 指向)
      estimated - 跟 block 一样渲染, 但前端可以加个虚线框表示是估算值
    """

    building_id: str
    building_code: str | None = None
    display_name: str | None = None
    model_kind: str = Field(..., description="block / splat / estimated")
    dimensions: Dimensions
    position: Position
    color_metric: ColorMetric
    anomaly_status: AnomalyStatus
    energy_composition: EnergyCompositionBadge
    model_id: str | None = Field(None, description="estimated 时为 None")


class SceneResponse(BaseModel):
    """GET /sites/{id}/scene 的响应。一次返整个园区的 3D 元数据。"""

    site_id: str
    time_range: dict
    metric: str
    buildings: list[BuildingScene]


class VisualModelOut(BaseModel):
    """GET /buildings/{id}/visual-model 的响应。

    单条 visual_model 记录的对外结构。没设过 visual_model 时路由层返 404,
    不会到这里。
    """

    id: str
    building_id: str
    model_mode: str = Field(..., description="BLOCK / PHOTO_SINGLE / PHOTO_MULTI / MANUAL_GLTF")
    render_format: str = Field(..., description="BOX / GLB / GLTF / PLY / SPLAT / SPZ")
    storage_path: str | None = None
    preview_image_path: str | None = None
    dimensions: Dimensions
    position: Position
    is_active: bool
    created_at: str
