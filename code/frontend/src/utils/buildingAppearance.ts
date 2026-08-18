// ============================================================================
// buildingAppearance.ts - 按楼栋用途生成程序化建筑外观
// ----------------------------------------------------------------------------
// 目的: 未做 3D 重建前, 用"程序化表皮 + 屋顶"把纯色体块升级成"示意建筑",
// 让评委一眼看出这是"有玻璃幕墙的教学楼 / 有砖墙的学生中心", 而不是几个色块。
//
// 思路 (借鉴参考仓库, 但不用 three.js shader / gltf 模型):
//   - 表皮: 用 canvas 画"窗格模块"纹理, Cesium ImageMaterialProperty 贴到 Box 侧面,
//     按楼宽/层数 repeat, 让窗户密度随建筑尺寸自适应
//   - 屋顶: 平顶(女儿墙薄板) / 坡顶(退台) / 桶形(体育馆, 用压扁的椭球做弧顶)
//   - 颜色: 全部走 tokens 的低饱和暖灰/岩蓝灰 + 一点暖砖色, 不引入高饱和新色
//
// 参考: ArchViz-Lite 的"素模/风格化"思路 (不追求写实, 追求统一有质感的示意);
//       HouseViz3D 的"真实纹理 (砖墙/玻璃/屋顶)"目标 (但我们用程序化而非贴图)。
//
// 为什么用 sub_use 而不是 primary_use 选表皮: BDG2 的 primary_use 只有
// Education/Public services 等粗粒度, 分不出"学术楼 vs 学生中心 vs 图书馆",
// sub_use (Academic/Sports Facility/Science Facility/Student Center/Library)
// 才是真正区分建筑样式的字段。
// ============================================================================

import type { SceneBuilding } from '@/api/visual'

// 屋顶类型
export type RoofType = 'flat' | 'pitched' | 'barrel'
// 窗格样式: 幕墙(满铺玻璃) / 点窗(砖墙挖小窗) / 竖向窄窗 / 横向带窗
export type WindowStyle = 'curtain' | 'punch' | 'vertical' | 'horizontal'

export interface BuildingAppearance {
  roofType: RoofType
  facadeMain: string    // 立面主色 (幕墙的玻璃 / 砖墙的砖色 / 石材)
  facadeWindow: string  // 窗户玻璃色
  facadeTrim: string    // 窗框 / mullion 分隔色
  windowStyle: WindowStyle
  roofColor: string     // 屋顶色 (比立面深一档)
}

// ---- 默认外观 (sub_use / primary_use 都匹配不上时用) ----
const DEFAULT_APPEARANCE: BuildingAppearance = {
  roofType: 'flat',
  facadeMain: '#8FA0B0',
  facadeWindow: '#4A5A68',
  facadeTrim: '#C2CFDA',
  windowStyle: 'curtain',
  roofColor: '#6B7A8A',
}

// ---- 按 sub_use 匹配 (更细, 优先) ----
const SUB_USE_APPEARANCE: Array<{ match: RegExp; spec: BuildingAppearance }> = [
  {
    // 学术楼: 玻璃幕墙 + 平顶 (现代教学楼)
    match: /academic|education|teaching/i,
    spec: {
      roofType: 'flat', facadeMain: '#8FA0B0', facadeWindow: '#4A5A68',
      facadeTrim: '#C2CFDA', windowStyle: 'curtain', roofColor: '#6B7A8A',
    },
  },
  {
    // 体育馆 / 礼堂: 大跨度 + 平顶 + 横向带窗 (暖砖色体量)
    // 之前用 barrel (椭球弧顶) 视觉上像个奇怪的椭圆球, 不符合真实体育馆大平顶的形态。
    // 改成 flat 平顶: 大跨度楼用平顶更符合工程实际, 横向带窗已经能体现"体育馆"特征。
    match: /sports|assembly|entertainment|auditorium|gym/i,
    spec: {
      roofType: 'flat', facadeMain: '#B8A98C', facadeWindow: '#5A6B7C',
      facadeTrim: '#D1CEC5', windowStyle: 'horizontal', roofColor: '#8A7C5E',
    },
  },
  {
    // 科学楼 / 实验楼: 现代玻璃 + 平顶, 玻璃更深更冷
    match: /science|technology|laboratory|lab\b|research/i,
    spec: {
      roofType: 'flat', facadeMain: '#7E93A6', facadeWindow: '#3E5566',
      facadeTrim: '#AFC2D4', windowStyle: 'curtain', roofColor: '#5A6B7C',
    },
  },
  {
    // 学生中心: 砖墙 + 点窗 + 退台坡顶 (暖砖色, 有生活感)
    // 注意不含 housing: 纯住宅走下面的"居民楼"预设, 这里只管学生宿舍/学生中心
    match: /student|dormitory|residence/i,
    spec: {
      roofType: 'pitched', facadeMain: '#A8836B', facadeWindow: '#5E5E5E',
      facadeTrim: '#D1CEC5', windowStyle: 'punch', roofColor: '#8A6B52',
    },
  },
  {
    // 图书馆: 石材 + 竖向窄窗 + 平顶 (稳重、体块感)
    // 注意不含 office: 办公楼走下面的"商业楼宇"玻璃幕墙, 这里只管图书馆/公共服务
    match: /library|public|service|admin/i,
    spec: {
      roofType: 'flat', facadeMain: '#8A8275', facadeWindow: '#4A4A4A',
      facadeTrim: '#B5AFA3', windowStyle: 'vertical', roofColor: '#6B6557',
    },
  },
  {
    // 零售商铺: 浅暖底 + 大面积横向橱窗 + 平顶 (临街商铺感)
    match: /retail|shop|store|mall|supermarket|market/i,
    spec: {
      roofType: 'flat', facadeMain: '#C4B49A', facadeWindow: '#4E5E6E',
      facadeTrim: '#DDD5C4', windowStyle: 'horizontal', roofColor: '#96886B',
    },
  },
  {
    // 居民楼: 米黄暖灰外墙 + 点窗 + 坡顶 (住宅生活感, 比学生中心的红砖更素)
    match: /residential|apartment|condo|villa|housing/i,
    spec: {
      roofType: 'pitched', facadeMain: '#C9BFA8', facadeWindow: '#5E5E5E',
      facadeTrim: '#E2DACA', windowStyle: 'punch', roofColor: '#8F8268',
    },
  },
  {
    // 商业楼宇: 深冷玻璃幕墙 + 平顶 (写字楼/商厦感, 比学术楼的玻璃更深更冷)
    match: /commercial|business|bank|hotel|office/i,
    spec: {
      roofType: 'flat', facadeMain: '#6E8090', facadeWindow: '#38485A',
      facadeTrim: '#9FB2C4', windowStyle: 'curtain', roofColor: '#57666F',
    },
  },
]

