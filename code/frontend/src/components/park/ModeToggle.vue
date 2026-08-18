<script setup lang="ts">
// ============================================================================
// ModeToggle - 园区渲染模式切换 (虚拟外观 / 能效着色)
// ----------------------------------------------------------------------------
// 浮在 Cesium 画布左上角的 segmented 控件, 切 park.renderMode:
//   realistic: 程序化建筑外观 + 道路绿化背景, 建筑不涂能耗色 (数据进详情面板)
//   energy:    建筑按能效着色 + 异常 halo + 能耗 badge (园区级总览)
//
// 为什么做成独立浮层而不是塞进顶栏: 模式是 Park 页专属的"看什么"选择,
// 不是全局上下文 (顶栏的站点/时间/指标才是全局), 放画布内更贴近使用场景。
// 位置放左上角避开顶部居中的园区信息条和右上角的图例。
// ============================================================================

import { computed } from 'vue'
import { Building2, Activity } from 'lucide-vue-next'
import { useParkStore, type ParkRenderMode } from '@/stores/park'

const park = useParkStore()
const mode = computed(() => park.renderMode)

const options: Array<{ value: ParkRenderMode; label: string; icon: typeof Building2 }> = [
  { value: 'realistic', label: '虚拟外观', icon: Building2 },
  { value: 'energy', label: '能效着色', icon: Activity },
]

function setMode(v: ParkRenderMode) {
  park.setRenderMode(v)
}
</script>

<template>
  <div class="mode-toggle" role="tablist" aria-label="园区渲染模式">
    <button
      v-for="o in options"
      :key="o.value"
      class="mode-toggle__btn"
      :class="{ 'mode-toggle__btn--active': mode === o.value }"
      role="tab"
      :aria-selected="mode === o.value"
      @click="setMode(o.value)"
    >
      <component :is="o.icon" :size="14" />
      <span>{{ o.label }}</span>
    </button>
  </div>
</template>

<style scoped lang="scss">
.mode-toggle {
  position: absolute;
  left: $space-4;
  top: $space-4;
  z-index: $z-popover;
  display: flex;
  gap: 2px;
  padding: 2px;
  background: rgba(245, 242, 235, 0.92);
  backdrop-filter: blur(10px);
  border: 1px solid rgba(74, 74, 74, 0.12);
  border-radius: $radius-sm;
  box-shadow: $shadow-md;
  user-select: none;

  &__btn {
    display: flex;
    align-items: center;
    gap: $space-1;
    padding: $space-1 $space-3;
    border: none;
    border-radius: $radius-xs;
    background: transparent;
    color: $color-stone;
    font-size: $fs-sm;
    font-weight: $fw-medium;
    cursor: pointer;
    transition: all $transition-base;
    white-space: nowrap;

    svg {
      transition: color $transition-base;
    }

    &:hover {
      color: $color-concrete;
    }

    &--active {
      background: $color-card;
      color: $color-amber-deep;
      box-shadow: $shadow-xs;

      svg {
        color: $color-amber;
      }
    }
  }
}
</style>
