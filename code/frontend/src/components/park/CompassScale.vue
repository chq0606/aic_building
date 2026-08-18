<script setup lang="ts">
// ============================================================================
// CompassScale - 左下角罗盘 + 比例尺
// ----------------------------------------------------------------------------
// 两个 HTML overlay, 不挂在 Cesium 内:
//   1. 罗盘: 圆形 + 指北针 N, 跟随相机 heading 旋转 (反方向)
//   2. 比例尺: 当前相机距离换算成米/像素 -> 显示 100m / 50m / 10m 段
//
// 为什么用 HTML overlay 而非 Cesium 实体:
//   - HTML 文字始终清晰, 不随相机距离模糊
//   - 罗盘指针用 CSS transform 旋转, 性能比 Cesium Entity 更新属性快
//   - 不参与 picking, 不挡建筑 hover
//
// 跟随相机更新: 每帧 postRender 拿 viewer.camera.heading + 当前视口
// 米/像素换算用 scene.camera.positionCartographic 不行 (本地坐标无经纬度)
// 这里用 camera.position 到 sceneOrigin 的笛卡尔距离 + 视口 height 估算
// ============================================================================

import { inject, ref, onMounted, onBeforeUnmount, watch, type Ref } from 'vue'
import * as Cesium from 'cesium'
import { Navigation2 } from 'lucide-vue-next'

const viewerRef = inject<Ref<Cesium.Viewer | null>>('cesiumViewer', ref(null))
const sceneOrigin = inject<Cesium.Cartesian3>('sceneOrigin', Cesium.Cartesian3.ZERO)

const compassRotation = ref(0)
const scaleText = ref('100 m')
const scaleWidthPx = ref(80)

let renderListener: (() => void) | null = null

function updateOverlay() {
  const viewer = viewerRef.value
  if (!viewer) return

  // 罗盘旋转: camera heading 是相机朝向 (弧度), 罗盘要反向旋转才指向"正北"
  // heading=0 时相机朝北, 罗盘指北针应朝上 (rotation=0)
  // heading=π/2 时相机朝东, 罗盘指北针应朝左 (rotation=-π/2)
  const heading = viewer.camera.heading
  compassRotation.value = -heading * (180 / Math.PI)

  // 比例尺: 算米/像素
  // 算法: 取视口中心 + 右侧两个像素点, 投影到本地坐标, 算实际距离
  const canvas = viewer.canvas
  const cw = canvas.clientWidth
  const ch = canvas.clientHeight

  // 取屏幕中心 + 中心右偏 100px 的两个点
  const center = new Cesium.Cartesian2(cw / 2, ch / 2)
  const right = new Cesium.Cartesian2(cw / 2 + 100, ch / 2)

  // 拾取世界坐标 (本地笛卡尔系下)
  const centerWorld = viewer.scene.pickPosition(center)
  const rightWorld = viewer.scene.pickPosition(right)

  if (centerWorld && rightWorld) {
    // 两点笛卡尔距离 -> 米
    const dist = Cesium.Cartesian3.distance(centerWorld, rightWorld)
    // 100 像素对应 dist 米, 找最近的标准刻度 (1, 2, 5, 10, 20, 50, 100, 200, 500)
    const targetM = dist
    const scale = pickScale(targetM)
    scaleText.value = formatScale(scale)
    // scale 米对应的像素数: scale / targetM * 100
    scaleWidthPx.value = Math.max(40, Math.min(180, (scale / targetM) * 100))
  }
}

function pickScale(targetM: number): number {
  // 100 像素覆盖 targetM 米, 找一个接近 80px 的刻度
  const candidates = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000]
  // 期望 scale / targetM * 100 ≈ 80, 即 scale ≈ 0.8 * targetM
  const ideal = 0.8 * targetM
  let best = candidates[0]
  let bestDiff = Math.abs(candidates[0] - ideal)
  for (const c of candidates) {
    const d = Math.abs(c - ideal)
    if (d < bestDiff) {
      best = c
      bestDiff = d
    }
  }
  return best
}

function formatScale(m: number): string {
  if (m >= 1000) return `${m / 1000} km`
  return `${m} m`
}

function attachRenderListener() {
  const viewer = viewerRef.value
  if (!viewer) return
  renderListener = () => updateOverlay()
  viewer.scene.postRender.addEventListener(renderListener as any)
  updateOverlay()
}

