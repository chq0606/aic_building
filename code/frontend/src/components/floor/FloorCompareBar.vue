<script setup lang="ts">
// ============================================================================
// FloorCompareBar - 楼层能耗对比堆叠柱状图
// ----------------------------------------------------------------------------
// 数据源: floorApi.compareFloors (每层 × 每能源)
// 视觉: 堆叠柱状图, X 轴楼层 (1F/2F/...), Y 轴 kWh, 按能源堆叠
// extra slot: 能源类型筛选 (只看某能源时不堆叠, 单柱)
//
// 视觉对齐细节:
//   - X 轴楼层标签 mono, 居中对齐
//   - Y 轴 kWh 标签 mono 右对齐
//   - 当前选中层柱条高亮 (琥珀边框 + 阴影)
//   - tooltip 表格形式, 按数值降序, 能源名+数值+单位对齐
// ============================================================================

import { computed, ref, watch } from 'vue'
import type { EChartsOption } from 'echarts'
import ChartCard from '@/components/analysis/ChartCard.vue'
import { floorApi, type FloorCompareResponse } from '@/api/floor'
import { getEnergyMeta } from './energy-meta'
import { ECHARTS_COLORS } from '@/styles/echarts-theme'

const props = defineProps<{
  buildingId: string | null
  currentFloorId: string | null
  start: string
  end: string
}>()

const selectedEnergy = ref<string | null>(null)

const data = ref<FloorCompareResponse | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function loadData() {
  if (!props.buildingId) return
  loading.value = true
  error.value = null
  try {
    data.value = await floorApi.compareFloors(props.buildingId, {
      start: props.start,
      end: props.end,
      energy_type: selectedEnergy.value ?? undefined,
    })
  } catch (e) {
    error.value = (e as Error).message
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.buildingId, props.start, props.end, selectedEnergy.value],
  () => loadData(),
  { immediate: true },
)

function formatKwh(v: number): string {
  if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(2) + 'M'
  if (Math.abs(v) >= 1_000)     return (v / 1_000).toFixed(1) + 'k'
  return v.toFixed(1)
}

// 所有出现的能源类型 (按字母序, 跟后端 series 一致)
const allEnergies = computed<string[]>(() => {
  if (!data.value) return []
  const set = new Set<string>()
  for (const f of data.value.floors) {
    for (const eb of f.energy_breakdown) {
      set.add(eb.energy_type)
    }
  }
  return Array.from(set).sort()
})

