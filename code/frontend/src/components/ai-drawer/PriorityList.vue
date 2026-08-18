<script setup lang="ts">
// ============================================================================
// PriorityList - 节能优化方案的优先级建议 Top 3
// ----------------------------------------------------------------------------
// 显示按"节能率 ÷ 实施难度"排序的 top 3 措施推荐 + 推荐理由
// ============================================================================

import { Award } from 'lucide-vue-next'
import type { OptimizationPlan } from '@/api/assistant'

defineProps<{
  priorities: OptimizationPlan['priorities']
}>()
</script>

<template>
  <div class="priority-list">
    <div class="priority-list__header">
      <Award :size="12" />
      <span>优先级建议 Top {{ priorities.length }}</span>
    </div>
    <div class="priority-list__items">
      <div
        v-for="(p, i) in priorities"
        :key="i"
        class="priority-item"
      >
        <div class="priority-item__rank" :class="`priority-item__rank--${i + 1}`">
          {{ i + 1 }}
        </div>
        <div class="priority-item__body">
          <div class="priority-item__name">{{ p.measure_name }}</div>
          <div v-if="p.reason" class="priority-item__reason">{{ p.reason }}</div>
        </div>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.priority-list {
  margin-top: $space-3;

  &__header {
    display: flex;
    align-items: center;
    gap: $space-1;
    color: $color-amber-deep;
    font-size: $fs-sm;
    font-weight: $fw-semibold;
    margin-bottom: $space-2;

    svg { color: $color-amber; }
  }

  &__items {
    display: flex;
    flex-direction: column;
    gap: $space-1;
  }
}

.priority-item {
  display: flex;
  align-items: flex-start;
  gap: $space-2;
  padding: $space-2 $space-3;
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-sm;

  &__rank {
    width: 22px;
    height: 22px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    color: $color-card;
    font-weight: $fw-bold;
    font-size: $fs-xs;
    flex-shrink: 0;

    &--1 { background: $color-amber; }
    &--2 { background: #b8b8b8; }
    &--3 { background: #c89878; }
  }

  &__body {
    flex: 1;
  }

  &__name {
    color: $color-concrete;
    font-weight: $fw-medium;
    font-size: $fs-sm;
  }

  &__reason {
    color: $color-text-secondary;
    font-size: $fs-xs;
    line-height: 1.5;
    margin-top: 2px;
  }
}
</style>
