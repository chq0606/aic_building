// ============================================================================
// sceneTransform.ts - Cesium 本地坐标 + 颜色映射 + 网格布局辅助
// ----------------------------------------------------------------------------
// 这里集中放园区 3D 探索页的纯计算函数 (不依赖 Cesium 实例):
//   1. 颜色映射: level (low/mid/high/critical) -> hex 色
//   2. 网格布局: scene API 返的 position 为 NULL 时, 按 building 顺序自动排到 2D 网格
//   3. 异常 halo 颜色: severity_max (LOW/MEDIUM/HIGH) -> hex 色
//   4. mock splat 切换: 环境变量 VITE_MOCK_SPLAT_BUILDING 让特定楼强制走 splat
//
// Cesium 实例相关 (Viewer / Entity / Cartesian3) 不放这里, 放 CesiumViewer.vue
// 和 BuildingBlock.vue 里, 因为这些是命令式 API, 不适合抽成纯函数。
// ============================================================================

import type { ColorLevel, AnomalySeverity, SceneBuilding, ModelKind } from '@/api/visual'

// ---------------------------------------------------------------------------
// z 轴视觉放大系数
//   BDG2 数据集 sqm 是总建筑面积, 按 footprint = sqm/floors 算出来 footprint 仍很大,
//   配 floors*3.5m 层高视觉扁平如纸。渲染时给 z 乘这个系数让建筑挺拔, 后端数据不动。
//   BuildingBlock (楼高) 和 AnomalyHalo (光晕高度) 都用这个系数, 保证 halo 贴屋顶。
//
//   2.5 这个值是按真实建筑典型 L:H 比例校准的:
//     - 低层 (1-2f) 真实 L:H 多在 3-5:1 (办公/仓储偏宽)
//     - 中层 (3-5f) 真实 L:H 多在 1.5-3:1
//     - 高层 (6f+) 真实 L:H 多在 0.5-2:1
//   BDG2 估算层高 3.5m 配 footprint 太扁 (1 层 Franklin 150×100×3.5 = 43:1 纸片),
//   乘 2.5 后大部分楼落在健康区间, 仍偏扁的走 computeVisualHeight 的 minHeight 兜底。
// ---------------------------------------------------------------------------
export const Z_VISUAL_SCALE = 2.5

// ---------------------------------------------------------------------------
// 计算建筑视觉渲染高度 (米)
//   线性放大不够: 1 层 Franklin 3.5m*2.5=8.75m 配 150x100 footprint 仍像纸片。
//   给一个按层数递增的最小视觉高度兜底, 让所有楼都"挺拔"。
//   规则: max(实际高度*2.5, 25 + floors*6)
//   - 1 层: 至少 31m (Franklin 150x100x31, L:H=4.8 健康)
//   - 2 层: 至少 37m (Alissa 91x61x37, L:H=2.5 健康)
//   - 4 层: 至少 49m (Seth 80x54x49, L:H=1.6 健康)
//   - 6 层: 至少 61m, 但实际 21*2.5=52.5 < 61, 取 61 (Tammy 50x34x61, L:H=0.8 偏瘦但合理)
//   BuildingBlock (主体高度) 和 AnomalyHalo (halo z) 共用此函数, 保证 halo 贴屋顶
// ---------------------------------------------------------------------------
export function computeVisualHeight(building: SceneBuilding): number {
  const actualHeight = (building.dimensions.height_m ?? 20) * Z_VISUAL_SCALE
  const floors = building.dimensions.floors_count ?? 1
  const minHeight = 25 + floors * 6
  return Math.max(actualHeight, minHeight)
}

// ---------------------------------------------------------------------------
// 颜色映射 (来自 tokens.scss 色板)
//   低能耗翠绿 / 中能耗暖灰 / 高能耗琥珀 / 异常深红
//   返 hex 字符串, Cesium Color.fromCssColorString 直接吃
// ---------------------------------------------------------------------------

export const LEVEL_COLOR: Record<ColorLevel, string> = {
  low: '#3D7E6A',       // 翠绿 (节能)
  mid: '#4A4A4A',       // 暖灰 (中等)
  high: '#D49B3B',      // 琥珀 (偏高)
  critical: '#B84A3C',  // 赤陶 (异常严重)
}

export const LEVEL_LABEL: Record<ColorLevel, string> = {
  low: '节能',
  mid: '中等',
  high: '偏高',
  critical: '严重',
}

export function levelToColor(level: ColorLevel): string {
  return LEVEL_COLOR[level] ?? LEVEL_COLOR.mid
}

// ---------------------------------------------------------------------------
// 异常 halo 颜色
//   severity_max 来自 scene API 的 anomaly_status.severity_max
//   null 表示无异常 (不应渲染 halo)
// ---------------------------------------------------------------------------

export const SEVERITY_COLOR: Record<AnomalySeverity, string> = {
  LOW: '#3D7E6A',
  MEDIUM: '#D49B3B',
  HIGH: '#B84A3C',
}

