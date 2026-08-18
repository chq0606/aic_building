<script setup lang="ts">
// ============================================================================
// CampusGround - 程序化园区地面 (道路 / 地块 / 绿化 / 阴影接收)
// ----------------------------------------------------------------------------
// 职责:
//   1. 园区基座 (site pad): 整个园区范围铺一块浅米色铺装 (pavement 纹理),
//      作为道路和地块的底色, 区分"道路 / 内部庭院 / 楼栋绿地"三个层次。
//   2. 道路网: 环形路 + 楼间路, 道路边缘严格按"楼 footprint 外缘 + 退距"算,
//      确保不穿过任何楼。沥青色 + 中央黄色虚线 polyline 模拟车道分隔。
//   3. 建筑红线地块 (plot): 每栋楼脚下铺一块草皮纹理 (grass), 让楼有"绿地落点"。
//   4. 程序化树: 沿道路两侧成排种, 多种树冠形状 (球/伞/锥) + 随机偏移,
//      打破"严丝合缝非常整齐反而显得假"的机械感。
//
// 阴影接收: site pad / plot 的 polygon 设 shadows: ENABLED, 让 Cesium
// shadowMap 把 BuildingBlock 投射的阴影画到地面 polygon 上, 解决"建筑飘在
// 空中"的视觉感 (配合 CesiumViewer 启用的 shadowMap + sun 光源)。
//
// 坐标: 全部走 localToEcef (跟 BuildingBlock 一致), z 分层避免 z-fighting:
//   site pad 0.02 / plot 0.03 / road 0.04 / 中央虚线 0.05 / 树实体几何从 0 往上
//
// perPositionHeight: true 必须加, 否则 globe.show=false 时 polygon 被强制贴到
// globe surface (已关闭), 不渲染。所有 polygon 都加这个让顶点 z 起作用。
//
// 通用性: 所有道路/地块/树位置从 props.buildings 动态算, 不写死坐标。任何
// 用户上传楼栋 (有 dimensions + position 或 NULL 走网格兜底) 都能自动铺。
// ============================================================================

import { inject, ref, onMounted, onBeforeUnmount, computed, watch, type Ref } from 'vue'
import * as Cesium from 'cesium'
import type { SceneBuilding } from '@/api/visual'
import { computeGridLayout, resolveBuildingPosition, type GridPosition } from '@/utils/sceneTransform'

const props = defineProps<{
  buildings: SceneBuilding[]
}>()

const viewerRef = inject<Ref<Cesium.Viewer | null>>('cesiumViewer', ref(null))
const localToEcef = inject<(x: number, y: number, z: number) => Cesium.Cartesian3>(
  'localToEcef',
  () => new Cesium.Cartesian3(),
)

const COLORS = {
  road: '#8A8275',         // 道路主体 = gray-500 (深暖灰, 模拟沥青)
  roadCenter: '#D49B3B',    // 中央双黄线 = amber
  trunk: '#6B6557',        // 树干 = gray-600
  canopy: '#7CB34A',       // 树冠浅绿
  canopyDark: '#5E8C42',   // 树冠深绿
  canopyAlt: '#92B86A',    // 球状树冠偏黄绿 (阔叶树)
  canopyUmbrella: '#6B9450', // 伞状树冠偏深绿
}

// 当前挂上去的 entity, 重建时统一清掉
let entities: Cesium.Entity[] = []

const gridLayout = computed(() => computeGridLayout(props.buildings))

// ---- canvas 纹理生成 (草皮 / 铺装) ----
// 这些纹理只生成一次, 多个 polygon 共享 (单例)。repeat 控制密度。

