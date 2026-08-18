<script setup lang="ts">
// ============================================================================
// DataQualityPanel - 数据质量面板
// ----------------------------------------------------------------------------
// 数据源:
//   1. useAnalysisData 的 buildings (顶部下拉框选项)
//   2. dataQualityApi.getBuildingQuality(buildingId) - 单楼 6 项质量指标
//
// 布局: 一张 ChartCard 内上下两栏 (跟 AnomalyOverview 同样的上下策略)
//   上: 雷达图 - 3 项主指标 (completeness / invalid_value_rate / missing_gap_count)
//        都转成 0-100 分 (越高越好), 占满宽度 ~309px 标签不被裁
//   下: 6 张指标卡 2x3 网格 - 原始值 + status chip
//
// 之前左右切分雷达区仅 160px, 标签 "异常值率"/"缺失段数" 被卡片边框裁切
// 改上下后雷达横向有 ~309px, 半径可放到 60% 标签完整显示
//
// 6 项指标 (后端返):
//   completeness              完整度 0-1 越高越好
//   duplicate_rate            重复率 0-1 越低越好 (building 视角恒 null)
//   invalid_value_rate        异常值率 0-1 越低越好
//   missing_gap_count         缺失段数 整数 越低越好
//   timezone_parse_error_count 时区错误 整数 越低越好 (building 视角恒 null)
//   unit_normalization_count  单位转换 整数 (building 视角恒 null)
//
// 雷达图转分公式 (0-100, 越高越好):
//   - completeness: score = value * 100
//   - invalid_value_rate: score = (1 - value) * 100
//   - missing_gap_count: score = max(0, 100 - value * 5)
//
// 选楼: 顶部 a-select (extra slot), 默认第 1 栋
// ============================================================================

import { ref, onMounted, onBeforeUnmount, watch, nextTick, computed } from 'vue'
import * as echarts from 'echarts'
import { useAnalysisData } from '@/composables/useAnalysisData'
import { dataQualityApi, METRIC_META, STATUS_META } from '@/api/data_quality'
import type { BuildingQuality, QualityMetric, QualityStatus } from '@/api/data_quality'
import { useContextStore } from '@/stores/context'
import ChartCard from './ChartCard.vue'
import { ECHARTS_THEME, ECHARTS_COLORS, shortBuildingName } from '@/styles/echarts-theme'
import type { EChartsOption } from 'echarts'

const context = useContextStore()
const { buildings, loading, error, reload } = useAnalysisData()

// 当前选中的楼 (默认 EUI 最高那栋, 没数据则 undefined)
// 用 undefined 而非 null 因为 a-select 的 SelectValue 类型不接受 null
const selectedBuildingId = ref<string | undefined>(undefined)
const buildingQuality = ref<BuildingQuality | null>(null)
const buildingQualityLoading = ref(false)
const buildingQualityError = ref<string | null>(null)

// 顶部下拉框选项
const buildingOptions = computed(() => {
  if (!buildings.value) return []
  return buildings.value.buildings
    .map(b => ({
      value: b.building_id,
      label: b.display_name,
    }))
})

// buildings 加载后默认选第 1 栋 (按 EUI 降序, EUI 最高的)
watch(buildingOptions, (opts) => {
  if (!selectedBuildingId.value && opts.length > 0) {
    selectedBuildingId.value = opts[0].value
  }
})

// 拉单楼 6 项质量指标
async function loadBuildingQuality() {
  if (!selectedBuildingId.value) return
  buildingQualityLoading.value = true
  buildingQualityError.value = null
  try {
    const range = context.currentRange
    buildingQuality.value = await dataQualityApi.getBuildingQuality(
      selectedBuildingId.value,
      { start: range.start.toISOString(), end: range.end.toISOString() },
    )
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : '加载建筑质量数据失败'
    buildingQualityError.value = msg
  } finally {
    buildingQualityLoading.value = false
  }
}

// 选楼 / 时间范围变化时拉新数据
watch(selectedBuildingId, loadBuildingQuality)
watch(() => context.currentRange, loadBuildingQuality, { deep: true })

// ---- 雷达图 ----
const radarChartRef = ref<HTMLDivElement | null>(null)
let radarChart: echarts.ECharts | null = null
let radarResizeObserver: ResizeObserver | null = null

