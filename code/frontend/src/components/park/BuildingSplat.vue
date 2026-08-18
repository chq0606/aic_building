<script setup lang="ts">
// ============================================================================
// BuildingSplat - 真实 3D 高斯泼溅渲染 (Cesium 原生 3D Tiles + KHR_gaussian_splatting)
// ----------------------------------------------------------------------------
// 三种渲染路径, 按 building.has_tiles 和 mock 模式优先级选择:
//
//   1. 真实 3D Tiles (优先): building.has_tiles=true 且 model_id 存在
//      - 后端 worker 用 3dgs-ply-3dtiles-converter 把 .ply 转 3D Tiles
//      - 前端 Cesium3DTileset.fromUrl(tileset.json) 加载, Cesium 内部自动
//        解析 KHR_gaussian_splatting GLB, 用真椭球 splat 渲染 (含 LOD)
//      - modelMatrix 把 tileset 摆到 building.position, 旋转对齐 ENU
//
//   2. .ply 静态点云降级: has_tiles=false (转换失败 / 老记录) 且非 mock
//      - fetch .ply -> 解析 17 字段顶点 -> PointPrimitive 渲染
//      - 视觉比 3D Tiles 差 (没 LOD / 没真椭球), 但保证有画面
//
//   3. mock .ply (dev 测试): shouldUseMockSplat(building)=true
//      - 走 .ply 路径但用 /mock/mock_splat.ply, 不需要鉴权
//      - .env.local 设 VITE_MOCK_SPLAT_BUILDING=Tammy 强制某楼走 mock
//
// 鉴权: 后端 /splat-tiles/{path} 和 /splat.ply 都走 JWT。3D Tiles 用
// Cesium.Resource + headers 带 Authorization (Cesium 自动传给子 tile 请求);
// .ply 走 fetch + Authorization 拿 blob URL。
//
// 坐标系 (两套, 不同路径用不同处理):
//
//   3D Tiles 路径 - tileset 局部已经是 tile Z-up (= ENU 直接映射):
//     - converter 把 .ply (camera Y-down Z-forward) 转 glTF Y-up 写进 glb
//     - Cesium 加载 3D Tiles 1.1 时自动把 glTF Y-up 转成 tile Z-up
//     - tile Z-up: X=length (east), Y=width (north), Z=height (up) = ENU
//     - modelMatrix 只需 ENU 平移, 不需要额外 rotation
//     - 验证: build_summary.json 的 bounding box 显示 Z 半轴 = height_m
//
//   .ply 路径 - .ply 顶点是 camera Y-down Z-forward (TripoSplat 原始输出):
//     - calibrate_ply_scale 只缩放+平移, 不旋转, .ply 保留原始朝向
//     - camera -> ENU 逐点变换 (renderPly 里):
//         camera +X (right)   -> ENU +X (east)   [不变]
//         camera +Y (down)    -> ENU -Z (down)   [Y -> -Z]
//         camera +Z (forward) -> ENU +Y (north)  [Z -> +Y]
//
//   平移目标: (pos.x, pos.y, height/2) 让 splat 中心在楼高一半 (底部贴地)
// ============================================================================

import { inject, ref, watch, onMounted, onBeforeUnmount, computed, type Ref } from 'vue'
import * as Cesium from 'cesium'
import type { SceneBuilding } from '@/api/visual'
import { useParkStore } from '@/stores/park'
import {
  resolveBuildingPosition,
  shouldUseMockSplat,
  MOCK_SPLAT_URL,
  computeVisualHeight,
  type GridPosition,
} from '@/utils/sceneTransform'
import { buildSplatTilesResource } from '@/api/visual'

const props = defineProps<{
  building: SceneBuilding
  index: number
}>()

const park = useParkStore()

const viewerRef = inject<Ref<Cesium.Viewer | null>>('cesiumViewer', ref(null))
const localToEcef = inject<(x: number, y: number, z: number) => Cesium.Cartesian3>('localToEcef', () => new Cesium.Cartesian3())
const gridLayoutRef = inject<Ref<Map<string, GridPosition>>>('gridLayout', ref(new Map()))