// ---- 按 primary_use 匹配 (sub_use 匹配不上时回退) ----
const PRIMARY_USE_APPEARANCE: Array<{ match: RegExp; spec: BuildingAppearance }> = [
  { match: /education/i, spec: SUB_USE_APPEARANCE[0].spec },       // 教育 -> 学术玻璃
  { match: /science|technology/i, spec: SUB_USE_APPEARANCE[2].spec }, // 科技 -> 科学玻璃
  { match: /entertainment|assembly/i, spec: SUB_USE_APPEARANCE[1].spec }, // 集会 -> 体育馆
  { match: /public/i, spec: SUB_USE_APPEARANCE[4].spec },          // 公共服务 -> 图书馆石材
  { match: /retail|shop/i, spec: SUB_USE_APPEARANCE[5].spec },     // 零售 -> 商铺橱窗
  { match: /residential|lodging/i, spec: SUB_USE_APPEARANCE[6].spec }, // 住宅 -> 居民楼
  { match: /commercial|business|office/i, spec: SUB_USE_APPEARANCE[7].spec }, // 商业/办公 -> 玻璃幕墙
]

export function getBuildingAppearance(building: SceneBuilding): BuildingAppearance {
  const sub = building.sub_use ?? ''
  const pri = building.primary_use ?? ''
  for (const { match, spec } of SUB_USE_APPEARANCE) {
    if (match.test(sub)) return spec
  }
  for (const { match, spec } of PRIMARY_USE_APPEARANCE) {
    if (match.test(pri)) return spec
  }
  return DEFAULT_APPEARANCE
}

