<script setup lang="ts">
// ============================================================================
// FloorHourlyHeatmap - 24h × 7d 楼层能耗热力图
// ----------------------------------------------------------------------------
// 数据源: floorApi.getFloorTimeseries (granularity=hour, 拉全周/全月数据)
// 视觉: 热力图, X 轴 24 小时 (0-23), Y 轴 7 天 (周一-周日)
//   - 颜色: 浅纸张 (#F5F2EB) -> 琥珀 (#D49B3B) -> 深琥珀 (#8B5A1F)
//   - 单元格悬停显示 该小时均值 + 时间
//
// 视觉对齐细节:
//   - X 轴小时标签 mono, 整点显示
//   - Y 轴星期标签居中
//   - tooltip 表格形式, 时间 + 平均能耗 + 单位对齐
//   - visualMap 横向放在底部, 标签 mono
// ============================================================================

import { computed, ref, watch } from 'vue'
import dayjs from 'dayjs'
import type { EChartsOption } from 'echarts'
import ChartCard from '@/components/analysis/ChartCard.vue'
import { floorApi, type FloorTimeseriesResponse } from '@/api/floor'
import { ECHARTS_COLORS } from '@/styles/echarts-theme'

const props = defineProps<{
  buildingId: string | null
  floorId: string | null
  start: string
  end: string
}>()

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
        granularity: 'hour',
        start: props.start,
        end: props.end,
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
  () => [props.buildingId, props.floorId, props.start, props.end],
  () => loadData(),
  { immediate: true },
)

// 把时序数据聚合成 24h × 7d 矩阵 (按 hour-of-day × day-of-week 求平均)
const heatmapData = computed(() => {
  if (!data.value || data.value.series.length === 0) return { matrix: [], max: 0, totalPoints: 0 }

  // 7 行 (周一-周日) × 24 列 (0-23 时), 累加每种能源的值
  const sum: number[][] = Array.from({ length: 7 }, () => Array(24).fill(0))
  const cnt: number[][] = Array.from({ length: 7 }, () => Array(24).fill(0))

  let totalPoints = 0
  for (const s of data.value.series) {
    for (const p of s.points) {
      const d = dayjs(p.ts)
      const dow = (d.day() + 6) % 7  // day() 0=Sunday, 转成 0=Monday
      const hour = d.hour()
      sum[dow][hour] += p.value
      cnt[dow][hour] += 1
      totalPoints += 1
    }
  }

  const matrix: Array<[number, number, number]> = []
  let max = 0
  for (let dow = 0; dow < 7; dow++) {
    for (let h = 0; h < 24; h++) {
      const avg = cnt[dow][h] > 0 ? sum[dow][h] / cnt[dow][h] : 0
      matrix.push([h, dow, Number(avg.toFixed(3))])
      if (avg > max) max = avg
    }
  }
  return { matrix, max, totalPoints }
})

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

const chartOption = computed<EChartsOption | null>(() => {
  if (!data.value || heatmapData.value.matrix.length === 0) return null
  const { matrix, max } = heatmapData.value

  return {
    grid: { left: 8, right: 8, top: 8, bottom: 56, containLabel: true },
    tooltip: {
      position: 'top',
      formatter: (p: unknown) => {
        const param = p as { value: [number, number, number] }
        const [h, dow, v] = param.value
        return `
          <div style="font-weight:600;color:${ECHARTS_COLORS.concrete};margin-bottom:4px">${WEEKDAYS[dow]} ${h.toString().padStart(2, '0')}:00</div>
          <div style="display:flex;align-items:center;gap:8px;font-family:'Fira Code',monospace;font-size:11px">
            <span style="flex:1;color:${ECHARTS_COLORS.textSecondary}">平均能耗</span>
            <span style="font-weight:600;color:${ECHARTS_COLORS.amberDeep};min-width:64px;text-align:right">${v.toFixed(2)}</span>
            <span style="color:${ECHARTS_COLORS.textTertiary};font-size:10px">kWh</span>
          </div>
        `
      },
    },
    xAxis: {
      type: 'category',
      data: Array.from({ length: 24 }, (_, i) => i.toString().padStart(2, '0')),
      splitArea: { show: false },
      axisLine: { lineStyle: { color: ECHARTS_COLORS.border } },
      axisTick: { show: false },
      axisLabel: {
        color: ECHARTS_COLORS.textSecondary,
        fontSize: 10,
        fontFamily: "'Fira Code', monospace",
        interval: 2,  // 每 3 小时显示一个标签
      },
    },
    yAxis: {
      type: 'category',
      data: WEEKDAYS,
      splitArea: { show: false },
      axisLine: { lineStyle: { color: ECHARTS_COLORS.border } },
      axisTick: { show: false },
      axisLabel: {
        color: ECHARTS_COLORS.textSecondary,
        fontSize: 11,
      },
    },
    visualMap: {
      min: 0,
      max: max > 0 ? max : 1,
      calculable: true,
      orient: 'horizontal',
      left: 'center',
      bottom: 4,
      itemWidth: 12,
      itemHeight: 120,
      textStyle: {
        color: ECHARTS_COLORS.textSecondary,
        fontSize: 10,
        fontFamily: "'Fira Code', monospace",
      },
      inRange: {
        color: ['#F5F2EB', '#F5E6C8', '#D49B3B', '#8B5A1F'],
      },
    },
    series: [
      {
        type: 'heatmap',
        data: matrix,
        label: { show: false },
        emphasis: {
          itemStyle: {
            shadowBlur: 8,
            shadowColor: 'rgba(212, 155, 59, 0.4)',
          },
        },
        itemStyle: {
          borderColor: ECHARTS_COLORS.card,
          borderWidth: 1,
        },
      },
    ],
  }
})

const subtitle = computed(() => {
  if (!data.value) return ''
  const { totalPoints, max } = heatmapData.value
  const range = `${dayjs(props.start).format('YYYY-MM-DD')} ~ ${dayjs(props.end).format('YYYY-MM-DD')}`
  return `${range} · 折叠为典型周 (每格 = 该星期几该小时的均值) · 共 ${totalPoints} 点 · 峰值 ${max.toFixed(2)} kWh`
})
</script>

<template>
  <ChartCard
    title="24小时 × 星期 热力图"
    :subtitle="subtitle"
    :loading="loading"
    :error="error"
    :empty="!chartOption"
    :option="chartOption"
    :height="280"
    @retry="loadData"
  />
</template>
