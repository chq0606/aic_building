<script setup lang="ts">
// ============================================================================
// EnergyBadge - 建筑上方能源构成小卡
// ----------------------------------------------------------------------------
// 在建筑屋顶上方悬浮一个小型水平堆叠条 (80px 宽), 显示各能源类型占比
// (电 / 热水 / 冷冻水 / 燃气 / 水 / 太阳能 6 段)
//
// 用 HTML overlay 而非 Cesium Billboard:
//   Billboard 是 2D 图贴在 3D 空间, 文字会模糊。
//   HTML overlay 用 Cesium.SceneTransforms.wgs84ToDrawingBufferCoordinates
//   每帧算屏幕坐标, 跟随相机移动, 文字始终清晰。
//
// 能源类型颜色 (跟设计系统对齐):
//   electricity     琥珀 #D49B3B
//   hotwater        赤陶 #B84A3C
//   chilledwater    岩蓝灰 #5A6B7C
//   gas             暖灰 #4A4A4A
//   water           翠绿 #3D7E6A
//   solar           翠绿浅 #7CB34A
// ============================================================================

import { inject, ref, onMounted, onBeforeUnmount, computed, watch, type Ref } from 'vue'
import * as Cesium from 'cesium'
import type { SceneBuilding } from '@/api/visual'
import { resolveBuildingPosition, type GridPosition } from '@/utils/sceneTransform'

const props = defineProps<{
  building: SceneBuilding
}>()

const viewerRef = inject<Ref<Cesium.Viewer | null>>('cesiumViewer', ref(null))
const localToEcef = inject<(x: number, y: number, z: number) => Cesium.Cartesian3>('localToEcef', () => new Cesium.Cartesian3())
const gridLayoutRef = inject<Ref<Map<string, GridPosition>>>('gridLayout', ref(new Map()))

const overlayRef = ref<HTMLDivElement | null>(null)
let renderListener: (() => void) | null = null

// 能源类型 -> 颜色 / 中文名
const ENERGY_META: Record<string, { color: string; label: string }> = {
  electricity: { color: '#D49B3B', label: '电' },
  hotwater: { color: '#B84A3C', label: '热水' },
  chilledwater: { color: '#5A6B7C', label: '冷冻水' },
  gas: { color: '#4A4A4A', label: '燃气' },
  water: { color: '#3D7E6A', label: '水' },
  solar: { color: '#7CB34A', label: '太阳能' },
}

const composition = computed(() => props.building.energy_composition?.composition ?? [])
const position = computed<GridPosition>(() => {
  return resolveBuildingPosition(props.building, gridLayoutRef.value)
})
const badgeHeight = computed(() => (props.building.dimensions.height_m ?? 20) + 4)

// 拿能源类型的中文名 + 颜色
function getMeta(type: string) {
  const key = type.toLowerCase()
  return ENERGY_META[key] ?? { color: '#A8A294', label: type }
}

// 每帧更新 overlay 屏幕位置
function updateScreenPosition() {
  const viewer = viewerRef.value
  if (!viewer || !overlayRef.value) return
  const pos = position.value
  const ecef = localToEcef(pos.x, pos.y, badgeHeight.value)
  // Cesium 1.122+ 重命名: wgs84ToDrawingBufferCoordinates -> worldToDrawingBufferCoordinates
  const screen = Cesium.SceneTransforms.worldToDrawingBufferCoordinates(viewer.scene, ecef)
  if (!screen) {
    overlayRef.value.style.display = 'none'
    return
  }
  // 检查是否在视锥内 (screen 为 undefined 说明在相机背后)
  overlayRef.value.style.display = 'block'
  // 居中: overlay 宽 80px, 高 24px, 减一半
  overlayRef.value.style.left = `${screen.x - 40}px`
  overlayRef.value.style.top = `${screen.y - 12}px`
}

function attachRenderListener() {
  const viewer = viewerRef.value
  if (!viewer) return
  // 每帧渲染后更新位置 (POST_RENDER 事件)
  renderListener = () => updateScreenPosition()
  viewer.scene.postRender.addEventListener(renderListener as any)
  updateScreenPosition()
}

onMounted(() => {
  // Vue 3 子先父后: viewerRef 此刻可能仍 null, watch 等就绪再挂监听
  if (viewerRef.value) {
    attachRenderListener()
  } else {
    const stop = watch(viewerRef, (v) => {
      if (v) {
        attachRenderListener()
        stop()
      }
    })
  }
})

onBeforeUnmount(() => {
  const viewer = viewerRef.value
  if (viewer && renderListener) {
    try {
      if (!viewer.isDestroyed?.()) {
        viewer.scene.postRender.removeEventListener(renderListener as any)
      }
    } catch (e) {
      // viewer 已销毁, 静默忽略
    }
    renderListener = null
  }
})

// 没 composition 不显示
const hasComposition = computed(() => composition.value.length > 0 && composition.value.some(c => c.kwh > 0))
</script>

<template>
  <div v-if="hasComposition" ref="overlayRef" class="energy-badge">
    <div class="energy-badge__bar">
      <div
        v-for="(item, idx) in composition"
        :key="idx"
        class="energy-badge__seg"
        :style="{
          width: `${item.pct * 100}%`,
          background: getMeta(item.type).color,
        }"
        :title="`${getMeta(item.type).label}: ${(item.pct * 100).toFixed(1)}%`"
      />
    </div>
    <div class="energy-badge__label">{{ building.building_code.split('_').pop() }}</div>
  </div>
</template>

<style scoped lang="scss">
.energy-badge {
  position: absolute;
  pointer-events: none;
  width: 80px;
  z-index: $z-popover;
  text-align: center;

  &__bar {
    display: flex;
    height: 6px;
    border-radius: $radius-pill;
    overflow: hidden;
    box-shadow: 0 1px 3px rgba(74, 74, 74, 0.2);
    border: 1px solid rgba(255, 255, 255, 0.5);
  }

  &__seg {
    height: 100%;
    transition: width $transition-base;
  }

  &__label {
    font-family: $font-mono;
    font-size: 10px;
    color: $color-stone;
    margin-top: 2px;
    text-shadow: 0 0 3px rgba(245, 242, 235, 0.9), 0 0 6px rgba(245, 242, 235, 0.7);
    font-weight: $fw-medium;
  }
}
</style>
