<script setup lang="ts">
// ============================================================================
// BuildingBlock - 建筑渲染 (程序化表皮 + 能效着色双模式)
// ----------------------------------------------------------------------------
// 职责: 渲染单个建筑。按 park.renderMode 走两条视觉路径:
//
//   realistic (虚拟外观, 默认): 用 buildingAppearance 按 sub_use 生成
//     程序化表皮 (canvas 窗格纹理贴 Box 侧面) + 屋顶 (平顶/退台/桶形弧顶)。
//     不做能耗着色, 让建筑看起来像"有玻璃幕墙的教学楼 / 砖墙学生中心"。
//
//   energy (能效着色): 纯色体块按 color_metric.level 着色 (绿/灰/琥珀/红),
//     加楼层分隔线, 配合 halo/badge 做园区级能耗总览 (原 Step 11 的行为)。
//
// 两种模式共用: 入场动画 (拔地而起) / hover 抬升 / 选中描边 / 颜色过渡。
//
// hidden 模式 (splat 楼用): 只渲染透明 box 做 click target, 不画表皮/屋顶/楼层线。
//
// 为什么表皮用 canvas 纹理而不是真实贴图: 用户上传的园区楼栋类型未知, 没法
// 为每种楼都备一套贴图。canvas 程序化生成窗格纹理, 按楼宽/层数 repeat,
// 任何尺寸、任何用途的楼都能铺出合理的窗户密度, 零外部资源、秒加载。
// ============================================================================

import { inject, watch, onMounted, onBeforeUnmount, ref, computed, type Ref } from 'vue'
import * as Cesium from 'cesium'
import type { SceneBuilding } from '@/api/visual'
import { useParkStore } from '@/stores/park'
import {
  resolveBuildingPosition,
  levelToColor,
  hexToRgb,
  lerpColor,
  computeVisualHeight,
  type GridPosition,
} from '@/utils/sceneTransform'
import { getBuildingAppearance, generateFacadeTexture, getRoofSpec } from '@/utils/buildingAppearance'

const props = defineProps<{
  building: SceneBuilding
  index: number  // 入场动画错峰用
  // hidden=true 时只渲染透明 box 作 click target (splat 模式的隐形交互层)
  hidden?: boolean
}>()

const park = useParkStore()

const viewerRef = inject<Ref<Cesium.Viewer | null>>('cesiumViewer', ref(null))
const localToEcef = inject<(x: number, y: number, z: number) => Cesium.Cartesian3>('localToEcef', () => new Cesium.Cartesian3())
const gridLayoutRef = inject<Ref<Map<string, GridPosition>>>('gridLayout', ref(new Map()))

let entity: Cesium.Entity | null = null
let bodyEntity: Cesium.Entity | null = null
const roofEntities: Cesium.Entity[] = []
const floorLineEntities: Cesium.Entity[] = []
const startTime = ref<Cesium.JulianDate | null>(null)

const dims = computed(() => {
  const b = props.building
  return {
    length: b.dimensions.length_m ?? 30,
    width: b.dimensions.width_m ?? 20,
    height: b.dimensions.height_m ?? 20,
    floors: b.dimensions.floors_count ?? 1,
  }
})

const position = computed<GridPosition>(() => {
  return resolveBuildingPosition(props.building, gridLayoutRef.value)
})

const appearance = computed(() => getBuildingAppearance(props.building))
const isRealistic = computed(() => park.renderMode === 'realistic')

// 表皮纹理: 每个 BuildingBlock 实例只生成一次 (building 的 sub_use 会话内不变)
let facadeCanvas: HTMLCanvasElement | null = null
function getFacadeCanvas(): HTMLCanvasElement {
  if (!facadeCanvas) facadeCanvas = generateFacadeTexture(appearance.value)
  return facadeCanvas
}

// 纹理 repeat: 水平每 ~9m 一个开间, 垂直每层一格。让窗户密度随建筑尺寸自适应。
const facadeRepeat = computed(() => {
  const len = dims.value.length
  const floors = Math.max(1, dims.value.floors)
  return new Cesium.Cartesian2(Math.max(2, Math.round(len / 9)), floors)
})