interface RadarMetric {
  key: string
  label: string
  rawValue: number | null
  rawUnit: string
  score: number  // 0-100, 越高越好
  status: QualityStatus
}

// metrics map (key -> QualityMetric), 方便查
const metricsMap = computed<Record<string, QualityMetric>>(() => {
  if (!buildingQuality.value) return {}
  const m: Record<string, QualityMetric> = {}
  for (const item of buildingQuality.value.metrics) {
    m[item.metric] = item
  }
  return m})

// 雷达展示 3 项 (completeness / invalid_value_rate / missing_gap_count)
// 这 3 项在 building 视角永远非 null (参后端 data_quality_service.get_building_quality)
// duplicate_rate / timezone_parse_error_count / unit_normalization_count 在 building
// 视角后端故意返 null (fact 表已去重, staging 指标查不到), 不能进雷达 (score=0 会让三角形退化)
//
// 打分公式 (0-100, 越高越好):
//   - completeness (0-1 小数): score = value * 100
//   - invalid_value_rate (0-1 小数): score = (1 - value) * 100
//   - missing_gap_count (整数): score = max(0, 100 - value * 5)  // 1 gap 扣 5 分, 20 gap 归 0
const radarMetrics = computed<RadarMetric[]>(() => {
  const map = metricsMap.value
  const list: RadarMetric[] = []
  // 1. 完整度: 越高越好, 0-1 小数 -> 0-100 分
  const comp = map['completeness']
  if (comp) {
    list.push({
      key: 'completeness',
      label: METRIC_META.completeness.label,
      rawValue: comp.value,
      rawUnit: METRIC_META.completeness.unit,
      score: (comp.value ?? 0) * 100,
      status: comp.status,
    })
  }
  // 2. 异常值率: 越低越好, 0-1 小数 -> 0-100 分 (score = (1 - rate) * 100)
  const inv = map['invalid_value_rate']
  if (inv) {
    list.push({
      key: 'invalid_value_rate',
      label: METRIC_META.invalid_value_rate.label,
      rawValue: inv.value,
      rawUnit: METRIC_META.invalid_value_rate.unit,
      score: (1 - (inv.value ?? 0)) * 100,
      status: inv.status,
    })
  }
  // 3. 缺失段数: 越低越好, 整数 -> 0-100 分 (1 gap 扣 5 分, 20 gap 归 0)
  const gap = map['missing_gap_count']
  if (gap) {
    list.push({
      key: 'missing_gap_count',
      label: METRIC_META.missing_gap_count.label,
      rawValue: gap.value,
      rawUnit: METRIC_META.missing_gap_count.unit,
      score: Math.max(0, 100 - (gap.value ?? 0) * 5),
      status: gap.status,
    })
  }
  return list
})

const radarOption = computed<EChartsOption | null>(() => {
  if (radarMetrics.value.length < 3) return null
  return {
    tooltip: {
      formatter: (params: any) => {
        const scores = params.value ?? []
        let html = `<b>${buildingQuality.value?.display_name ?? ''}</b>`
        radarMetrics.value.forEach((m, i) => {
          const raw = m.rawValue != null ? `${m.rawValue} ${m.rawUnit}` : 'N/A'
          html += `<br/>${m.label}: ${raw} (得分 ${scores[i] ?? 0})`
        })
        return html
      },
    },
    radar: {
      indicator: radarMetrics.value.map(m => ({
        name: m.label,
        max: 100,
      })),
      shape: 'polygon',
      // 上下布局后雷达占满宽度 ~309px, 半径可放到 60% 标签完整显示
      // center Y 50% -> 55% 略下移, 给上方 "完整度" 标签留空间
      radius: '60%',
      center: ['50%', '55%'],
      axisName: {
        color: ECHARTS_COLORS.concrete,
        fontSize: 11,
        padding: [2, 4],
        // overflow: 'none' 禁用 ECharts 默认标签截断 (超 width 截成 "...")
        overflow: 'none',
      },
      splitLine: {
        lineStyle: { color: ECHARTS_COLORS.split },
      },
      splitArea: {
        areaStyle: {
          color: ['rgba(245, 242, 235, 0.3)', 'rgba(74, 74, 74, 0.02)'],
        },
      },
      axisLine: {
        lineStyle: { color: ECHARTS_COLORS.border },
      },
    },
    series: [
      {
        type: 'radar',
        data: [
          {
            value: radarMetrics.value.map(m => m.score),
            name: buildingQuality.value?.display_name ?? '',
            areaStyle: {
              color: 'rgba(212, 155, 59, 0.18)',
            },
            lineStyle: {
              color: ECHARTS_COLORS.amber,
              width: 2,
            },
            itemStyle: {
              color: ECHARTS_COLORS.amber,
            },
          },
        ],
      },
    ],
  }
})

