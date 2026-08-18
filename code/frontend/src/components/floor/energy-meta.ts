// ============================================================================
// 能源类型元数据 - 楼层图表组件共享
// ----------------------------------------------------------------------------
// 颜色跟 EnergyBadge.vue 对齐 (electricity=琥珀 / hotwater=赤陶 / etc.),
// 中文标签跟 bdg2 数据集能源类型字面量对齐。
// 所有图表组件都用这个, 保证同一能源在 5 个图表 + 剖面图里颜色/名称/图标一致。
// ============================================================================

import {
  Zap, Droplet, Snowflake, Flame, Sun, Droplets, type LucideIcon,
} from 'lucide-vue-next'

export interface EnergyMeta {
  color: string
  label: string
  shortLabel: string  // 图例/坐标轴短标签 (空间紧时用)
  icon: LucideIcon    // 设备点 / 设备表格用的能源图标
}

export const ENERGY_META: Record<string, EnergyMeta> = {
  electricity:   { color: '#D49B3B', label: '电力',   shortLabel: '电',   icon: Zap },
  hotwater:      { color: '#B84A3C', label: '热水',   shortLabel: '热水', icon: Droplet },
  chilledwater:  { color: '#5A6B7C', label: '冷冻水', shortLabel: '冷',   icon: Snowflake },
  gas:           { color: '#4A4A4A', label: '燃气',   shortLabel: '气',   icon: Flame },
  water:         { color: '#3D7E6A', label: '水',     shortLabel: '水',   icon: Droplets },
  solar:         { color: '#7CB34A', label: '太阳能', shortLabel: '光',   icon: Sun },
  steam:         { color: '#8B5A1F', label: '蒸汽',   shortLabel: '汽',   icon: Flame },
  irrigation:    { color: '#A8A294', label: '灌溉',   shortLabel: '灌',   icon: Droplets },
}

export function getEnergyMeta(type: string): EnergyMeta {
  const key = type.toLowerCase()
  return ENERGY_META[key] ?? { color: '#A8A294', label: type, shortLabel: type, icon: Droplet }
}

// 把能源类型数组转成 ECharts 调色板 (按能源在数组里的顺序)
export function energyPalette(types: string[]): string[] {
  return types.map(t => getEnergyMeta(t).color)
}