// ---------------------------------------------------------------------------
// 生成"窗格模块"纹理 (一个窗格的 canvas)
// ---------------------------------------------------------------------------
// 返回 256x256 canvas, 表示"一个开间 × 一层"的外立面单元。调用方用
// ImageMaterialProperty 的 repeat 把它平铺: repeat.x = 楼宽/开间宽, repeat.y = 层数。
// 这样窗户密度随建筑尺寸自适应, 不像贴一整张固定图那样被拉伸变形。
//
// 视觉细节 (相对之前的纯色版本):
//   - 立面加随机噪点: 模拟砖墙颗粒感 / 涂料肌理, 避免"光滑色块"的塑料感
//   - 玻璃加反射渐变: 顶部天空色 -> 底部地面色, 模拟玻璃反射环境光
//     (curtain 幕墙强反射, vertical/horizontal 中等, punch 砖墙小窗暗玻璃)
// ---------------------------------------------------------------------------
export function generateFacadeTexture(a: BuildingAppearance): HTMLCanvasElement {
  const S = 256
  const c = document.createElement('canvas')
  c.width = S
  c.height = S
  const ctx = c.getContext('2d')!

  // ---- 立面底色 + 随机噪点 (砖墙颗粒感) ----
  ctx.fillStyle = a.facadeMain
  ctx.fillRect(0, 0, S, S)
  // 200 个随机噪点, 颜色在 facadeMain ± 亮度区间随机
  const baseRgb = hexToRgbArr(a.facadeMain)
  for (let i = 0; i < 200; i++) {
    const x = Math.random() * S
    const y = Math.random() * S
    const delta = (Math.random() - 0.5) * 30  // ±15 亮度偏移
    const r = clamp255(baseRgb[0] + delta)
    const g = clamp255(baseRgb[1] + delta)
    const b = clamp255(baseRgb[2] + delta)
    ctx.fillStyle = `rgb(${r},${g},${b})`
    ctx.fillRect(x, y, 1.5, 1.5)
  }

  // ---- 窗格比例 ----
  let wx = 0
  let wy = 0
  let ww = 0
  let wh = 0
  switch (a.windowStyle) {
    case 'curtain':
      wx = S * 0.08; wy = S * 0.05; ww = S * 0.84; wh = S * 0.90
      break
    case 'punch':
      wx = S * 0.22; wy = S * 0.18; ww = S * 0.56; wh = S * 0.64
      break
    case 'vertical':
      wx = S * 0.30; wy = S * 0.08; ww = S * 0.40; wh = S * 0.84
      break
    case 'horizontal':
      wx = S * 0.08; wy = S * 0.32; ww = S * 0.84; wh = S * 0.36
      break
  }

  // ---- 玻璃: 反射渐变 (顶部天空色 -> 底部地面色) ----
  // curtain 幕墙强反射 (天空蓝 -> 地面色), 其他样式玻璃暗 (弱反射或纯色)
  if (a.windowStyle === 'curtain') {
    const grad = ctx.createLinearGradient(0, wy, 0, wy + wh)
    grad.addColorStop(0, '#A8C5DC')       // 顶部: 天空浅蓝 (反射天空)
    grad.addColorStop(0.45, a.facadeWindow) // 中部: 玻璃主色
    grad.addColorStop(1, '#3A4855')       // 底部: 地面色 (反射地面暗影)
    ctx.fillStyle = grad
  } else if (a.windowStyle === 'vertical' || a.windowStyle === 'horizontal') {
    // 中等反射: 天空色 -> 玻璃色 (不反射地面)
    const grad = ctx.createLinearGradient(0, wy, 0, wy + wh)
    grad.addColorStop(0, '#7A95AC')
    grad.addColorStop(1, a.facadeWindow)
    ctx.fillStyle = grad
  } else {
    // punch 砖墙小窗: 暗玻璃, 几乎不反射
    ctx.fillStyle = a.facadeWindow
  }
  ctx.fillRect(wx, wy, ww, wh)

  // 玻璃竖向 mullion 分隔
  ctx.strokeStyle = a.facadeTrim
  ctx.lineWidth = S * 0.015
  const mullions = a.windowStyle === 'vertical' ? 1 : 2
  for (let i = 1; i <= mullions; i++) {
    const x = wx + (ww * i) / (mullions + 1)
    ctx.beginPath()
    ctx.moveTo(x, wy)
    ctx.lineTo(x, wy + wh)
    ctx.stroke()
  }

  // 窗框
  ctx.strokeStyle = a.facadeTrim
  ctx.lineWidth = S * 0.03
  ctx.strokeRect(wx, wy, ww, wh)

  return c
}

// hex -> [r, g, b]
function hexToRgbArr(hex: string): [number, number, number] {
  const m = hex.replace('#', '').match(/.{2}/g)
  if (!m) return [128, 128, 128]
  return [parseInt(m[0], 16), parseInt(m[1], 16), parseInt(m[2], 16)]
}

function clamp255(n: number): number {
  return Math.max(0, Math.min(255, Math.round(n)))
}

// ---------------------------------------------------------------------------
// 屋顶几何参数 (供 BuildingBlock 摆放屋顶实体用)
// ---------------------------------------------------------------------------
// 返回屋顶相对楼体顶部的偏移与尺寸, BuildingBlock 直接拿去 add 屋顶 entity。
// 楼体底面在 z=0, 顶部在 visualFullHeight。屋顶放在顶部往上。
// ---------------------------------------------------------------------------
export interface RoofSpec {
  // 平顶/坡顶的"女儿墙薄板": 比 footprint 略外挑, 高 ~1.2m
  parapetHeight: number
  parapetInset: number      // 负值 = 外挑 (比楼体宽), 用负数表示往外扩
  // 坡顶的"退台"第二层: 比楼体小一圈的实心块
  setbackHeight: number
  setbackScale: number      // 退台占楼体平面比例
}

export function getRoofSpec(a: BuildingAppearance): RoofSpec {
  switch (a.roofType) {
    case 'pitched':
      return { parapetHeight: 1.2, parapetInset: -1.0, setbackHeight: 3.0, setbackScale: 0.72 }
    case 'barrel':
      return { parapetHeight: 0.8, parapetInset: -2.0, setbackHeight: 0, setbackScale: 1.0 }
    default: // flat
      return { parapetHeight: 1.2, parapetInset: -0.6, setbackHeight: 0, setbackScale: 1.0 }
  }
}
