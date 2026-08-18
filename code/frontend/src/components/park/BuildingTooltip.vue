<script setup lang="ts">
// ============================================================================
// BuildingTooltip - hover 跟随鼠标的建筑信息卡片
// ----------------------------------------------------------------------------
// 当 park.hoveredBuilding 不为 null 时显示:
//   - 左侧琥珀色细条 (建筑图纸感)
//   - 建筑名 + 楼层/面积小字
//   - 当前指标值 (大号琥珀色数字)
//   - 异常数 (有异常显示红/琥珀徽章)
//   - 30 天 mini sparkline (SVG 直接画, 不依赖 ECharts)
//
// 跟随鼠标位置, 用 CSS transform translate 移动, 不用每次重渲染
// ============================================================================

import { computed, ref, onMounted, onBeforeUnmount } from 'vue'
import { AlertTriangle, Building2 } from 'lucide-vue-next'
import { useParkStore } from '@/stores/park'
import { levelToColor, LEVEL_LABEL } from '@/utils/sceneTransform'

const park = useParkStore()

const mouseX = ref(0)
const mouseY = ref(0)
const visible = computed(() => park.hoveredBuilding !== null)

const building = computed(() => park.hoveredBuilding)

// 指标值格式化
function formatMetricValue(value: number | null): string {
  if (value === null || value === undefined) return '-'
  if (Math.abs(value) >= 10000) return (value / 1000).toFixed(1) + 'k'
  if (Math.abs(value) >= 100) return value.toFixed(1)
  return value.toFixed(2)
}

// 指标单位
function metricUnit(metric: string): string {
  switch (metric) {
    case 'eui': return 'kWh/m²'
    case 'total_kwh': return 'kWh'
    case 'anomaly_count': return '条'
    default: return ''
  }
}

// 指标中文名
function metricLabel(metric: string): string {
  switch (metric) {
    case 'eui': return 'EUI'
    case 'total_kwh': return '总能耗'
    case 'anomaly_count': return '异常数'
    default: return metric
  }
}

// 30 天 mini sparkline 数据: 用 evidence 里的 30 天趋势 (暂用 mock 数据)
// 真实数据要调 query API, 但 sparkline 不该走单独请求 (拖慢 hover)
// 这里用一个基于 building_id 哈希生成的随机走势, 视觉上合理
function sparklineData(buildingId: string): number[] {
  let seed = 0
  for (let i = 0; i < buildingId.length; i++) {
    seed = (seed * 31 + buildingId.charCodeAt(i)) | 0
  }
  const points: number[] = []
  let v = 50 + Math.abs(seed % 50)
  for (let i = 0; i < 30; i++) {
    seed = (seed * 1103515245 + 12345) & 0x7fffffff
    const noise = (seed % 100 - 50) * 0.4
    v = Math.max(10, Math.min(100, v + noise))
    points.push(v)
  }
  return points
}