// 当前活跃的 Cesium 资源 (3D Tiles 或 PointPrimitiveCollection), 卸载时清掉
let activeTileset: Cesium.Cesium3DTileset | null = null
let pointCollection: Cesium.PointPrimitiveCollection | null = null

const loading = ref(false)
const error = ref<string | null>(null)
const renderMode = ref<'tiles' | 'ply' | 'mock' | null>(null)
const pointCount = ref(0)

// mock 模式强制走 .ply (没有 mock 的 3D Tiles)
const isSplatMode = computed(() => shouldUseMockSplat(props.building) || props.building.model_kind === 'splat')

// 真实 3D Tiles 可用: has_tiles=true 且非 mock 且有 model_id
const useRealTiles = computed(() =>
  !shouldUseMockSplat(props.building) &&
  props.building.has_tiles === true &&
  !!props.building.model_id,
)

const position = computed<GridPosition>(() => {
  return resolveBuildingPosition(props.building, gridLayoutRef.value)
})

// splat 中心高度偏移: 跟 BuildingBlock 共用 computeVisualHeight (含 Z_VISUAL_SCALE
// 和 minHeight 兜底), 让 splat 视觉高度跟 BuildingBlock 一致。
// 之前直接用 (height_m ?? 20) / 2 没乘 Z_VISUAL_SCALE, splat 比 BuildingBlock 矮
// 一截, AnomalyHalo (用 computeVisualHeight 算 z) 也飘在 splat 屋顶上方悬空。
const centerHeight = computed(() => computeVisualHeight(props.building) / 2)

// splat z 方向视觉缩放比例: computeVisualHeight / actualHeight
// BuildingBlock 的 z 走 computeVisualHeight (放大 2.5 倍 + 兜底), splat 后端
// calibrate_ply_scale 输出的是真实尺寸 (没放大), 前端 modelMatrix 给 z 乘这个
// 比例让 splat 视觉高度跟 BuildingBlock 对齐, halo 才能贴屋顶。
// xy 不放大 (footprint 保持真实尺寸), 只拔高。
// 注意: 这会让 splat Gaussian 椭球在 z 方向被拉长, 但比 splat 矮一截 + halo
// 悬空更糟, 视觉对齐优先。
const zVisualScale = computed(() => {
  const actualHeight = props.building.dimensions.height_m ?? 20
  return computeVisualHeight(props.building) / actualHeight
})

// splat xy 方向视觉补偿系数
//   后端 calibrate_ply_scale 修复轴映射 bug 后 (camera Y->height, camera Z->width),
//   splat 的 xy 已经是真实的 length × width, 跟 BuildingBlock footprint 一致,
//   不需要前端补偿。保留这个常量是为了将来如果发现照片背景占比大导致建筑主体
//   比bbox 小一圈时, 可以微调 (1.1-1.2) 做视觉补偿。
//   之前 1.5 是为了压住 calibrate bug 的视觉症状临时加的, 根因修了就回退到 1.0。
const SPLAT_XY_VISUAL_SCALE = 1.0

// 水平旋转角度 (度)。后端 building_visual_model.yaw_deg, 默认 0 不旋转。
// 3D Tiles 路径在 modelMatrix 里乘 R_z (绕 tile Z-up 轴, 即 ENU "上"方向);
// .ply 路径在逐点变换里把 (enuX, enuY) 做 2D 旋转。
const yawDeg = computed(() => {
  const v = props.building.position?.yaw_deg
  return (v ?? 0) as number
})

