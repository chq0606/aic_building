<script setup lang="ts">
// ============================================================================
// AnomalyLegend - 异常光晕图例
// ----------------------------------------------------------------------------
// 说明屋顶红色脉动圆盘的含义: 该建筑存在能耗异常点
// 显示严重度色阶 (LOW 翠绿 / MEDIUM 琥珀 / HIGH 赤陶) + 文字说明
// ============================================================================

import { AlertCircle } from 'lucide-vue-next'
import { useParkStore } from '@/stores/park'
import { SEVERITY_COLOR } from '@/utils/sceneTransform'

const park = useParkStore()

// 统计各严重度的建筑数
const severityCounts = (() => {
  const counts = { LOW: 0, MEDIUM: 0, HIGH: 0 }
  for (const b of park.buildings) {
    if (!b.anomaly_status.has_anomaly) continue
    const s = b.anomaly_status.severity_max
    if (s && s in counts) counts[s]++
  }
  return counts
})()
</script>

<template>
  <div v-if="park.buildings.some(b => b.anomaly_status.has_anomaly)" class="anomaly-legend">
    <div class="anomaly-legend__header">
      <AlertCircle :size="14" />
      <span>异常标记</span>
    </div>
    <div class="anomaly-legend__desc">屋顶脉动圆盘 = 该建筑存在能耗异常</div>
    <div class="anomaly-legend__rows">
      <div v-for="(sev, key) in { LOW: 'LOW', MEDIUM: 'MEDIUM', HIGH: 'HIGH' }" :key="key" class="anomaly-legend__row">
        <span class="anomaly-legend__dot" :style="{ background: SEVERITY_COLOR[sev as keyof typeof SEVERITY_COLOR] }" />
        <span class="anomaly-legend__label">{{ sev === 'LOW' ? '低' : sev === 'MEDIUM' ? '中' : '高' }}严重度</span>
        <span class="anomaly-legend__count">{{ severityCounts[sev as keyof typeof severityCounts] }} 栋</span>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.anomaly-legend {
  position: absolute;
  right: $space-4;
  bottom: $space-4;
  z-index: $z-popover;
  width: 200px;
  padding: $space-3 $space-4;
  background: rgba(245, 242, 235, 0.92);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(74, 74, 74, 0.15);
  border-radius: $radius-md;
  box-shadow: $shadow-md;
  pointer-events: none;
  user-select: none;

  &__header {
    display: flex;
    align-items: center;
    gap: $space-2;
    color: $color-red;
    font-size: $fs-sm;
    font-weight: $fw-semibold;
    margin-bottom: $space-1;
  }

  &__desc {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-bottom: $space-2;
    line-height: 1.4;
  }

  &__rows {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  &__row {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-xs;
  }

  &__dot {
    width: 10px;
    height: 10px;
    border-radius: 50%;
    flex-shrink: 0;
    box-shadow: 0 0 4px currentColor;
  }

  &__label {
    color: $color-concrete;
    flex: 1;
  }

  &__count {
    font-family: $font-mono;
    color: $color-text-secondary;
    font-weight: $fw-medium;
  }
}
</style>