let grassTexture: HTMLCanvasElement | null = null
function getGrassTexture(): HTMLCanvasElement {
  if (grassTexture) return grassTexture
  const S = 128
  const c = document.createElement('canvas')
  c.width = S
  c.height = S
  const ctx = c.getContext('2d')!
  // 底色: 中绿
  ctx.fillStyle = '#A8C088'
  ctx.fillRect(0, 0, S, S)
  // 随机噪点: 深绿 / 浅绿 / 黄绿, 模拟草丛颗粒
  const colors = ['#8FAE6E', '#B8D29C', '#94B574', '#7CA063']
  for (let i = 0; i < 400; i++) {
    const x = Math.random() * S
    const y = Math.random() * S
    const r = 1 + Math.random() * 2
    ctx.fillStyle = colors[Math.floor(Math.random() * colors.length)]
    ctx.fillRect(x, y, r, r)
  }
  // 几片深色斑块 (草丛阴影)
  for (let i = 0; i < 12; i++) {
    const x = Math.random() * S
    const y = Math.random() * S
    const r = 4 + Math.random() * 6
    ctx.fillStyle = 'rgba(94, 140, 66, 0.3)'
    ctx.beginPath()
    ctx.arc(x, y, r, 0, Math.PI * 2)
    ctx.fill()
  }
  grassTexture = c
  return c
}

// 地平线圆盘纹理: 径向渐变, 中心 = 地面暖米色, 边缘 = 天顶浅蓝
// 让园区基座到画面边缘之间有柔和过渡, 替代被废弃的 skyAtmosphere (无 globe 时
// 渲染出黑白噪点带 + 弧形切边, 不可用)。
let horizonTexture: HTMLCanvasElement | null = null
function getHorizonTexture(): HTMLCanvasElement {
  if (horizonTexture) return horizonTexture
  const S = 512
  const c = document.createElement('canvas')
  c.width = S
  c.height = S
  const ctx = c.getContext('2d')!
  const grad = ctx.createRadialGradient(S / 2, S / 2, 0, S / 2, S / 2, S / 2)
  grad.addColorStop(0, '#EFECE4')     // 中心: 跟 site pad 铺装同色系
  grad.addColorStop(0.55, '#E6E3D9')  // 中段: 过渡带
  grad.addColorStop(0.85, '#C8D6E6')  // 外圈: 接近天顶色
  grad.addColorStop(1, '#C8D6E6')     // 边缘: 与 scene.backgroundColor 无缝融合
  ctx.fillStyle = grad
  ctx.fillRect(0, 0, S, S)
  horizonTexture = c
  return c
}

let pavementTexture: HTMLCanvasElement | null = null
function getPavementTexture(): HTMLCanvasElement {
  if (pavementTexture) return pavementTexture
  const S = 128
  const c = document.createElement('canvas')
  c.width = S
  c.height = S
  const ctx = c.getContext('2d')!
  // 底色: 暖米色 (跟 paper 接近, 略深)
  ctx.fillStyle = '#E5E0D2'
  ctx.fillRect(0, 0, S, S)
  // 砖纹网格: 32x32 一格 (4 块), 模拟铺装石材
  ctx.strokeStyle = '#C9C3B6'
  ctx.lineWidth = 1
  const cell = 32
  for (let i = 0; i <= S / cell; i++) {
    const p = i * cell + 0.5
    ctx.beginPath(); ctx.moveTo(p, 0); ctx.lineTo(p, S); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(0, p); ctx.lineTo(S, p); ctx.stroke()
  }
  // 随机噪点: 浅米色 + 深米色, 模拟石材颗粒
  for (let i = 0; i < 200; i++) {
    const x = Math.random() * S
    const y = Math.random() * S
    ctx.fillStyle = Math.random() < 0.5 ? '#D8D2C2' : '#EFEBDE'
    ctx.fillRect(x, y, 1.5, 1.5)
  }
  pavementTexture = c
  return c
}

// ---- 工具 ----

// 加一个米单位矩形 polygon (四个角点按 z 高)
// material: 可以是 ColorMaterialProperty (纯色) 或 ImageMaterialProperty (纹理)
// shadows: ENABLED 让 polygon 接收 building 投射的阴影 (Cesium shadowMap)
function addRect(
  viewer: Cesium.Viewer,
  xMin: number, yMin: number, xMax: number, yMax: number,
  z: number,
  material: Cesium.MaterialProperty,
  shadows: Cesium.ShadowMode = Cesium.ShadowMode.ENABLED,
) {
  const corners = [
    localToEcef(xMin, yMin, z),
    localToEcef(xMax, yMin, z),
    localToEcef(xMax, yMax, z),
    localToEcef(xMin, yMax, z),
  ]
  entities.push(viewer.entities.add({
    polygon: {
      hierarchy: new Cesium.PolygonHierarchy(corners),
      material,
      perPositionHeight: true,
      shadows,
    },
  }))
}

