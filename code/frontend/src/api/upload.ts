// ============================================================================
// Upload API - CSV/XLSX 上传 + 列映射 + 校验 + commit + 模板下载
// ----------------------------------------------------------------------------
// 对齐后端 backend/app/api/upload.py 的全部 endpoint:
//   POST   /uploads/single                         单 CSV 上传, A/B 档共用入口
//   POST   /uploads/multi                          多文件批量上传, C 档
//   POST   /uploads/photo                          通用照片上传 (重建用)
//   GET    /uploads/templates                       列模板类型
//   GET    /uploads/templates/{type}                下载 CSV 模板 (返文件流)
//   GET    /uploads/{id}/mapping-suggest            读 header + 前 5 行, 猜映射
//   POST   /uploads/{id}/mapping                    保存用户确认的映射 (UPLOADED -> MAPPED)
//   POST   /uploads/{id}/validate                   校验 (MAPPED -> VALIDATED / FAILED)
//   POST   /uploads/{id}/commit                     进 staging (VALIDATED -> COMMITTED)
//   GET    /uploads                                 session 列表
//   GET    /uploads/{id}                            session 详情
//
// 状态机: UPLOADED -> MAPPED -> VALIDATED -> COMMITTED, 失败置 FAILED
// ============================================================================

import { api, default as client } from './client'

// ---------------------------------------------------------------------------
// 类型定义
// ---------------------------------------------------------------------------

export type UploadTargetType = 'POINT' | 'WEATHER' | 'BUILDING'
export type SessionStatus = 'UPLOADED' | 'MAPPED' | 'VALIDATED' | 'COMMITTED' | 'FAILED'

export interface UploadSingleResult {
  session_id: string
  upload_file_id: string
}

export interface UploadMultiResult {
  sessions: Array<{
    filename: string
    session_id: string
    upload_file_id: string
  }>
  errors: Array<{
    filename: string | undefined
    reason: string
  }>
}

export interface PhotoUploadResult {
  photo_id: string
  file_path: string
  file_size: number
  mime_type: string
}

export interface TemplateInfo {
  type: string
  label: string
  filename: string
}

// 列映射配置, 对齐 backend/app/models/upload.py 的 MappingConfig
export interface WideMeltRule {
  id_cols: string[]
  value_cols: string[]
  column_parse: 'underscore_2' | 'underscore_3' | 'fixed_building' | 'regex'
  fixed_building?: string | null
}

export interface MappingConfig {
  timestamp_col: string
  building_col?: string | null
  energy_col?: string | null
  value_col?: string | null
  unit_col?: string | null
  quality_col?: string | null
  wide_melt?: WideMeltRule | null
  default_energy_type?: string | null
  default_unit?: string | null
  timezone: string
  timestamp_format?: string | null
}

export interface MappingSuggestResponse {
  columns: string[]
  suggested: MappingConfig
  sample_rows: Array<Record<string, unknown>>
}

export interface SaveMappingRequest {
  mapping: MappingConfig
  profile_name?: string | null
}

export interface ValidateResult {
  row_count_total: number
  row_count_valid: number
  row_count_error: number
  errors: Array<Record<string, unknown>>
  can_commit: boolean
}

export interface CommitResult {
  session_id: string
  batch_id: string
  row_count_inserted: number
}

export interface UploadSession {
  id: string
  status: SessionStatus
  target_type: UploadTargetType
  original_filename: string
  row_count_total: number
  row_count_valid: number
  row_count_error: number
  error_summary: string | null
  committed_batch_id: string | null
  created_at: string
  updated_at: string
}

export interface UploadSessionDetail extends UploadSession {
  mapping: string | null  // JSON string, 前端用 JSON.parse 反序列化
  timezone: string | null
  timestamp_format: string | null
}

// FileUpload 组件 emit 的 payload (单文件 / 多文件两种形态, 用 mode 鉴别联合)
// 放在 api/upload.ts 而非组件内 export, 因为 Vue <script setup> 顶层 export
// 在 vue-tsc 下行为不稳, 走普通 TS 模块更可靠
export type UploadedPayload =
  | { mode: 'single'; session_id: string; upload_file_id: string; filename: string }
  | { mode: 'multi'; result: UploadMultiResult }

// ---------------------------------------------------------------------------
// API 调用
// ---------------------------------------------------------------------------

export const uploadApi = {
  // 上传单文件 (CSV/XLSX). target_type 决定后端按什么表去校验 + commit
  uploadSingle(file: File, targetType: UploadTargetType = 'POINT') {
    const fd = new FormData()
    fd.append('file', file)
    return api.upload<UploadSingleResult>(`/uploads/single?target_type=${targetType}`, fd)
  },

  // 多文件批量上传
  uploadMulti(files: File[], targetType: UploadTargetType = 'POINT') {
    const fd = new FormData()
    files.forEach((f) => fd.append('files', f))
    return api.upload<UploadMultiResult>(`/uploads/multi?target_type=${targetType}`, fd)
  },

  // 通用照片上传 (重建用)
  uploadPhoto(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    return api.upload<PhotoUploadResult>('/uploads/photo', fd)
  },

  // 列模板列表 + 下载. 下载走 client 拿原始 blob, 不走 api.unwrap (api 会期望 envelope)
  listTemplates() {
    return api.get<TemplateInfo[]>('/uploads/templates')
  },

  // 模板下载用 axios 拿原始响应, 跳过 envelope 解包. 需要手动加 Authorization.
  // 返回 Blob 给前端, 调用方用 URL.createObjectURL 触发 <a download>
  async downloadTemplate(type: string): Promise<{ blob: Blob; filename: string }> {
    const token = localStorage.getItem('aic_access_token')
    const resp = await client.get(`/uploads/templates/${type}`, {
      responseType: 'blob',
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    })
    // 从 Content-Disposition 拿 filename, 后端固定返 "{type}.csv"
    const disp = resp.headers['content-disposition'] || ''
    const m = /filename="?([^";]+)"?/.exec(disp)
    const filename = m ? m[1] : `${type}.csv`
    return { blob: resp.data as Blob, filename }
  },

  // 列名映射猜测. GET 返回 columns + suggested MappingConfig + sample_rows
  mappingSuggest(sessionId: string) {
    return api.get<MappingSuggestResponse>(`/uploads/${sessionId}/mapping-suggest`)
  },

  // 保存用户确认的映射. 状态 UPLOADED -> MAPPED
  saveMapping(sessionId: string, req: SaveMappingRequest) {
    return api.post<{ session_id: string; profile_id: string | null }>(
      `/uploads/${sessionId}/mapping`,
      req,
    )
  },

  // 校验. 状态 MAPPED -> VALIDATED / FAILED
  validate(sessionId: string) {
    return api.post<ValidateResult>(`/uploads/${sessionId}/validate`)
  },

  // 提交到 staging. 状态 VALIDATED -> COMMITTED, 返 batch_id 给 merge 用
  commit(sessionId: string) {
    return api.post<CommitResult>(`/uploads/${sessionId}/commit`)
  },

  // session 列表
  listSessions() {
    return api.get<UploadSession[]>('/uploads')
  },

  // session 详情. 用于 MappingWizard 回填已保存的 mapping
  getSession(sessionId: string) {
    return api.get<UploadSessionDetail>(`/uploads/${sessionId}`)
  },
}
