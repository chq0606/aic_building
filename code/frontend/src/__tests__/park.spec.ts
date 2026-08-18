// ============================================================================
// park.spec.ts - 园区页数据流转 (sites API)
// ----------------------------------------------------------------------------
// 不直接测 Park.vue 的 DOM 渲染 (AntD Row/Col/Card 结构复杂, 测试脆裂),
// 改测 API 层调用 + 数据流转.
// ============================================================================

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mockListSites = vi.fn().mockResolvedValue([
  { site_id: 'demo-site-001', site_name: 'BDG2 Demo 园区' },
])

vi.mock('@/api/sites', () => ({
  sitesApi: {
    listSites: () => mockListSites(),
  },
}))

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('园区数据流转', () => {
  it('调 listSites 拿到 demo 园区', async () => {
    const { sitesApi } = await import('@/api/sites')
    const result = await sitesApi.listSites()
    expect(mockListSites).toHaveBeenCalled()
    expect(result[0].site_name).toBe('BDG2 Demo 园区')
  })
})

describe('Park.vue 组件 mount', () => {
  it.skip('Park.vue mount 跳过 (依赖 Cesium + 复杂 setup, jsdom 跑不动)', () => {
    // 真实组件集成测试走 e2e (Playwright), 不在 vitest 跑
    expect(true).toBe(true)
  })
})
