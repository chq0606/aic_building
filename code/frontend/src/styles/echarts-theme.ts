// ============================================================================
// ECharts 主题 - 分析中心 6 图表共享
// ----------------------------------------------------------------------------
// 色板跟 tokens.scss 对齐, 字体跟 Park.vue / BuildingDetailDrawer 一致。
// 数字用 Fira Code 等宽 (EUI/total_kwh 等数据对齐), 标题用 Noto Sans SC。
//
// 用法:
//   const chart = echarts.init(dom)
//   chart.setOption({ ...ECHARTS_THEME, ...localOption })
//
// 不用 echarts.registerTheme 是因为 registerTheme 要传完整 schema (几十字段),
// 我们只需要色板 + 字体 + tooltip 风格, 直接展开合并更可控。
// ============================================================================

import type { EChartsOption } from 'echarts'

export const ECHARTS_COLORS = {
  amber: '#D49B3B',       // 主色 - 前 3 名 / MEDIUM 异常 / 主线
  concrete: '#4A4A4A',    // 暖灰 - 后 3 名 / 主文本
  green: '#3D7E6A',       // 翠绿 - 节能 / LOW 异常
  red: '#B84A3C',         // 赤陶 - 高 / HIGH 异常 / 警告
  stone: '#5A6B7C',       // 岩蓝灰 - 中性 / 气温轴
  amberDeep: '#8B5A1F',   // 深琥珀 - 强调 / 国标线
  paper: '#F5F2EB',       // 纸张色 - 背景
  card: '#FFFFFF',        // 卡片背景
  border: '#D1CEC5',      // 边线
  split: '#E5E1D8',       // 网格线
  textSecondary: '#8A8275', // 次级文本
  textTertiary: '#A8A294',  // 三级文本
} as const

// 6 色色阶, 用作 6 类饼图 / 6 条折线默认配色
export const ECHARTS_PALETTE = [
  ECHARTS_COLORS.amber,
  ECHARTS_COLORS.stone,
  ECHARTS_COLORS.green,
  ECHARTS_COLORS.red,
  ECHARTS_COLORS.concrete,
  ECHARTS_COLORS.amberDeep,
]

// 异常 severity 配色 (跟 sceneTransform SEVERITY_COLOR 对齐)
export const SEVERITY_COLORS = {
  LOW: ECHARTS_COLORS.green,
  MEDIUM: ECHARTS_COLORS.amber,
  HIGH: ECHARTS_COLORS.red,
} as const

// 异常类型配色 (6 类, 用 ECHARTS_PALETTE)
export const ANOMALY_TYPE_COLORS: Record<string, string> = {
  SPIKE: ECHARTS_COLORS.amber,
  DRIFT: ECHARTS_COLORS.stone,
  PROLONGED_ZERO: ECHARTS_COLORS.green,
  MISSING_GAP: ECHARTS_COLORS.red,
  SCHEDULE_VIOLATION: ECHARTS_COLORS.concrete,
  BASELINE_DEVIATION: ECHARTS_COLORS.amberDeep,
}

export const ANOMALY_TYPE_LABELS: Record<string, string> = {
  SPIKE: '突增',
  DRIFT: '漂移',
  PROLONGED_ZERO: '持续零值',
  MISSING_GAP: '数据缺失',
  SCHEDULE_VIOLATION: '作息异常',
  BASELINE_DEVIATION: '基线偏离',
  ML_OUTLIER: '机器学习异常',
}

