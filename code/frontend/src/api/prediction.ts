// ============================================================================
// Prediction API - 能耗预测
// ----------------------------------------------------------------------------
// 3 个 endpoint, 全部挂在 /api/v1 前缀下:
//   POST /prediction/buildings/{id}/forecast  提交预测 job (202, demo 也可用)
//   GET  /prediction/jobs/{id}                 查 job 详情 (前端轮询到 SUCCEEDED/FAILED)
//   GET  /prediction/buildings/{id}/latest     取最近一次 SUCCEEDED 预测 (进面板时先调这个)
//
// 异步流程: POST 拿 job_id -> 每 2s 轮询 GET /jobs/{id} -> SUCCEEDED 拿 result_jsonb
// result_jsonb 结构: { history, forecast, mape, model_type, warning }
//   history:  训练用过的历史日数据 [{date, value}]
//   forecast: 未来 horizon 天预测 [{date, yhat, yhat_lower, yhat_upper}]
//   mape:     最后 eval_days 天实际 vs 预测算的 MAPE (越小越好, <20% 优秀)
//   warning:  训练数据不足提示 (BDG2 demo 单年数据 Prophet 学不到年季节性)
// ============================================================================

import { api } from './client'

// ---------------------------------------------------------------------------
// 模型类型 - 对齐后端 mart.prediction_job.model_type CHECK 约束
//   prophet: Facebook 时序预测, 主力 (年/周/趋势分量自动学习)
//   lstm:    PyTorch LSTM demo (CPU, 50 epoch, 自回归预测)
//   linear:  sklearn LinearRegression baseline (X=day_index, 给上面两个做参考)
//   gbm:     sklearn GradientBoosting 天气驱动回归 (气温/露点/风速/云量 + 时间特征)
// ---------------------------------------------------------------------------
export type PredictionModel = 'prophet' | 'lstm' | 'linear' | 'gbm'

// gbm 特征重要性 [{feature, importance}], importance 0-1 和=1, 降序
export interface FeatureImportance {
  feature: string
  importance: number
}

// job 状态机: PENDING -> RUNNING -> SUCCEEDED / FAILED
// 失败立即置 FAILED 不自动重试 (与 triposplat 不同, 详见后端 prediction_worker)
export type PredictionJobStatus = 'PENDING' | 'RUNNING' | 'SUCCEEDED' | 'FAILED'

// ---------------------------------------------------------------------------
// result_jsonb 里的子结构
// ---------------------------------------------------------------------------
export interface PredictionHistoryPoint {
  date: string         // YYYY-MM-DD
  value: number        // kWh, 当天总能耗
}

export interface PredictionForecastPoint {
  date: string                 // YYYY-MM-DD
  yhat: number                 // 预测值
  yhat_lower: number | null    // 置信区间下界 (Prophet 给, LSTM/linear 给 null)
  yhat_upper: number | null    // 置信区间上界
}

export interface PredictionResult {
  history: PredictionHistoryPoint[]
  forecast: PredictionForecastPoint[]
  mape: number | null          // 百分比, 例如 6.21 表示 6.21%
  model_type: PredictionModel
  warning: string | null       // 训练数据不足提示, null 表示无警告
  // gbm 特征重要性 (只有 gbm 模型有, 其他模型 undefined)
  feature_importances?: FeatureImportance[]
}

// ---------------------------------------------------------------------------
// job 详情 (GET /prediction/jobs/{id} 和 /latest 共用)
//   status=SUCCEEDED 时 result 字段有值
//   status=FAILED    时 error_message 有值
//   status=PENDING/RUNNING 时 result/error_message 都为 null
// ---------------------------------------------------------------------------
export interface PredictionJob {
  id: string
  building_id: string
  model_type: PredictionModel
  horizon_days: number
  status: PredictionJobStatus
  mape: number | null
  result: PredictionResult | null
  error_message: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

// ---------------------------------------------------------------------------
// 请求体 - POST /prediction/buildings/{id}/forecast
//   horizon_days: 预测天数 1-90 (后端 CHECK 约束)
//   model_type:   模型类型
// ---------------------------------------------------------------------------
export interface ForecastRequest {
  horizon_days: number
  model_type: PredictionModel
}

export const predictionApi = {
  // 提交预测 job. 202 Accepted + job_id. demo 也可用 (异步只读任务, 不写 fact 表).
  createForecast(buildingId: string, body: ForecastRequest) {
    return api.post<{ job_id: string; status: 'PENDING' }>(
      `/prediction/buildings/${buildingId}/forecast`,
      body,
    )
  },

  // 查 job 详情. 前端轮询直到 status=SUCCEEDED 或 FAILED.
  getJob(jobId: string) {
    return api.get<PredictionJob>(`/prediction/jobs/${jobId}`)
  },

  // 取最近一次 SUCCEEDED 的预测. 进 PredictionPanel 时先调这个, 有缓存直接显示.
  // 没跑过预测返 200 + null (后端把"没跑过"当合法初始态, 不返 404).
  getLatest(buildingId: string, params?: { model_type?: PredictionModel }) {
    return api.get<PredictionJob | null>(`/prediction/buildings/${buildingId}/latest`, { params })
  },
}
