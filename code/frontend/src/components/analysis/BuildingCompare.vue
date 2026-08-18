<script setup lang="ts">
// ============================================================================
// BuildingCompare - 多楼时序对比折线图
// ----------------------------------------------------------------------------
// 数据源:
//   1. useAnalysisData 的 buildings (拿全部 6 栋楼的列表)
//   2. queryApi.compareBuildings(selectedIds, { metric, start, end }) - 日时序对比
//
// 布局: ChartCard 内 default slot 上下两栏
//   上: 6 个建筑小卡 (Chip 风格, 点击切换显隐)
//   下: 折线图 (每楼一条, 颜色跟 chip 同色)
//
// 默认选中 EUI 前 3 名 (跟用户对齐的决策 3)
//
// 切 metric / 时间范围 / 选楼都触发重新拉数据 (watch)
// ============================================================================

import { ref, onMounted, onBeforeUnmount, watch, nextTick, computed } from 'vue'
import * as echarts from 'echarts'
import { useContextStore } from '@/stores/context'
import { useAnalysisData } from '@/composables/useAnalysisData'
import { queryApi, type BuildingCompareResponse } from '@/api/query'
import ChartCard from './ChartCard.vue'
import {
  ECHARTS_THEME,
  ECHARTS_PALETTE,
  ECHARTS_COLORS,
  shortBuildingName,
  formatNumber,
} from '@/styles/echarts-theme'
import type { EChartsOption } from 'echarts'

const context = useContextStore()
const {
  buildings,
  loading: sharedLoading,
  error: sharedError,
  reload: sharedReload,
} = useAnalysisData()

// ---- 选楼状态 ----
// 默认 EUI 前 3 名 (没 EUI 数据时退化为前 3 个)
const selectedIds = ref<Set<string>>(new Set())

// 对比数据 (单次拉所有选中楼的日时序)
const compareData = ref<BuildingCompareResponse | null>(null)
const compareLoading = ref(false)
const compareError = ref<string | null>(null)

// 全部楼列表 (共享缓存)
const allBuildings = computed(() => buildings.value?.buildings ?? [])

// 按 EUI 降序排 (EUI 高的排前面, 默认选中前 3)
const rankedByEui = computed(() => {
  return [...allBuildings.value]
    .sort((a, b) => (b.eui_kwh_per_m2 ?? 0) - (a.eui_kwh_per_m2 ?? 0))
})

// 默认选中前 3 (buildings 加载后触发一次, 之后用户自由切)
watch(rankedByEui, (list) => {
  if (selectedIds.value.size === 0 && list.length > 0) {
    selectedIds.value = new Set(list.slice(0, 3).map(b => b.building_id))
  }
})

// chip 颜色按楼在 ranked 列表里的位置取 PALETTE
function getChipColor(buildingId: string): string {
  const idx = rankedByEui.value.findIndex(b => b.building_id === buildingId)
  if (idx < 0) return ECHARTS_COLORS.concrete
  return ECHARTS_PALETTE[idx % ECHARTS_PALETTE.length] ?? ECHARTS_COLORS.concrete
}

// ---- 拉对比数据 ----
async function loadCompare() {
  if (selectedIds.value.size === 0) {
    compareData.value = null
    return
  }
  const siteId = context.siteId
  if (!siteId) {
    compareError.value = '请先选择园区'
    return
  }

  compareLoading.value = true
  compareError.value = null

  try {
    const range = context.currentRange
    const ids = Array.from(selectedIds.value)
    // 后端 /query/buildings/compare 暂只支持 metric=interval_energy_kwh
    // (传入其他值会返 400 "暂只支持 metric=interval_energy_kwh")
    // 这里固定传 interval_energy_kwh, 不跟顶栏 metric 切换联动 (异常/费用没时序对比意义)
    compareData.value = await queryApi.compareBuildings(ids, {
      metric: 'interval_energy_kwh',
      start: range.start.toISOString(),
      end: range.end.toISOString(),
    })
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '加载对比数据失败'
    compareError.value = msg
  } finally {
    compareLoading.value = false
  }
}

// 选楼变化 / 时间范围变化都重拉
watch(
  selectedIds,
  () => { loadCompare() },
  { deep: true },
)
watch(
  () => context.currentRange,
  () => { loadCompare() },
  { deep: true },
)

// 切楼选中状态
function toggleBuilding(id: string) {
  const newSet = new Set(selectedIds.value)
  if (newSet.has(id)) {
    if (newSet.size <= 1) return  // 至少保留 1 栋, 全不选没意义
    newSet.delete(id)
  } else {
    newSet.add(id)
  }
  selectedIds.value = newSet
}