// ---- 入场动画 ----
const ANIM_DURATION_MS = 800
const STAGGER_MS = 100

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3)
}

// 视觉高度 (z 放大后, 带按层数递增的最小高度兜底)
function visualFullHeight(): number {
  return computeVisualHeight(props.building)
}

// 每层视觉高度
function visualFloorHeight(): number {
  const f = dims.value.floors
  if (f <= 1) return visualFullHeight()
  return visualFullHeight() / f
}

// 当前渲染高度 (含入场动画 + hover 抬升)
function computeCurrentHeight(time: Cesium.JulianDate): { height: number; zOffset: number } {
  const fullH = visualFullHeight()
  if (props.hidden) {
    return { height: fullH, zOffset: 0 }
  }
  // startTime 未初始化时直接给满高: 宁可没有入场动画, 不能让楼卡在 0 高度变纸片
  if (!startTime.value) return { height: fullH, zOffset: 0 }
  let elapsedMs = Cesium.JulianDate.secondsDifference(time, startTime.value) * 1000 - props.index * STAGGER_MS
  // 时钟基准错位保护: viewer.clock 被固定到 2024 (sun 光源), 若 startTime 误用了
  // 真实当前时间 (2026), elapsed 会是负几十亿毫秒且永远追不上, 楼永远 0 高度。
  // 偏差超过 5s 视为错位, 按 0 处理让动画立即开始。
  if (elapsedMs < -5000) elapsedMs = 0
  if (elapsedMs <= 0) return { height: 0, zOffset: 0 }
  if (elapsedMs >= ANIM_DURATION_MS) {
    const isHovered = park.hoveredBuildingId === props.building.building_id
    return { height: fullH, zOffset: isHovered ? 0.5 : 0 }
  }
  const t = easeOutCubic(elapsedMs / ANIM_DURATION_MS)
  const isHovered = park.hoveredBuildingId === props.building.building_id
  return { height: fullH * t, zOffset: isHovered ? 0.5 : 0 }
}

// 楼体顶部高度 (屋顶 / 楼层线都以它为准, 保证不悬空)
function bodyTopHeight(time: Cesium.JulianDate): number {
  const { height, zOffset } = computeCurrentHeight(time)
  return zOffset + height
}

// ---- 能效着色颜色过渡 (energy 模式用) ----
let colorAnimFrom: string = levelToColor(props.building.color_metric.level)
let colorAnimStart: Cesium.JulianDate | null = null
const COLOR_TRANSITION_MS = 200

function computeCurrentColor(time: Cesium.JulianDate): Cesium.Color {
  const targetHex = levelToColor(props.building.color_metric.level)
  if (colorAnimStart === null) {
    colorAnimFrom = targetHex
    return Cesium.Color.fromCssColorString(targetHex)
  }
  const elapsedMs = Cesium.JulianDate.secondsDifference(time, colorAnimStart) * 1000
  if (elapsedMs >= COLOR_TRANSITION_MS) {
    colorAnimFrom = targetHex
    return Cesium.Color.fromCssColorString(targetHex)
  }
  const t = elapsedMs / COLOR_TRANSITION_MS
  const hex = lerpColor(colorAnimFrom, targetHex, t)
  return Cesium.Color.fromCssColorString(hex)
}

// 选中 / hover / 普通三态描边色
function currentOutlineColor(): Cesium.Color {
  if (park.selectedBuildingId === props.building.building_id) {
    return Cesium.Color.fromCssColorString('#D49B3B')
  }
  if (park.hoveredBuildingId === props.building.building_id) {
    return Cesium.Color.fromCssColorString('#8B5A1F')
  }
  return Cesium.Color.fromCssColorString('#2C2C2C')
}

