<script setup lang="ts">
// ============================================================================
// EuiBaseline - EUI 基准对比柱状图
// ----------------------------------------------------------------------------
// 数据源: useAnalysisData 的 buildings (跟 BuildingRanking 共享缓存, 不重复请求)
//
// 视觉:
//   - 每柱 = 一栋楼的实测 EUI, 柱色按 building_kind (教育/综合体/公共/科研...)
//   - 双参考线:
//     · 实线段 = 同类均值 (按 kind 分组算, 仅跨同类柱, 跟柱色一致)
//     · 红色虚线 = GB 55015-2019 国标默认限值 70 kWh/m²·a (跨全图)
//   - 柱顶标偏差% (实测 EUI vs 同类均值, 偏高赤陶 / 偏低翠绿)
//
// 关键决策 (参 plan):
//   BDG2 是国外数据集, 实测 EUI vs GB 国标偏差 -69% ~ +210% (教育类 Alissa +121%
//   / Dylan +210% / Seth -69%)。单用 GB 国标会误导, 双参考线让用户一眼看到偏差。
//
// X 轴顺序: 按 kind 分组 (同 kind 的楼聚一起), 组内按 EUI 降序
// 这样 markLine 段就能只跨同类柱, 不会覆盖其他 kind 的柱
// ============================================================================

import { computed } from 'vue'
import { useAnalysisData } from '@/composables/useAnalysisData'
import ChartCard from './ChartCard.vue'
import {
  ECHARTS_COLORS,
  shortBuildingName,
  parseBuildingKind,
  GB_55015_LIMITS,
  getGbLimit,
} from '@/styles/echarts-theme'
import type { EChartsOption } from 'echarts'

const { buildings, loading, error, reload } = useAnalysisData()

// building_kind -> 颜色 + 中文 (跟 design system 色板对齐)
const KIND_COLORS: Record<string, string> = {
  education: ECHARTS_COLORS.amber,
  assembly: ECHARTS_COLORS.red,
  public: ECHARTS_COLORS.green,
  science: ECHARTS_COLORS.stone,
  office: ECHARTS_COLORS.concrete,
  residential: ECHARTS_COLORS.amberDeep,
  default: ECHARTS_COLORS.concrete,
}

const KIND_LABELS: Record<string, string> = {
  education: '教育',
  assembly: '综合体',
  public: '公共',
  science: '科研',
  office: '办公',
  residential: '居住',
  default: '其他',
}

interface BuildingRow {
  building_id: string
  building_code: string
  display_name: string
  primary_use: string
  shortName: string
  kind: string
  eui: number  // eui_kwh_per_m2 (null -> 0, 用 hasEui 标记)
  hasEui: boolean
  kindAvg: number  // 同类均值
  gbLimit: number  // GB 55015 限值 (按 kind 映射)
  deviationPct: number  // 实际 vs 同类均值 偏差 % (正=偏高 负=偏低)
}

// 1. 算同类均值 (按 building_kind 分组, eui_kwh_per_m2 非 null 才入算)
const kindAverages = computed<Record<string, number>>(() => {
  if (!buildings.value) return {}
  const groups: Record<string, number[]> = {}
  for (const b of buildings.value.buildings) {
    const kind = parseBuildingKind(b.building_code)
    if (b.eui_kwh_per_m2 != null) {
      if (!groups[kind]) groups[kind] = []
      groups[kind].push(b.eui_kwh_per_m2)
    }
  }
  const result: Record<string, number> = {}
  for (const kind in groups) {
    const arr = groups[kind]
    result[kind] = arr.reduce((s, v) => s + v, 0) / arr.length
  }
  return result
})

// 2. 构造每行 (含同类均值 / GB 限值 / 偏差%)
const rows = computed<BuildingRow[]>(() => {
  if (!buildings.value) return []
  return buildings.value.buildings
    .map(b => {
      const kind = parseBuildingKind(b.building_code)
      const eui = b.eui_kwh_per_m2 ?? 0
      const kindAvg = kindAverages.value[kind] ?? 0
      const gbLimit = getGbLimit(kind)
      const deviationPct = kindAvg > 0 ? ((eui - kindAvg) / kindAvg) * 100 : 0
      return {
        building_id: b.building_id,
        building_code: b.building_code,
        display_name: b.display_name,
        primary_use: b.primary_use,
        shortName: shortBuildingName(b.building_code),
        kind,
        eui,
        hasEui: b.eui_kwh_per_m2 != null,
        kindAvg,
        gbLimit,
        deviationPct,
      }
    })
    // 先按 kind 分组 (字母序), 组内按 EUI 降序
    // 这样 markLine 段就只跨同类柱
    .sort((a, b) => {
      if (a.kind !== b.kind) return a.kind.localeCompare(b.kind)
      return b.eui - a.eui
    })
})

// 出现过的 kind 列表 (用于 markLine + legend)
const kindList = computed(() => {
  const set = new Set(rows.value.map(r => r.kind))
  return Array.from(set)
})

const subtitle = computed(() => {
  if (rows.value.length === 0) return ''
  return `${rows.value.length} 栋楼 · 双参考线: 同类均值实线 + GB 55015 国标虚线`
})