// 加一条道路 strip: 沿 a->b 方向宽 width 米的矩形, 纯色沥青底 + 中央黄色虚线
function addRoadStrip(
  viewer: Cesium.Viewer,
  a: GridPosition, b: GridPosition, width: number,
  z: number, withCenterLine = true,
) {
  const dx = b.x - a.x
  const dy = b.y - a.y
  const len = Math.hypot(dx, dy)
  if (len < 1e-6) return
  const ux = dx / len
  const uy = dy / len
  const px = -uy
  const py = ux
  const half = width / 2
  const corners = [
    { x: a.x + px * half, y: a.y + py * half },
    { x: b.x + px * half, y: b.y + py * half },
    { x: b.x - px * half, y: b.y - py * half },
    { x: a.x - px * half, y: a.y - py * half },
  ]
  const positions = corners.map(c => localToEcef(c.x, c.y, z))
  entities.push(viewer.entities.add({
    polygon: {
      hierarchy: new Cesium.PolygonHierarchy(positions),
      material: Cesium.Color.fromCssColorString(COLORS.road),
      perPositionHeight: true,
      shadows: Cesium.ShadowMode.ENABLED,
    },
  }))

  // 中央黄色虚线 (车道分隔), z 略高于路面避免 z-fighting
  if (withCenterLine) {
    entities.push(viewer.entities.add({
      polyline: {
        positions: [
          localToEcef(a.x, a.y, z + 0.02),
          localToEcef(b.x, b.y, z + 0.02),
        ],
        width: 2,
        material: new Cesium.PolylineDashMaterialProperty({
          color: Cesium.Color.fromCssColorString(COLORS.roadCenter),
          dashLength: 12,
        }),
      },
    }))
  }
}

// 树冠形状: 圆锥(松柏) / 球状(阔叶) / 伞状(榕树)
type TreeShape = 'cone' | 'sphere' | 'umbrella'

// 加一棵程序化树: 树干圆柱 + 树冠 (按 shape 选不同几何体)
function addTree(
  viewer: Cesium.Viewer,
  x: number, y: number, scale: number,
  shape: TreeShape,
) {
  const trunkH = 2.4 * scale
  const trunkR = 0.45 * scale
  const canopyH = 5.5 * scale
  const canopyR = 2.6 * scale

  // 树干 (所有形状共用)
  entities.push(viewer.entities.add({
    position: localToEcef(x, y, trunkH / 2),
    cylinder: {
      length: trunkH,
      topRadius: trunkR * 0.7,
      bottomRadius: trunkR,
      material: Cesium.Color.fromCssColorString(COLORS.trunk),
      shadows: Cesium.ShadowMode.CAST_ONLY,
    },
  }))

  // 树冠按形状选不同几何体 + 略不同的颜色 (让树种视觉上区分)
  let canopyColor = COLORS.canopy
  if (shape === 'sphere') canopyColor = COLORS.canopyAlt
  else if (shape === 'umbrella') canopyColor = COLORS.canopyUmbrella

  if (shape === 'cone') {
    // 圆锥 (松柏类)
    entities.push(viewer.entities.add({
      position: localToEcef(x, y, trunkH + canopyH / 2),
      cylinder: {
        length: canopyH,
        topRadius: canopyR * 0.05,
        bottomRadius: canopyR,
        material: Cesium.Color.fromCssColorString(canopyColor),
        shadows: Cesium.ShadowMode.CAST_ONLY,
      },
    }))
  } else if (shape === 'sphere') {
    // 球状 (阔叶树, 比如樟树/橡树): 压扁的椭球
    entities.push(viewer.entities.add({
      position: localToEcef(x, y, trunkH + canopyR * 0.7),
      ellipsoid: {
        radii: new Cesium.Cartesian3(canopyR, canopyR, canopyR * 0.85),
        material: Cesium.Color.fromCssColorString(canopyColor),
      },
    }))
  } else {
    // 伞状 (榕树/棕榈): 宽扁椭球, 位置略高
    entities.push(viewer.entities.add({
      position: localToEcef(x, y, trunkH + canopyR * 0.6),
      ellipsoid: {
        radii: new Cesium.Cartesian3(canopyR * 1.3, canopyR * 1.3, canopyH * 0.4),
        material: Cesium.Color.fromCssColorString(canopyColor),
      },
    }))
  }
}

