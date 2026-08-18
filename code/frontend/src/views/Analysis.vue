<script setup lang="ts">
// ============================================================================
// Analysis - 分析中心页
// ----------------------------------------------------------------------------
// 一页全展示 7 个 ECharts 图表组件 (2x4 网格, PredictionPanel 跨 2 列):
//
//   ┌──────────────┬──────────────┬──────────────┬──────────────┐
//   │ Ranking      │ Compare      │ EuiBaseline  │ Anomaly      │
//   ├──────────────┼──────────────┼──────────────┴──────────────┤
//   │ Weather      │ DataQuality  │ Prediction (跨 2 列)         │
//   └──────────────┴──────────────┴─────────────────────────────┘
//
// PredictionPanel 跨 2 列 (能耗预测是核心模块, 折线图横轴日期密集,
// 宽一点才能看清). align-items: stretch 让同行卡片等高.
//
// 顶部信息条: 园区名 + 时间范围 + 指标 + 数据更新时间 + 手动刷新
// 响应式: > 1440px 4 列 / 768-1440px 2 列 / < 768px 1 列
//
// 数据流: useAnalysisData 集中拉 buildings + anomaly overview + quality overview
// 各图表组件 inject 拿共享缓存, 切园区/时间范围自动 reload
// ============================================================================

import { computed, onMounted, watch, ref } from 'vue'
import { useRouter } from 'vue-router'
import { useContextStore } from '@/stores/context'
import { useAnalysisData, loadAnalysisData } from '@/composables/useAnalysisData'
import { sitesApi } from '@/api/sites'
import BuildingRanking from '@/components/analysis/BuildingRanking.vue'
import BuildingCompare from '@/components/analysis/BuildingCompare.vue'
import EuiBaseline from '@/components/analysis/EuiBaseline.vue'
import AnomalyOverview from '@/components/analysis/AnomalyOverview.vue'
import WeatherCorrelation from '@/components/analysis/WeatherCorrelation.vue'
import DataQualityPanel from '@/components/analysis/DataQualityPanel.vue'
import PredictionPanel from '@/components/analysis/PredictionPanel.vue'
import { Building2, Activity, Gauge, AlertTriangle, RefreshCw, Layers } from 'lucide-vue-next'

const context = useContextStore()
const router = useRouter()
const {
  buildings,
  anomalyOverview,
  loading,
  error,
  lastLoadTs,
  reload,
} = useAnalysisData()

// ---- 顶部信息条数据 ----
// 当前园区名 (从 sites API 拿, 因为 buildings 接口返的 building_code 不含完整园区名)
const siteName = ref<string>('')

const PRESET_LABELS: Record<string, string> = {
  today: '今日',
  this_week: '本周',
  this_month: '本月',
  this_quarter: '本季度',
  this_year: '本年',
  custom: '自定义',
}

const METRIC_ICON = {
  total_kwh: Activity,
  eui: Gauge,
  anomaly_count: AlertTriangle,
  cost: Activity,
} as const

const timeRangeText = computed(() => {
  const r = context.currentRange
  const fmt = (d: any) => d?.format?.('YYYY-MM-DD') ?? ''
  return `${fmt(r.start)} ~ ${fmt(r.end)}`
})

const presetText = computed(() => {
  return PRESET_LABELS[context.timePreset] ?? context.timePreset
})

const metricText = computed(() => context.metricLabel)

