<script setup lang="ts">
// ============================================================================
// CesiumViewer - CesiumJS 容器
// ----------------------------------------------------------------------------
// 职责:
//   1. 初始化 Cesium.Viewer (关掉地球 / 星空 / 太阳 / 月亮, 用 paper 色背景)
//   2. 摆相机: 在 (0,0) 经纬度上方 30° 斜俯视, 看园区原点
//   3. 网格地面: 自定义 GroundPrimitive + 网格纹理 (8px 工程图纸感)
//   4. 鼠标事件: hover / click 检测 entity -> parkStore.setHovered / setSelected
//   5. provide viewer 给子组件 (BuildingBlock / BuildingSplat / ...)
//   6. onUnmounted 销毁 viewer 释放 GPU 资源
//
// 本地坐标系实现:
//   - globe.show = false 关地球渲染, 但 Cesium 内部仍是 ECEF 坐标系
//   - 所有 building 放在 (0,0) 经纬度附近, 用 position(x,y) 米偏移 + z 高度
//   - 相机用 lookAt(Cartesian3.fromDegrees(0,0,0), HeadingPitchRange) 摆位
//   - 这样视觉上看到的是空旷 paper 色背景里的 3D 体块, 不依赖地球影像
//
// 不挂 entities 在这里:
//   体块 / splat / halo / energy badge 由子组件 BuildingBlock / BuildingSplat
//   等挂, 它们通过 inject('cesiumViewer') 拿到 viewer 实例。
//   CesiumViewer 只负责"舞台", 不负责"演员"。
// ============================================================================

import { onMounted, onBeforeUnmount, ref, watch, computed } from 'vue'
import * as Cesium from 'cesium'
import 'cesium/Build/Cesium/Widgets/widgets.css'
import { useParkStore } from '@/stores/park'
import type { SceneBuilding } from '@/api/visual'
import { computeGridLayout, resolveBuildingPosition, computeVisualHeight } from '@/utils/sceneTransform'

const props = defineProps<{
  buildings: SceneBuilding[]
}>()

const emit = defineEmits<{
  (e: 'ready', viewer: Cesium.Viewer): void
}>()

const park = useParkStore()

const containerRef = ref<HTMLDivElement | null>(null)
// viewer 用 ref 包, onMounted 后赋值。provide 由父组件 Park.vue 负责 (因为
// BuildingBlock 等是 CesiumViewer 的兄弟, 不是子组件, 这里 provide 它们 inject
// 不到)。CesiumViewer 通过 emit('ready', v) 把 viewer 实例回传给 Park.vue。
const viewer = ref<Cesium.Viewer | null>(null)

// 园区原点: 所有 building 相对这个点偏移
const SCENE_ORIGIN = Cesium.Cartesian3.fromDegrees(0, 0, 0)

// 网格布局 (position NULL 的楼自动摆位)
const gridLayout = computed(() => computeGridLayout(props.buildings))

// 将 (x, y, z) 米偏移转成 ECEF Cartesian3
// 用 eastNorthUpToFixedFrame 建本地坐标系, 然后 x=east, y=north, z=up
function localToEcef(x: number, y: number, z: number): Cesium.Cartesian3 {
  const transform = Cesium.Transforms.eastNorthUpToFixedFrame(SCENE_ORIGIN)
  const local = new Cesium.Cartesian3(x, y, z)
  return Cesium.Matrix4.multiplyByPoint(transform, local, new Cesium.Cartesian3())
}

