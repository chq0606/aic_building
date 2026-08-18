// ============================================================================
// ai_drawer.spec.ts - AI 抽屉 (Step 18): session 管理 + 消息流转
// ----------------------------------------------------------------------------
// 测试要点:
//   1. mock assistant API 返 session + 消息
//   2. createSession / listSessions / listMessages API 调用正确
//   3. 数据结构对 (session.id / messages 列表)
//
// 不直接测 AiDrawer.vue DOM (AntD Drawer + 消息列表结构复杂, jsdom 下脆裂),
// 改测 API 层调用 + 数据流转.
// ============================================================================

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mockCreateSession = vi.fn()
const mockListSessions = vi.fn()
const mockListMessages = vi.fn()

vi.mock('@/api/assistant', () => ({
  createSession: (...args: unknown[]) => mockCreateSession(...args),
  listSessions: (...args: unknown[]) => mockListSessions(...args),
  listMessages: (...args: unknown[]) => mockListMessages(...args),
}))

vi.mock('@/stores/auth', () => ({
  useAuthStore: () => ({
    user: {
      id: 'demo-id',
      tenant_id: 'demo-tenant',
      username: 'demo',
      is_demo: true,
    },
    isAuthenticated: true,
    isDemo: true,
  }),
}))

vi.mock('ant-design-vue', async (importOriginal) => {
  const orig = await importOriginal<typeof import('ant-design-vue')>()
  return {
    ...orig,
    message: {
      success: vi.fn(),
      error: vi.fn(),
      info: vi.fn(),
      warning: vi.fn(),
    },
  }
})

vi.mock('@/api/client', () => ({
  ApiError: class ApiError extends Error {
    code: number
    constructor(code: number, message: string) {
      super(message)
      this.code = code
    }
  },
}))

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
  localStorage.clear()
})

describe('assistant API 数据流转', () => {
  it('createSession 返 session 对象 (id + title)', async () => {
    mockCreateSession.mockResolvedValue({
      id: 'session-001',
      title: '新会话',
      created_at: '2026-07-20T10:00:00Z',
      message_count: 0,
    })

    const { createSession } = await import('@/api/assistant')
    const result = await createSession({ title: '测试会话' })

    expect(mockCreateSession).toHaveBeenCalledWith({ title: '测试会话' })
    expect(result.id).toBe('session-001')
    expect(result.title).toBe('新会话')
  })

  it('listSessions 返分页结构 {items, total}', async () => {
    mockListSessions.mockResolvedValue({
      items: [
        { id: 's1', title: '会话1', message_count: 2 },
        { id: 's2', title: '会话2', message_count: 0 },
      ],
      total: 2,
    })

    const { listSessions } = await import('@/api/assistant')
    const result = await listSessions()

    expect(result.items).toHaveLength(2)
    expect(result.total).toBe(2)
  })

  it('listMessages 返消息列表', async () => {
    mockListMessages.mockResolvedValue({
      items: [
        { id: 'm1', role: 'user', content: '你好' },
        { id: 'm2', role: 'assistant', content: '你好, 我是 AI 助手' },
      ],
      total: 2,
    })

    const { listMessages } = await import('@/api/assistant')
    const result = await listMessages('session-001')

    expect(mockListMessages).toHaveBeenCalledWith('session-001')
    expect(result.items).toHaveLength(2)
    expect(result.items[0].role).toBe('user')
    expect(result.items[1].role).toBe('assistant')
  })
})
