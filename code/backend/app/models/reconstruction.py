"""
单图重建 worker 的请求/响应模型 (Step 12)。

五个对外接口对应 schema:
  POST   /buildings/{id}/reconstruction/single-photo   -> SinglePhotoRequest
  GET    /reconstruction/jobs/{id}/status              -> JobOut
  GET    /reconstruction/jobs                          -> list[JobListOut]
  POST   /reconstruction/jobs/{id}/retry               (无 body)
  DELETE /reconstruction/jobs/{id}                     (无 body)

字段约定:
  status  in {PENDING, RUNNING, SUCCEEDED, FAILED}
  engine  固定 TRIPOSPLAT (一期只接一个引擎, 表上 CHECK 约束还允许
          PYCOLMAP_GAUSSIAN_SPLATTING / MANUAL_BOX, 留给后续)
  model_mode 固定 PHOTO_SINGLE (单图模式, 多图 PHOTO_MULTI 一期不做)

不挂 response_model 到路由上, 项目响应统一包 {code, message, data},
schema 在 service 层组装时当结构定义用。
"""
from pydantic import BaseModel, Field


class SinglePhotoRequest(BaseModel):
    """POST /buildings/{id}/reconstruction/single-photo 的 body。

    客户上传照片拿到 photo_upload_id 后, 连同建筑尺寸一起提交。尺寸
    用于 splat 尺度校准 -- TripoSplat 输出已归一化到 1x1x1, 按
    length/width/height 三轴独立缩放到用户给的真实尺寸。
    """

    photo_upload_id: str = Field(..., description="照片上传返回的 photo_id (uuid4)")
    length_m: float = Field(..., gt=0, le=10000, description="建筑长度(米), 用于 splat 尺度校准")
    width_m: float = Field(..., gt=0, le=10000, description="建筑宽度(米)")
    height_m: float = Field(..., gt=0, le=10000, description="建筑总高(米)")
    floors_count: int = Field(..., ge=1, le=200, description="楼层数")
    position_x: float | None = Field(None, description="园区内 X 坐标(米), 可空")
    position_y: float | None = Field(None, description="园区内 Y 坐标(米), 可空")


class Dimensions(BaseModel):
    """回显用户提交的尺寸参数。worker 拿这组参数做尺度校准。"""

    length_m: float
    width_m: float
    height_m: float
    floors_count: int


class Position(BaseModel):
    """回显 position_x / position_y。"""

    x: float | None = None
    y: float | None = None


class JobOut(BaseModel):
    """GET /reconstruction/jobs/{id}/status 的响应。"""

    id: str
    building_id: str
    status: str = Field(..., description="PENDING / RUNNING / SUCCEEDED / FAILED")
    engine: str = Field(..., description="TRIPOSPLAT")
    model_mode: str = Field(..., description="PHOTO_SINGLE")
    retry_count: int
    photo_upload_id: str | None = None
    output_model_id: str | None = Field(None, description="SUCCEEDED 后指向 building_visual_model.id")
    error_message: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    created_at: str
    dimensions: Dimensions
    position: Position


class JobListOut(BaseModel):
    """GET /reconstruction/jobs 列表项, 比 JobOut 精简。"""

    id: str
    building_id: str
    status: str
    retry_count: int
    output_model_id: str | None = None
    error_message: str | None = None
    created_at: str
    finished_at: str | None = None
