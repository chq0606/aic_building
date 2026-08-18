<script setup lang="ts">
// ============================================================================
// AnomalyOverview - 异常事件汇总
// ----------------------------------------------------------------------------
// 数据源: useAnalysisData 的 anomalyOverview (site 级异常总览)
//
// 布局: 一张 ChartCard 内左右两栏 (跟其他 6 个分析卡片等高 360px)
//   左: 饼图 - 按 event_type (SPIKE / DRIFT / PROLONGED_ZERO / MISSING_GAP
//        / SCHEDULE_VIOLATION / BASELINE_DEVIATION 6 类) 占比
//   右: 堆叠柱图 - 每楼异常总数, 按严重度 LOW/MEDIUM/HIGH 堆叠
//
// 1 列窄容器 (~128px 饼图区) 下饼图等比例缩小: radius 60% -> 38%, 标签
// 用紧凑单行 '{b} {d}%' + 短引导线, 避免 6 类标签溢出被裁
//
// 设计取舍:
//   - 用 ChartCard 的 default slot 自定义 body, ChartCard 提供 header/footer/loading/error
//   - 2 个 ECharts 实例 (pie + bar) 独立管理, 各自 ResizeObserver
//   - 没有再单独调 listBuildingAnomalies API, 信息密度够点具体楼去抽屉看详情
// ============================================================================

import { ref, onMounted, onBeforeUnmount, watch, nextTick, computed } from 'vue'
import * as echarts from 'echarts'
import { useAnalysisData } from '@/composables/useAnalysisData'
import ChartCard from './ChartCard.vue'
import {
  ECHARTS_THEME,
  ECHARTS_COLORS,
  SEVERITY_COLORS,
  ANOMALY_TYPE_COLORS,
  ANOMALY_TYPE_LABELS,
  shortBuildingName,
} from '@/styles/echarts-theme'
import type { EChartsOption } from 'echarts'

const { anomalyOverview, loading, error, reload } = useAnalysisData()

// 两个 chart 容器 + 实例 + ResizeObserver (pie + bar 各管各的)
const pieChartRef = ref<HTMLDivElement | null>(null)
const barChartRef = ref<HTMLDivElement | null>(null)
let pieChart: echarts.ECharts | null = null
let barChart: echarts.ECharts | null = null
let pieResizeObserver: ResizeObserver | null = null
let barResizeObserver: ResizeObserver | null = null

const isEmpty = computed(
  () => !anomalyOverview.value || anomalyOverview.value.total_anomalies === 0,
)

const subtitle = computed(() => {
  if (!anomalyOverview.value) return ''
  const o = anomalyOverview.value
  const high = o.by_severity?.HIGH ?? 0
  const medium = o.by_severity?.MEDIUM ?? 0
  const low = o.by_severity?.LOW ?? 0
  return `共 ${o.total_anomalies} 条 · 高危 ${high} · 中危 ${medium} · 低危 ${low}`
})

// ---- 左: 饼图 - 6 类异常占比 ----
// 关键决策: AnomalyOverview 在 Analysis.vue 里跨 2 列 (grid-column: span 2),
// 左侧饼图容器 ~480px, 6 类标签 + 引导线 + 占比数字舒展显示不会被裁
// 之前在 1 列里挤, 容器仅 128px, 标签全挤一起
const typeBarData = computed(() => {
  if (!anomalyOverview.value) return { data: [], total: 0 }
  const byType = anomalyOverview.value.by_type ?? {}
  const total = anomalyOverview.value.total_anomalies ?? 0
  const TYPES = ['SPIKE', 'DRIFT', 'PROLONGED_ZERO', 'MISSING_GAP', 'SCHEDULE_VIOLATION', 'BASELINE_DEVIATION']
  const items = TYPES
    .map(t => {
      const count = byType[t] ?? 0
      return {
        key: t,
        label: ANOMALY_TYPE_LABELS[t] ?? t,
        color: ANOMALY_TYPE_COLORS[t] ?? ECHARTS_COLORS.stone,
        count,
        percent: total > 0 ? Math.round((count / total) * 100) : 0,
      }
    })
    .filter(item => item.count > 0)
  return { data: items, total }
})

