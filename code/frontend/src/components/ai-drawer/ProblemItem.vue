<script setup lang="ts">
// ============================================================================
// ProblemItem - 节能优化方案的单条问题卡片
// ----------------------------------------------------------------------------
// 显示: 编号 / 问题描述 / 证据依据 / 严重度徽章
// 严重度: high (红) / medium (橙) / low (绿)
// ============================================================================

import { computed } from 'vue'
import type { OptimizationPlan } from '@/api/assistant'

type Problem = OptimizationPlan['problems'][number]

const props = defineProps<{
  problem: Problem
  index: number
}>()

const severityLabel = computed(() => {
  const m: Record<string, string> = { high: '高', medium: '中', low: '低' }
  return m[props.problem.severity] || props.problem.severity
})
</script>

<template>
  <div class="problem" :class="`problem--${problem.severity}`">
    <div class="problem__header">
      <span class="problem__num">{{ index }}</span>
      <span class="problem__desc">{{ problem.problem_desc }}</span>
      <span class="problem__sev">{{ severityLabel }}</span>
    </div>
    <div v-if="problem.evidence_ref" class="problem__evidence">
      {{ problem.evidence_ref }}
    </div>
  </div>
</template>

<style scoped lang="scss">
.problem {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-sm;
  padding: $space-2 $space-3;
  margin-bottom: $space-1;
  border-left: 3px solid $color-text-secondary;

  &--high {
    border-left-color: $color-red;
  }

  &--medium {
    border-left-color: $color-amber;
  }

  &--low {
    border-left-color: $color-green;
  }

  &__header {
    display: flex;
    align-items: center;
    gap: $space-2;
  }

  &__num {
    display: grid;
    place-items: center;
    width: 20px;
    height: 20px;
    background: $gray-100;
    color: $color-text-secondary;
    border-radius: 50%;
    font-size: $fs-xs;
    font-weight: $fw-semibold;
    flex-shrink: 0;
  }

  &__desc {
    flex: 1;
    color: $color-concrete;
    font-weight: $fw-medium;
    font-size: $fs-sm;
  }

  &__sev {
    padding: 1px 8px;
    border-radius: $radius-pill;
    font-size: $fs-xs;
    font-weight: $fw-semibold;
    color: $color-card;
    flex-shrink: 0;
  }

  &--high &__sev { background: $color-red; }
  &--medium &__sev { background: $color-amber; }
  &--low &__sev { background: $color-green; }

  &__evidence {
    margin-top: $space-1;
    margin-left: 28px;
    font-size: $fs-xs;
    color: $color-text-secondary;
    line-height: 1.5;
    font-style: italic;
  }
}
</style>
