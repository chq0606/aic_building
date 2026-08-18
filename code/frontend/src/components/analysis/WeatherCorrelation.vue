<script setup lang="ts">
// ============================================================================
// WeatherCorrelation - 能耗与天气关联分析
// ----------------------------------------------------------------------------
// 数据源 (按选中楼单独拉, 不在共享缓存):
//   1. queryApi.buildingTimeseries(buildingId, { granularity: 'day' }) - 日能耗
//   2. queryApi.buildingWeather(buildingId) - 小时级天气
//
// 布局: ChartCard 内 default slot 上下两栏
//   上: 双轴折线 - 左轴日能耗 kWh (琥珀), 右轴日均气温 ℃ (岩蓝灰)
//   下: 散点图 - X = 日均气温, Y = 当日能耗
//
// 标题展示 Pearson 相关系数 + 强弱标签 (强相关 / 中等相关 / 弱相关)
// ============================================================================

import { ref, onMounted, onBeforeUnmount, watch, nextTick, computed } from 'vue'
import * as echarts from 'echarts'
import { useContextStore } from '@/stores/context'
import { useAnalysisData } from '@/composables/useAnalysisData'
import { queryApi, type BuildingTimeseries, type BuildingWeather } from '@/api/query'
import ChartCard from './ChartCard.vue'
import {
  ECHARTS_THEME,
  ECHARTS_COLORS,
  shortBuildingName,
  formatNumber,
  pearson,
  correlationLabel,
} from '@/styles/echarts-theme'
import type { EChartsOption } from 'echarts'

const context = useContextStore()
const { buildings, loading: sharedLoading, error: sharedError, reload: sharedReload } = useAnalysisData()

// 选中的楼 (默认 EUI 最高, undefined 而非 null 因为 a-select 的 SelectValue 类型)
const selectedBuildingId = ref<string | undefined>(undefined)

const timeseriesData = ref<BuildingTimeseries | null>(null)
const weatherData = ref<BuildingWeather | null>(null)
const localLoading = ref(false)
const localError = ref<string | null>(null)

const allBuildings = computed(() => buildings.value?.buildings ?? [])

const buildingOptions = computed(() => {
  return allBuildings.value
    .map(b => ({
      value: b.building_id,
      label: b.display_name,
    }))
})

// buildings 加载后默认选 EUI 最高那栋
watch(buildingOptions, (opts) => {
  if (!selectedBuildingId.value && opts.length > 0) {
    // 按 EUI 降序拿第 1 个
    const sorted = [...allBuildings.value].sort(
      (a, b) => (b.eui_kwh_per_m2 ?? 0) - (a.eui_kwh_per_m2 ?? 0),
    )
    selectedBuildingId.value = sorted[0]?.building_id ?? opts[0].value
  }
})

async function loadWeatherCorrelation() {
  if (!selectedBuildingId.value) return
  localLoading.value = true
  localError.value = null
  try {
    const range = context.currentRange
    const start = range.start.toISOString()
    const end = range.end.toISOString()
    // 并行拉 timeseries + weather
    const [ts, w] = await Promise.all([
      queryApi.buildingTimeseries(selectedBuildingId.value, {
        granularity: 'day',
        start,
        end,
      }),
      queryApi.buildingWeather(selectedBuildingId.value, { start, end }),
    ])
    timeseriesData.value = ts
    weatherData.value = w
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '加载天气关联数据失败'
    localError.value = msg
  } finally {
    localLoading.value = false
  }
}

watch(selectedBuildingId, loadWeatherCorrelation)
watch(() => context.currentRange, loadWeatherCorrelation, { deep: true })

// ---- 数据预处理: 把日能耗 + 日均气温按日期配对 ----
interface DayPair {
  date: string  // YYYY-MM-DD
  energy: number | null  // 日能耗 kWh
  temp: number | null    // 日均气温 ℃
}

