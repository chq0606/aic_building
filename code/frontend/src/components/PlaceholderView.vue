<script setup lang="ts">
// ============================================================================
// PlaceholderView - 占位页通用组件
// ----------------------------------------------------------------------------
// Park/Analysis/DataHub/Settings 都用这个, 后续替换为真实组件
// ============================================================================

import type { Component } from 'vue'

defineProps<{
  icon: Component
  title: string
  description: string
  step?: string
  stats?: Array<{ label: string; value: string | number; sub?: string }>
}>()
</script>

<template>
  <div class="placeholder">
    <div class="placeholder__hero">
      <div class="placeholder__icon">
        <component :is="icon" :size="32" :stroke-width="1.5" />
      </div>
      <h1 class="placeholder__title">{{ title }}</h1>
      <p class="placeholder__desc">{{ description }}</p>
      <div v-if="step" class="placeholder__badge">
        {{ step }}
      </div>
    </div>

    <div v-if="stats && stats.length" class="placeholder__stats">
      <div v-for="stat in stats" :key="stat.label" class="stat-card">
        <div class="stat-card__label">{{ stat.label }}</div>
        <div class="stat-card__value">{{ stat.value }}</div>
        <div v-if="stat.sub" class="stat-card__sub">{{ stat.sub }}</div>
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.placeholder {
  display: flex;
  flex-direction: column;
  gap: $space-6;
  max-width: 1080px;
  margin: 0 auto;
}

.placeholder__hero {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-xl;
  padding: $space-8;
  text-align: center;
  box-shadow: $shadow-sm;
  position: relative;
  overflow: hidden;

  &::before {
    content: '';
    position: absolute;
    top: 0;
    left: 50%;
    transform: translateX(-50%);
    width: 120px;
    height: 3px;
    background: $color-amber;
  }
}

.placeholder__icon {
  width: 72px;
  height: 72px;
  margin: 0 auto $space-4;
  background: $color-amber-soft;
  color: $color-amber-deep;
  border-radius: $radius-lg;
  display: grid;
  place-items: center;
}

.placeholder__title {
  font-size: $fs-3xl;
  font-weight: $fw-bold;
  color: $color-concrete;
  margin-bottom: $space-2;
}

.placeholder__desc {
  font-size: $fs-md;
  color: $color-stone;
  line-height: $lh-relaxed;
  max-width: 540px;
  margin: 0 auto $space-5;
}

.placeholder__badge {
  display: inline-flex;
  align-items: center;
  gap: $space-1;
  padding: $space-1 $space-3;
  background: $color-amber-soft;
  color: $color-amber-deep;
  border-radius: $radius-pill;
  font-size: $fs-sm;
  font-weight: $fw-medium;
}

.placeholder__stats {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  gap: $space-4;
}

.stat-card {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-5;
  box-shadow: $shadow-xs;
  transition: box-shadow $transition-base;

  &:hover {
    box-shadow: $shadow-sm;
  }

  &__label {
    font-size: $fs-xs;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    color: $color-text-secondary;
    margin-bottom: $space-2;
  }

  &__value {
    font-size: $fs-2xl;
    font-weight: $fw-bold;
    color: $color-concrete;
    font-family: $font-mono;
    line-height: 1.1;
    margin-bottom: $space-1;
  }

  &__sub {
    font-size: $fs-xs;
    color: $color-text-secondary;
  }
}
</style>