function initViewer() {
  if (!containerRef.value) return

  let v: Cesium.Viewer
  try {
    v = new Cesium.Viewer(containerRef.value, {
      baseLayer: false,  // 不加载默认 ion 影像 (避免触发 token)
      baseLayerPicker: false,
      geocoder: false,
      homeButton: false,
      sceneModePicker: false,
      navigationHelpButton: false,
      animation: false,
      timeline: false,
      fullscreenButton: false,
      infoBox: false,
      selectionIndicator: false,
      creditContainer: document.createElement('div'),  // 隐藏 Cesium logo
    })
  } catch (e) {
    console.error('[CesiumViewer] Viewer construct failed:', e)
    return
  }
  viewer.value = v

  // 关掉地球 / 星空 / 大气 / 月亮, 保留太阳 (阴影需要)。
  // skyAtmosphere 必须关: globe.show=false 时它没有 globe 轮廓可依附, 渲染出
  // 黑白噪点带 + 弧形切边 (上一版踩过)。天空感改由"背景纯色 + CampusGround
  // 的超大渐变地平线圆盘"实现, 全走已验证的 polygon 路径, 不碰 alpha canvas
  // (alpha canvas 与 CSS 混合会把无几何区域混成黑底)。
  const scene = v.scene
  scene.globe.show = false
  if (scene.skyBox) scene.skyBox.show = false
  if (scene.skyAtmosphere) scene.skyAtmosphere.show = false
  // 启用 sun: shadowMap 需要光源, 且 sun 在地平线上方提供方向光
  if (scene.sun) scene.sun.show = true
  if (scene.moon) scene.moon.show = false
  // 背景 = 天顶浅蓝 (跟 CampusGround 地平线圆盘边缘色一致, 远处无缝融合)
  scene.backgroundColor = Cesium.Color.fromCssColorString('#C8D6E6')
  // 关掉大气散射让颜色更纯
  scene.fog.enabled = false

  // 启用 shadowMap: 让建筑 / 树投射阴影到地面 polygon, 解决"飘在空中"的视觉感
  // globe 关了之后, 需要 plot/site pad polygon 设 shadows: ENABLED 接收阴影。
  // shadowMap 默认 enabled=false, 这里开启; softShadows 让阴影边缘软化不硬边。
  v.shadowMap.enabled = true
  v.shadowMap.softShadows = true
  v.shadowMap.size = 2048  // 阴影贴图分辨率, 2048 平衡清晰度和性能

  // 光源时刻跟随用户本地时间 (小时:分钟), 阴影方向随早晚变化: 上午影子偏西北,
  // 下午偏东北。场景经度在 0°, 把本地时间当场景太阳时用, 直观对应墙上时钟。
  // 夜里 (8 点前 / 16 点后) 太阳在场景地平线下, 画面会整体变暗, 钳到 8~16 点
  // 保住白天光照 (钳制时段分钟固定 :30, 避免边界跳变)。
  // 日期固定 6 月中旬: 太阳直射北回归线附近, 光照角度最稳定, 不引入季节差异。
  const localH = new Date().getHours()
  const sunH = Math.min(16, Math.max(8, localH))
  const sunM = localH === sunH ? new Date().getMinutes() : 30
  v.clock.currentTime = Cesium.JulianDate.fromIso8601(
    `2024-06-15T${String(sunH).padStart(2, '0')}:${String(sunM).padStart(2, '0')}:00Z`,
  )

  ;(scene.screenSpaceCameraController as any).minimumZoomDistance = 10
  ;(scene.screenSpaceCameraController as any).maximumZoomDistance = 5000

  // 相机: 在原点上方 30° 斜俯视, 距离 500m (能看全 6 栋楼)
  v.camera.lookAt(
    SCENE_ORIGIN,
    new Cesium.HeadingPitchRange(
      0,                            // heading: 不偏航, 正北朝向
      Cesium.Math.toRadians(-30),   // pitch: -30° 斜俯视 (0 平视, -90 垂直俯瞰)
      500,                          // range: 距原点 500m
    ),
  )

  // 开启 clock 动画: Cesium 默认 shouldAnimate = false, clock.currentTime 不走动,
  // BuildingBlock 的 CallbackProperty 拿到的 time 参数永远是同一个值,
  // secondsDifference 永远 = 0, 入场动画 elapsedMs <= 0 高度卡在 0, 楼变成纸片。
  // 开启后 currentTime 实时推进, 入场动画能正常从 0 生长到 fullH。
  v.clock.shouldAnimate = true

// 网格地面: 用 canvas 画网格纹理, 通过 ImageMaterialProperty 喂给 polygon
// ----------------------------------------------------------------------------
// 这里不能用 new Cesium.Material({ fabric: { type: 'Grid' } }) 然后塞给 entity
// 的 material 字段。Entity API 的 material 字段只接受 MaterialProperty
// (ColorMaterialProperty / ImageMaterialProperty / ...) 或纯 Color / string。
// 直接塞 Material 实例会触发 createMaterialProperty 抛 "Unable to infer material
// type" DeveloperError, 导致整个 viewer 初始化失败 (中央 canvas 不渲染)。
// 解决: canvas 画 16x16 网格, ImageMaterialProperty.repeat 设 20x20 让网格在
// 1000m 平面上重复 20 次, 即每格 50m, 跟原来 type:'Grid' 视觉一致。
function createGridCanvas(): HTMLCanvasElement {
  const c = document.createElement('canvas')
  c.width = 256
  c.height = 256
  const ctx = c.getContext('2d')!
  // 透明背景 (让 paper 色透出)
  ctx.clearRect(0, 0, 256, 256)
  // 网格线 #D1CEC5
  ctx.strokeStyle = '#D1CEC5'
  ctx.lineWidth = 1
  // 16x16 = 每格 16px, repeat 20 次后每格对应 1000m / (16*20) = ~3m, 太密
  // 改成 4x4 = 每格 64px, repeat 20 次后每格 1000m / (4*20) = 12.5m, 接近原 20m
  const cells = 4
  const cellSize = 256 / cells
  for (let i = 0; i <= cells; i++) {
    const p = i * cellSize
    ctx.beginPath(); ctx.moveTo(p + 0.5, 0); ctx.lineTo(p + 0.5, 256); ctx.stroke()
    ctx.beginPath(); ctx.moveTo(0, p + 0.5); ctx.lineTo(256, p + 0.5); ctx.stroke()
  }
  return c
}

// 地面 primitive: 1000x1000m 平面, z=0.01 略高于 0 避免与 building 底面 z-fighting
const groundPositions = [
  localToEcef(-500, -500, 0.01),
  localToEcef(500, -500, 0.01),
  localToEcef(500, 500, 0.01),
  localToEcef(-500, 500, 0.01),
]
v.entities.add({
  id: 'scene-ground',
  polygon: {
    hierarchy: new Cesium.PolygonHierarchy(groundPositions),
    // ImageMaterialProperty 接受 canvas 作为 image, 配 repeat 控制密度
    material: new Cesium.ImageMaterialProperty({
      image: createGridCanvas(),
      repeat: new Cesium.Cartesian2(20, 20),
      transparent: true,
    }),
  },
})

  // 刻度尺: 沿 X 轴每 50m 一格刻度 (用 polyline 模拟)
  // 简单做法: 沿 X 轴放 10 个小盒子作为刻度
  for (let i = -5; i <= 5; i++) {
    const x = i * 100
    v.entities.add({
      id: `ruler-${i}`,
      position: localToEcef(x, 540, 0.1),
      box: {
        dimensions: new Cesium.Cartesian3(2, 2, 0.5),
        material: Cesium.Color.fromCssColorString('#8A8275'),
      },
    })
  }

  // 鼠标事件
  setupMouseEvents(v)

  // 通知父组件 viewer 就绪
  emit('ready', v)
}