export function severityToColor(s: AnomalySeverity): string {
  return SEVERITY_COLOR[s] ?? SEVERITY_COLOR.MEDIUM
}

// ---------------------------------------------------------------------------
// 网格布局 - position 为 NULL 时自动摆位
//   6 栋楼按 2 列 3 行排, 间距取所有楼最大 length 的 1.8 倍
//   返回 (x, y) 米单位, 后端 position 优先级 > 网格兜底
//
//   算法:
//     1. 拿到所有 building 的 dimensions (estimated 模式可能 length=None, 走默认 30m)
//     2. 找最大 length 作为列宽 + 间距, 最大 width 作为行高 + 间距
//     3. 按顺序填网格, 每 rowCols 个换行
//     4. 整体居中: 减去总宽/2 让园区中心在 (0,0)
// ---------------------------------------------------------------------------

export interface GridPosition {
  x: number
  y: number
}

export function computeGridLayout(
  buildings: SceneBuilding[],
  options: { rowCols?: number; gapFactor?: number } = {},
): Map<string, GridPosition> {
  const { rowCols = 3, gapFactor = 1.8 } = options
  const result = new Map<string, GridPosition>()
  if (buildings.length === 0) return result

  // 拿所有楼的最大 length / width 作为网格单元尺寸
  let maxLen = 30
  let maxWid = 20
  for (const b of buildings) {
    const len = b.dimensions.length_m ?? 30
    const wid = b.dimensions.width_m ?? 20
    if (len > maxLen) maxLen = len
    if (wid > maxWid) maxWid = wid
  }

  const cellW = maxLen * gapFactor  // 列宽 (含间距)
  const cellH = maxWid * gapFactor  // 行高 (含间距)
  const rows = Math.ceil(buildings.length / rowCols)
  const totalW = cellW * rowCols
  const totalH = cellH * rows

  buildings.forEach((b, idx) => {
    const row = Math.floor(idx / rowCols)
    const col = idx % rowCols
    // 居中: 让园区中心在 (0, 0)
    const x = col * cellW - totalW / 2 + cellW / 2
    const y = -(row * cellH - totalH / 2 + cellH / 2)  // y 轴朝上 (建筑往北排)
    result.set(b.building_id, { x, y })
  })

  return result
}

// ---------------------------------------------------------------------------
// 拿 building 的 position (DB 优先, NULL 走网格兜底)
//   返回 {x, y} 米单位, 用于 Cesium BoxGeometry 摆位
// ---------------------------------------------------------------------------

export function resolveBuildingPosition(
  building: SceneBuilding,
  grid: Map<string, GridPosition>,
): GridPosition {
  if (building.position.x !== null && building.position.y !== null) {
    return { x: building.position.x, y: building.position.y }
  }
  return grid.get(building.building_id) ?? { x: 0, y: 0 }
}

// ---------------------------------------------------------------------------
// mock splat 切换
//   .env.local 设 VITE_MOCK_SPLAT_BUILDING=Tammy 让特定楼强制走 splat
//   匹配规则: building_code 末段 (下划线后最后一段) 包含目标字符串
//   返 mock 模式时用的 .ply URL (前端 public/mock/mock_splat.ply)
// ---------------------------------------------------------------------------

export const MOCK_SPLAT_URL = '/mock/mock_splat.ply'

export function shouldUseMockSplat(building: SceneBuilding): boolean {
  const target = import.meta.env.VITE_MOCK_SPLAT_BUILDING as string | undefined
  if (!target) return false
  // building_code 末段: "Bobcat_science_Tammy" -> "Tammy"
  const segments = building.building_code.split('_')
  const lastSegment = segments[segments.length - 1] ?? ''
  return lastSegment.toLowerCase() === target.toLowerCase()
}

export function resolveModelKind(building: SceneBuilding): ModelKind {
  // mock splat 优先级最高 (dev 模式测试用)
  if (shouldUseMockSplat(building)) return 'splat'
  return building.model_kind
}

// ---------------------------------------------------------------------------
// 颜色插值 (用于指标切换时体块颜色平滑过渡)
//   Cesium MaterialProperty 用 SampledProperty 实现插值, 这里只给当前色
//   插值动画在 BuildingBlock.vue 内部用 Cesium CallbackProperty 做
// ---------------------------------------------------------------------------

export function hexToRgb(hex: string): [number, number, number] {
  const m = hex.replace('#', '').match(/.{2}/g)
  if (!m) return [0, 0, 0]
  return [parseInt(m[0], 16), parseInt(m[1], 16), parseInt(m[2], 16)]
}

export function rgbToHex(r: number, g: number, b: number): string {
  const toHex = (n: number) => Math.round(n).toString(16).padStart(2, '0')
  return `#${toHex(r)}${toHex(g)}${toHex(b)}`
}

export function lerpColor(a: string, b: string, t: number): string {
  const [r1, g1, b1] = hexToRgb(a)
  const [r2, g2, b2] = hexToRgb(b)
  return rgbToHex(r1 + (r2 - r1) * t, g1 + (g2 - g1) * t, b1 + (b2 - b1) * t)
}
