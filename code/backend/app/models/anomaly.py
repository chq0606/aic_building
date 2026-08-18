"""
异常检测模块的请求/响应模型。

evidence 字段虽然存在 jsonb 列里,但用 Pydantic 模型把结构定死,方便:
  1. 后端 detector 写入时按 schema 填,字段不漂移
  2. 前端 / AI 助手读取时有明确类型
  3. Step 18 AI 助手可以直接拿 evidence 给 LLM 当素材

evidence 结构按决策点 #6:
  baseline_method   算法名(zscore / rolling_mean / peer_compare 等)
  baseline_window   对比基准的时段描述(自然语言)
  observed_window   观察到的异常时段描述
  stats             统计摘要(mean/median/std/max/min)
  sample_points     前 20 个原始读数,可追溯
"""
from pydantic import BaseModel, Field


class AnomalyStats(BaseModel):
    """evidence.stats 的结构。"""

    mean: float | None = Field(None, description="观察窗口内的均值")
    median: float | None = Field(None, description="中位数,抗离群点")
    std: float | None = Field(None, description="标准差")
    max: float | None = Field(None, description="最大值")
    min: float | None = Field(None, description="最小值")


class AnomalyEvidence(BaseModel):
    """
    evidence 字段结构。

    detector 把检测过程的可追溯信息全塞这里:
    - 用了什么算法(baseline_method)
    - 对比的基准时段是什么(baseline_window)
    - 观察到的异常时段是什么(observed_window)
    - 窗口内的统计摘要(stats)
    - 前 20 个原始读数(sample_points),让 AI 助手能直接引到具体数据点
    """

    baseline_method: str = Field(..., description="算法名: zscore / rolling_mean / peer_compare / iqr 等")
    baseline_window: str = Field(..., description="对比基准时段描述,自然语言")
    observed_window: str = Field(..., description="观察到的异常时段描述,自然语言")
    stats: AnomalyStats = Field(default_factory=AnomalyStats)
    sample_points: list[dict] = Field(
        default_factory=list,
        description="前 20 个原始读数 [{ts, value}], 超出截断",
    )
    extra: dict = Field(default_factory=dict, description=" detector 自定义字段,如 thresholds / peer_group 等")


class AnomalyEventOut(BaseModel):
    """单条异常事件的对外结构。"""

    id: str
    building_id: str
    building_code: str | None = None
    display_name: str | None = None
    point_id: str | None = Field(None, description="building-level 检测时为 NULL")
    point_code: str | None = None
    energy_type: str | None = None
    event_type: str
    severity: str
    metric_code: str
    start_ts: str
    end_ts: str
    observed_value: float | None = None
    baseline_value: float | None = None
    evidence: dict
    status: str
    created_at: str


class DetectOptions(BaseModel):
    """
    触发检测时的可选参数。

    event_types 默认全部 6 类。如果用户只想跑某一类(比如重新检测 SPIKE),
    可以传 ["SPIKE"]。校验在 service 层做,模型层只做类型约束。
    """

    building_id: str | None = Field(None, description="不传则跑整个 site 的所有楼")
    site_id: str | None = Field(None, description="building_id 为空时必传,标识检测范围")
    start: str | None = Field(None, description="ISO8601,不传按租户实际数据范围")
    end: str | None = Field(None, description="ISO8601,不传按租户实际数据范围")
    event_types: list[str] | None = Field(
        None,
        description="要跑的检测类型子集,不传全部跑。可选: SPIKE/DRIFT/PROLONGED_ZERO/MISSING_GAP/SCHEDULE_VIOLATION/BASELINE_DEVIATION",
    )


class DetectResultResponse(BaseModel):
    """POST /anomalies/detect 的响应。"""

    building_count: int
    point_count: int
    time_range: dict
    event_types_run: list[str]
    anomalies_inserted: int
    anomalies_deleted: int
    by_type: dict[str, int]
    by_severity: dict[str, int]
    duration_ms: int


class AnomalyListResponse(BaseModel):
    """GET /anomalies/buildings/{id} 的响应。"""

    building_id: str
    building_code: str | None = None
    display_name: str | None = None
    time_range: dict
    total: int
    by_type: dict[str, int]
    by_severity: dict[str, int]
    anomalies: list[AnomalyEventOut]


class SiteOverviewResponse(BaseModel):
    """GET /anomalies/sites/{id}/overview 的响应。"""

    site_id: str
    time_range: dict
    building_count: int
    total_anomalies: int
    by_type: dict[str, int]
    by_severity: dict[str, int]
    buildings: list[dict]