function setupMouseEvents(v: Cesium.Viewer) {
  const handler = new Cesium.ScreenSpaceEventHandler(v.canvas)

  // 从 pick 列表里找第一个挂了 building_id 的 entity
  // splat 模式下点击会同时命中 3D Tiles (primitive, id=undefined) + 透明 box (entity,
  // properties.building_id), drillPick 穿透拿所有 hit, 找到带 building_id 的那个
  function pickBuildingId(windowPos: Cesium.Cartesian2): string | null {
    const picks = v.scene.drillPick(windowPos)
    for (const p of picks) {
      const ent = p.id as Cesium.Entity | undefined
      if (ent && (ent as any).properties?.building_id?.getValue?.()) {
        return (ent as any).properties.building_id.getValue()
      }
    }
    return null
  }

  // hover: 检测鼠标下的 entity, 拿 building_id
  handler.setInputAction((movement: any) => {
    const bid = pickBuildingId(movement.endPosition)
    if (bid) {
      park.setHovered(bid)
      return
    }
    park.setHovered(null)
  }, Cesium.ScreenSpaceEventType.MOUSE_MOVE)

  // click: 选中
  handler.setInputAction((click: any) => {
    const bid = pickBuildingId(click.position)
    if (bid) {
      park.setSelected(bid)
      return
    }
    park.setSelected(null)
  }, Cesium.ScreenSpaceEventType.LEFT_CLICK)
}

let resizeObserver: ResizeObserver | null = null

onMounted(() => {
  initViewer()
  // 监听容器尺寸变化, 强制 viewer resize
  if (containerRef.value && window.ResizeObserver) {
    resizeObserver = new ResizeObserver(() => {
      viewer.value?.resize()
    })
    resizeObserver.observe(containerRef.value)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  if (viewer.value) {
    viewer.value.destroy()
    viewer.value = null
  }
})

// buildings 变化时不重建 viewer (子组件自己 watch buildings 增删 entity)
watch(() => props.buildings, () => {
  // 触发 gridLayout 重算 (computed 自动)
}, { deep: false })

defineExpose({
  getViewer: () => viewer.value,
  // 聚焦某栋楼: 相机飞到能框住这栋楼的位置, 之后左键拖拽就绕这栋楼转。
  // 用 flyToBoundingSphere + offset(range=0 自动框住) 而不是手算相机位置,
  // 因为手算 heading/pitch/distance 转 ENU 容易错, bounding sphere 由 Cesium 自己算。
  flyToBuilding: (buildingId: string) => {
    const v = viewer.value
    if (!v) return
    const b = props.buildings.find(b => b.building_id === buildingId)
    if (!b) return
    const pos = resolveBuildingPosition(b, gridLayout.value)
    const L = b.dimensions.length_m ?? 30
    const W = b.dimensions.width_m ?? 20
    const H = computeVisualHeight(b)
    // 包围球中心取视觉高度 0.45 处 (略偏下, 让镜头更多看到立面而不是屋顶)
    const center = localToEcef(pos.x, pos.y, H * 0.45)
    const radius = Math.hypot(L, W, H) / 2
    v.camera.flyToBoundingSphere(new Cesium.BoundingSphere(center, radius), {
      duration: 1.0,
      offset: new Cesium.HeadingPitchRange(
        Cesium.Math.toRadians(-35),   // heading: 从西南侧看, 3/4 视角
        Cesium.Math.toRadians(-30),   // pitch: 斜俯视
        0,                            // range=0 自动计算能框住球的距离
      ),
    })
  },
})
</script>

<template>
  <div ref="containerRef" class="cesium-viewer" />
</template>

<style scoped lang="scss">
.cesium-viewer {
  width: 100%;
  height: 100%;
  position: relative;

  // 隐藏 Cesium 默认 toolbar (即使关掉了 widget 仍可能有残留)
  :deep(.cesium-viewer-toolbar) { display: none; }
  :deep(.cesium-widget-credits) { display: none; }

  // Cesium canvas 抗锯齿
  :deep(canvas) {
    outline: none;
    display: block;
  }
}
</style>