const chartOption = computed<EChartsOption | null>(() => {
  if (!data.value || data.value.floors.length === 0) return null

  // floors 是顶层在前 (floor_number DESC), X 轴从左到右画 1F -> 顶层, 所以反转
  const floors = [...data.value.floors].reverse()
  const xData = floors.map(f => `${f.floor_number}F`)

  const energies = allEnergies.value

  // 堆叠柱: 每种能源一个 series
  const series = energies.map(et => {
    const meta = getEnergyMeta(et)
    return {
      name: meta.label,
      type: 'bar' as const,
      stack: 'total',
      data: floors.map(f => {
        const eb = f.energy_breakdown.find(b => b.energy_type === et)
        return eb ? Number(eb.total_kwh.toFixed(3)) : 0
      }),
      itemStyle: { color: meta.color },
      emphasis: { focus: 'series' as const },
      barMaxWidth: 40,
    }
  })

  return {
    color: energies.map(et => getEnergyMeta(et).color),
    grid: { left: 8, right: 16, top: 32, bottom: 8, containLabel: true },
    legend: {
      top: 0,
      right: 0,
      orient: 'horizontal',
      icon: 'roundRect',
      itemWidth: 12,
      itemHeight: 8,
      itemGap: 12,
      textStyle: { fontSize: 11, color: ECHARTS_COLORS.textSecondary },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow', shadowStyle: { color: 'rgba(212, 155, 59, 0.08)' } },
      formatter: (params: unknown) => {
        const arr = params as Array<{ axisValue: string; seriesName: string; data: number; color: string; dataIndex: number }>
        if (!arr.length) return ''
        const total = arr.reduce((s, p) => s + (p.data || 0), 0)
        const floorNum = arr[0].axisValue
        const sorted = [...arr].filter(p => p.data > 0).sort((a, b) => b.data - a.data)
        const rows = sorted.map(p => `
          <div style="display:flex;align-items:center;gap:8px;font-family:'Fira Code',monospace;font-size:11px;line-height:1.6">
            <span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${p.color}"></span>
            <span style="flex:1;color:${ECHARTS_COLORS.textSecondary}">${p.seriesName}</span>
            <span style="font-weight:600;color:${ECHARTS_COLORS.concrete};min-width:64px;text-align:right">${formatKwh(p.data)}</span>
            <span style="color:${ECHARTS_COLORS.textTertiary};font-size:10px">kWh</span>
          </div>
        `).join('')
        const totalRow = `
          <div style="display:flex;align-items:center;gap:8px;font-family:'Fira Code',monospace;font-size:11px;line-height:1.8;border-top:1px dashed ${ECHARTS_COLORS.border};margin-top:4px;padding-top:4px">
            <span style="flex:1;color:${ECHARTS_COLORS.concrete};font-weight:600">${floorNum} 合计</span>
            <span style="font-weight:700;color:${ECHARTS_COLORS.amberDeep};min-width:64px;text-align:right">${formatKwh(total)}</span>
            <span style="color:${ECHARTS_COLORS.textTertiary};font-size:10px">kWh</span>
          </div>
        `
        return rows + totalRow
      },
    },
    xAxis: {
      type: 'category',
      data: xData,
      axisLine: { lineStyle: { color: ECHARTS_COLORS.border } },
      axisTick: { lineStyle: { color: ECHARTS_COLORS.border } },
      axisLabel: {
        color: ECHARTS_COLORS.textSecondary,
        fontSize: 11,
        fontFamily: "'Fira Code', monospace",
        fontWeight: 500,
      },
    },
    yAxis: {
      type: 'value',
      name: 'kWh',
      nameTextStyle: { color: ECHARTS_COLORS.textTertiary, fontSize: 10, padding: [0, 0, 0, -24] },
      axisLabel: {
        color: ECHARTS_COLORS.textSecondary,
        fontSize: 10,
        fontFamily: "'Fira Code', monospace",
        formatter: (v: number) => formatKwh(v),
        hideOverlap: true,
      },
      splitLine: { lineStyle: { color: ECHARTS_COLORS.split, type: 'dashed' } },
    },
    series,
  }
})

const subtitle = computed(() => {
  if (!data.value) return ''
  const total = data.value.floors.reduce((s, f) => s + f.total_kwh, 0)
  return `${data.value.floors.length} 层对比 · 全楼合计 ${formatKwh(total)} kWh`
})
</script>

<template>
  <ChartCard
    title="楼层能耗对比"
    :subtitle="subtitle"
    :loading="loading"
    :error="error"
    :empty="!chartOption"
    :option="chartOption"
    :height="280"
    @retry="loadData"
  >
    <template #extra>
      <select
        v-if="allEnergies.length > 1"
        v-model="selectedEnergy"
        class="energy-select"
      >
        <option :value="null">全部能源 (堆叠)</option>
        <option v-for="et in allEnergies" :key="et" :value="et">
          {{ getEnergyMeta(et).label }}
        </option>
      </select>
    </template>
  </ChartCard>
</template>

<style scoped lang="scss">
.energy-select {
  padding: 3px 8px;
  border: 1px solid $gray-200;
  border-radius: $radius-sm;
  background: $color-card;
  color: $color-stone;
  font-size: $fs-xs;
  font-family: inherit;
  cursor: pointer;

  &:hover {
    border-color: $color-amber;
  }
}
</style>