// 确定性伪随机 (mulberry32, 比 LCG 质量好, 树位置不随重建抖动)
function mulberry32(seed: number): () => number {
  let s = seed >>> 0
  return () => {
    s = (s + 0x6D2B79F5) >>> 0
    let t = s
    t = Math.imul(t ^ (t >>> 15), t | 1)
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61)
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

// ---- 主构建 ----

function build() {
  const viewer = viewerRef.value
  if (!viewer) return
  cleanup()

  const buildings = props.buildings
  if (buildings.length === 0) return

  const positions = buildings.map(b => resolveBuildingPosition(b, gridLayout.value))
  const halfLs = buildings.map(b => (b.dimensions.length_m ?? 30) / 2)
  const halfWs = buildings.map(b => (b.dimensions.width_m ?? 20) / 2)

  const ROAD_W = 8
  const SETBACK = 4

  // 园区包围盒: 基于楼 footprint 外缘 + 退距 + 道路宽度
  const xMinBuilding = Math.min(...positions.map((p, i) => p.x - halfLs[i]))
  const xMaxBuilding = Math.max(...positions.map((p, i) => p.x + halfLs[i]))
  const yMinBuilding = Math.min(...positions.map((p, i) => p.y - halfWs[i]))
  const yMaxBuilding = Math.max(...positions.map((p, i) => p.y + halfWs[i]))

  const xMin = xMinBuilding - SETBACK - ROAD_W
  const xMax = xMaxBuilding + SETBACK + ROAD_W
  const yMin = yMinBuilding - SETBACK - ROAD_W
  const yMax = yMaxBuilding + SETBACK + ROAD_W

  // 0. 超大地平线圆盘: 径向渐变纹理 (中心铺装色 -> 边缘天色), 铺到画面边缘。
  //    z 略低于 site pad (-0.5), 不接收阴影 (太远的盘面收阴影会糊成一片)。
  //    半径保守取: 上一版用对角线 6 倍 (直径 ~9km) 后建筑集体消失 (建筑是全场
  //    唯一 CallbackProperty 每帧重算的动态几何, 超大 polygon 会拖垮这条更新
  //    链路; 静态的道路/铺装不受影响)。这里缩到 2.5 倍 + 最小 1600m, 边缘色
  //    与 scene.backgroundColor 相同, 缩小后依然无缝; arcType NONE 关掉大圆
  //    边界的测地线插值, 顶点按传入值直接用, 避免 9km 级圆弧重投影。
  const center = { x: (xMin + xMax) / 2, y: (yMin + yMax) / 2 }
  const diskR = Math.max(1600, Math.hypot(xMax - xMin, yMax - yMin) * 2.5)
  const diskPositions: Cesium.Cartesian3[] = []
  for (let i = 0; i < 64; i++) {
    const ang = (i / 64) * Math.PI * 2
    diskPositions.push(localToEcef(
      center.x + Math.cos(ang) * diskR,
      center.y + Math.sin(ang) * diskR,
      -0.5,
    ))
  }
  entities.push(viewer.entities.add({
    polygon: {
      hierarchy: new Cesium.PolygonHierarchy(diskPositions),
      material: new Cesium.ImageMaterialProperty({
        image: getHorizonTexture(),
        // 不 repeat, 径向渐变从圆心 (园区中心) 一路平滑过渡到边缘 (画面尽头)
        color: Cesium.Color.WHITE,
      }),
      perPositionHeight: true,
      arcType: Cesium.ArcType.NONE,
      shadows: Cesium.ShadowMode.DISABLED,
    },
  }))

  // 1. 园区基座: 浅米色铺装纹理 (pavement), 接收阴影
  const pavementMat = new Cesium.ImageMaterialProperty({
    image: getPavementTexture(),
    repeat: new Cesium.Cartesian2(20, 20),  // 大范围重复, 颗粒感
  })
  addRect(viewer, xMin - 12, yMin - 12, xMax + 12, yMax + 12, 0.02, pavementMat)

  // 2. 环形路: 沿包围盒边缘一圈
  const ring = [
    { x: xMin, y: yMin }, { x: xMax, y: yMin },
    { x: xMax, y: yMax }, { x: xMin, y: yMax }, { x: xMin, y: yMin },
  ]
  for (let i = 0; i < ring.length - 1; i++) {
    addRoadStrip(viewer, ring[i], ring[i + 1], ROAD_W, 0.04)
  }

  // 3. 楼间路: 在相邻楼列/行的中点贯穿
  const xs = positions.map(p => p.x)
  const ys = positions.map(p => p.y)
  const uniqueX = [...new Set(xs)].sort((a, b) => a - b)
  const uniqueY = [...new Set(ys)].sort((a, b) => a - b)
  for (let i = 0; i < uniqueX.length - 1; i++) {
    const midX = (uniqueX[i] + uniqueX[i + 1]) / 2
    const crossesBuilding = positions.some((p, idx) => Math.abs(p.x - midX) < halfLs[idx])
    if (crossesBuilding) continue
    addRoadStrip(
      viewer,
      { x: midX, y: yMin + ROAD_W / 2 }, { x: midX, y: yMax - ROAD_W / 2 },
      ROAD_W, 0.04,
    )
  }
  for (let i = 0; i < uniqueY.length - 1; i++) {
    const midY = (uniqueY[i] + uniqueY[i + 1]) / 2
    const crossesBuilding = positions.some((p, idx) => Math.abs(p.y - midY) < halfWs[idx])
    if (crossesBuilding) continue
    addRoadStrip(
      viewer,
      { x: xMin + ROAD_W / 2, y: midY }, { x: xMax - ROAD_W / 2, y: midY },
      ROAD_W, 0.04,
    )
  }

  // 4. 建筑红线地块 (plot): 每栋楼脚下铺草皮纹理, 接收阴影
  //    让楼有"绿地落点", 跟园区基座的铺装区分开 (内部庭院 vs 广场铺装)
  const PLOT_MARGIN = 4
  const grassMat = new Cesium.ImageMaterialProperty({
    image: getGrassTexture(),
    repeat: new Cesium.Cartesian2(3, 3),  // 小范围重复, 草丛颗粒清晰
  })
  buildings.forEach((b, idx) => {
    const p = positions[idx]
    const len = halfLs[idx] + PLOT_MARGIN
    const wid = halfWs[idx] + PLOT_MARGIN
    addRect(viewer, p.x - len, p.y - wid, p.x + len, p.y + wid, 0.03, grassMat)
  })

  // 5. 程序化树: 沿道路两侧成排种, 多种形状 + 随机偏移
  const TREE_SPACING = 10
  const TREE_OFFSET = ROAD_W / 2 + 2  // 树离路中心 6m, 在路内侧 2m (人行道绿化带)

  const ringEdges = [
    { from: { x: xMin, y: yMin }, to: { x: xMax, y: yMin }, offset: { x: 0, y: TREE_OFFSET } },
    { from: { x: xMax, y: yMin }, to: { x: xMax, y: yMax }, offset: { x: -TREE_OFFSET, y: 0 } },
    { from: { x: xMax, y: yMax }, to: { x: xMin, y: yMax }, offset: { x: 0, y: -TREE_OFFSET } },
    { from: { x: xMin, y: yMax }, to: { x: xMin, y: yMin }, offset: { x: TREE_OFFSET, y: 0 } },
  ]
  const treePositions: { x: number; y: number }[] = []
  const rnd = mulberry32(20260814)
  const SHAPES: TreeShape[] = ['cone', 'sphere', 'umbrella']

  function tryPlantTree(v: Cesium.Viewer, x: number, y: number, scale: number) {
    // 避开楼 plot
    const inPlot = positions.some((p, idx) => {
      const halfL = halfLs[idx] + PLOT_MARGIN + 1
      const halfW = halfWs[idx] + PLOT_MARGIN + 1
      return Math.abs(p.x - x) < halfL && Math.abs(p.y - y) < halfW
    })
    if (inPlot) return
    // 避开已种的树
    if (treePositions.some(tp => Math.hypot(tp.x - x, tp.y - y) < 4)) return

    // 随机偏移: 位置 ±0.75m, 高度 ±15%, 形状随机
    // 打破"严丝合缝非常整齐反而显得假"的机械感
    const offsetX = (rnd() - 0.5) * 1.5
    const offsetY = (rnd() - 0.5) * 1.5
    const heightScale = scale * (0.85 + rnd() * 0.3)
    const shape = SHAPES[Math.floor(rnd() * SHAPES.length)]

    treePositions.push({ x: x + offsetX, y: y + offsetY })
    addTree(v, x + offsetX, y + offsetY, heightScale, shape)
  }

  for (const edge of ringEdges) {
    const dx = edge.to.x - edge.from.x
    const dy = edge.to.y - edge.from.y
    const len = Math.hypot(dx, dy)
    const count = Math.max(2, Math.floor(len / TREE_SPACING))
    for (let i = 1; i < count; i++) {
      const t = i / count
      const x = edge.from.x + dx * t + edge.offset.x
      const y = edge.from.y + dy * t + edge.offset.y
      tryPlantTree(viewer, x, y, 0.8 + rnd() * 0.4)
    }
  }

  // 5.2 沿楼间路两侧种
  for (let i = 0; i < uniqueX.length - 1; i++) {
    const midX = (uniqueX[i] + uniqueX[i + 1]) / 2
    const crossesBuilding = positions.some((p, idx) => Math.abs(p.x - midX) < halfLs[idx])
    if (crossesBuilding) continue
    for (const sign of [-1, 1]) {
      const treeX = midX + sign * TREE_OFFSET
      const yStart = yMin + ROAD_W / 2
      const yEnd = yMax - ROAD_W / 2
      const dy = yEnd - yStart
      const count = Math.max(2, Math.floor(dy / TREE_SPACING))
      for (let j = 1; j < count; j++) {
        const t = j / count
        tryPlantTree(viewer, treeX, yStart + dy * t, 0.7 + rnd() * 0.4)
      }
    }
  }
  for (let i = 0; i < uniqueY.length - 1; i++) {
    const midY = (uniqueY[i] + uniqueY[i + 1]) / 2
    const crossesBuilding = positions.some((p, idx) => Math.abs(p.y - midY) < halfWs[idx])
    if (crossesBuilding) continue
    for (const sign of [-1, 1]) {
      const treeY = midY + sign * TREE_OFFSET
      const xStart = xMin + ROAD_W / 2
      const xEnd = xMax - ROAD_W / 2
      const dx = xEnd - xStart
      const count = Math.max(2, Math.floor(dx / TREE_SPACING))
      for (let j = 1; j < count; j++) {
        const t = j / count
        tryPlantTree(viewer, xStart + dx * t, treeY, 0.7 + rnd() * 0.4)
      }
    }
  }
}

function cleanup() {
  const viewer = viewerRef.value
  if (viewer && !viewer.isDestroyed?.()) {
    for (const e of entities) viewer.entities.remove(e)
  }
  entities = []
}

onMounted(() => {
  if (viewerRef.value) {
    build()
  } else {
    const stop = watch(viewerRef, (v) => {
      if (v) {
        build()
        stop()
      }
    })
  }
})

watch(
  () => props.buildings,
  () => build(),
  { deep: false },
)

onBeforeUnmount(cleanup)
</script>

<template>
  <div style="display: none" />
</template>

<style scoped lang="scss">
div { display: none; }
</style>
