<script setup lang="ts">
// ============================================================================
// MetricLegend - 右上角指标色阶图例
// ----------------------------------------------------------------------------
// 显示当前指标 + 4 档色阶 (low/mid/high/critical) + 阈值范围
// 切指标时文字标签 + 阈值数字跟着变
//
// 信息密度:
//   - 指标名 + 单位 (大字)
//   - 4 档色块横排, 每档下标小字写数值范围 (e.g. "0-50")
//   - 底部小字写当前指标说明 (e.g. "EUI 越低越节能")
//
// 阈值范围 (跟后端 visual_model_service.py 的 compute_level 对齐):
//   EUI:        low < 80, mid 80-150, high 150-250, critical > 250
//   total_kwh:  low < 5000, mid 5000-15000, high 15000-30000, critical > 30000
//   anomaly_count: low 0, mid 1-3, high 4-10, critical > 10
// ============================================================================

import { computed } from 'vue'
import { Activity, Gauge, AlertTriangle } from 'lucide-vue-next'
import { useParkStore } from '@/stores/park'
import { LEVEL_COLOR, LEVEL_LABEL } from '@/utils/sceneTransform'
import type { SceneMetric } from '@/api/visual'

const park = useParkStore()

interface LevelRange {
  level: 'low' | 'mid' | 'high' | 'critical'
  range: string
}

const METRIC_META: Record<SceneMetric, {
  label: string
  unit: string
  desc: string
  ranges: LevelRange[]
}> = {
  eui: {
    label: 'EUI',
    unit: 'kWh/m²',
    desc: '单位面积年能耗, 越低越节能',
    ranges: [
      { level: 'low',      range: '< 80' },
      { level: 'mid',      range: '80 – 150' },
      { level: 'high',     range: '150 – 250' },
      { level: 'critical', range: '> 250' },
    ],
  },
  total_kwh: {
    label: '总能耗',
    unit: 'kWh',
    desc: '统计区间内累计用电量',
    ranges: [
      { level: 'low',      range: '< 5k' },
      { level: 'mid',      range: '5k – 15k' },
      { level: 'high',     range: '15k – 30k' },
      { level: 'critical', range: '> 30k' },
    ],
  },
  anomaly_count: {
    label: '异常数',
    unit: '条',
    desc: '异常事件总数, 越多越需关注',
    ranges: [
      { level: 'low',      range: '0' },
      { level: 'mid',      range: '1 – 3' },
      { level: 'high',     range: '4 – 10' },
      { level: 'critical', range: '> 10' },
    ],
  },
}

const metric = computed<SceneMetric>(() => park.currentMetric)
const meta = computed(() => METRIC_META[metric.value])
const icon = computed(() => {
  if (metric.value === 'eui') return Gauge
  if (metric.value === 'total_kwh') return Activity
  return AlertTriangle
})

// 当前时间范围
const timeRange = computed(() => {
  const r = park.scene?.time_range
  if (!r) return ''
  const fmt = (s: string) => s.slice(0, 10)
  return `${fmt(r.start)} ~ ${fmt(r.end)}`
})
</script>

<template>
  <div class="metric-legend">
    <div class="metric-legend__header">
      <div class="metric-legend__title">
        <component :is="icon" :size="14" />
        <span>{{ meta.label }}</span>
      </div>
      <div class="metric-legend__unit">{{ meta.unit }}</div>
    </div>

    <div class="metric-legend__levels">
      <div
        v-for="r in meta.ranges"
        :key="r.level"
        class="metric-legend__level"
      >
        <div class="metric-legend__color" :style="{ background: LEVEL_COLOR[r.level] }" />
        <div class="metric-legend__label">{{ LEVEL_LABEL[r.level] }}</div>
        <div class="metric-legend__range">{{ r.range }}</div>
      </div>
    </div>

    <div class="metric-legend__desc">{{ meta.desc }}</div>

    <div v-if="timeRange" class="metric-legend__time">
      <span class="metric-legend__time-label">统计区间</span>
      <span class="metric-legend__time-value">{{ timeRange }}</span>
    </div>
  </div>
</template>

<style scoped lang="scss">
.metric-legend {
  position: absolute;
  right: $space-4;
  top: $space-4;
  z-index: $z-popover;
  width: 260px;
  background: rgba(245, 242, 235, 0.92);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(74, 74, 74, 0.12);
  border-radius: $radius-md;
  box-shadow: $shadow-md;
  padding: $space-3 $space-3 $space-2;
  user-select: none;

  &__header {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    padding-bottom: $space-2;
    margin-bottom: $space-2;
    border-bottom: 1px solid rgba(74, 74, 74, 0.1);
  }

  &__title {
    display: flex;
    align-items: center;
    gap: $space-1;
    color: $color-concrete;
    font-size: $fs-md;
    font-weight: $fw-semibold;
  }

  &__unit {
    font-family: $font-mono;
    font-size: $fs-xs;
    color: $color-text-secondary;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  &__levels {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: $space-1;
    margin-bottom: $space-2;
  }

  &__level {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
    text-align: center;
  }

  &__color {
    width: 100%;
    height: 14px;
    border-radius: $radius-xs;
    box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.3);
    transition: transform $transition-base;
  }

  &__level:hover &__color {
    transform: scaleY(1.2);
  }

  &__label {
    font-size: $fs-xs;
    color: $color-concrete;
    font-weight: $fw-medium;
  }

  &__range {
    font-family: $font-mono;
    font-size: 10px;
    color: $color-text-secondary;
  }

  &__desc {
    font-size: $fs-xs;
    color: $color-text-secondary;
    line-height: 1.5;
    padding-top: $space-1;
    border-top: 1px solid rgba(74, 74, 74, 0.06);
  }

  &__time {
    display: flex;
    justify-content: space-between;
    align-items: center;
    margin-top: $space-1;
    padding-top: $space-1;
    border-top: 1px dashed rgba(74, 74, 74, 0.1);
  }

  &__time-label {
    font-size: $fs-xs;
    color: $color-text-secondary;
  }

  &__time-value {
    font-family: $font-mono;
    font-size: $fs-xs;
    color: $color-concrete;
    font-weight: $fw-medium;
  }
}
</style>