// ---- 3D Tiles 渲染 (优先路径) ----
async function renderTiles() {
  const viewer = viewerRef.value
  if (!viewer) return
  const modelId = props.building.model_id
  if (!modelId) {
    error.value = 'model_id 缺失, 无法加载 3D Tiles'
    return
  }

  loading.value = true
  error.value = null

  try {
    // 清掉旧的渲染产物 (不管是 tileset 还是 pointCollection)
    cleanupActive(viewer)

    const url = buildSplatTilesResource(props.building.building_id, modelId)
    const token = localStorage.getItem('aic_access_token')

    // 用 Resource 包 URL, headers 带 Authorization。Cesium 内部 fetch 子 tile
    // (tiles/{level}/{x}.glb) 时会复用同一份 headers, 不需要在每个请求重复设。
    const resource = new Cesium.Resource({
      url,
      headers: token ? { Authorization: `Bearer ${token}` } : undefined,
    })

    const tileset = await Cesium.Cesium3DTileset.fromUrl(resource, {
      // 关掉 Cesium ion token 检查 (我们走自己后端, 不用 ion 服务)
      // 不设 show: false 让 tileset 立即可见, 加载完直接显示
    })

    // 应用 modelMatrix: 把 tileset 摆到 building 位置 + 旋转对齐 ENU
    // + yaw_deg 水平旋转 (绕 ENU Z 轴, 让用户调整建筑朝向)
    // + z 方向乘 zVisualScale 让 splat 视觉高度跟 BuildingBlock 对齐
    // + xy 方向乘 SPLAT_XY_VISUAL_SCALE 补偿 .ply 里建筑主体占 bbox 一部分的视觉损失
    tileset.modelMatrix = computeSplatModelMatrix(
      position.value,
      centerHeight.value,
      zVisualScale.value,
      SPLAT_XY_VISUAL_SCALE,
      yawDeg.value,
    )

    // 维持 tileset 自身的大小: 3D Tiles 已经按真实米单位 (calibrate_ply_scale
    // 后的 .ply 转出来), 不要让 Cesium 再缩放。
    // 默认 Cesium3DTileset 不缩放, 这里不设 scale。

    viewer.scene.primitives.add(tileset)
    activeTileset = tileset
    renderMode.value = 'tiles'

    // tile 加载完触发 initialTilesLoaded, 这里只 log 一下
    tileset.initialTilesLoaded.addEventListener(() => {
      // eslint-disable-next-line no-console
      console.debug('[BuildingSplat] 3D Tiles initial load done:', props.building.building_code)
    })
  } catch (e: any) {
    error.value = e?.message ?? String(e)
    // eslint-disable-next-line no-console
    console.warn('[BuildingSplat] 3D Tiles 加载失败, 尝试降级到 .ply:', e)
    // 降级到 .ply (可能后端转换失败导致 tileset.json 404)
    await renderPly()
  } finally {
    loading.value = false
  }
}

