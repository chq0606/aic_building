// ============================================================================
// Context Store (Pinia) - 全局上下文
// ----------------------------------------------------------------------------
// 提示词要求:
//   "Pinia context store: 全局上下文, AI 抽屉自动继承"
//
// 全局上下文包括:
//   - site_id:    当前园区 (顶栏切换)
//   - building_id: 当前建筑 (可选, 园区页 hover/click 选中后设置)
//   - time_range: 时间范围 (顶栏切换, 默认本月)
//   - metric:     当前指标 (EUI / 总能耗 / 异常数, 顶栏切换)
//
// AI 抽屉会自动读这些上下文, 不需要每次让用户重新说一遍。
// ============================================================================

import { defineStore } from 'pinia'
import { ref, computed, watch } from 'vue'
import dayjs, { type Dayjs } from 'dayjs'
// dayjs 默认不支持 quarter, 装插件
import quarterOfYear from 'dayjs/plugin/quarterOfYear'

dayjs.extend(quarterOfYear)

export type Metric = 'total_kwh' | 'eui' | 'anomaly_count' | 'cost'
export type TimePreset = 'today' | 'this_week' | 'this_month' | 'this_quarter' | 'this_year' | 'custom'

export interface TimeRange {
  start: Dayjs
  end: Dayjs
  preset: TimePreset
}

const CONTEXT_KEY = 'aic_context'

interface PersistedContext {
  site_id: string | null
  // site_name 也持久化, 这样刷新页面后 AI 抽屉能立刻拿到园区名注入 LLM 上下文,
  // 不用等 listSites 异步拉完。空字符串表示 site_id 有但 name 没拿到 (兜底用 site_id)。
  site_name: string | null
  building_id: string | null
  building_name?: string | null
  metric: Metric
  time_preset: TimePreset
  // 自定义范围的具体起止时间 (ISO 字符串)
  // 不存的话, 刷新页面后 time_preset='custom' 还在, 但 customRange 会被
  // presetToRange('custom') 重算成 "30 天前到现在", 用户之前选的 2017 范围就丢了
  custom_start: string | null
  custom_end: string | null
  // 用户是否主动改过时间范围 (UI 点预设/选自定义范围都算主动改)
  // 自动检测 (DefaultLayout 调 getSiteDataRange 拿到 DB 实际范围切到 custom) 不算主动改,
  // 但自动检测走的是 setCustomRange, 也会把这个标记翻成 true, 防止下次 onMounted 又重新拉.
  // 旧版 persisted 没这字段时: time_preset 非 'this_month' (默认值) 就当作用户主动选过,
  // 不再触发自动检测覆盖用户的选择.
  user_adjusted_time?: boolean
}

function loadFromStorage(): PersistedContext | null {
  const raw = localStorage.getItem(CONTEXT_KEY)
  if (!raw) return null
  try {
    return JSON.parse(raw) as PersistedContext
  } catch {
    return null
  }
}

function presetToRange(preset: TimePreset): TimeRange {
  const now = dayjs()
  switch (preset) {
    case 'today':       return { start: now.startOf('day'),     end: now.endOf('day'),     preset }
    case 'this_week':   return { start: now.startOf('week'),    end: now.endOf('week'),     preset }
    case 'this_month':   return { start: now.startOf('month'),  end: now.endOf('month'),   preset }
    case 'this_quarter': return { start: now.startOf('quarter'), end: now.endOf('quarter'), preset }
    case 'this_year':   return { start: now.startOf('year'),    end: now.endOf('year'),    preset }
    case 'custom':      return { start: now.subtract(30, 'day'), end: now, preset }
  }
}

