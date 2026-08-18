// ============================================================================
// Imports API - staging -> fact merge + 回滚
// ----------------------------------------------------------------------------
// 4 个 endpoint:
//   POST   /imports/{batch_id}/run       触发 merge (LOADING -> MERGING)
//   GET    /imports/{batch_id}/status    查 batch 状态, 前端轮询
//   GET    /imports                       batch 列表, 可按 status 过滤
//   POST   /imports/{batch_id}/rollback  回滚 (删 fact 数据 + 重算日聚合)
//
// batch 状态机: LOADING -> MERGING -> SUCCEEDED / FAILED
//   LOADING   session 刚 commit 完, 还没触发 merge
//   MERGING   后台线程跑 upsert + aggregate, 前端轮询 status
//   SUCCEEDED merge 成功, 数据已进 fact.point_reading + mart.building_daily_energy
//   FAILED    merge 过程异常, error_message 字段记录原因
// ============================================================================

import { api } from './client'

export type BatchStatus = 'LOADING' | 'MERGING' | 'SUCCEEDED' | 'FAILED'

// 后端 import_service.list_batches / get_batch_status 返的字段名:
//   batch_id / status / dataset_source / target_type / row_count_total /
//   row_count_success / row_count_error / error_summary / started_at /
//   finished_at / created_at
// 前端类型必须跟后端字面量对齐, 不然 ImportBatchList 里 b.id / b.row_count_inserted
// / b.error_message 全是 undefined, shortId(b.id) 调 .slice 直接炸
export interface ImportBatch {
  batch_id: string
  status: BatchStatus
  dataset_source: string | null
  target_type: string | null
  row_count_total: number
  row_count_success: number
  row_count_error: number
  error_summary: string | null
  started_at: string | null
  finished_at: string | null
  created_at: string
}

export interface RunMergeResult {
  batch_id: string
  status: 'MERGING'
}

export interface RollbackResult {
  batch_id: string
  rows_deleted: number
  buildings_affected: number
  days_recalculated: number
  status: 'FAILED'
}

export const importsApi = {
  // 触发 merge. 抢锁失败 (batch 状态不是 LOADING) 后端返 409
  runMerge(batchId: string) {
    return api.post<RunMergeResult>(`/imports/${batchId}/run`)
  },

  // 查 batch 状态. 前端轮询直到 SUCCEEDED / FAILED
  getStatus(batchId: string) {
    return api.get<ImportBatch>(`/imports/${batchId}/status`)
  },

  // batch 列表. 可按 status 过滤, 默认按 created_at 倒序
  listBatches(params?: { status?: BatchStatus }) {
    return api.get<ImportBatch[]>('/imports', { params })
  },

  // 回滚. 删 fact.point_reading WHERE source_batch_id=batch_id, 重算日聚合
  rollback(batchId: string) {
    return api.post<RollbackResult>(`/imports/${batchId}/rollback`)
  },
}