// ---- .ply 静态点云渲染 (降级路径 / mock 路径) ----
async function renderPly() {
  const viewer = viewerRef.value
  if (!viewer) return

  loading.value = true
  error.value = null

  try {
    const isMock = shouldUseMockSplat(props.building)
    const modelId = props.building.model_id

    let url: string
    let useAuth: boolean
    if (isMock) {
      url = MOCK_SPLAT_URL
      useAuth = false
      renderMode.value = 'mock'
    } else if (modelId) {
      // 真实 .ply: 走 fetch + Authorization 拿 blob URL (避免 Cesium Resource
      // 套娃, .ply 解析在主线程做, 32K 顶点 < 100ms)
      const token = localStorage.getItem('aic_access_token')
      const resp = await fetch(
        `/api/v1/buildings/${props.building.building_id}/visual-models/${modelId}/splat.ply`,
        { headers: token ? { Authorization: `Bearer ${token}` } : undefined },
      )
      if (!resp.ok) throw new Error(`splat .ply 加载失败: ${resp.status} ${resp.statusText}`)
      const blob = await resp.blob()
      url = URL.createObjectURL(blob)
      useAuth = false  // blob URL 自带权限, 不再需要 Authorization
      renderMode.value = 'ply'
    } else {
      error.value = '无 model_id, 无法加载 .ply'
      return
    }

    const points = await loadPly(url)

    // 清掉旧的
    cleanupActive(viewer)

    pointCollection = new Cesium.PointPrimitiveCollection()
    const pos = position.value
    const cz = centerHeight.value
    const zScale = zVisualScale.value
    const xyScale = SPLAT_XY_VISUAL_SCALE
    // yaw 转 2D 旋转矩阵 (绕 ENU Z 轴), 让 splat 水平旋转
    //   enuX' = enuX * cos(yaw) - enuY * sin(yaw)
    //   enuY' = enuX * sin(yaw) + enuY * cos(yaw)
    // 跟 3D Tiles 路径的 R_z 数学等价 (rotateZ 在 XY 平面上做同样的 2D 旋转)
    const yawRad = (yawDeg.value * Math.PI) / 180
    const cosY = Math.cos(yawRad)
    const sinY = Math.sin(yawRad)

    // .ply 顶点是 camera 坐标系 (X right, Y down, Z forward)
    // 转 ENU (X east, Y north, Z up) 再加楼位置偏移:
    //   ENU.x = p.x        (camera X right -> ENU X east)
    //   ENU.y = p.z        (camera Z forward -> ENU Y north)
    //   ENU.z = -p.y       (camera Y down -> ENU -Z down, 即取反变 up)
    // z 方向乘 zScale 让 splat 视觉高度跟 BuildingBlock 对齐 (跟 3D Tiles 路径
    // 的 modelMatrix z scale 一致), xy 方向乘 xyScale 补偿 .ply 建筑主体占
    // bbox 一部分的视觉损失。之前直接 p.x/p.y/p.z 当 ENU 偏移是错的 (Y 当
    // north 实际是 down, Z 当 up 实际是 forward), 修了之后 .ply 路径朝向
    // 跟 3D Tiles 一致。yaw 旋转在 xy 平面应用 (ENU X/Y 2D 旋转), z 不动。
    for (const p of points) {
      const enuX0 = p.x * xyScale
      const enuY0 = p.z * xyScale
      const enuZ = -p.y * zScale
      const enuX = enuX0 * cosY - enuY0 * sinY
      const enuY = enuX0 * sinY + enuY0 * cosY
      const worldPos = localToEcef(pos.x + enuX, pos.y + enuY, cz + enuZ)
      pointCollection.add({
        position: worldPos,
        color: new Cesium.Color(p.r / 255, p.g / 255, p.b / 255, p.a),
        pixelSize: p.size,
        id: `splat-${props.building.building_id}`,
        disableDepthTestDistance: Number.POSITIVE_INFINITY,
      })
    }

    viewer.scene.primitives.add(pointCollection)
    pointCount.value = points.length
  } catch (e: any) {
    error.value = e?.message ?? String(e)
    // eslint-disable-next-line no-console
    console.warn('[BuildingSplat] .ply 渲染失败:', e)
  } finally {
    loading.value = false
  }
}

// ---- 主渲染入口: 按 useRealTiles 分流 ----
async function renderSplat() {
  if (!isSplatMode.value) return
  if (useRealTiles.value) {
    await renderTiles()
  } else {
    await renderPly()
  }
}

