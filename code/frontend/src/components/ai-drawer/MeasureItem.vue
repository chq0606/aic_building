<script setup lang="ts">
// ============================================================================
// MeasureItem - 节能优化方案的单条措施卡片
// ----------------------------------------------------------------------------
// 显示: 编号 / 措施名 / 节能量 / CO2 减排 / 回收期 / 难度 / 国标条款引用
// 难度: easy (绿) / medium (橙) / hard (红)
// ============================================================================

import { computed } from 'vue'
import { Leaf, Clock, TrendingDown } from 'lucide-vue-next'
import type { OptimizationPlan } from '@/api/assistant'

type Measure = OptimizationPlan['measures'][number]

const props = defineProps<{
  measure: Measure
  index: number
}>()

const difficultyLabel = computed(() => {
  const m: Record<string, string> = { easy: '易', medium: '中', hard: '难' }
  return m[props.measure.difficulty] || props.measure.difficulty
})
</script>

<template>
  <div class="measure">
    <div class="measure__header">
      <span class="measure__num">{{ index }}</span>
      <span class="measure__name">{{ measure.measure_name }}</span>
      <span class="measure__diff" :class="`measure__diff--${measure.difficulty}`">
        {{ difficultyLabel }}
      </span>
    </div>

    <!-- 数字指标 -->
    <div class="measure__stats">
      <div class="mstat">
        <TrendingDown :size="12" />
        <span class="mstat__label">节能量</span>
        <span class="mstat__value">{{ measure.saved_kwh.toFixed(0) }}</span>
        <span class="mstat__unit">kWh</span>
      </div>
      <div class="mstat mstat--green">
        <Leaf :size="12" />
        <span class="mstat__label">CO₂</span>
        <span class="mstat__value">{{ measure.saved_co2.toFixed(0) }}</span>
        <span class="mstat__unit">kg</span>
      </div>
      <div class="mstat">
        <Clock :size="12" />
        <span class="mstat__label">回收期</span>
        <span class="mstat__value">{{ measure.payback_months.toFixed(1) }}</span>
        <span class="mstat__unit">月</span>
      </div>
    </div>

    <!-- 国标条款引用 -->
    <div v-if="measure.clause_ref" class="measure__clause">
      <span class="measure__clause-label">国标条款</span>
      <span class="measure__clause-text">{{ measure.clause_ref }}</span>
    </div>
  </div>
</template>

<style scoped lang="scss">
.measure {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-sm;
  padding: $space-2 $space-3;
  margin-bottom: $space-1;
  border-left: 3px solid $color-amber;

  &__header {
    display: flex;
    align-items: center;
    gap: $space-2;
    margin-bottom: $space-2;
  }

  &__num {
    display: grid;
    place-items: center;
    width: 20px;
    height: 20px;
    background: $color-amber-soft;
    color: $color-amber-deep;
    border-radius: 50%;
    font-size: $fs-xs;
    font-weight: $fw-semibold;
    flex-shrink: 0;
  }

  &__name {
    flex: 1;
    color: $color-concrete;
    font-weight: $fw-medium;
    font-size: $fs-sm;
  }

  &__diff {
    padding: 1px 8px;
    border-radius: $radius-pill;
    font-size: $fs-xs;
    font-weight: $fw-semibold;
    color: $color-card;
    flex-shrink: 0;

    &--easy { background: $color-green; }
    &--medium { background: $color-amber; }
    &--hard { background: $color-red; }
  }

  &__stats {
    display: flex;
    gap: $space-2;
    margin-bottom: $space-1;
    flex-wrap: wrap;
  }

  &__clause {
    display: flex;
    align-items: baseline;
    gap: $space-1;
    margin-top: $space-1;
    padding-top: $space-1;
    border-top: 1px dashed $gray-200;
    font-size: $fs-xs;
  }

  &__clause-label {
    color: $color-text-secondary;
    flex-shrink: 0;
  }

  &__clause-text {
    color: $color-amber-deep;
    font-weight: $fw-medium;
    font-family: $font-mono;
  }
}

.mstat {
  display: flex;
  align-items: baseline;
  gap: 4px;
  padding: 4px 8px;
  background: $gray-50;
  border-radius: $radius-sm;
  font-size: $fs-xs;

  svg {
    color: $color-text-secondary;
    align-self: center;
  }

  &--green svg {
    color: $color-green;
  }

  &__label {
    color: $color-text-secondary;
  }

  &__value {
    color: $color-concrete;
    font-weight: $fw-semibold;
    font-variant-numeric: tabular-nums;
  }

  &__unit {
    color: $color-text-secondary;
    font-size: 10px;
  }
}
</style>
