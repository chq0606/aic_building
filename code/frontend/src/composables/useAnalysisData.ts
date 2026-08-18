// ============================================================================
// useAnalysisData - 分析中心页共享数据加载
// ----------------------------------------------------------------------------
// 6 个图表组件共用 3 份基础数据:
//   1. buildings (listSiteBuildings)        - BuildingRanking / EuiBaseline / BuildingCompare
//   2. anomalyOverview (site anomaly)      - AnomalyOverview 饼图 + 每楼摘要
//   3. qualityOverview (site data quality) - DataQualityPanel
//
// WeatherCorrelation + DataQualityPanel 选楼后还会单独调 buildingTimeseries /
// buildingWeather / getBuildingQuality, 那些不在共享缓存里 (按楼拉, 各管各的)。
//
// 缓存策略:
//   - 模块级 singleton ref, 多个组件 import 拿到同一份 reactive state
//   - 缓存 key = siteId + start + end, 切园区/时间范围自动失效
//   - 顶栏 metric 切换不影响缓存 (Ranking/Compare 内部读 metric 决定怎么画, 数据一样)
//
// 调用方:
//   Analysis.vue onMounted 调一次 loadAnalysisData(), 并 watch context 自动 reload
//   各图表组件直接 import { buildings, anomalyOverview, qualityOverview, loading, error }
//   或调 useAnalysisData() 拿 refs + reload
// ============================================================================

import { ref } from 'vue'
import { useContextStore } from '@/stores/context'
import { queryApi, type BuildingListResponse } from '@/api/query'
import { anomalyApi, type SiteAnomalyOverview } from '@/api/anomaly'
import { dataQualityApi, type SiteQualityOverview } from '@/api/data_quality'
import { ApiError } from '@/api/client'

// ---- 模块级共享 state ----
// 跨组件共享, 任意一处 reload 所有组件都能响应 (因为都是同一份 ref)
export const buildings = ref<BuildingListResponse | null>(null)
export const anomalyOverview = ref<SiteAnomalyOverview | null>(null)
export const qualityOverview = ref<SiteQualityOverview | null>(null)
export const loading = ref(false)
export const error = ref<string | null>(null)
export const lastLoadTs = ref(0)

// 缓存 key (siteId + 时间范围), 用于判断是否需要重新拉
let lastLoadKey = ''

// ---- 加载函数 ----
// force=true 强制刷新 (用户点重试按钮)
export async function loadAnalysisData(force = false): Promise<void> {
  const context = useContextStore()
  const siteId = context.siteId
  if (!siteId) {
    error.value = '请先在顶栏选择园区'
    return
  }

  const range = context.currentRange
  const start = range.start.toISOString()
  const end = range.end.toISOString()
  const loadKey = `${siteId}|${start}|${end}`

  // 缓存命中: 同 key + 已有数据 + 非强制刷新
  if (
    !force &&
    loadKey === lastLoadKey &&
    buildings.value &&
    anomalyOverview.value &&
    qualityOverview.value
  ) {
    return
  }

  loading.value = true
  error.value = null

  try {
    // 3 个 endpoint 并行拉, 总耗时取最慢的一个 (而非串行累加)
    // 后端 site overview 数据量小, demo 6 栋楼实测 < 300ms
    const [b, a, q] = await Promise.all([
      queryApi.listSiteBuildings(siteId, { start, end }),
      anomalyApi.getSiteAnomalyOverview(siteId, { start, end }),
      dataQualityApi.getSiteQualityOverview(siteId, { start, end }),
    ])

    buildings.value = b
    anomalyOverview.value = a
    qualityOverview.value = q
    lastLoadKey = loadKey
    lastLoadTs.value = Date.now()
  } catch (err) {
    // 失败时不清空旧数据, 让图表继续显示老数据 + 顶栏提示错误
    // 这样切时间范围失败时不会黑屏, 老数据仍有参考价值
    error.value = err instanceof ApiError ? err.message : '加载分析数据失败'
  } finally {
    loading.value = false
  }
}

// ---- composable 入口 ----
// 任意组件调 useAnalysisData() 拿到同一份 refs + reload 方法
export function useAnalysisData() {
  return {
    buildings,
    anomalyOverview,
    qualityOverview,
    loading,
    error,
    lastLoadTs,
    reload: () => loadAnalysisData(true),
  }
}