// ---- 右侧 6 张状态卡 (原始值 + status chip) ----
const metricCards = computed(() => {
  if (!buildingQuality.value) return []
  // 按 METRIC_META 顺序固定展示 (completeness -> duplicate -> invalid -> missing -> timezone -> unit)
  const order = ['completeness', 'duplicate_rate', 'invalid_value_rate', 'missing_gap_count', 'timezone_parse_error_count', 'unit_normalization_count']
  return order
    .map(key => {
      const m = buildingQuality.value!.metrics.find(x => x.metric === key)
      if (!m) return null
      const meta = METRIC_META[key]
      // 后端 _judge_status 偶尔会返枚举外的值 (或未来扩展新等级), STATUS_META[m.status]
      // 取到 undefined 时 ?? 兜底到 n/a, 不让前端整个面板崩
      const statusMeta = STATUS_META[m.status] ?? STATUS_META['n/a']
      // isRate=true 的指标后端返 0-1 小数, 前端展示 *100 转百分比 (0.9599 -> 95.99)
      // isRate=false 的指标后端返整数计数, 直接展示
      const displayValue = m.value != null && meta?.isRate
        ? Math.round(m.value * 10000) / 100  // *100 后保留 2 位小数
        : m.value
      return {
        key,
        label: meta?.label ?? key,
        value: displayValue,
        unit: meta?.unit ?? '',
        status: m.status,
        statusLabel: statusMeta.label,
        color: statusMeta.color,
        description: m.description,
        isNa: m.status === 'n/a',
      }
    })
    .filter(x => x !== null) as Array<{
      key: string
      label: string
      value: number | null
      unit: string
      status: QualityStatus
      statusLabel: string
      color: string
      description: string
      isNa: boolean
    }>
})

// ---- 加载/错误/空态合并 ----
const isOverallLoading = computed(() => loading.value || buildingQualityLoading.value)
const overallError = computed(() => error.value ?? buildingQualityError.value)
const isEmpty = computed(
  () => !buildings.value || buildings.value.buildings.length === 0,
)

const subtitle = computed(() => {
  if (!selectedBuildingId.value) return '请选择建筑'
  const b = buildings.value?.buildings.find(b => b.building_id === selectedBuildingId.value)
  if (!b) return ''
  // subtitle 只放短楼名 + 指标数, display_name 已在 a-select 选项里显示不重复
  // (160px a-select 在右侧, 长 subtitle 会被挤掉)
  const shortName = shortBuildingName(b.building_code)
  return `${shortName} · 6 项数据质量指标`
})

function initRadar() {
  if (!radarChartRef.value) return
  radarChart = echarts.init(radarChartRef.value, undefined, { renderer: 'svg' })
  radarChart.setOption(ECHARTS_THEME as EChartsOption)
  if (radarOption.value) {
    radarChart.setOption(radarOption.value as EChartsOption, { replaceMerge: ['series'] })
  }
  radarResizeObserver = new ResizeObserver(() => radarChart?.resize())
  radarResizeObserver.observe(radarChartRef.value)
}

function updateRadar() {
  if (!radarChart) return
  if (radarOption.value) {
    radarChart.setOption(radarOption.value as EChartsOption, { replaceMerge: ['series'] })
  } else {
    radarChart.clear()
  }
}

onMounted(() => {
  nextTick(initRadar)
  // 如果有 buildings, 立即触发选中 (loadBuildingQuality 由 watch 触发)
  if (buildingOptions.value.length > 0 && !selectedBuildingId.value) {
    selectedBuildingId.value = buildingOptions.value[0].value
  } else if (selectedBuildingId.value) {
    loadBuildingQuality()
  }
})

