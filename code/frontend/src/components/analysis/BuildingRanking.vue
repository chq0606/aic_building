<script setup lang="ts">
// ============================================================================
// BuildingRanking - 建筑能耗排名 横向柱状图
// ----------------------------------------------------------------------------
// 数据源: useAnalysisData 的 buildings (共享缓存)
// 排序: 按当前顶栏 metric 降序 (EUI / 总能耗 / 异常数 / 估算费用)
// 视觉:
//   - 6 栋楼横排, 排名 1~3 琥珀色高亮, 排名 4~6 暖灰
//   - 柱条右侧标签显示数值 + 单位
//   - hover tooltip 显示完整楼信息 (编码/用途/面积)
//
// 跟随顶栏 metric 切换自动重排 (computed 依赖 context.metric)
// ============================================================================

import { computed } from 'vue'
import { useContextStore, type Metric } from '@/stores/context'
import { useAnalysisData } from '@/composables/useAnalysisData'
import ChartCard from './ChartCard.vue'
import {
  ECHARTS_COLORS,
  shortBuildingName,
  parseBuildingKind,
  formatNumber,
} from '@/styles/echarts-theme'
import type { EChartsOption } from 'echarts'

const context = useContextStore()
const { buildings, loading, error, reload } = useAnalysisData()

// context.metric (eui / total_kwh / anomaly_count / cost) -> 后端 buildings 数组字段名
const METRIC_FIELD: Record<Metric, 'total_kwh' | 'eui_kwh_per_m2' | 'anomaly_count'> = {
  total_kwh: 'total_kwh',
  eui: 'eui_kwh_per_m2',
  anomaly_count: 'anomaly_count',
  // cost 字段后端没单独返, 复用 total_kwh 排序 (能耗高费用高, 排名一致)
  cost: 'total_kwh',
}

const METRIC_META: Record<Metric, { label: string; unit: string; desc: string }> = {
  total_kwh: { label: '总能耗', unit: 'kWh', desc: '统计区间内累计用电量' },
  eui: { label: 'EUI', unit: 'kWh/m²', desc: '单位面积年能耗, 越低越节能' },
  anomaly_count: { label: '异常数', unit: '条', desc: '异常事件总数' },
  cost: { label: '估算费用', unit: '元', desc: '按电价 0.8 元/kWh 估算' },
}

// 排好序的建筑数组 (降序, 第 0 个是最高)
const ranked = computed(() => {
  if (!buildings.value) return []
  const field = METRIC_FIELD[context.metric]
  return [...buildings.value.buildings]
    .map(b => ({
      ...b,
      value: b[field] ?? 0,
      shortName: shortBuildingName(b.building_code),
      kind: parseBuildingKind(b.building_code),
    }))
    .sort((a, b) => b.value - a.value)
})

const subtitle = computed(() => {
  const meta = METRIC_META[context.metric]
  const count = ranked.value.length
  return `按${meta.label}降序 · 共 ${count} 栋 · ${meta.unit}`
})

const chartOption = computed<EChartsOption | null>(() => {
  if (ranked.value.length === 0) return null

  const meta = METRIC_META[context.metric]
  const list = ranked.value
  // ECharts 横向柱图 yAxis category 是从下往上排的, 我们要最高的在最上
  // 所以反转数组, 让 list[0] (最高) 显示在最后一行 (即最上面)
  const reversed = [...list].reverse()
  const total = reversed.length

  return {
    grid: { left: 12, right: 80, top: 12, bottom: 12, containLabel: true },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: any) => {
        // reversed 数组里 params[0].dataIndex 对应反转后的索引
        const idx = params[0]?.dataIndex ?? 0
        const b = reversed[idx]
        if (!b) return ''
        return [
          `<b>${b.display_name}</b>`,
          `建筑编码: ${b.building_code}`,
          `主要用途: ${b.primary_use}`,
          `建筑面积: ${b.sqm ? b.sqm + ' m²' : '-'}`,
          `排名: 第 ${total - idx} 名 / 共 ${total} 栋`,
          `${meta.label}: <b style="color:${ECHARTS_COLORS.amber}">${formatNumber(b.value)}</b> ${meta.unit}`,
        ].join('<br/>')
      },
    },
    xAxis: {
      type: 'value',
      axisLabel: {
        formatter: (v: number) => formatNumber(v, 0),
      },
      splitLine: {
        lineStyle: { color: ECHARTS_COLORS.split, type: 'dashed' },
      },
    },
    yAxis: {
      type: 'category',
      data: reversed.map(b => b.shortName),
      axisLine: { show: false },
      axisTick: { show: false },
      axisLabel: {
        fontFamily: "'Noto Sans SC', sans-serif",
        fontSize: 12,
        color: ECHARTS_COLORS.concrete,
        // 长建筑名截断防挤柱区, hover tooltip 显示全名
        width: 60,
        overflow: 'truncate',
        ellipsis: '...',
      },
    },
    series: [
      {
        type: 'bar',
        data: reversed.map((b, i) => {
          // 反转后: i=0 是最低 (排名最后), i=total-1 是最高 (排名第 1)
          // 排名前 3 琥珀色, 后 3 暖灰
          const rank = total - i  // i=total-1 -> rank 1, i=0 -> rank total
          const isTop3 = rank <= 3
          const color = isTop3 ? ECHARTS_COLORS.amber : ECHARTS_COLORS.concrete
          // 前 3 名用渐变 (顶部深琥珀到底部浅琥珀), 后 3 名纯暖灰
          const itemStyle = isTop3
            ? {
                color: {
                  type: 'linear' as const,
                  x: 0, y: 0, x2: 1, y2: 0,
                  colorStops: [
                    { offset: 0, color: ECHARTS_COLORS.amberDeep },
                    { offset: 1, color: ECHARTS_COLORS.amber },
                  ],
                },
                borderRadius: [0, 4, 4, 0] as [number, number, number, number],
              }
            : {
                color: ECHARTS_COLORS.concrete,
                borderRadius: [0, 4, 4, 0] as [number, number, number, number],
              }
          return {
            value: b.value,
            itemStyle,
            // 柱条右侧标数值 + 单位
            label: {
              show: true,
              position: 'right',
              distance: 8,
              formatter: () => `${formatNumber(b.value)} ${meta.unit}`,
              fontFamily: "'Fira Code', monospace",
              fontSize: 11,
              fontWeight: 500,
              color: isTop3 ? ECHARTS_COLORS.amberDeep : ECHARTS_COLORS.textSecondary,
            },
          }
        }),
        barWidth: '58%',
        animationDuration: 900,
        animationEasing: 'cubicOut',
      },
    ],
  }
})

const isEmpty = computed(() => !buildings.value || buildings.value.buildings.length === 0)
</script>

<template>
  <ChartCard
    title="建筑能耗排名"
    :subtitle="subtitle"
    :loading="loading"
    :error="error"
    :empty="isEmpty"
    :option="chartOption"
    @retry="reload"
  />
</template>