// ---- ECharts 实例 ----
const chartRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const chartOption = computed<EChartsOption | null>(() => {
  if (!compareData.value || compareData.value.buildings.length === 0) return null
  const data = compareData.value

  // 各楼共用同一套日期 X 轴 (取第一栋的 points.ts)
  const first = data.buildings[0]
  if (!first) return null
  // 后端返回字段是 points[{ts, value}], ts 是 YYYY-MM-DD 字符串
  const dates = first.points.map(p => p.ts)

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line' },
      formatter: (params: any) => {
        if (!params || params.length === 0) return ''
        const date = params[0]?.axisValue ?? ''
        let html = `<b>${date}</b>`
        for (const p of params) {
          const b = data.buildings.find(x => x.building_id === p.seriesId)
          if (!b) continue
          const val = p.value?.[1]
          html += `<br/>${p.marker} ${b.display_name}: <b>${val != null ? formatNumber(val) : '-'}</b> kWh`
        }
        return html
      },
    },
    legend: { show: false }, // 用自定义 chip 不用内置 legend
    grid: { left: 12, right: 16, top: 16, bottom: 24, containLabel: true },
    xAxis: {
      type: 'category',
      data: dates,
      boundaryGap: false,
      axisLabel: {
        // YYYY-MM-DD -> MM-DD 节省横向空间
        formatter: (val: string) => val.length >= 10 ? val.slice(5, 10) : val,
      },
    },
    yAxis: {
      type: 'value',
      name: '日能耗 (kWh)',
      nameTextStyle: { fontSize: 11, color: ECHARTS_COLORS.textSecondary },
    },
    series: data.buildings.map(b => ({
      id: b.building_id,
      name: b.display_name,
      type: 'line',
      data: b.points.map(p => [p.ts, p.value]),
      smooth: true,
      symbol: 'circle',
      symbolSize: 4,
      showSymbol: false,  // 数据点密集时不显示 symbol, hover 才显示
      lineStyle: { width: 2 },
      itemStyle: { color: getChipColor(b.building_id) },
      emphasis: { focus: 'series' },
    })),
  }
})

function initChart() {
  if (!chartRef.value) return
  chart = echarts.init(chartRef.value, undefined, { renderer: 'svg' })
  chart.setOption(ECHARTS_THEME as EChartsOption)
  if (chartOption.value) {
    chart.setOption(chartOption.value as EChartsOption, { replaceMerge: ['series'] })
  }
  resizeObserver = new ResizeObserver(() => chart?.resize())
  resizeObserver.observe(chartRef.value)
}

function updateChart() {
  if (!chart) return
  if (chartOption.value) {
    chart.setOption(chartOption.value as EChartsOption, { replaceMerge: ['series'] })
  } else {
    chart.clear()
  }
}

onMounted(() => {
  nextTick(initChart)
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})

watch(chartOption, updateChart, { deep: true })

// ---- 三态合并 ----
const isOverallLoading = computed(() => sharedLoading.value || compareLoading.value)
const overallError = computed(() => sharedError.value ?? compareError.value)
const isEmpty = computed(() => allBuildings.value.length === 0)

const subtitle = computed(() => {
  const count = selectedIds.value.size
  return `已选 ${count} 栋 · 默认 EUI 前 3 · 点击小卡切换显隐`
})

function onRetry() {
  sharedReload()
  loadCompare()
}
</script>

<template>
  <ChartCard
    title="多楼时序对比"
    :subtitle="subtitle"
    :loading="isOverallLoading"
    :error="overallError"
    :empty="isEmpty"
    :height="420"
    @retry="onRetry"
  >
    <div class="compare">
      <!-- 顶部建筑小卡 (Chip 风格, 点击切换显隐) -->
      <div class="compare__chips">
        <button
          v-for="b in rankedByEui"
          :key="b.building_id"
          type="button"
          class="compare-chip"
          :class="{ 'is-active': selectedIds.has(b.building_id) }"
          @click="toggleBuilding(b.building_id)"
        >
          <span
            class="compare-chip__dot"
            :style="{ background: getChipColor(b.building_id) }"
          />
          <span class="compare-chip__name">{{ shortBuildingName(b.building_code) }}</span>
          <span class="compare-chip__value">
            {{ b.eui_kwh_per_m2 != null ? formatNumber(b.eui_kwh_per_m2) : '-' }}
          </span>
        </button>
      </div>

      <!-- 折线图容器 -->
      <div ref="chartRef" class="compare__chart" />
    </div>
  </ChartCard>
</template>

<style scoped lang="scss">
.compare {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  gap: $space-2;

  &__chips {
    display: flex;
    flex-wrap: wrap;
    gap: $space-1;
    flex-shrink: 0;
  }

  &__chart {
    flex: 1;
    min-height: 0;
    width: 100%;
  }
}

.compare-chip {
  display: inline-flex;
  align-items: center;
  gap: $space-1;
  padding: 4px 10px 4px 8px;
  border: 1px solid $gray-200;
  border-radius: $radius-pill;
  background: $color-card;
  cursor: pointer;
  transition: all $transition-fast;
  font-family: inherit;

  &.is-active {
    border-color: $color-amber;
    background: $color-amber-soft;
    box-shadow: 0 0 0 1px $color-amber inset;
  }

  &:not(.is-active) {
    opacity: 0.5;

    &:hover {
      opacity: 0.85;
      border-color: $color-line;
    }
  }

  &__dot {
    width: 8px;
    height: 8px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  &__name {
    font-size: $fs-xs;
    color: $color-concrete;
    font-weight: $fw-medium;
    line-height: 1.2;
  }

  &__value {
    font-size: $fs-xs;
    font-family: $font-mono;
    color: $color-text-secondary;
    line-height: 1.2;
  }
}
</style>
