// ============================================================================
// Assistant API - AI 抽屉的 REST 接口包装
// ----------------------------------------------------------------------------
// 6 个 REST 接口 (SSE 流式 sendMessage 不在这里, 在 stores/ai.ts 里手写
// fetch + ReadableStream, 因为 EventSource 不支持 POST + JWT header)。
//
// 后端响应统一走 {code, message, data} envelope, api.get/post 已经自动 unwrap
// data 字段, 调用方拿到的是 T。
// ============================================================================

import { api } from './client'

// ---------------------------------------------------------------------------
// 类型定义
// ---------------------------------------------------------------------------

export interface ChatContext {
  site_id?: string
  site_name?: string
  building_id?: string
  building_name?: string
  time_range?: { start: string; end: string }
  metric?: string
  scenario?: 'optimization' | 'anomaly' | 'standard' | 'data'
}

export interface ChatSession {
  id: string
  title: string
  context: Record<string, unknown>
  created_at: string
  updated_at?: string
  message_count: number
}

export interface ToolCall {
  id: string
  name: string
  arguments: Record<string, unknown>
  ok?: boolean
}

export interface Citation {
  type: 'data' | 'anomaly' | 'document'
  [key: string]: unknown
}

export interface OptimizationPlan {
  summary: string
  problems: Array<{
    problem_desc: string
    evidence_ref: string
    severity: 'high' | 'medium' | 'low'
  }>
  measures: Array<{
    measure_name: string
    saved_kwh: number
    saved_co2: number
    payback_months: number
    difficulty: 'easy' | 'medium' | 'hard'
    clause_ref: string
  }>
  priorities: Array<{
    measure_name: string
    reason: string
  }>
}

export interface ChatMessage {
  id: string
  session_id?: string
  role: 'user' | 'assistant' | 'tool' | 'system'
  content: string
  tool_calls?: ToolCall[]
  citations?: Citation[]
  referenced_chunk_ids?: string[]
  referenced_point_ids?: string[]
  optimization_plan?: OptimizationPlan | null
  tokens_used?: number
  latency_ms?: number
  created_at: string
}

// ---------------------------------------------------------------------------
// 会话管理
// ---------------------------------------------------------------------------

export async function createSession(opts?: {
  title?: string
  context?: ChatContext
}): Promise<ChatSession> {
  return api.post<ChatSession>('/assistant/sessions', {
    title: opts?.title,
    context: opts?.context,
  })
}

export async function listSessions(limit = 50): Promise<{ items: ChatSession[]; total: number }> {
  return api.get('/assistant/sessions', { params: { limit } })
}

export async function listMessages(sessionId: string, limit = 100): Promise<{ items: ChatMessage[]; total: number }> {
  return api.get(`/assistant/sessions/${sessionId}/messages`, { params: { limit } })
}

// ---------------------------------------------------------------------------
// 节能优化 + PDF
// ---------------------------------------------------------------------------

export async function exportPdf(messageId: string): Promise<Blob> {
  // 用 client 直接调, 因为要拿 raw blob 不能让 api 拦截器 unwrap
  const client = (await import('./client')).default
  const resp = await client.post(`/assistant/messages/${messageId}/export-pdf`, null, {
    responseType: 'blob',
  })
  return resp.data as Blob
}