const pairedDays = computed<DayPair[]>(() => {
  if (!timeseriesData.value && !weatherData.value) return []

  // 1. 日能耗按 date 分组 (timeseries granularity=day, 一天一个点)
  const energyByDate: Record<string, number> = {}
  if (timeseriesData.value) {
    for (const p of timeseriesData.value.points) {
      const date = p.ts.slice(0, 10)
      energyByDate[date] = p.value
    }
  }

  // 2. 天气按 date 聚合 (weather 可能是小时级, 取日均气温)
  const tempAggByDate: Record<string, { sum: number; count: number }> = {}
  if (weatherData.value) {
    for (const r of weatherData.value.points) {
      if (r.air_temp_c == null) continue
      const date = r.ts.slice(0, 10)
      if (!tempAggByDate[date]) tempAggByDate[date] = { sum: 0, count: 0 }
      tempAggByDate[date].sum += r.air_temp_c
      tempAggByDate[date].count++
    }
  }

  // 3. 取并集日期, 排序
  const dateSet = new Set<string>([
    ...Object.keys(energyByDate),
    ...Object.keys(tempAggByDate),
  ])
  const dates = Array.from(dateSet).sort()

  return dates.map(date => ({
    date,
    energy: energyByDate[date] ?? null,
    temp: tempAggByDate[date]
      ? tempAggByDate[date].sum / tempAggByDate[date].count
      : null,
  }))
})

// ---- Pearson 相关系数 ----
const correlation = computed(() => {
  const pairs = pairedDays.value
    .filter(p => p.energy != null && p.temp != null)
    .map(p => [p.temp as number, p.energy as number])
  if (pairs.length < 2) return 0
  return pearson(
    pairs.map(p => p[0]),
    pairs.map(p => p[1]),
  )
})

const correlationText = computed(() => {
  const r = correlation.value
  return `${r.toFixed(2)} (${correlationLabel(r)})`
})

