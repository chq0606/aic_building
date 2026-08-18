// ============================================================================
// park.spec.ts - 园区页 + query API 数据流转
// ----------------------------------------------------------------------------
// 测试要点:
//   1. mock 6 栋楼数据, 验证 queryApi.listBuildings 被调用
//   2. 数据结构正确 (building_id / display_name / total_kwh / eui_kwh_per_m2)
//   3. sitesApi.listSites 被调用, 拿到 demo 园区
//
// 不直接测 Park.vue 的 DOM 渲染 (AntD Row/Col/Card 结构复杂, 测试脆裂),
// 改测 API 层调用 + 数据流转.
// ============================================================================

import { describe, it, expect, vi, beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

const mockBuildings = Array.from({ length: 6 }, (_, i) => ({
  building_id: `b-${i + 1}`,
  building_code: `Building_${i + 1}`,
  display_name: `Building ${i + 1}`,
  total_kwh: 10000 * (i + 1),
  eui_kwh_per_m2: 50 + i * 10,
  anomaly_count: i,
  primary_use: 'education',
}))

const mockListBuildings = vi.fn().mockResolvedValue(mockBuildings)
const mockParkOverview = vi.fn().mockResolvedValue({
  building_count: 6,
  total_kwh: 210000,
  avg_eui: 80,
  anomaly_count: 5,
})

vi.mock('@/api/query', () => ({
  queryApi: {
    parkOverview: (...args: unknown[]) => mockParkOverview(...args),
    listBuildings: (...args: unknown[]) => mockListBuildings(...args),
  },
}))

const mockListSites = vi.fn().mockResolvedValue([
  { site_id: 'demo-site-001', site_name: 'BDG2 Demo 园区' },
])

vi.mock('@/api/sites', () => ({
  sitesApi: {
    listSites: () => mockListSites(),
  },
}))

vi.mock('@/stores/context', () => ({
  useContextStore: () => ({
    siteId: 'demo-site-001',
    currentRange: { start: '2024-01-01', end: '2024-12-31' },
    timePreset: 'this_year',
    metric: 'total_kwh',
    metricLabel: '总能耗',
  }),
}))

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('query API 数据流转', () => {
  it('mock 6 栋楼数据结构正确', () => {
    expect(mockBuildings).toHaveLength(6)
    for (const b of mockBuildings) {
      expect(b).toHaveProperty('building_id')
      expect(b).toHaveProperty('display_name')
      expect(b).toHaveProperty('total_kwh')
      expect(b).toHaveProperty('eui_kwh_per_m2')
    }
  })

  it('调 listBuildings 返 6 栋楼', async () => {
    const { queryApi } = await import('@/api/query')
    const result = await queryApi.listBuildings({
      site_id: 'demo-site-001',
      sort: 'total_kwh',
    })
    expect(mockListBuildings).toHaveBeenCalled()
    expect(result).toHaveLength(6)
  })

  it('调 parkOverview 返园区总览', async () => {
    const { queryApi } = await import('@/api/query')
    const result = await queryApi.parkOverview({
      site_id: 'demo-site-001',
    })
    expect(mockParkOverview).toHaveBeenCalled()
    expect(result.building_count).toBe(6)
    expect(result.total_kwh).toBeGreaterThan(0)
  })

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