onMounted(() => {
  // Vue 3 子先父后: watch viewerRef, 就绪后挂 postRender 监听
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
  // viewer 可能已被 destroy (CesiumViewer onBeforeUnmount 先跑), 这时
  // viewer.isDestroyed() = true, 访问 viewer.scene 会抛错。try/catch 兜底。
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

// viewer 就绪标志 (控制模板 v-if, 替代之前 setInterval 轮询)
const ready = ref(false)
if (viewerRef.value) {
  ready.value = true
} else {
  const stop = watch(viewerRef, (v) => {
    if (v) {
      ready.value = true
      stop()
    }
  })
}
</script>

<template>
  <div v-if="ready" class="compass-scale">
    <!-- 罗盘 -->
    <div class="compass-scale__compass">
      <div class="compass-scale__dial" :style="{ transform: `rotate(${compassRotation}deg)` }">
        <div class="compass-scale__needle compass-scale__needle--n">N</div>
        <div class="compass-scale__needle compass-scale__needle--s">S</div>
        <div class="compass-scale__needle compass-scale__needle--e">E</div>
        <div class="compass-scale__needle compass-scale__needle--w">W</div>
        <div class="compass-scale__center" />
      </div>
      <div class="compass-scale__ring" />
      <Navigation2 :size="14" class="compass-scale__icon" />
    </div>

    <!-- 比例尺 -->
    <div class="compass-scale__scale">
      <div class="compass-scale__scale-bar" :style="{ width: scaleWidthPx + 'px' }">
        <div class="compass-scale__scale-fill" />
        <div class="compass-scale__scale-tick compass-scale__scale-tick--left" />
        <div class="compass-scale__scale-tick compass-scale__scale-tick--right" />
      </div>
      <div class="compass-scale__scale-text">{{ scaleText }}</div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.compass-scale {
  position: absolute;
  left: $space-4;
  bottom: $space-4;
  z-index: $z-popover;
  display: flex;
  flex-direction: column;
  gap: $space-3;
  pointer-events: none;
  user-select: none;

  // 罗盘
  &__compass {
    position: relative;
    width: 64px;
    height: 64px;
    border-radius: 50%;
    background: rgba(245, 242, 235, 0.85);
    backdrop-filter: blur(8px);
    border: 1px solid rgba(74, 74, 74, 0.15);
    box-shadow: $shadow-md;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  &__dial {
    position: absolute;
    inset: 4px;
    border-radius: 50%;
    transition: transform 100ms linear;
  }

  &__needle {
    position: absolute;
    left: 50%;
    top: 50%;
    font-size: 10px;
    font-family: $font-mono;
    font-weight: $fw-bold;
    color: $color-text-secondary;
    transform-origin: 0 0;
    transform: translate(-50%, -50%);

    &--n {
      top: 4px;
      transform: translate(-50%, 0);
      color: $color-red;
    }
    &--s {
      top: auto;
      bottom: 4px;
      transform: translate(-50%, 0);
    }
    &--e {
      left: auto;
      right: 4px;
      top: 50%;
      transform: translate(0, -50%);
    }
    &--w {
      left: 4px;
      top: 50%;
      transform: translate(0, -50%);
    }
  }

  &__center {
    position: absolute;
    left: 50%;
    top: 50%;
    width: 4px;
    height: 4px;
    border-radius: 50%;
    background: $color-amber;
    transform: translate(-50%, -50%);
    box-shadow: 0 0 4px rgba(212, 155, 59, 0.6);
  }

  &__ring {
    position: absolute;
    inset: 8px;
    border-radius: 50%;
    border: 1px dashed rgba(74, 74, 74, 0.2);
  }

  &__icon {
    position: absolute;
    right: -4px;
    bottom: -4px;
    color: $color-text-secondary;
    background: $color-card;
    border-radius: $radius-pill;
    padding: 2px;
    box-shadow: $shadow-sm;
  }

  // 比例尺
  &__scale {
    background: rgba(245, 242, 235, 0.85);
    backdrop-filter: blur(8px);
    border: 1px solid rgba(74, 74, 74, 0.15);
    border-radius: $radius-md;
    padding: $space-1 $space-2;
    box-shadow: $shadow-sm;
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: 2px;
  }

  &__scale-bar {
    position: relative;
    height: 8px;
    display: flex;
    align-items: center;
  }

  &__scale-fill {
    height: 2px;
    width: 100%;
    background: $color-concrete;
    position: relative;
    &::before, &::after {
      content: '';
      position: absolute;
      top: -3px;
      width: 2px;
      height: 8px;
      background: $color-concrete;
    }
    &::before { left: 0; }
    &::after  { right: 0; }
  }

  &__scale-tick {
    position: absolute;
    width: 1px;
    height: 6px;
    background: $color-concrete;
    top: 1px;
    &--left  { left: 0; }
    &--right { right: 0; }
  }

  &__scale-text {
    font-family: $font-mono;
    font-size: $fs-xs;
    color: $color-concrete;
    font-weight: $fw-medium;
  }
}
</style>
