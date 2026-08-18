// ============================================================================
// Knowledge API - 文档上传 / 解析 / embedding / 检索
// ============================================================================
//
// 后端状态机 (knowledge.document.status):
//   pending   刚上传, 等待解析
//   parsing   后台解析中 (PDF -> 文本 -> 切 chunk)
//   parsed    解析完成, chunk 已入库, 可走 BM25 关键词检索
//   embedding 向量化中 (BGE 推理, chunk.embedding 写库中)
//   failed    解析或向量化失败, parse_error 字段记原因
//
// embed 完成 status 回到 'parsed', 用 embedded_chunk_count 区分是否已向量化:
//   embedded_chunk_count = 0  未向量化 (只能 BM25 检索)
//   embedded_chunk_count > 0  已向量化 (向量 + BM25 混检)
// ============================================================================

import { api } from './client'

export type KnowledgeDocStatus = 'pending' | 'parsing' | 'parsed' | 'embedding' | 'failed'

export interface KnowledgeDocument {
  id: string
  doc_type: string  // 'standard' | 'manual' | 'sop' | 'report' | 'paper' | 'other'
  title: string
  source_path: string
  file_hash: string
  mime_type: string
  page_count: number | null
  chunk_count: number
  status: KnowledgeDocStatus
  parse_error: string | null
  metadata: {
    standard_no?: string | null
    [k: string]: unknown
  }
  uploaded_at: string
  parsed_at: string | null
  embedded_chunk_count: number
}

export const knowledgeApi = {
  uploadDocument(file: File) {
    const fd = new FormData()
    fd.append('file', file)
    return api.upload<KnowledgeDocument>('/knowledge/documents', fd)
  },

  parse(documentId: string) {
    return api.post<{ task_id: string }>(`/knowledge/documents/${documentId}/parse`)
  },

  embed(documentId: string) {
    return api.post<{ task_id: string }>(`/knowledge/documents/${documentId}/embed`)
  },

  list() {
    return api.get<KnowledgeDocument[]>('/knowledge/documents')
  },

  delete(documentId: string) {
    return api.delete<void>(`/knowledge/documents/${documentId}`)
  },
}
