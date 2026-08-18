<script setup lang="ts">
// ============================================================================
// FloorEuiRanking - 楼层 EUI 排名横向柱状图
// ----------------------------------------------------------------------------
// 数据源: floorStore.floors (楼层列表里的 eui_kwh_per_m2 字段)
// 视觉: 横向柱状, Y 轴楼层 (按 EUI 降序, 最高在上), X 轴 EUI (kWh/m²)
//   - 排名第 1 (最高 EUI) 琥珀色高亮
//   - 当前选中层加琥珀边框 + 数值标签
//   - 国标 EUI 限值参考线 (虚线)
//
// 视觉对齐细节:
//   - Y 轴楼层标签 mono (3F / 2F / 1F), 右对齐
//   - X 轴 EUI 标签 mono
//   - 柱条右侧数值标签 mono, 跟坐标轴对齐
//   - tooltip 显示楼层名 + EUI + 总能耗 + 面积
// ============================================================================

import { computed } from 'vue'
import type { EChartsOption } from 'echarts'
import ChartCard from '@/components/analysis/ChartCard.vue'
import { ECHARTS_COLORS, getGbLimit } from '@/styles/echarts-theme'
import { useFloorStore } from '@/stores/floor'
import { parseBuildingKind } from '@/styles/echarts-theme'
import { useContextStore } from '@/stores/context'

const floorStore = useFloorStore()
const context = useContextStore()

const ranked = computed(() => {
  return [...floorStore.floors]
    .filter(f => f.eui_kwh_per_m2 !== null)
    .sort((a, b) => (b.eui_kwh_per_m2 ?? 0) - (a.eui_kwh_per_m2 ?? 0))
})

// GB 55015 国标限值 (按楼栋 kind 查表, 这里默认 education 65)
// FloorView 没拉 building_code, 用 default 即可
const gbLimit = computed(() => {
  // 没法从 floorStore 拿 building_code, 用默认值
  return getGbLimit('default')
})

