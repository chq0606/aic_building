<script setup lang="ts">
// ============================================================================
// FloorEnergyComposition - 楼层能源构成饼图 (玫瑰图)
// ----------------------------------------------------------------------------
// 数据源: floorApi.getFloorComposition (overall + by_floor)
// 视觉: 饼图 (玫瑰图模式, 半径按数值变化), 每片一种能源
// extra slot: 全楼 / 单层 切换
//
// 视觉对齐细节:
//   - 饼图居中, legend 右侧竖排, 跟图表垂直对齐
//   - tooltip 显示能源 + kWh + pct, 数值右对齐 mono
//   - 中心显示总能耗 (大字 mono + 单位小字)
//   - 颜色跟 ENERGY_META 一致 (跟时序图/对比图同色)
// ============================================================================

import { computed, ref, watch } from 'vue'
import type { EChartsOption } from 'echarts'
import ChartCard from '@/components/analysis/ChartCard.vue'
import { floorApi, type FloorCompositionResponse } from '@/api/floor'
import { getEnergyMeta } from './energy-meta'
import { ECHARTS_COLORS } from '@/styles/echarts-theme'

const props = defineProps<{
  buildingId: string | null
  floorId: string | null  // 当前选中楼层 (单层模式用)
  start: string
  end: string
}>()

// 全楼 vs 单层切换
const mode = ref<'building' | 'floor'>('floor')  // 默认单层 (跟左侧选中层联动)

const data = ref<FloorCompositionResponse | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function loadData() {
  if (!props.buildingId) return
  loading.value = true
  error.value = null
  try {
    data.value = await floorApi.getFloorComposition(props.buildingId, {
      start: props.start,
      end: props.end,
      floor_id: mode.value === 'floor' ? (props.floorId ?? undefined) : undefined,
    })
  } catch (e) {
    error.value = (e as Error).message
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.buildingId, props.floorId, props.start, props.end, mode.value],
  () => loadData(),
  { immediate: true },
)

function formatKwh(v: number): string {
  if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(2) + 'M'
  if (Math.abs(v) >= 1_000)     return (v / 1_000).toFixed(1) + 'k'
  return v.toFixed(1)
}

const totalKwh = computed(() =>
  data.value?.overall.reduce((s, c) => s + c.total_kwh, 0) ?? 0,
)

const chartOption = computed<EChartsOption | null>(() => {
  if (!data.value || data.value.overall.length === 0) return null

  const series = data.value.overall.map(c => ({
    name: getEnergyMeta(c.energy_type).label,
    value: Number(c.total_kwh.toFixed(3)),
    itemStyle: { color: getEnergyMeta(c.energy_type).color },
  }))

  return {
    grid: { left: 0, right: 0, top: 0, bottom: 0, containLabel: false },
    legend: {
      orient: 'vertical',
      right: 8,
      top: 'center',
      icon: 'roundRect',
      itemWidth: 10,
      itemHeight: 10,
      itemGap: 10,
      textStyle: { fontSize: 11, color: ECHARTS_COLORS.textSecondary },
      formatter: (name: string) => {
        const item = data.value?.overall.find(c => getEnergyMeta(c.energy_type).label === name)
        if (!item) return name
        return `${name}  ${item.pct.toFixed(1)}%`
      },
    },
    tooltip: {
      trigger: 'item',
      formatter: (p: unknown) => {
        const param = p as { name: string; value: number; percent: number; color: string }
        return `
          <div style="display:flex;align-items:center;gap:8px;font-family:'Fira Code',monospace;font-size:11px;line-height:1.6">
            <span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${param.color}"></span>
            <span style="flex:1;color:${ECHARTS_COLORS.textSecondary}">${param.name}</span>
            <span style="font-weight:600;color:${ECHARTS_COLORS.concrete};min-width:64px;text-align:right">${formatKwh(param.value)}</span>
            <span style="color:${ECHARTS_COLORS.textTertiary};font-size:10px">kWh</span>
          </div>
          <div style="text-align:right;font-size:10px;color:${ECHARTS_COLORS.textTertiary};margin-top:2px">占比 ${param.percent.toFixed(2)}%</div>
        `
      },
    },
    series: [
      {
        type: 'pie',
        radius: ['38%', '68%'],
        center: ['28%', '50%'],
        avoidLabelOverlap: true,
        label: { show: false },
        labelLine: { show: false },
        data: series,
        emphasis: {
          scale: true,
          scaleSize: 8,
          itemStyle: { shadowBlur: 16, shadowColor: 'rgba(212, 155, 59, 0.3)' },
        },
      },
    ],
    graphic: [
      {
        type: 'text',
        left: '28%',
        top: '46%',
        style: {
          text: formatKwh(totalKwh.value),
          textAlign: 'center',
          fill: ECHARTS_COLORS.concrete,
          fontSize: 18,
          fontWeight: 700,
          fontFamily: "'Fira Code', monospace",
        },
        z: 10,
      },
      {
        type: 'text',
        left: '28%',
        top: '60%',
        style: {
          text: 'kWh',
          textAlign: 'center',
          fill: ECHARTS_COLORS.textTertiary,
          fontSize: 10,
        },
        z: 10,
      },
    ],
  }
})

const subtitle = computed(() => {
  if (!data.value) return ''
  return mode.value === 'floor' ? '当前楼层 · 各能源占比' : '全楼汇总 · 各能源占比'
})
</script>

<template>
  <ChartCard
    title="能源构成"
    :subtitle="subtitle"
    :loading="loading"
    :error="error"
    :empty="!chartOption"
    :option="chartOption"
    :height="300"
    @retry="loadData"
  >
    <template #extra>
      <div class="seg-group">
        <button
          class="seg-btn"
          :class="{ 'seg-btn--active': mode === 'floor' }"
          @click="mode = 'floor'"
        >当前层</button>
        <button
          class="seg-btn"
          :class="{ 'seg-btn--active': mode === 'building' }"
          @click="mode = 'building'"
        >全楼</button>
      </div>
    </template>
  </ChartCard>
</template>

<style scoped lang="scss">
.seg-group {
  display: inline-flex;
  padding: 2px;
  background: $gray-100;
  border-radius: $radius-sm;
}

.seg-btn {
  padding: 3px 10px;
  border: none;
  background: transparent;
  color: $color-stone;
  font-size: $fs-xs;
  font-weight: $fw-medium;
  border-radius: $radius-xs;
  cursor: pointer;
  transition: all $transition-fast;
  font-family: inherit;

  &:hover:not(.seg-btn--active) {
    color: $color-concrete;
  }

  &--active {
    background: $color-card;
    color: $color-amber-deep;
    box-shadow: $shadow-xs;
  }
}
</style>
