// ============================================================================
// Settings API - 系统设置
// ----------------------------------------------------------------------------
// 7 个 endpoint 分 3 组:
//   GLM Key 配置: getGlmKeyStatus / saveGlmKey / testGlm
//   BGE 状态:     getBgeInfo / testBge
//   数据源 + seed: getDataSourceStatus / triggerSeedDemo / getSeedStatus
// ============================================================================

import { api } from './client'

// ---------------------------------------------------------------------------
// 类型
// ---------------------------------------------------------------------------

export interface GlmKeyStatus {
  configured: boolean
  hint: string | null
  updated_at: string | null
}

export interface TestResult {
  ok: boolean
  message: string
  detail: string | null
}

export interface BgeStatus {
  configured: boolean
  model_path: string
  expected_dim: number
  actual_dim: number | null
  loaded: boolean
  device: string | null
}

export interface DataSourceStatus {
  building_count: number
  point_count: number
  reading_count: number
  anomaly_count: number
  last_seed_at: string | null
  last_seed_batch_id: string | null
  last_seed_status: string | null
}

// seed 任务状态跟后端 import_batch 状态机对齐: LOADING / SUCCEEDED / FAILED
export type SeedStatus = 'LOADING' | 'SUCCEEDED' | 'FAILED' | string

export interface SeedTaskStatus {
  batch_id: string
  status: SeedStatus
  row_count_total: number
  row_count_success: number
  row_count_error: number
  error_summary: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string | null
}

// ---------------------------------------------------------------------------
// GLM API Key 配置
// ---------------------------------------------------------------------------

export const settingsApi = {
  getGlmKeyStatus() {
    return api.get<GlmKeyStatus>('/settings/glm-api-key')
  },

  saveGlmKey(apiKey: string) {
    return api.put<GlmKeyStatus>('/settings/glm-api-key', { api_key: apiKey })
  },

  testGlm(apiKey?: string) {
    // 测试时优先用入参 apiKey (输入框里的临时值), 没传就传空让后端用已保存的 Key
    return api.post<TestResult>('/settings/test-glm', { api_key: apiKey ?? '' })
  },

  // ---------------------------------------------------------------------------
  // BGE 状态 (只读)
  // ---------------------------------------------------------------------------

  getBgeInfo() {
    return api.get<BgeStatus>('/settings/bge-info')
  },

  testBge() {
    return api.post<TestResult>('/settings/test-bge')
  },

  // ---------------------------------------------------------------------------
  // 数据源 + 重新 seed
  // ---------------------------------------------------------------------------

  getDataSourceStatus() {
    return api.get<DataSourceStatus>('/settings/data-source')
  },

  // 调 /admin/seed-demo?reset=true 触发重新 seed
  // 返回 {batch_id, status: 'LOADING'}, 前端用 batch_id 轮询 getSeedStatus
  triggerSeedDemo(reset: boolean) {
    return api.post<{ batch_id: string; status: string }>(
      `/admin/seed-demo?reset=${reset ? 'true' : 'false'}`,
    )
  },

  getSeedStatus(batchId: string) {
    return api.get<SeedTaskStatus>(`/admin/seed-demo/${batchId}/status`)
  },
}