// ---- ECharts 实例 (上半双轴 + 下半散点合一张图, 用 grid 切分) ----
const chartRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const chartOption = computed<EChartsOption | null>(() => {
  if (pairedDays.value.length === 0) return null
  const days = pairedDays.value

  return {
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        if (!params || params.length === 0) return ''
        const date = params[0]?.axisValue ?? ''
        const day = days.find(d => d.date === date)
        if (!day) return date
        const e = day.energy != null ? `${formatNumber(day.energy)} kWh` : '-'
        const t = day.temp != null ? `${day.temp.toFixed(1)} ℃` : '-'
        return `<b>${date}</b><br/>能耗: ${e}<br/>气温: ${t}`
      },
    },
    // 显式 legend 放顶部居中: 之前没配 legend, ECharts 自动渲染跟 yAxis name
    // "日能耗 (kWh)"/"气温 (℃)" 重叠成 "杂糅" (橙圆+日能耗/能耗, 蓝圆+气温, 黄圆+...)
    // 显式定位后 legend 独占顶部一行, 单位写到 legend 文字里, yAxis name 删掉防重叠
    legend: {
      data: ['能耗 (kWh)', '气温 (℃)', '能耗 vs 气温'],
      top: 4,
      left: 'center',
      itemWidth: 10,
      itemHeight: 10,
      icon: 'circle',
      textStyle: { fontSize: 11, color: ECHARTS_COLORS.textSecondary },
    },
    // 双 grid: 上半双轴折线 (60%), 下半散点 (40%)
    // top: 36 给 legend 让一行, yAxis name 已删不用再留 name 空间
    grid: [
      { left: 12, right: 56, top: 36, height: '55%', containLabel: true },
      { left: 12, right: 56, bottom: 16, height: '28%', containLabel: true },
    ],
    xAxis: [
      // 上: 日期 category
      {
        type: 'category',
        gridIndex: 0,
        data: days.map(d => d.date),
        boundaryGap: false,
        axisLabel: {
          formatter: (val: string) => val.length >= 10 ? val.slice(5, 10) : val,
        },
      },
      // 下: 气温 value
      {
        type: 'value',
        gridIndex: 1,
        name: '日均气温 (℃)',
        nameTextStyle: { fontSize: 10, color: ECHARTS_COLORS.stone },
        position: 'bottom',
        axisLabel: { fontSize: 10, color: ECHARTS_COLORS.stone },
        splitLine: { show: false },
      },
    ],
    yAxis: [
      // 左轴: 能耗 (上半) - name 删掉, 单位已在 legend "能耗 (kWh)" 里, 防跟 legend 杂糅
      {
        type: 'value',
        gridIndex: 0,
        axisLabel: {
          color: ECHARTS_COLORS.amberDeep,
          formatter: (v: number) => formatNumber(v, 0),
        },
        splitLine: { lineStyle: { color: ECHARTS_COLORS.split, type: 'dashed' } },
      },
      // 右轴: 气温 (上半) - name 删掉, 单位已在 legend "气温 (℃)" 里
      {
        type: 'value',
        gridIndex: 0,
        position: 'right',
        axisLabel: { color: ECHARTS_COLORS.stone, formatter: '{value}℃' },
        splitLine: { show: false },
      },
      // 下半散点 Y: 能耗 - name 删掉, legend "能耗 vs 气温" 已说明
      {
        type: 'value',
        gridIndex: 1,
        axisLabel: {
          color: ECHARTS_COLORS.amberDeep,
          formatter: (v: number) => formatNumber(v, 0),
        },
        splitLine: { lineStyle: { color: ECHARTS_COLORS.split, type: 'dashed' } },
      },
    ],
    series: [
      // 上半: 能耗折线 (左轴)
      {
        name: '能耗 (kWh)',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 0,
        data: days.map(d => d.energy),
        smooth: true,
        showSymbol: false,
        lineStyle: { color: ECHARTS_COLORS.amber, width: 2 },
        itemStyle: { color: ECHARTS_COLORS.amber },
      },
      // 上半: 气温折线 (右轴)
      {
        name: '气温 (℃)',
        type: 'line',
        xAxisIndex: 0,
        yAxisIndex: 1,
        data: days.map(d => d.temp),
        smooth: true,
        showSymbol: false,
        lineStyle: { color: ECHARTS_COLORS.stone, width: 2, type: 'dashed' },
        itemStyle: { color: ECHARTS_COLORS.stone },
      },
      // 下半: 散点 (X=气温, Y=能耗)
      {
        name: '能耗 vs 气温',
        type: 'scatter',
        xAxisIndex: 1,
        yAxisIndex: 2,
        data: days
          .filter(d => d.energy != null && d.temp != null)
          .map(d => [d.temp, d.energy]),
        symbolSize: 8,
        itemStyle: {
          color: ECHARTS_COLORS.amber,
          opacity: 0.7,
        },
        emphasis: {
          itemStyle: {
            color: ECHARTS_COLORS.amberDeep,
            opacity: 1,
          },
        },
      },
    ],
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
  if (buildingOptions.value.length > 0 && !selectedBuildingId.value) {
    const sorted = [...allBuildings.value].sort(
      (a, b) => (b.eui_kwh_per_m2 ?? 0) - (a.eui_kwh_per_m2 ?? 0),
    )
    selectedBuildingId.value = sorted[0]?.building_id ?? buildingOptions.value[0].value
  } else if (selectedBuildingId.value) {
    loadWeatherCorrelation()
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})

watch(chartOption, updateChart, { deep: true })

// ---- 三态合并 ----
const isOverallLoading = computed(() => sharedLoading.value || localLoading.value)
const overallError = computed(() => sharedError.value ?? localError.value)
const isEmpty = computed(() => allBuildings.value.length === 0)

const subtitle = computed(() => {
  if (!selectedBuildingId.value) return '请选择建筑'
  const b = allBuildings.value.find(b => b.building_id === selectedBuildingId.value)
  if (!b) return ''
  return `${b.display_name} · 相关系数 ${correlationText.value}`
})

function onRetry() {
  sharedReload()
  loadWeatherCorrelation()
}
</script>

<template>
  <ChartCard
    title="能耗 - 天气关联"
    :subtitle="subtitle"
    :loading="isOverallLoading"
    :error="overallError"
    :empty="isEmpty"
    :height="480"
    @retry="onRetry"
  >
    <template #extra>
      <a-select
        v-model:value="selectedBuildingId"
        size="small"
        style="width: 160px"
        :options="buildingOptions"
        placeholder="选择建筑"
        :loading="sharedLoading"
      />
    </template>

    <div ref="chartRef" class="weather-chart" />
  </ChartCard>
</template>

<style scoped lang="scss">
.weather-chart {
  width: 100%;
  height: 100%;
  min-height: 0;
}
</style>