// ---- 主体材质 (按模式 + hidden 三选一) ----
function buildBodyMaterial(): Cesium.MaterialProperty {
  if (props.hidden) {
    // 透明 click target: 不能用 alpha=0 (pick pass 会剔除), 0.01 视觉不可见但能 pick
    return new Cesium.ColorMaterialProperty(
      new Cesium.CallbackProperty(() => Cesium.Color.WHITE.withAlpha(0.01), false),
    )
  }
  if (isRealistic.value) {
    return new Cesium.ImageMaterialProperty({
      image: getFacadeCanvas(),
      repeat: facadeRepeat.value,
    })
  }
  return new Cesium.ColorMaterialProperty(
    new Cesium.CallbackProperty((time?: Cesium.JulianDate) => {
      const t = time ?? Cesium.JulianDate.now()
      return computeCurrentColor(t)
    }, false),
  )
}

// ---- 楼层分隔线 (energy 模式才画, 表皮纹理已经能看出楼层) ----
function buildFloorLines() {
  const viewer = viewerRef.value
  if (!viewer || !entity || props.hidden || isRealistic.value) return
  const pos = position.value
  const L = dims.value.length / 2
  const W = dims.value.width / 2
  const floorH = visualFloorHeight()

  for (let i = 1; i < dims.value.floors; i++) {
    const zFromBottom = i * floorH
    const lineEntity = viewer.entities.add({
      id: `floor-line-${props.building.building_id}-${i}`,
      parent: entity,
      properties: { building_id: props.building.building_id },
      polyline: {
        positions: new Cesium.CallbackProperty((time?: Cesium.JulianDate) => {
          const t = time ?? Cesium.JulianDate.now()
          const { height: animHeight, zOffset } = computeCurrentHeight(t)
          if (animHeight < zFromBottom) return []
          const z = zOffset + zFromBottom
          return [
            localToEcef(pos.x - L, pos.y - W, z),
            localToEcef(pos.x + L, pos.y - W, z),
            localToEcef(pos.x + L, pos.y + W, z),
            localToEcef(pos.x - L, pos.y + W, z),
            localToEcef(pos.x - L, pos.y - W, z),
          ]
        }, false),
        width: 1,
        material: new Cesium.ColorMaterialProperty(
          new Cesium.CallbackProperty(() => {
            if (park.selectedBuildingId === props.building.building_id) {
              return Cesium.Color.fromCssColorString('#D49B3B').withAlpha(0.9)
            }
            if (park.hoveredBuildingId === props.building.building_id) {
              return Cesium.Color.fromCssColorString('#8B5A1F').withAlpha(0.9)
            }
            return Cesium.Color.fromCssColorString('#2C2C2C').withAlpha(0.55)
          }, false),
        ),
        clampToGround: false,
      },
    })
    floorLineEntities.push(lineEntity)
  }
}

function clearFloorLines() {
  const viewer = viewerRef.value
  if (viewer && !viewer.isDestroyed?.()) {
    for (const e of floorLineEntities) viewer.entities.remove(e)
  }
  floorLineEntities.length = 0
}