// ---- 计算 splat modelMatrix ----
//   3D Tiles 1.1 规范: converter 输出 glb 是 glTF Y-up, Cesium 运行时自动
//   把 glTF Y-up 转成 tile Z-up。所以 tileset 局部坐标系已经是 Z-up
//   (X east, Y north, Z up), 直接等于 ENU, 不需要额外 rotation。
//
//   之前误以为 tile 内容还是 glTF Y-up 加了一层 rotation, 结果把 tile 的
//   Y(north) 转到 ENU Z(up), 建筑就侧躺了 (屋顶朝南, 镜头方向)。
//
//   yaw_deg 水平旋转 (Step 13): 在 ENU 平移和 tile 局部缩放之间插入 R_z
//   (绕 tile Z-up 轴, 即 ENU "上"方向), 让用户调整建筑朝向。矩阵乘法顺序:
//     modelMatrix = ENU * R_z(yaw) * Scale
//   先 Scale (tile 局部 xyz 缩放), 再 R_z (水平旋转), 最后 ENU 平移到位置。
//   R_z 不影响 z 方向 (只旋转 XY 平面), 所以 zVisualScale 仍只作用在 z 轴。
//
//   z 方向乘 zVisualScale: BuildingBlock 的 z 走 computeVisualHeight (放大 2.5 倍
//   + minHeight 兜底), splat 后端输出真实尺寸 (没放大), 这里给 z 乘同样比例
//   让 splat 视觉高度跟 BuildingBlock 对齐, halo 才能贴屋顶。xy 不放大。
//
//   验证: build_summary.json 的 bounding box 显示
//   X 半轴 40m (length), Y 半轴 27.5m (width), Z 半轴 11.9m (height),
//   说明 tile Z-up, Z 是建筑高度方向。
function computeSplatModelMatrix(
  pos: GridPosition,
  cz: number,
  zScale: number,
  xyScale: number = 1,
  yawDeg: number = 0,
): Cesium.Matrix4 {
  // 平移: 把 tileset 局部原点 (0,0,0) 对到园区的 (pos.x, pos.y, cz)
  // tile 局部 (X,Y,Z) = ENU (east, north, up), 直接用 eastNorthUpToFixedFrame
  const ecefPosition = localToEcef(pos.x, pos.y, cz)
  const baseMatrix = Cesium.Transforms.eastNorthUpToFixedFrame(ecefPosition)
  // yaw 旋转 (绕 tile Z-up = ENU up): 只在 XY 平面旋转, z 不动
  const yawRad = (yawDeg * Math.PI) / 180
  const rotationMatrix = Cesium.Matrix4.fromRotationTranslation(
    Cesium.Matrix3.fromRotationZ(yawRad),
    Cesium.Cartesian3.ZERO,
    new Cesium.Matrix4(),
  )
  // xyz 方向分别缩放: z 用 zScale 跟 BuildingBlock 对齐, xy 用 xyScale 补偿
  // .ply 里建筑主体占 bbox 一部分的视觉损失
  const scaleMatrix = Cesium.Matrix4.fromScale(
    new Cesium.Cartesian3(xyScale, xyScale, zScale),
  )
  // modelMatrix = ENU * R_z * Scale (右结合: 先 Scale 再 R_z 再 ENU)
  const scaled = Cesium.Matrix4.multiply(rotationMatrix, scaleMatrix, new Cesium.Matrix4())
  return Cesium.Matrix4.multiply(baseMatrix, scaled, new Cesium.Matrix4())
}

// ---- .ply 解析 (3DGS 标准 17 字段) ----
//   x/y/z      - 位置 (米单位, 相对楼中心)
//   nx/ny/nz   - 法向量 (不影响渲染, 跳过)
//   f_dc_0/1/2 - SH 0 阶系数 (颜色 = f_dc / 0.2821 * 255)
//   opacity    - logit 空间, alpha = 1/(1+exp(-opacity))
//   scale_0/1/2 - log 空间方差半径, 实际值 = exp(scale)
//   rot_0/1/2/3 - 四元数 (这里不用)
async function loadPly(url: string): Promise<Array<{
  x: number; y: number; z: number
  r: number; g: number; b: number; a: number
  size: number
}>> {
  const res = await fetch(url)
  if (!res.ok) throw new Error(`加载 splat 失败: ${res.status}`)
  const buf = await res.arrayBuffer()

  const headerBytes = new Uint8Array(buf)
  let headerEnd = 0
  for (let i = 0; i < headerBytes.length - 10; i++) {
    if (
      headerBytes[i] === 0x65 && headerBytes[i + 1] === 0x6e &&
      headerBytes[i + 2] === 0x64 && headerBytes[i + 3] === 0x5f &&
      headerBytes[i + 4] === 0x68 && headerBytes[i + 5] === 0x65 &&
      headerBytes[i + 6] === 0x61 && headerBytes[i + 7] === 0x64 &&
      headerBytes[i + 8] === 0x65 && headerBytes[i + 9] === 0x72 &&
      headerBytes[i + 10] === 0x0a
    ) {
      headerEnd = i + 11
      break
    }
  }
  if (headerEnd === 0) throw new Error('.ply header 解析失败: 找不到 end_header')

  const headerText = new TextDecoder().decode(headerBytes.slice(0, headerEnd))
  const countMatch = headerText.match(/element vertex (\d+)/)
  if (!countMatch) throw new Error('.ply header 缺少 vertex 数量')
  const vertexCount = parseInt(countMatch[1], 10)

  const FLOATS_PER_VERTEX = 17
  const BYTES_PER_VERTEX = FLOATS_PER_VERTEX * 4
  const view = new DataView(buf, headerEnd)

  const points: Array<{
    x: number; y: number; z: number
    r: number; g: number; b: number; a: number
    size: number
  }> = []

  const SH_C0 = 0.2821

  for (let i = 0; i < vertexCount; i++) {
    const offset = i * BYTES_PER_VERTEX
    const x = view.getFloat32(offset + 0, true)
    const y = view.getFloat32(offset + 4, true)
    const z = view.getFloat32(offset + 8, true)
    const f_dc_0 = view.getFloat32(offset + 24, true)
    const f_dc_1 = view.getFloat32(offset + 28, true)
    const f_dc_2 = view.getFloat32(offset + 32, true)
    const opacityLogit = view.getFloat32(offset + 36, true)
    const scale_0 = view.getFloat32(offset + 40, true)
    const scale_1 = view.getFloat32(offset + 44, true)
    const scale_2 = view.getFloat32(offset + 48, true)

    const r = Math.max(0, Math.min(255, Math.round(f_dc_0 / SH_C0 * 255)))
    const g = Math.max(0, Math.min(255, Math.round(f_dc_1 / SH_C0 * 255)))
    const b = Math.max(0, Math.min(255, Math.round(f_dc_2 / SH_C0 * 255)))
    const a = 1 / (1 + Math.exp(-opacityLogit))
    const sizeActual = Math.exp((scale_0 + scale_1 + scale_2) / 3)
    const pixelSize = Math.max(4, Math.min(14, sizeActual * 20 + 4))

    points.push({ x, y, z, r, g, b, a, size: pixelSize })
  }

  return points
}

