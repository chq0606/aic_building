<script setup lang="ts">
// ============================================================================
// AnomalyHalo - 异常建筑屋顶发光圆环
// ----------------------------------------------------------------------------
// 在异常建筑屋顶上方叠一个发光圆环, 按 severity_max 决定颜色:
//   LOW 翠绿 / MEDIUM 琥珀 / HIGH 赤陶
// 圆环用 Cesium.EllipseGraphics + 自定义发光材质, 2s 周期脉动
//
// 只在 anomaly_status.has_anomaly = true 时渲染。
// ============================================================================

import { inject, watch, onMounted, onBeforeUnmount, computed, ref, type Ref } from 'vue'
import * as Cesium from 'cesium'
import type { SceneBuilding } from '@/api/visual'
import { severityToColor, resolveBuildingPosition, computeVisualHeight, type GridPosition } from '@/utils/sceneTransform'

const props = defineProps<{
  building: SceneBuilding
}>()

const viewerRef = inject<Ref<Cesium.Viewer | null>>('cesiumViewer', ref(null))
const localToEcef = inject<(x: number, y: number, z: number) => Cesium.Cartesian3>('localToEcef', () => new Cesium.Cartesian3())
const gridLayoutRef = inject<Ref<Map<string, GridPosition>>>('gridLayout', ref(new Map()))

let entity: Cesium.Entity | null = null

const shouldRender = computed(() => props.building.anomaly_status.has_anomaly)
const severity = computed(() => props.building.anomaly_status.severity_max)

const position = computed<GridPosition>(() => {
  return resolveBuildingPosition(props.building, gridLayoutRef.value)
})

// halo 在屋顶上方 0.3m (贴近屋顶, 不飘), 半径取楼较短边的 1/3 (不超出楼顶边界)
// 高度跟 BuildingBlock 共用 computeVisualHeight, 保证 halo 贴屋顶不悬空
const haloSpec = computed(() => {
  const len = props.building.dimensions.length_m ?? 30
  const wid = props.building.dimensions.width_m ?? 20
  const minSide = Math.min(len, wid)
  return {
    z: computeVisualHeight(props.building) + 0.3,
    radius: minSide / 3,
  }
})

// 异常光晕材质: 用 ColorMaterialProperty + CallbackProperty 实现 2s 脉动
// alpha 范围 0.25-0.5 (柔和, 不刺眼)
function createPulseColorProperty(baseColor: Cesium.Color): Cesium.CallbackProperty {
  return new Cesium.CallbackProperty(() => {
    const pulse = 0.5 + 0.5 * Math.sin(performance.now() * 0.003)
    return baseColor.clone().withAlpha(0.25 + 0.25 * pulse)
  }, false)
}

function createEntity() {
  const viewer = viewerRef.value
  if (!viewer || !shouldRender.value || !severity.value) return

  const colorHex = severityToColor(severity.value)
  const color = Cesium.Color.fromCssColorString(colorHex)

  const pos = position.value
  const h = haloSpec.value

  // 实心圆盘 (柔和半透明) + outline 让圆盘边缘清晰
  entity = viewer.entities.add({
    id: `halo-${props.building.building_id}`,
    position: localToEcef(pos.x, pos.y, h.z),
    ellipse: {
      semiMajorAxis: h.radius,
      semiMinorAxis: h.radius,
      height: h.z,
      material: new Cesium.ColorMaterialProperty(createPulseColorProperty(color)),
      outline: true,
      outlineColor: new Cesium.CallbackProperty(() => {
        const pulse = 0.5 + 0.5 * Math.sin(performance.now() * 0.003)
        return color.clone().withAlpha(0.5 + 0.3 * pulse)
      }, false),
      outlineWidth: 1,
    },
  })
}

onMounted(() => {
  // Vue 3 子先父后: watch viewerRef, 就绪后 createEntity
  if (viewerRef.value) {
    createEntity()
  } else {
    const stop = watch(viewerRef, (v) => {
      if (v) {
        createEntity()
        stop()
      }
    })
  }
})

onBeforeUnmount(() => {
  const viewer = viewerRef.value
  if (viewer && entity) {
    try {
      if (!viewer.isDestroyed?.()) {
        viewer.entities.remove(entity)
      }
    } catch (e) {
      // viewer 已销毁, 静默忽略
    }
    entity = null
  }
})

// severity 变化时重建 entity (颜色变)
watch(
  () => severity.value,
  () => {
    const viewer = viewerRef.value
    if (viewer && entity) {
      viewer.entities.remove(entity)
      entity = null
    }
    createEntity()
  },
)
</script>

<template>
  <div style="display: none" />
</template>

<style scoped lang="scss">
div { display: none; }
</style>
