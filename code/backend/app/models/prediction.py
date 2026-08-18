"""
能耗预测 API 请求/响应模型 (Step 19)。

 ForecastRequest  POST /prediction/buildings/{id}/forecast 的请求体
"""
from typing import Literal

from pydantic import BaseModel, Field


ModelType = Literal["prophet", "lstm", "linear", "gbm"]


class ForecastRequest(BaseModel):
    """提交预测 job 的请求体。

    horizon_days: 预测天数, 1-90 (和 SQL CHECK 约束对齐)
    model_type: 模型类型, prophet / lstm / linear / gbm
    """
    horizon_days: int = Field(
        ...,
        ge=1,
        le=90,
        description="预测天数, 1-90",
    )
    model_type: ModelType = Field(
        ...,
        description="模型类型: prophet (主力) / lstm (demo) / linear (baseline) / gbm (天气驱动)",
    )