// ---- 清掉当前活跃的渲染产物 ----
function cleanupActive(viewer: Cesium.Viewer) {
  if (activeTileset) {
    try {
      viewer.scene.primitives.remove(activeTileset)
    } catch (e) {
      // 静默忽略 (viewer 已销毁等)
    }
    activeTileset = null
  }
  if (pointCollection) {
    try {
      viewer.scene.primitives.remove(pointCollection)
    } catch (e) {
      // 静默忽略
    }
    pointCollection = null
  }
  pointCount.value = 0
}

onMounted(() => {
  // Vue 3 子先父后: viewerRef.value 此时可能仍是 null, watch 等就绪再渲染
  if (viewerRef.value) {
    if (isSplatMode.value) renderSplat()
  } else {
    const stop = watch(viewerRef, (v) => {
      if (v) {
        if (isSplatMode.value) renderSplat()
        stop()
      }
    })
  }
})

onBeforeUnmount(() => {
  const viewer = viewerRef.value
  if (viewer && !viewer.isDestroyed?.()) {
    cleanupActive(viewer)
  } else {
    // viewer 已销毁, 直接清引用让 GC
    activeTileset = null
    pointCollection = null
  }
})

// mock 切换 / model_id 变化 / has_tiles 变化 / yaw 变化时重新渲染
// yaw 变化时: 3D Tiles 路径只需更新 modelMatrix (不重载 tileset, 快);
// .ply 路径要重算所有点位置 (没法部分更新), 走完整 renderSplat。
watch(
  () => [props.building.model_id, props.building.has_tiles, isSplatMode.value],
  () => {
    if (isSplatMode.value) renderSplat()
  },
)

watch(
  () => yawDeg.value,
  (newYaw) => {
    if (!isSplatMode.value) return
    if (activeTileset) {
      // 3D Tiles 路径: 只更新 modelMatrix, 不重载 (毫秒级, 滑块拖动流畅)
      activeTileset.modelMatrix = computeSplatModelMatrix(
        position.value,
        centerHeight.value,
        zVisualScale.value,
        SPLAT_XY_VISUAL_SCALE,
        newYaw,
      )
    } else {
      // .ply 路径或还没加载完: 重渲 (点位置要重算)
      renderSplat()
    }
  },
)
</script>

<template>
  <!-- splat 不渲染 DOM, 纯 Cesium primitive -->
  <div style="display: none" />
</template>

<style scoped lang="scss">
div { display: none; }
</style>
