<script setup lang="ts">
// ============================================================================
// FloorEnergyTrend - 楼层能耗时序折线图
// ----------------------------------------------------------------------------
// 数据源: floorApi.getFloorTimeseries (hour/day/month 粒度, 按能源分组)
// 视觉: 多条折线 (每种能源一条), X 轴时间, Y 轴 kWh
// extra slot: 粒度切换 (hour/day/month) + 能源类型筛选
//
// 视觉对齐细节:
//   - Y 轴标签右对齐, mono 字体, kWh 单位
//   - X 轴标签按粒度格式化 (hour=HH:00, day=MM-DD, month=YYYY-MM)
//   - tooltip 用表格形式, 能源名 + 数值 + 单位, 数值右对齐
//   - legend 顶部居中, 跟 ChartCard extra slot 对齐
//   - grid 左右留白一致, 跟其他图表对齐
// ============================================================================

import { computed, ref, watch } from 'vue'
import dayjs from 'dayjs'
import type { EChartsOption } from 'echarts'
import ChartCard from '@/components/analysis/ChartCard.vue'
import { floorApi, type Granularity, type FloorTimeseriesResponse } from '@/api/floor'
import { getEnergyMeta } from './energy-meta'
import { ECHARTS_COLORS } from '@/styles/echarts-theme'

const props = defineProps<{
  buildingId: string | null
  floorId: string | null
  start: string
  end: string
}>()

// ---- 粒度 + 能源筛选 ----
const granularity = ref<Granularity>('day')
const selectedEnergy = ref<string | null>(null)  // null = 全部能源

// ---- 数据 ----
const data = ref<FloorTimeseriesResponse | null>(null)
const loading = ref(false)
const error = ref<string | null>(null)

async function loadData() {
  if (!props.buildingId || !props.floorId) return
  loading.value = true
  error.value = null
  try {
    data.value = await floorApi.getFloorTimeseries(
      props.buildingId,
      props.floorId,
      {
        granularity: granularity.value,
        start: props.start,
        end: props.end,
        energy_type: selectedEnergy.value ?? undefined,
      },
    )
  } catch (e) {
    error.value = (e as Error).message
    data.value = null
  } finally {
    loading.value = false
  }
}

watch(
  () => [props.buildingId, props.floorId, props.start, props.end, granularity.value, selectedEnergy.value],
  () => loadData(),
  { immediate: true },
)

// ---- 可选能源列表 (从首次加载的 series 推, 切粒度/楼层时更新) ----
const availableEnergies = computed<string[]>(() => {
  if (!data.value) return []
  return data.value.series.map(s => s.energy_type)
})

// ---- X 轴时间格式化 ----
function formatTs(ts: string, g: Granularity): string {
  const d = dayjs(ts)
  if (g === 'hour')  return d.format('MM-DD HH:mm')
  if (g === 'day')   return d.format('MM-DD')
  return d.format('YYYY-MM')
}

function formatTsShort(ts: string, g: Granularity): string {
  const d = dayjs(ts)
  if (g === 'hour')  return d.format('HH:mm')
  if (g === 'day')   return d.format('MM-DD')
  return d.format('M月')
}

// ---- Y 轴数值格式化 (kWh) ----
function formatKwh(v: number): string {
  if (Math.abs(v) >= 1_000_000) return (v / 1_000_000).toFixed(2) + 'M'
  if (Math.abs(v) >= 1_000)     return (v / 1_000).toFixed(1) + 'k'
  return v.toFixed(1)
}