// ---- 屋顶 + 顶/底封板 (realistic 模式才画) ----
// BoxGraphics 的 material 贴满 6 个面 (含顶面/底面), 俯视/仰视会看到 box 顶/底
// 面的窗户纹理 -- 真实建筑顶面是屋顶、底面是地基, 不该有窗户。所以无论哪种
// roofType 都额外加两块薄板:
//   - 底封板: 在 box 底面下方 0.05m, 用 facadeMain 色 (建筑底座感), 仰视时盖住 box 底面窗户
//   - 顶封板: 仅 barrel 类型需要 (椭球是圆形截面盖不住矩形 box 四角); flat/pitched 的
//     parapet slab 已经外挑 +0.6m 完全盖住顶面, 不再重复加
function buildRoof() {
  const viewer = viewerRef.value
  if (!viewer || !entity || props.hidden || !isRealistic.value) return
  clearRoof()

  const pos = position.value
  const a = appearance.value
  const roof = getRoofSpec(a)
  const L = dims.value.length
  const W = dims.value.width
  const roofColorProp = new Cesium.ColorMaterialProperty(Cesium.Color.fromCssColorString(a.roofColor))
  const baseColorProp = new Cesium.ColorMaterialProperty(Cesium.Color.fromCssColorString(a.facadeMain))

  // 1. 底封板 (所有 roofType 都加): 盖住 box 底面的窗户纹理
  //    z 中心在 box 底面下方 0.05m, 厚 0.1m, 比 box 外挑 0.4m
  roofEntities.push(viewer.entities.add({
    parent: entity,
    position: new Cesium.CallbackProperty((time?: Cesium.JulianDate) => {
      const t = time ?? Cesium.JulianDate.now()
      const { zOffset } = computeCurrentHeight(t)
      return localToEcef(pos.x, pos.y, zOffset - 0.05)
    }, false) as any,
    box: {
      dimensions: new Cesium.Cartesian3(L + 0.4, W + 0.4, 0.1),
      material: baseColorProp,
    },
  }))

  if (a.roofType === 'barrel') {
    // 2. 顶封板 (仅 barrel): 椭球是圆形截面盖不住矩形 box 四角, 加一块薄板垫底
    roofEntities.push(viewer.entities.add({
      parent: entity,
      position: new Cesium.CallbackProperty((time?: Cesium.JulianDate) => {
        const t = time ?? Cesium.JulianDate.now()
        return localToEcef(pos.x, pos.y, bodyTopHeight(t) + 0.05)
      }, false) as any,
      box: {
        dimensions: new Cesium.Cartesian3(L + 0.4, W + 0.4, 0.1),
        material: roofColorProp,
      },
    }))
    // 3. 桶形弧顶 (压扁椭球), 底坐在顶封板上方, 向上拱起
    const zR = Math.min(L, W) / 5
    roofEntities.push(viewer.entities.add({
      parent: entity,
      position: new Cesium.CallbackProperty((time?: Cesium.JulianDate) => {
        const t = time ?? Cesium.JulianDate.now()
        return localToEcef(pos.x, pos.y, bodyTopHeight(t) + 0.1 + zR)
      }, false) as any,
      ellipsoid: {
        radii: new Cesium.Cartesian3(L / 2, W / 2, zR),
        material: roofColorProp,
      },
    }))
  } else {
    // 2. 女儿墙薄板 (平顶/坡顶): 外挑 overhang, 完全盖住顶面
    const overhang = Math.abs(roof.parapetInset)
    roofEntities.push(viewer.entities.add({
      parent: entity,
      position: new Cesium.CallbackProperty((time?: Cesium.JulianDate) => {
        const t = time ?? Cesium.JulianDate.now()
        return localToEcef(pos.x, pos.y, bodyTopHeight(t) + roof.parapetHeight / 2)
      }, false) as any,
      box: {
        dimensions: new Cesium.Cartesian3(
          L + 2 * overhang, W + 2 * overhang, roof.parapetHeight,
        ),
        material: roofColorProp,
      },
    }))

    // 3. 坡顶退台 (第二层实心块)
    if (a.roofType === 'pitched' && roof.setbackHeight > 0) {
      roofEntities.push(viewer.entities.add({
        parent: entity,
        position: new Cesium.CallbackProperty((time?: Cesium.JulianDate) => {
          const t = time ?? Cesium.JulianDate.now()
          return localToEcef(pos.x, pos.y, bodyTopHeight(t) + roof.parapetHeight + roof.setbackHeight / 2)
        }, false) as any,
        box: {
          dimensions: new Cesium.Cartesian3(L * roof.setbackScale, W * roof.setbackScale, roof.setbackHeight),
          material: roofColorProp,
        },
      }))
    }
  }
}

function clearRoof() {
  const viewer = viewerRef.value
  if (viewer && !viewer.isDestroyed?.()) {
    for (const e of roofEntities) viewer.entities.remove(e)
  }
  roofEntities.length = 0
}