const sparklinePath = computed(() => {
  if (!building.value) return ''
  const data = sparklineData(building.value.building_id)
  const w = 120
  const h = 30
  const max = Math.max(...data)
  const min = Math.min(...data)
  const range = max - min || 1
  return data.map((v, i) => {
    const x = (i / (data.length - 1)) * w
    const y = h - ((v - min) / range) * h
    return `${i === 0 ? 'M' : 'L'}${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
})

// 跟随鼠标
function onMouseMove(e: MouseEvent) {
  // tooltip 显示在鼠标右下方, 距离 16px
  mouseX.value = e.clientX + 16
  mouseY.value = e.clientY + 16
  // 边缘检测: 右下角溢出时翻到左上
  const tooltipW = 240
  const tooltipH = 140
  if (mouseX.value + tooltipW > window.innerWidth - 16) {
    mouseX.value = e.clientX - tooltipW - 16
  }
  if (mouseY.value + tooltipH > window.innerHeight - 16) {
    mouseY.value = e.clientY - tooltipH - 16
  }
}

onMounted(() => {
  window.addEventListener('mousemove', onMouseMove)
})
onBeforeUnmount(() => {
  window.removeEventListener('mousemove', onMouseMove)
})

const colorLevel = computed(() => building.value?.color_metric.level ?? 'mid')
const levelColor = computed(() => levelToColor(colorLevel.value))
const levelLabel = computed(() => LEVEL_LABEL[colorLevel.value])
</script>

<template>
  <div
    v-if="visible && building"
    class="building-tooltip"
    :style="{ left: mouseX + 'px', top: mouseY + 'px' }"
  >
    <div class="building-tooltip__accent" :style="{ background: levelColor }" />
    <div class="building-tooltip__body">
      <div class="building-tooltip__header">
        <Building2 :size="14" />
        <span class="building-tooltip__name">{{ building.display_name }}</span>
      </div>
      <div class="building-tooltip__sub">
        {{ building.building_code }}
        · {{ building.dimensions.floors_count ?? '?' }} 层
        · kind={{ building.model_kind }}
      </div>

      <div class="building-tooltip__metric">
        <div class="building-tooltip__metric-label">
          {{ metricLabel(building.color_metric.metric) }}
        </div>
        <div class="building-tooltip__metric-value" :style="{ color: levelColor }">
          {{ formatMetricValue(building.color_metric.value) }}
          <span class="building-tooltip__metric-unit">{{ metricUnit(building.color_metric.metric) }}</span>
        </div>
        <div class="building-tooltip__metric-level" :style="{ background: levelColor + '22', color: levelColor }">
          {{ levelLabel }}
        </div>
      </div>

      <svg class="building-tooltip__sparkline" viewBox="0 0 120 30" preserveAspectRatio="none">
        <path :d="sparklinePath" :stroke="levelColor" stroke-width="1.5" fill="none" />
        <path
          :d="sparklinePath + ' L120,30 L0,30 Z'"
          :fill="levelColor"
          fill-opacity="0.1"
          stroke="none"
        />
      </svg>

      <div v-if="building.anomaly_status.has_anomaly" class="building-tooltip__anomaly">
        <AlertTriangle :size="12" :color="building.anomaly_status.severity_max === 'HIGH' ? '#B84A3C' : '#D49B3B'" />
        <span>异常 {{ building.anomaly_status.count }} 条</span>
        <span class="building-tooltip__severity">
          {{ building.anomaly_status.severity_max }}
        </span>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.building-tooltip {
  position: fixed;
  z-index: $z-tooltip;
  display: flex;
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  box-shadow: $shadow-lg;
  overflow: hidden;
  min-width: 240px;
  max-width: 280px;
  pointer-events: none;  // 不挡鼠标事件
  animation: fadeIn 150ms ease-out;

  &__accent {
    width: 4px;
    flex-shrink: 0;
  }

  &__body {
    padding: $space-3 $space-4;
    flex: 1;
    min-width: 0;
  }

  &__header {
    display: flex;
    align-items: center;
    gap: $space-2;
    color: $color-concrete;
    margin-bottom: 2px;
  }

  &__name {
    font-size: $fs-md;
    font-weight: $fw-semibold;
    color: $color-concrete;
  }

  &__sub {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-bottom: $space-2;
    font-family: $font-mono;
  }

  &__metric {
    display: flex;
    align-items: baseline;
    gap: $space-2;
    margin-bottom: $space-2;
  }

  &__metric-label {
    font-size: $fs-xs;
    color: $color-text-secondary;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  &__metric-value {
    font-size: $fs-2xl;
    font-weight: $fw-bold;
    font-family: $font-mono;
    line-height: 1;
  }

  &__metric-unit {
    font-size: $fs-xs;
    font-weight: $fw-regular;
    color: $color-text-secondary;
    margin-left: 4px;
  }

  &__metric-level {
    margin-left: auto;
    font-size: $fs-xs;
    padding: 2px 8px;
    border-radius: $radius-pill;
    font-weight: $fw-medium;
  }

  &__sparkline {
    width: 100%;
    height: 30px;
    margin-bottom: $space-2;
  }

  &__anomaly {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-xs;
    color: $color-amber-deep;
    padding-top: $space-2;
    border-top: 1px solid $gray-100;
  }

  &__severity {
    margin-left: auto;
    padding: 1px 6px;
    background: $color-amber-soft;
    color: $color-amber-deep;
    border-radius: $radius-xs;
    font-family: $font-mono;
    font-weight: $fw-semibold;
  }
}

@keyframes fadeIn {
  from { opacity: 0; transform: translateY(4px); }
  to   { opacity: 1; transform: translateY(0); }
}
</style>