export const useContextStore = defineStore('context', () => {
  // ---- state ----
  const persisted = loadFromStorage()
  const siteId = ref<string | null>(persisted?.site_id ?? null)
  const siteName = ref<string | null>(persisted?.site_name ?? null)
  const buildingId = ref<string | null>(persisted?.building_id ?? null)
  const buildingName = ref<string | null>(persisted?.building_name ?? null)
  const metric = ref<Metric>(persisted?.metric ?? 'eui')
  const timePreset = ref<TimePreset>(persisted?.time_preset ?? 'this_month')

  // customRange 还原: 持久化里有 custom_start/end 就用, 否则用 preset 默认值
  // (presetToRange('custom') 返 now-30d ~ now, 是兜底默认, 不是用户选的)
  const customRange = ref<TimeRange>(
    persisted?.time_preset === 'custom'
      && persisted?.custom_start
      && persisted?.custom_end
      ? {
          start: dayjs(persisted.custom_start),
          end: dayjs(persisted.custom_end),
          preset: 'custom',
        }
      : presetToRange(timePreset.value)
  )

  // 用户是否主动改过时间范围:
  //   persisted.user_adjusted_time 有就用它
  //   旧版 persisted (没这字段): time_preset 非 'this_month' 默认值就当作已主动改过,
  //   避免新功能上线后老用户的 'this_year' 等显式选择被自动检测覆盖
  const userAdjustedTime = ref<boolean>(
    persisted?.user_adjusted_time
      ?? (persisted != null && persisted.time_preset !== 'this_month')
      ?? false
  )

  // 园区列表刷新信号: 上传/commit 数据后, 后端会自动建 'default' 园区, 但顶栏
  // 的 listSites 只在 DefaultLayout onMounted 拉一次。DataHub commit 完调
  // bumpSitesVersion() 自增, DefaultLayout watch 它重新拉列表 + 自动选中。
  const sitesVersion = ref(0)

  // ---- getters ----
  const currentRange = computed<TimeRange>(() => {
    if (timePreset.value === 'custom') return customRange.value
    return presetToRange(timePreset.value)
  })

  const metricLabel = computed(() => {
    switch (metric.value) {
      case 'total_kwh':     return '总能耗'
      case 'eui':           return 'EUI'
      case 'anomaly_count': return '异常数'
      case 'cost':          return '费用'
    }
  })

  // ---- actions ----
  function setSite(id: string | null, name: string | null = null) {
    siteId.value = id
    siteName.value = name
    // 切 site 后 building 失效
    buildingId.value = null
    buildingName.value = null
  }

  function setBuilding(id: string | null, name: string | null = null) {
    buildingId.value = id
    buildingName.value = name
  }

  function setMetric(m: Metric) {
    metric.value = m
  }

  function setPreset(p: TimePreset) {
    timePreset.value = p
    userAdjustedTime.value = true
    if (p !== 'custom') {
      customRange.value = presetToRange(p)
    }
  }

  function setCustomRange(start: Dayjs, end: Dayjs) {
    customRange.value = { start, end, preset: 'custom' }
    timePreset.value = 'custom'
    userAdjustedTime.value = true
  }

  function bumpSitesVersion() {
    sitesVersion.value += 1
  }

  // 登录态切换时彻底重置上下文 (siteId/buildingId/时间范围), 防止上一个账号的
  // 园区 id 通过 localStorage 泄漏给新账号 (新账号看不到别人的数据, 反而会
  // 带着别人的 site_id 去调 scene 接口报 404/500)
  function reset() {
    siteId.value = null
    siteName.value = null
    buildingId.value = null
    buildingName.value = null
    metric.value = 'eui'
    timePreset.value = 'this_month'
    customRange.value = presetToRange('this_month')
    userAdjustedTime.value = false
    localStorage.removeItem(CONTEXT_KEY)
  }

  // ---- 持久化 (站点 + 站点名 + 指标 + 时间范围 + 自定义起止 + 用户改时间标记, 不存 building) ----
  // customRange 起止也存, 否则刷新后 time_preset='custom' 还在但范围被重置
  // site_name 也存, 刷新后 AI 抽屉能立刻拿到园区名注入 LLM 上下文
  watch(
    [siteId, siteName, metric, timePreset, customRange, userAdjustedTime],
    ([s, sn, m, p, cr, uat]) => {
      const data: PersistedContext = {
        site_id: s,
        site_name: sn,
        building_id: null,
        metric: m,
        time_preset: p,
        custom_start: p === 'custom' && cr ? cr.start.toISOString() : null,
        custom_end: p === 'custom' && cr ? cr.end.toISOString() : null,
        user_adjusted_time: uat,
      }
      localStorage.setItem(CONTEXT_KEY, JSON.stringify(data))
    },
    { deep: true },
  )

  return {
    // state
    siteId,
    siteName,
    buildingId,
    buildingName,
    metric,
    timePreset,
    customRange,
    userAdjustedTime,
    sitesVersion,
    // getters
    currentRange,
    metricLabel,
    // actions
    setSite,
    setBuilding,
    setMetric,
    setPreset,
    setCustomRange,
    bumpSitesVersion,
    reset,
  }
})