const chartOption = computed<EChartsOption | null>(() => {
  if (rows.value.length === 0) return null
  const list = rows.value

  // markLine 数据: 每个 kind 一段实线均值 (仅跨同类柱) + 一条 GB 默认虚线 (跨全图)
  // markLine data 支持 [start, end] 对的形式画线段, 起止点带 xAxis/yAxis
  const markLineData: Array<[{ xAxis: number; yAxis: number; }, { xAxis: number; yAxis: number; }]> = []
  for (const kind of kindList.value) {
    const indices: number[] = []
    list.forEach((r, i) => {
      if (r.kind === kind) indices.push(i)
    })
    if (indices.length === 0) continue
    const start = Math.min(...indices)
    const end = Math.max(...indices)
    const avg = list[start].kindAvg
    if (avg <= 0) continue
    // ±0.4 让线段比柱稍宽, 视觉上不会贴齐柱边
    markLineData.push([
      { xAxis: start - 0.4, yAxis: avg },
      { xAxis: end + 0.4, yAxis: avg },
    ])
  }

  return {
    grid: { left: 12, right: 24, top: 24, bottom: 50, containLabel: true },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      formatter: (params: any) => {
        const idx = params[0]?.dataIndex ?? 0
        const r = list[idx]
        if (!r) return ''
        const devSign = r.deviationPct >= 0 ? '+' : ''
        const devColor = r.deviationPct >= 0 ? ECHARTS_COLORS.red : ECHARTS_COLORS.green
        return [
          `<b>${r.display_name}</b>`,
          `建筑编码: ${r.building_code}`,
          `主要用途: ${r.primary_use}`,
          `建筑类型: ${KIND_LABELS[r.kind] ?? r.kind}`,
          `实测 EUI: <b style="color:${KIND_COLORS[r.kind] ?? KIND_COLORS.default}">${r.hasEui ? r.eui.toFixed(1) : '-'} kWh/m²</b>`,
          `同类均值: ${r.kindAvg.toFixed(1)} kWh/m²`,
          `偏差: <b style="color:${devColor}">${devSign}${r.deviationPct.toFixed(1)}%</b>`,
          `GB 55015 限值: ${r.gbLimit} kWh/m² (仅供参考)`,
        ].join('<br/>')
      },
    },
    legend: {
      data: kindList.value.map(k => KIND_LABELS[k] ?? k),
      top: 0,
      itemWidth: 10,
      itemHeight: 10,
      icon: 'roundRect',
    },
    xAxis: {
      type: 'category',
      data: list.map(r => r.shortName),
      // 建筑名是关键信息, 45° 倾斜全显示 (hideOverlap:false 覆盖全局兜底).
      // grid bottom 已从 32 加到 50 给倾斜标签留垂直空间.
      axisLabel: { fontSize: 11, rotate: 45, interval: 0, hideOverlap: false },
    },
    yAxis: {
      type: 'value',
      name: 'EUI (kWh/m²)',
      nameTextStyle: { fontSize: 11, color: ECHARTS_COLORS.textSecondary },
    },
    series: [
      {
        type: 'bar',
        data: list.map(r => ({
          value: r.eui,
          itemStyle: {
            color: KIND_COLORS[r.kind] ?? KIND_COLORS.default,
            borderRadius: [4, 4, 0, 0],
          },
          // 柱顶标偏差% (实测 vs 同类均值), 偏高赤陶 / 偏低翠绿
          label: {
            show: true,
            position: 'top',
            formatter: () => {
              if (!r.hasEui) return 'N/A'
              const sign = r.deviationPct >= 0 ? '+' : ''
              return `${sign}${r.deviationPct.toFixed(0)}%`
            },
            fontSize: 10,
            fontWeight: 500,
            color: r.deviationPct >= 0 ? ECHARTS_COLORS.red : ECHARTS_COLORS.green,
            fontFamily: "'Fira Code', monospace",
          },
        })),
        barWidth: '48%',
        // 双参考线: markLine 内混 [start,end] 对 (同类均值段) 和单对象 (GB 默认虚线)
        markLine: {
          symbol: ['none', 'none'],
          silent: true,
          animation: false,
          data: [
            // 每个 kind 一段均值实线 (用同色, 仅跨同类柱)
            ...markLineData,
            // GB 55015 默认限值 (赤陶虚线, 跨全图)
            // label 删掉: 之前 "GB 默认 70" 显示在图内虚线末端跟柱顶偏差% 标签挤一起
            // 数值 70 已在 footer 文字里说明, 虚线本身赤陶色虚线视觉区分即可
            {
              yAxis: GB_55015_LIMITS.default,
              lineStyle: {
                color: ECHARTS_COLORS.red,
                type: 'dashed',
                width: 2,
              },
              label: { show: false },
            },
          ],
        },
      },
    ],
  }
})

const isEmpty = computed(
  () => rows.value.length === 0 || !rows.value.some(r => r.hasEui),
)
</script>

<template>
  <ChartCard
    title="EUI 基准对比"
    :subtitle="subtitle"
    :loading="loading"
    :error="error"
    :empty="isEmpty"
    empty-text="该园区本周期无 EUI 数据"
    :option="chartOption"
    @retry="reload"
  >
    <template #footer>
      <span>BDG2 国外数据集 · GB 55015-2019 国标限值仅供参考 (教育 65 / 综合体 90 / 公共 70 / 科研 65)</span>
    </template>
  </ChartCard>
</template>