const pieOption = computed<EChartsOption | null>(() => {
  const { data: items } = typeBarData.value
  if (items.length === 0) return null

  return {
    tooltip: {
      trigger: 'item',
      formatter: (params: any) => {
        const item = items.find(i => i.label === params.name)
        if (!item) return ''
        return `<b>${item.label}</b><br/>数量: <b style="color:${item.color}">${item.count}</b> 条<br/>占比: ${item.percent}%`
      },
    },
    series: [
      {
        type: 'pie',
        // 窄容器 (~128px) 等比例缩小: radius 38% -> 45%, 留外圈给标签和引导线
        // center Y 从 50% -> 55% 略下移, 给上方标签留更多空间
        radius: '65%',
        center: ['50%', '70%'],
        avoidLabelOverlap: true,
        data: items.map(i => ({
          name: i.label,
          value: i.count,
          itemStyle: { color: i.color },
        })),
        // 标签全关: 窄容器下 6 类标签无论怎么排都会被裁或重叠, 直接关掉
        // 信息靠 hover tooltip 显示 (名称+数量+占比), 饼图色块本身保留视觉区分
        label: { show: false },
        // 无引导线: 窄容器 + 饼图放大后外圈空间紧张, 引导线会被容器边缘裁掉
        labelLine: { show: false },
        // hideOverlap: true 让 ECharts 自动隐藏重叠标签 (标签关了不影响)
        labelLayout: { hideOverlap: true },
        emphasis: {
          scaleSize: 4,
          label: { fontWeight: 600 },
        },
      },
    ],
  }
})

// ---- 右: 堆叠柱图 option ----
const barOption = computed<EChartsOption | null>(() => {
  if (!anomalyOverview.value) return null
  const list = anomalyOverview.value.buildings ?? []
  if (list.length === 0) return null

  // 按异常总数降序, 严重的楼在左
  const sorted = [...list].sort((a, b) => b.anomaly_count - a.anomaly_count)

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: any) => {
        const idx = params[0]?.dataIndex ?? 0
        const b = sorted[idx]
        if (!b) return ''
        return [
          `<b>${b.display_name}</b>`,
          `建筑编码: ${b.building_code}`,
          `异常总数: <b style="color:${ECHARTS_COLORS.amberDeep}">${b.anomaly_count}</b> 条`,
          `高危 HIGH: ${b.high_count} 条`,
          `中危 MEDIUM: ${b.medium_count} 条`,
          `低危 LOW: ${b.low_count} 条`,
        ].join('<br/>')
      },
    },
    legend: {
      data: ['低危 LOW', '中危 MEDIUM', '高危 HIGH'],
      bottom: 0,
      itemWidth: 10,
      itemHeight: 10,
      icon: 'roundRect',
    },
    grid: { left: 8, right: 8, top: 16, bottom: 56, containLabel: true },
    xAxis: {
      type: 'category',
      data: sorted.map(b => shortBuildingName(b.building_code)),
      // 右柱区域窄 (~163px), 建筑名 45° 倾斜全显示. fontSize 缩到 10 防挤.
      // grid bottom 已从 36 加到 56 给倾斜标签留垂直空间.
      axisLabel: {
        fontSize: 10,
        color: ECHARTS_COLORS.textSecondary,
        rotate: 45,
        interval: 0,
        hideOverlap: false,
      },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      axisLabel: { fontSize: 11 },
    },
    series: [
      {
        name: '低危 LOW',
        type: 'bar',
        stack: 'anomaly',
        data: sorted.map(b => b.low_count ?? 0),
        itemStyle: { color: SEVERITY_COLORS.LOW },
        barWidth: '52%',
      },
      {
        name: '中危 MEDIUM',
        type: 'bar',
        stack: 'anomaly',
        data: sorted.map(b => b.medium_count ?? 0),
        itemStyle: { color: SEVERITY_COLORS.MEDIUM },
      },
      {
        name: '高危 HIGH',
        type: 'bar',
        stack: 'anomaly',
        data: sorted.map(b => b.high_count ?? 0),
        itemStyle: { color: SEVERITY_COLORS.HIGH },
      },
    ],
  }
})