onBeforeUnmount(() => {
  radarResizeObserver?.disconnect()
  radarChart?.dispose()
  radarChart = null
})

watch(radarOption, updateRadar, { deep: true })

function onRetry() {
  reload()
  loadBuildingQuality()
}
</script>

<template>
  <ChartCard
    title="数据质量面板"
    :subtitle="subtitle"
    :loading="isOverallLoading"
    :error="overallError"
    :empty="isEmpty"
    :height="480"
    @retry="onRetry"
  >
    <template #extra>
      <a-select
        v-model:value="selectedBuildingId"
        size="small"
        style="width: 160px"
        :options="buildingOptions"
        placeholder="选择建筑"
        :loading="loading"
      />
    </template>

    <div class="quality-panel">
      <div class="quality-panel__radar">
        <div ref="radarChartRef" class="quality-panel__radar-dom" />
      </div>
      <div class="quality-panel__cards">
        <div
          v-for="card in metricCards"
          :key="card.key"
          class="quality-card"
          :class="{ 'quality-card--na': card.isNa }"
          :title="card.description"
        >
          <div class="quality-card__label-wrap">
            <span
              class="quality-card__status-dot"
              :style="{ background: card.color }"
            />
            <span class="quality-card__label">{{ card.label }}</span>
          </div>
          <div class="quality-card__value-wrap">
            <span class="quality-card__value">
              {{ card.value != null ? card.value : '不适用' }}
            </span>
            <span v-if="card.value != null" class="quality-card__unit">{{ card.unit }}</span>
          </div>
        </div>
      </div>
    </div>
  </ChartCard>
</template>

<style scoped lang="scss">
.quality-panel {
  // 上下两行: 上雷达图 (3fr) 下 6 卡 2x3 网格 (2fr)
  // 之前左右切分雷达区仅 160px, 标签 "异常值率"/"缺失段数" 被卡片边框裁切
  // 改上下后雷达占满宽度 ~309px, 半径 60% 标签完整显示, 雷达图也更大
  display: grid;
  grid-template-rows: 3fr 2fr;
  gap: $space-3;
  height: 100%;
  min-height: 0;
  padding: $space-2 0;

  &__radar {
    height: 100%;
    min-height: 0;
    display: flex;
    align-items: center;
    justify-content: center;
  }

  &__radar-dom {
    width: 100%;
    height: 100%;
  }

  // 6 张卡 2 列 3 行网格, 每卡水平布局 (label 左 + value 右)
  // 上下布局后卡片区占满宽度 ~309px, 2 列每卡 ~150px 够水平排
  &__cards {
    display: grid;
    grid-template-columns: 1fr 1fr;
    grid-template-rows: repeat(3, 1fr);
    gap: $space-2;
    min-height: 0;
  }
}

.quality-card {
  // 横向布局: status dot + label 在左, value + unit 在右
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: $space-2;
  padding: $space-1 $space-3;
  border: 1px solid $gray-200;
  border-radius: $radius-sm;
  background: $color-card;
  transition: border-color $transition-fast, transform $transition-fast;
  cursor: default;
  min-height: 0;

  &:hover {
    border-color: $color-amber;
    transform: translateY(-1px);
  }

  // N/A 卡片整张灰显 (status === 'n/a'), 不再像报错
  &--na {
    background: $gray-50;
    border-color: $gray-200;

    .quality-card__value,
    .quality-card__label {
      color: $gray-400;
    }

    &:hover {
      border-color: $gray-300;
      transform: none;
    }
  }

  &__label-wrap {
    display: flex;
    align-items: center;
    gap: $space-2;
    min-width: 0;
    flex: 1;
  }

  &__status-dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    flex-shrink: 0;
  }

  &__label {
    font-size: $fs-xs;
    color: $color-text-secondary;
    line-height: $lh-tight;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__value-wrap {
    display: flex;
    align-items: baseline;
    gap: 2px;
    flex-shrink: 0;
  }

  &__value {
    font-family: $font-mono;
    font-size: $fs-sm;
    font-weight: $fw-semibold;
    color: $color-concrete;
    line-height: $lh-tight;
  }

  &__unit {
    font-size: 10px;
    color: $color-text-secondary;
    font-weight: $fw-regular;
  }
}
</style>