const lastUpdateText = computed(() => {
  if (!lastLoadTs.value) return '-'
  const d = new Date(lastLoadTs.value)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}:${String(d.getSeconds()).padStart(2, '0')}`
})

const metricIcon = computed(() => METRIC_ICON[context.metric] ?? Activity)

// 顶部信息条统计
const overviewStats = computed(() => {
  const buildingCount = buildings.value?.buildings.length ?? 0
  const totalAnomalies = anomalyOverview.value?.total_anomalies ?? 0
  const highAnomalies = anomalyOverview.value?.by_severity?.HIGH ?? 0
  return [
    {
      icon: Building2,
      label: '建筑',
      value: buildingCount,
      unit: '栋',
    },
    {
      icon: AlertTriangle,
      label: '异常',
      value: totalAnomalies,
      unit: '条',
      highlight: highAnomalies > 0,
    },
    {
      icon: Layers,
      label: '统计区间',
      value: presetText.value,
      unit: '',
    },
  ]
})

// ---- 加载/错误处理 ----
async function refresh() {
  if (!context.siteId) {
    // 没园区直接跳 park 让用户选
    router.push('/park')
    return
  }
  await loadAnalysisData(true)
}

// 拉园区名 (site name 不在 buildings 接口里, 单独拉)
async function loadSiteName() {
  if (!context.siteId) return
  try {
    const sites = await sitesApi.listSites()
    const s = sites.find(x => x.site_id === context.siteId)
    if (s) siteName.value = s.site_name
  } catch {
    // 拉不到就用 siteId 兜底
    siteName.value = context.siteId
  }
}

// 监听 context 变化自动 reload
watch(
  () => context.siteId,
  (id) => {
    if (id) {
      loadAnalysisData()
      loadSiteName()
    }
  },
)

watch(
  () => context.currentRange,
  () => {
    if (context.siteId) loadAnalysisData()
  },
  { deep: true },
)

onMounted(() => {
  if (context.siteId) {
    loadAnalysisData()
    loadSiteName()
  }
})
</script>

<template>
  <div class="analysis-page">
    <!-- 顶部信息条 -->
    <header class="analysis-header">
      <div class="analysis-header__left">
        <div class="analysis-header__title-wrap">
          <component :is="metricIcon" :size="16" class="analysis-header__icon" />
          <h1 class="analysis-header__title">
            {{ siteName || '分析中心' }}
          </h1>
          <span class="analysis-header__sub">分析中心</span>
        </div>
        <div class="analysis-header__stats">
          <div
            v-for="stat in overviewStats"
            :key="stat.label"
            class="stat-pill"
            :class="{ 'stat-pill--highlight': stat.highlight }"
          >
            <component :is="stat.icon" :size="12" />
            <span class="stat-pill__label">{{ stat.label }}</span>
            <span class="stat-pill__value">{{ stat.value }}</span>
            <span v-if="stat.unit" class="stat-pill__unit">{{ stat.unit }}</span>
          </div>
        </div>
      </div>

      <div class="analysis-header__right">
        <div class="meta-line">
          <span class="meta-line__label">指标</span>
          <span class="meta-line__value">{{ metricText }}</span>
        </div>
        <div class="meta-line">
          <span class="meta-line__label">时间</span>
          <span class="meta-line__value">{{ presetText }}</span>
          <span class="meta-line__hint">{{ timeRangeText }}</span>
        </div>
        <div class="meta-line">
          <span class="meta-line__label">更新</span>
          <span class="meta-line__value">{{ lastUpdateText }}</span>
        </div>
        <button class="refresh-btn" :disabled="loading" @click="refresh">
          <RefreshCw :size="12" :class="{ 'is-spinning': loading }" />
          <span>刷新</span>
        </button>
      </div>
    </header>

    <!-- 错误条 (全局错误, 单卡片错误由 ChartCard 自己显示) -->
    <div v-if="error" class="analysis-error">
      <AlertTriangle :size="14" />
      <span>{{ error }}</span>
      <button @click="refresh">重试</button>
    </div>

    <!-- 2x4 网格 (PredictionPanel 第 7 位, 跨 2 列) -->
    <div class="analysis-grid">
      <BuildingRanking />
      <BuildingCompare />
      <EuiBaseline />
      <AnomalyOverview />
      <WeatherCorrelation />
      <DataQualityPanel />
      <div class="analysis-grid__prediction">
        <PredictionPanel />
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.analysis-page {
  display: flex;
  flex-direction: column;
  gap: $space-3;
  padding: $space-4;
  min-height: 100%;
  background: $color-paper;
}

// ---- 顶部信息条 ----
.analysis-header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: $space-4;
  flex-wrap: wrap;
  padding: $space-3 $space-4;
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  box-shadow: $shadow-xs;

  &__left {
    display: flex;
    flex-direction: column;
    gap: $space-2;
    flex: 1;
    min-width: 0;
  }

  &__right {
    display: flex;
    align-items: center;
    gap: $space-3;
    flex-shrink: 0;
  }

  &__title-wrap {
    display: flex;
    align-items: center;
    gap: $space-2;
    color: $color-concrete;
  }

  &__icon {
    color: $color-amber;
  }

  &__title {
    font-size: $fs-lg;
    font-weight: $fw-semibold;
    margin: 0;
    line-height: $lh-tight;
  }

  &__sub {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-left: $space-1;
    padding: 2px 6px;
    background: $color-amber-soft;
    border-radius: $radius-xs;
    color: $color-amber-deep;
    font-weight: $fw-medium;
  }

  &__stats {
    display: flex;
    flex-wrap: wrap;
    gap: $space-2;
  }
}

.stat-pill {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 4px 10px;
  background: $gray-50;
  border: 1px solid $gray-200;
  border-radius: $radius-pill;
  font-size: $fs-xs;
  color: $color-text-secondary;

  &__label {
    font-weight: $fw-medium;
  }

  &__value {
    font-family: $font-mono;
    font-weight: $fw-semibold;
    color: $color-concrete;
  }

  &__unit {
    color: $color-text-secondary;
    font-size: 10px;
  }

  &--highlight {
    border-color: $color-red;
    background: rgba(184, 74, 60, 0.06);

    .stat-pill__value {
      color: $color-red;
    }
  }
}

.meta-line {
  display: flex;
  align-items: baseline;
  gap: 4px;
  padding: 4px 0;
  border-bottom: 1px dashed transparent;
  font-size: $fs-xs;
  position: relative;

  &__label {
    color: $color-text-secondary;
  }

  &__value {
    font-family: $font-mono;
    color: $color-concrete;
    font-weight: $fw-medium;
  }

  &__hint {
    color: $color-text-secondary;
    font-size: 10px;
    margin-left: 4px;
  }
}

.refresh-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 6px 12px;
  background: $color-amber;
  border: none;
  border-radius: $radius-sm;
  color: white;
  font-size: $fs-xs;
  font-weight: $fw-medium;
  cursor: pointer;
  transition: background $transition-fast, transform $transition-fast;

  &:hover:not(:disabled) {
    background: $color-amber-hover;
  }

  &:active:not(:disabled) {
    transform: translateY(1px);
  }

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  .is-spinning {
    animation: analysis-refresh-spin 1s linear infinite;
  }
}

// ---- 错误条 ----
.analysis-error {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: $space-2 $space-4;
  background: $color-red-soft;
  border: 1px solid $color-red;
  border-radius: $radius-sm;
  color: $color-red;
  font-size: $fs-sm;

  button {
    margin-left: auto;
    padding: 2px 8px;
    background: transparent;
    border: 1px solid $color-red;
    color: $color-red;
    border-radius: $radius-xs;
    font-size: $fs-xs;
    cursor: pointer;

    &:hover {
      background: $color-red;
      color: white;
    }
  }
}

// ---- 2x4 网格 (3 列 -> 4 列, PredictionPanel 跨 2 列) ----
.analysis-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: $space-3;
  align-items: stretch;
  grid-auto-rows: auto;

  // PredictionPanel 跨 2 列: 占据第 2 行后两个位置, 横向更宽
  // (折线图日期密集, 横向空间多才能看清预测区间)
  &__prediction {
    grid-column: span 2;
    display: flex;

    :deep(.chart-card) {
      width: 100%;
    }
  }

  // 2 列 (768-1440px): PredictionPanel 仍跨 2 列, 占据整行
  @media (max-width: 1440px) {
    grid-template-columns: repeat(2, 1fr);
  }

  // 1 列 (< 768px)
  @media (max-width: 768px) {
    grid-template-columns: 1fr;

    .analysis-grid__prediction {
      grid-column: span 1;
    }
  }
}

@keyframes analysis-refresh-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}
</style>