function initPie() {
  if (!pieChartRef.value) return
  pieChart = echarts.init(pieChartRef.value, undefined, { renderer: 'svg' })
  pieChart.setOption(ECHARTS_THEME as EChartsOption)
  if (pieOption.value) {
    pieChart.setOption(pieOption.value as EChartsOption, { replaceMerge: ['series'] })
  }
  pieResizeObserver = new ResizeObserver(() => pieChart?.resize())
  pieResizeObserver.observe(pieChartRef.value)
}

function initBar() {
  if (!barChartRef.value) return
  barChart = echarts.init(barChartRef.value, undefined, { renderer: 'svg' })
  barChart.setOption(ECHARTS_THEME as EChartsOption)
  if (barOption.value) {
    barChart.setOption(barOption.value as EChartsOption, { replaceMerge: ['series'] })
  }
  barResizeObserver = new ResizeObserver(() => barChart?.resize())
  barResizeObserver.observe(barChartRef.value)
}

function updatePie() {
  if (!pieChart) return
  if (pieOption.value) {
    pieChart.setOption(pieOption.value as EChartsOption, { replaceMerge: ['series'] })
  } else {
    pieChart.clear()
  }
}

function updateBar() {
  if (!barChart) return
  if (barOption.value) {
    barChart.setOption(barOption.value as EChartsOption, { replaceMerge: ['series'] })
  } else {
    barChart.clear()
  }
}

onMounted(() => {
  nextTick(() => {
    initPie()
    initBar()
  })
})

onBeforeUnmount(() => {
  pieResizeObserver?.disconnect()
  barResizeObserver?.disconnect()
  pieChart?.dispose()
  barChart?.dispose()
  pieChart = null
  barChart = null
})

watch(pieOption, updatePie, { deep: true })
watch(barOption, updateBar, { deep: true })

// 当 loading/error 切换时, container 可能从 hidden 切到 visible, 需要 resize
watch(
  () => [loading.value, error.value, isEmpty.value],
  () => {
    nextTick(() => {
      pieChart?.resize()
      barChart?.resize()
    })
  },
)
</script>

<template>
  <ChartCard
    title="异常事件汇总"
    :subtitle="subtitle"
    :loading="loading"
    :error="error"
    :empty="isEmpty"
    empty-text="该园区本周期无异常事件"
    @retry="reload"
  >
    <div class="anomaly-overview">
      <div class="anomaly-overview__chart">
        <div ref="pieChartRef" class="anomaly-overview__dom" />
        <div class="anomaly-overview__caption">按异常类型</div>
      </div>
      <div class="anomaly-overview__chart">
        <div ref="barChartRef" class="anomaly-overview__dom" />
        <div class="anomaly-overview__caption">每楼异常堆叠 (按严重度)</div>
      </div>
    </div>
  </ChartCard>
</template>

<style scoped lang="scss">
.anomaly-overview {
  // 1fr 1.4fr: 左饼图右柱图, 跟其他分析卡片等高 360px
  // 4 列 dashboard 下 ChartCard ~343px, 减 padding ~309px, 1:1.4 切分饼图 128px / 柱图 180px
  display: grid;
  grid-template-columns: 1fr 1.4fr;
  gap: $space-3;
  height: 100%;
  min-height: 0;
  padding: $space-2 0;

  &__chart {
    display: flex;
    flex-direction: column;
    height: 100%;
    min-height: 0;
  }

  &__dom {
    flex: 1;
    min-height: 0;
    width: 100%;
  }

  &__caption {
    margin-top: $space-1;
    font-size: $fs-xs;
    color: $color-text-secondary;
    text-align: center;
    font-weight: $fw-medium;
    letter-spacing: 0.5px;
    flex-shrink: 0;
  }
}
</style>
