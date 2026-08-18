<script setup lang="ts">
// ============================================================================
// ViewPresets - 相机视角预设 (俯瞰 / 正面 / 环绕)
// ----------------------------------------------------------------------------
// 三个预设作用在当前焦点:
//   - 选中某栋楼: 聚焦该楼 (单楼包围球, 相机框住这栋楼)
//   - 未选中: 聚焦整个园区 (所有楼的整体包围球, 相机框住所有楼)
//
// 俯瞰: 垂直俯视 (pitch -90)
// 正面: 接近平视 (pitch -15), 能看到建筑立面
// 环绕: 相机绕焦点匀速转一圈 (8s), 点一下开始、再点一下停
//
// 相机操作参考 HouseViz3D 的"视角预设 + 引导漫游"思路, 用 Cesium 的
// flyToBoundingSphere (range=0 自动框住) 和 lookAt (环绕动画) 实现,
// 不引入 GSAP, 避免手算 heading/pitch/distance 转 ENU 出错。
// ============================================================================

import { inject, ref, onBeforeUnmount, type Ref } from 'vue'
import * as Cesium from 'cesium'
import { Map as MapIcon, Building2, RotateCw } from 'lucide-vue-next'
import { useParkStore } from '@/stores/park'
import { resolveBuildingPosition, computeVisualHeight, type GridPosition } from '@/utils/sceneTransform'

const park = useParkStore()
const viewerRef = inject<Ref<Cesium.Viewer | null>>('cesiumViewer', ref(null))
const localToEcef = inject<(x: number, y: number, z: number) => Cesium.Cartesian3>(
  'localToEcef',
  () => new Cesium.Cartesian3(),
)
const gridLayoutRef = inject<Ref<Map<string, GridPosition>>>('gridLayout', ref(new Map()))

const isOrbiting = ref(false)

// 当前焦点: 选中楼优先 -> 否则整个园区 (所有楼的整体包围球)
// 之前没选中时默认聚焦 buildings[0] (Franklin), 其他楼被忽略, 用户误以为按钮
// "只针对 Franklin"。改成无选中时聚焦整个园区, 让三个预设成为"园区全景"工具。
function getFocusTarget(): { center: Cesium.Cartesian3; radius: number } | null {
  const viewer = viewerRef.value
  if (!viewer) return null

  // 1. 选中楼: 单楼包围球
  const selected = park.selectedBuilding
  if (selected) {
    const pos = resolveBuildingPosition(selected, gridLayoutRef.value)
    const L = selected.dimensions.length_m ?? 30
    const W = selected.dimensions.width_m ?? 20
    const H = computeVisualHeight(selected)
    return {
      center: localToEcef(pos.x, pos.y, H * 0.45),
      radius: Math.hypot(L, W, H) / 2,
    }
  }

  // 2. 无选中: 园区整体包围球
  //    中心 = 所有楼位置平均, 半径 = 最远楼到中心距离 + 楼半径
  if (park.buildings.length === 0) return null
  const positions = park.buildings.map(b => resolveBuildingPosition(b, gridLayoutRef.value))
  const cx = positions.reduce((s, p) => s + p.x, 0) / positions.length
  const cy = positions.reduce((s, p) => s + p.y, 0) / positions.length
  let maxR = 0
  for (let i = 0; i < park.buildings.length; i++) {
    const b = park.buildings[i]
    const p = positions[i]
    const L = b.dimensions.length_m ?? 30
    const W = b.dimensions.width_m ?? 20
    const H = computeVisualHeight(b)
    const buildingR = Math.hypot(L, W, H) / 2
    const distToCenter = Math.hypot(p.x - cx, p.y - cy)
    maxR = Math.max(maxR, distToCenter + buildingR)
  }
  // 园区中心 z 取平均楼高的 0.3 倍 (相机略看向楼中部)
  const avgH = park.buildings.reduce((s, b) => s + computeVisualHeight(b), 0) / park.buildings.length
  return {
    center: localToEcef(cx, cy, avgH * 0.3),
    radius: maxR,
  }
}

function stopOrbit() {
  if (orbitRaf !== null) {
    cancelAnimationFrame(orbitRaf)
    orbitRaf = null
  }
  isOrbiting.value = false
}

function flyPreset(headingDeg: number, pitchDeg: number) {
  const viewer = viewerRef.value
  if (!viewer) return
  stopOrbit()
  const t = getFocusTarget()
  if (!t) return
  viewer.camera.flyToBoundingSphere(new Cesium.BoundingSphere(t.center, t.radius), {
    duration: 1.0,
    offset: new Cesium.HeadingPitchRange(
      Cesium.Math.toRadians(headingDeg),
      Cesium.Math.toRadians(pitchDeg),
      0,
    ),
  })
}

function topView() {
  flyPreset(0, -90)
}

function frontView() {
  flyPreset(0, -15)
}

// ---- 环绕动画 ----
let orbitRaf: number | null = null

function toggleOrbit() {
  if (isOrbiting.value) {
    stopOrbit()
    return
  }
  const viewer = viewerRef.value
  if (!viewer) return
  const t = getFocusTarget()
  if (!t) return

  // 捕获为非空常量, 嵌套的 tick (rAF 回调) 里 TypeScript 不会保留外层 narrowing
  const v: Cesium.Viewer = viewer
  const center: Cesium.Cartesian3 = t.center

  const DURATION = 8000
  const pitch = Cesium.Math.toRadians(-32)
  const range = Math.max(t.radius * 2.6, 60)
  const startHeading = viewer.camera.heading
  const startTime = performance.now()
  isOrbiting.value = true

  function tick(now: number) {
    const elapsed = now - startTime
    const t01 = Math.min(1, elapsed / DURATION)
    const heading = startHeading + t01 * Cesium.Math.TWO_PI
    v.camera.lookAt(center, new Cesium.HeadingPitchRange(heading, pitch, range))
    if (t01 < 1) {
      orbitRaf = requestAnimationFrame(tick)
    } else {
      orbitRaf = null
      isOrbiting.value = false
    }
  }
  orbitRaf = requestAnimationFrame(tick)
}

onBeforeUnmount(stopOrbit)
</script>

<template>
  <div class="view-presets" role="group" aria-label="相机视角">
    <button class="view-presets__btn" title="俯瞰" @click="topView">
      <MapIcon :size="14" />
      <span>俯瞰</span>
    </button>
    <button class="view-presets__btn" title="正面" @click="frontView">
      <Building2 :size="14" />
      <span>正面</span>
    </button>
    <button
      class="view-presets__btn"
      :class="{ 'view-presets__btn--active': isOrbiting }"
      title="环绕"
      @click="toggleOrbit"
    >
      <RotateCw :size="14" :class="{ 'view-presets__spin': isOrbiting }" />
      <span>{{ isOrbiting ? '停止' : '环绕' }}</span>
    </button>
  </div>
</template>

<style scoped lang="scss">
.view-presets {
  position: absolute;
  left: $space-4;
  bottom: $space-4;
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
    padding: $space-1 $space-2;
    border: none;
    border-radius: $radius-xs;
    background: transparent;
    color: $color-stone;
    font-size: $fs-xs;
    font-weight: $fw-medium;
    cursor: pointer;
    transition: all $transition-base;
    white-space: nowrap;

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

  &__spin {
    animation: view-presets-spin 1s linear infinite;
  }
}

@keyframes view-presets-spin {
  from { transform: rotate(0); }
  to   { transform: rotate(360deg); }
}
</style>