// ---- 创建主体 entity ----
function createEntity() {
  const viewer = viewerRef.value
  if (!viewer) return

  // 入场动画基准 = 场景时钟当前值 (与渲染帧同一时钟, elapsed 单调递增)。
  // 绝不能用 Cesium.JulianDate.now() 真实时间: viewer.clock 为 sun 固定在
  // 2024 年, 真实 now 与帧时间差 -2 年, elapsed 永远为负, 楼卡 0 高度不可见。
  startTime.value = Cesium.JulianDate.clone(viewer.clock.currentTime)

  const pos = position.value

  // parent entity (容器)
  entity = viewer.entities.add({
    id: `building-${props.building.building_id}`,
    properties: { building_id: props.building.building_id },
    position: localToEcef(pos.x, pos.y, 0),
  })

  // 主体 box
  bodyEntity = viewer.entities.add({
    id: `block-${props.building.building_id}`,
    parent: entity,
    properties: { building_id: props.building.building_id },
    position: new Cesium.CallbackProperty((time?: Cesium.JulianDate) => {
      const t = time ?? Cesium.JulianDate.now()
      const { height: animHeight, zOffset } = computeCurrentHeight(t)
      return localToEcef(pos.x, pos.y, zOffset + animHeight / 2)
    }, false) as any,
    box: {
      dimensions: new Cesium.CallbackProperty((time?: Cesium.JulianDate) => {
        const t = time ?? Cesium.JulianDate.now()
        const { height: animHeight } = computeCurrentHeight(t)
        return new Cesium.Cartesian3(dims.value.length, dims.value.width, animHeight)
      }, false),
      material: buildBodyMaterial(),
      // 投射阴影到地面 polygon (CampusGround 的 plot/site pad shadows=ENABLED 接收)
      shadows: Cesium.ShadowMode.CAST_ONLY,
      outline: !props.hidden,
      outlineColor: new Cesium.CallbackProperty(() => {
        if (props.hidden) return Cesium.Color.WHITE.withAlpha(0)
        return currentOutlineColor()
      }, false),
      outlineWidth: 1,
    },
  })

  buildFloorLines()
  buildRoof()
}

// ---- 模式切换: 原地换材质 + 增删屋顶/楼层线, 不重启入场动画 ----
function applyRenderMode() {
  if (props.hidden || !bodyEntity) return
  bodyEntity.box!.material = buildBodyMaterial()
  if (isRealistic.value) {
    clearFloorLines()
    buildRoof()
  } else {
    clearRoof()
    buildFloorLines()
  }
}

// ---- watch color_metric.level 变化触发颜色过渡 (energy 模式) ----
watch(
  () => props.building.color_metric.level,
  () => {
    colorAnimFrom = levelToColor(props.building.color_metric.level)
    colorAnimStart = Cesium.JulianDate.now()
  },
)

// ---- watch 渲染模式切换 ----
watch(isRealistic, () => {
  applyRenderMode()
})

// ---- watch hidden 切换 (重建后 estimated -> splat, hidden false -> true) ----
// v-for 的 :key 是 building_id (不变), Vue 复用组件不重挂载, 只改 hidden prop。
// 不加这个 watch, 旧块 (estimated 时挂的可见表皮) 不会在新 splat 出现时隐藏,
// 导致旧块和 splat 叠在一起。hidden 变化时原地更新材质 + outline + 屋顶/楼层线。
watch(() => props.hidden, (hidden) => {
  if (!bodyEntity) return
  bodyEntity.box!.material = buildBodyMaterial()
  bodyEntity.box!.outline = new Cesium.ConstantProperty(!hidden)
  if (hidden) {
    clearRoof()
    clearFloorLines()
  } else {
    applyRenderMode()
  }
})

onMounted(() => {
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
  if (viewer && !viewer.isDestroyed?.()) {
    clearRoof()
    clearFloorLines()
    if (bodyEntity) {
      try { viewer.entities.remove(bodyEntity) } catch { /* 已销毁, 忽略 */ }
    }
    if (entity) {
      try { viewer.entities.remove(entity) } catch { /* 已销毁, 忽略 */ }
    }
  }
  entity = null
  bodyEntity = null
})
</script>

<template>
  <div style="display: none" />
</template>

<style scoped lang="scss">
div { display: none; }
</style>