const chartOption = computed<EChartsOption | null>(() => {
  if (ranked.value.length === 0) return null

  // ECharts 横向柱图 yAxis category 从下往上排, 最高 EUI 要在最上 -> 反转
  const reversed = [...ranked.value].reverse()
  const yData = reversed.map(f => `${f.floor_number}F`)

  return {
    grid: { left: 8, right: 60, top: 16, bottom: 8, containLabel: true },
    legend: { show: false },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(212, 155, 59, 0.08)' } },
      formatter: (params: unknown) => {
        const arr = params as Array<{ dataIndex: number; value: number }>
        const idx = arr[0].dataIndex
        const f = reversed[idx]
        const eui = f.eui_kwh_per_m2 ?? 0
        const overLimit = eui > gbLimit.value
        const limitTag = overLimit
          ? `<span style="color:${ECHARTS_COLORS.red};font-size:10px">超国标 ${(eui - gbLimit.value).toFixed(1)}</span>`
          : `<span style="color:${ECHARTS_COLORS.green};font-size:10px">符合国标</span>`
        return `
          <div style="font-weight:600;color:${ECHARTS_COLORS.concrete};margin-bottom:4px">${f.floor_number}F · ${f.floor_name}</div>
          <div style="display:flex;align-items:center;gap:8px;font-family:'Fira Code',monospace;font-size:11px;line-height:1.6">
            <span style="flex:1;color:${ECHARTS_COLORS.textSecondary}">EUI</span>
            <span style="font-weight:600;color:${ECHARTS_COLORS.amberDeep};min-width:64px;text-align:right">${eui.toFixed(1)}</span>
            <span style="color:${ECHARTS_COLORS.textTertiary};font-size:10px">kWh/m²</span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;font-family:'Fira Code',monospace;font-size:11px;line-height:1.6">
            <span style="flex:1;color:${ECHARTS_COLORS.textSecondary}">总能耗</span>
            <span style="font-weight:600;color:${ECHARTS_COLORS.concrete};min-width:64px;text-align:right">${(f.total_kwh / 1000).toFixed(1)}k</span>
            <span style="color:${ECHARTS_COLORS.textTertiary};font-size:10px">kWh</span>
          </div>
          <div style="display:flex;align-items:center;gap:8px;font-family:'Fira Code',monospace;font-size:11px;line-height:1.6">
            <span style="flex:1;color:${ECHARTS_COLORS.textSecondary}">面积</span>
            <span style="font-weight:600;color:${ECHARTS_COLORS.concrete};min-width:64px;text-align:right">${(f.area_sqm ?? 0).toFixed(0)}</span>
            <span style="color:${ECHARTS_COLORS.textTertiary};font-size:10px">m²</span>
          </div>
          <div style="margin-top:4px">${limitTag}</div>
        `
      },
    },
    xAxis: {
      type: 'value',
      name: 'kWh/m²',
      nameTextStyle: { color: ECHARTS_COLORS.textTertiary, fontSize: 10, padding: [0, 0, 0, -24] },
      axisLabel: {
        color: ECHARTS_COLORS.textSecondary,
        fontSize: 10,
        fontFamily: "'Fira Code', monospace",
        hideOverlap: true,
      },
      splitLine: { lineStyle: { color: ECHARTS_COLORS.split, type: 'dashed' } },
    },
    yAxis: {
      type: 'category',
      data: yData,
      axisLine: { lineStyle: { color: ECHARTS_COLORS.border } },
      axisTick: { show: false },
      axisLabel: {
        color: ECHARTS_COLORS.textSecondary,
        fontSize: 11,
        fontFamily: "'Fira Code', monospace",
        fontWeight: 500,
      },
    },
    series: [
      {
        type: 'bar',
        data: reversed.map(f => {
          const eui = f.eui_kwh_per_m2 ?? 0
          const isSelected = f.id === floorStore.currentFloorId
          // EUI 最高 (ranked[0]) 用琥珀, 选中层用深琥珀, 其他用 stone
          const isHighest = f.id === ranked.value[0]?.id
          let color: string = ECHARTS_COLORS.stone
          if (isHighest) color = ECHARTS_COLORS.amber
          if (isSelected) color = ECHARTS_COLORS.amberDeep
          return {
            value: Number(eui.toFixed(2)),
            itemStyle: {
              color,
              borderRadius: [0, 4, 4, 0],
              borderColor: isSelected ? ECHARTS_COLORS.amber : 'transparent',
              borderWidth: isSelected ? 2 : 0,
            },
          }
        }),
        barMaxWidth: 22,
        label: {
          show: true,
          position: 'right',
          formatter: (p: unknown) => {
            const param = p as { value: number }
            return param.value.toFixed(1)
          },
          color: ECHARTS_COLORS.concrete,
          fontSize: 11,
          fontFamily: "'Fira Code', monospace",
          fontWeight: 600,
        },
      },
      // 国标限值参考线 (用 markLine)
      {
        type: 'bar',
        data: [],
        markLine: {
          silent: true,
          symbol: 'none',
          lineStyle: { color: ECHARTS_COLORS.red, type: 'dashed', width: 1.2 },
          label: {
            show: true,
            position: 'insideEndTop',
            formatter: `国标 ${gbLimit.value}`,
            color: ECHARTS_COLORS.red,
            fontSize: 10,
            fontFamily: "'Fira Code', monospace",
          },
          data: [{ xAxis: gbLimit.value }],
        },
      },
    ],
  }
})

const subtitle = computed(() => {
  if (ranked.value.length === 0) return ''
  const max = ranked.value[0]?.eui_kwh_per_m2 ?? 0
  const min = ranked.value[ranked.value.length - 1]?.eui_kwh_per_m2 ?? 0
  return `按 EUI 降序 · 最高 ${max.toFixed(1)} · 最低 ${min.toFixed(1)} kWh/m²`
})
</script>

<template>
  <ChartCard
    title="楼层 EUI 排名"
    :subtitle="subtitle"
    :empty="!chartOption"
    :option="chartOption"
    :height="300"
  />
</template>