// ---- ECharts option ----
const chartOption = computed<EChartsOption | null>(() => {
  if (!data.value || data.value.series.length === 0) return null

  // X 轴标签: 取第一条 series 的 points 时间轴 (各 series 时间轴一致)
  const xData = data.value.series[0].points.map(p => formatTsShort(p.ts, granularity.value))

  // 多条折线, 每条一个能源
  const series = data.value.series.map(s => {
    const meta = getEnergyMeta(s.energy_type)
    return {
      name: meta.label,
      type: 'line' as const,
      data: s.points.map(p => Number(p.value.toFixed(3))),
      smooth: granularity.value !== 'hour',  // hour 粒度不平滑 (点太多)
      symbol: granularity.value === 'hour' ? 'none' : 'circle',
      symbolSize: 3,
      lineStyle: { width: 1.8, color: meta.color },
      itemStyle: { color: meta.color },
      emphasis: { focus: 'series' as const },
    }
  })

  return {
    color: data.value.series.map(s => getEnergyMeta(s.energy_type).color),
    grid: { left: 8, right: 16, top: 16, bottom: 8, containLabel: true },
    legend: {
      show: data.value.series.length > 1,
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
      axisPointer: { type: 'line', lineStyle: { color: ECHARTS_COLORS.border, type: 'dashed' } },
      formatter: (params: unknown) => {
        const arr = params as Array<{ axisValue: string; seriesName: string; data: number; color: string; dataIndex: number }>
        if (!arr.length) return ''
        // 找原始时间戳 (用第一个 series 的对应点)
        const idx = arr[0].dataIndex ?? 0
        const fullTs = data.value?.series[0].points[idx]?.ts ?? ''
        const header = `<div style="font-weight:600;margin-bottom:4px;color:${ECHARTS_COLORS.concrete}">${formatTs(fullTs, granularity.value)}</div>`
        // 按数值降序排
        const sorted = [...arr].sort((a, b) => b.data - a.data)
        const rows = sorted.map(p => `
          <div style="display:flex;align-items:center;gap:8px;font-family:'Fira Code',monospace;font-size:11px;line-height:1.6">
            <span style="display:inline-block;width:8px;height:8px;border-radius:2px;background:${p.color}"></span>
            <span style="flex:1;color:${ECHARTS_COLORS.textSecondary}">${p.seriesName}</span>
            <span style="font-weight:600;color:${ECHARTS_COLORS.concrete};min-width:64px;text-align:right">${formatKwh(p.data)}</span>
            <span style="color:${ECHARTS_COLORS.textTertiary};font-size:10px">kWh</span>
          </div>
        `).join('')
        return header + rows
      },
    },
    xAxis: {
      type: 'category',
      data: xData,
      boundaryGap: false,
      axisLine: { lineStyle: { color: ECHARTS_COLORS.border } },
      axisLabel: {
        color: ECHARTS_COLORS.textSecondary,
        fontSize: 10,
        fontFamily: "'Fira Code', monospace",
        hideOverlap: true,
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
  const points = data.value.series[0]?.points.length ?? 0
  return `${points} 个数据点 · ${granularity.value === 'hour' ? '小时' : granularity.value === 'day' ? '日' : '月'}粒度`
})
</script>

<template>
  <ChartCard
    title="能耗时序趋势"
    :subtitle="subtitle"
    :loading="loading"
    :error="error"
    :empty="!chartOption"
    :option="chartOption"
    :height="300"
    @retry="loadData"
  >
    <template #extra>
      <div class="trend-controls">
        <div class="seg-group">
          <button
            v-for="g in (['hour', 'day', 'month'] as Granularity[])"
            :key="g"
            class="seg-btn"
            :class="{ 'seg-btn--active': granularity === g }"
            @click="granularity = g"
          >
            {{ g === 'hour' ? '小时' : g === 'day' ? '日' : '月' }}
          </button>
        </div>
        <select
          v-if="availableEnergies.length > 1"
          v-model="selectedEnergy"
          class="energy-select"
        >
          <option :value="null">全部能源</option>
          <option v-for="et in availableEnergies" :key="et" :value="et">
            {{ getEnergyMeta(et).label }}
          </option>
        </select>
      </div>
    </template>
  </ChartCard>
</template>

<style scoped lang="scss">
.trend-controls {
  display: flex;
  align-items: center;
  gap: $space-2;
}

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