// 共享主题片段 - 展开合并到每个图表的 option
export const ECHARTS_THEME: Partial<EChartsOption> = {
  color: ECHARTS_PALETTE,
  textStyle: {
    fontFamily: "'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', sans-serif",
    color: ECHARTS_COLORS.concrete,
  },
  title: {
    textStyle: {
      color: ECHARTS_COLORS.concrete,
      fontSize: 14,
      fontWeight: 500,
    },
    subtextStyle: {
      color: ECHARTS_COLORS.textSecondary,
      fontSize: 11,
    },
  },
  legend: {
    textStyle: {
      color: ECHARTS_COLORS.textSecondary,
      fontSize: 11,
    },
    inactiveColor: ECHARTS_COLORS.textTertiary,
  },
  tooltip: {
    backgroundColor: 'rgba(245, 242, 235, 0.96)',
    borderColor: ECHARTS_COLORS.border,
    borderWidth: 1,
    textStyle: {
      color: ECHARTS_COLORS.concrete,
      fontSize: 12,
    },
    extraCssText: 'box-shadow: 0 4px 16px rgba(74, 74, 74, 0.12); backdrop-filter: blur(8px); border-radius: 6px;',
    // appendToBody: 把 tooltip DOM 挂到 document.body, 彻底脱离 ChartCard 的
    // overflow:hidden 裁剪 (ChartCard 用 overflow:hidden 让顶部进度条/header
    // border/footer 灰底跟圆角对齐, 不能删). confine: tooltip 仍约束在 chart
    // 容器视觉矩形内, 避免飞到相邻卡片上. 组合后 7 个图表组件 tooltip 全部
    // 完整可见, 不被卡片右/下边裁切.
    appendToBody: true,
    confine: true,
  },
  grid: {
    left: 48,
    right: 24,
    top: 32,
    bottom: 36,
    containLabel: true,
  },
  xAxis: {
    axisLine: {
      lineStyle: { color: ECHARTS_COLORS.border },
    },
    axisTick: {
      lineStyle: { color: ECHARTS_COLORS.border },
    },
    axisLabel: {
      color: ECHARTS_COLORS.textSecondary,
      fontSize: 11,
      fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
      // 兜底防重叠: 稀疏标签全显示, 密集日期轴自动隐藏重叠项.
      // 各组件局部可覆盖 hideOverlap:false 强制全显示 (如建筑名轴需要 45° 倾斜全显示).
      hideOverlap: true,
    },
    splitLine: {
      show: false,
    },
  },
  yAxis: {
    axisLine: {
      show: false,
    },
    axisTick: {
      show: false,
    },
    axisLabel: {
      color: ECHARTS_COLORS.textSecondary,
      fontSize: 11,
      fontFamily: "'Fira Code', 'JetBrains Mono', monospace",
      hideOverlap: true,
    },
    splitLine: {
      lineStyle: {
        color: ECHARTS_COLORS.split,
        type: 'dashed',
      },
    },
  },
  // 柱图圆角 + hover 高亮
  bar: {
    itemStyle: {
      borderRadius: [4, 4, 0, 0],
    },
    emphasis: {
      itemStyle: {
        shadowBlur: 8,
        shadowColor: 'rgba(74, 74, 74, 0.2)',
      },
    },
  },
  line: {
    smooth: true,
    symbol: 'circle',
    symbolSize: 4,
    lineStyle: {
      width: 2,
    },
    emphasis: {
      focus: 'series',
    },
  },
  // 饼图: 圆角 + 间距 + hover 抬升
  pie: {
    itemStyle: {
      borderRadius: 4,
      borderColor: ECHARTS_COLORS.card,
      borderWidth: 2,
    },
    emphasis: {
      itemStyle: {
        shadowBlur: 16,
        shadowColor: 'rgba(212, 155, 59, 0.3)',
      },
      scale: true,
      scaleSize: 6,
    },
  },
  // 注: radar 顶层是雷达坐标系配置 (indicator/radius/shape), 不含 lineStyle/areaStyle
  // 这些 series 级样式由各 chart 组件在 series 里自行设 (参 DataQualityPanel.vue)
  scatter: {
    symbolSize: 8,
    itemStyle: {
      opacity: 0.7,
    },
    emphasis: {
      itemStyle: {
        opacity: 1,
        shadowBlur: 8,
        shadowColor: 'rgba(212, 155, 59, 0.4)',
      },
    },
  },
}

// 工具函数: 把数字格式化成易读字符串
export function formatNumber(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return '-'
  if (Math.abs(v) >= 10000) return (v / 1000).toFixed(digits) + 'k'
  if (Math.abs(v) >= 100) return v.toFixed(0)
  return v.toFixed(digits)
}

// 工具函数: building_code 末段 (Bobcat_education_Alissa -> Alissa)
export function shortBuildingName(code: string): string {
  const parts = code.split('_')
  return parts[parts.length - 1] ?? code
}

// 工具函数: building_kind (Bobcat_education_Alissa -> education)
export function parseBuildingKind(code: string): string {
  const parts = code.split('_')
  // 第一段是园区名 (Bobcat), 第二段是 kind (education/assembly/public/science), 第三段是楼名
  return parts[1] ?? 'unknown'
}

// GB 55015-2019 国标 EUI 限值 (kWh/m²·a)
// 注: BDG2 是国外数据集, 气候带 + 建筑使用习惯不同, 国标仅供参考
export const GB_55015_LIMITS: Record<string, number> = {
  education: 65,
  assembly: 90,
  public: 70,
  science: 65,
  office: 70,
  residential: 110,
  default: 70,
}

export function getGbLimit(kind: string): number {
  return GB_55015_LIMITS[kind] ?? GB_55015_LIMITS.default
}

// Pearson 相关系数 (weather correlation 用)
export function pearson(xs: number[], ys: number[]): number {
  const n = Math.min(xs.length, ys.length)
  if (n < 2) return 0
  const mx = xs.slice(0, n).reduce((s, x) => s + x, 0) / n
  const my = ys.slice(0, n).reduce((s, y) => s + y, 0) / n
  let num = 0, dx = 0, dy = 0
  for (let i = 0; i < n; i++) {
    const ex = xs[i] - mx
    const ey = ys[i] - my
    num += ex * ey
    dx += ex * ex
    dy += ey * ey
  }
  const denom = Math.sqrt(dx * dy)
  if (denom === 0) return 0
  return num / denom
}

// 相关系数强度描述
export function correlationLabel(r: number): string {
  const a = Math.abs(r)
  if (a >= 0.7) return '强相关'
  if (a >= 0.4) return '中等相关'
  if (a >= 0.2) return '弱相关'
  return '几乎无相关'
}
