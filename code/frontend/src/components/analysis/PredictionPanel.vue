<script setup lang="ts">
// ============================================================================
// PredictionPanel - 能耗预测面板
// ----------------------------------------------------------------------------
// 嵌入 Analysis.vue 2x4 网格第 7 位。三种模型 (Prophet/LSTM/linear) 切换,
// 异步 worker 跑预测, 前端轮询 job 状态直到 SUCCEEDED/FAILED。
//
// 数据流:
//   1. 进入面板: 调 getLatest(buildingId, {model_type}) 拿缓存, 有则直接显示
//   2. 点预测按钮: createForecast(buildingId, {horizon, model_type}) 拿 job_id
//      -> 每 2s 轮询 getJob(jobId) 直到 status=SUCCEEDED/FAILED
//   3. 切模型: 先查 latest 缓存, 没有则让用户手动触发
//
// 图表: ECharts 折线图
//   - 实线: 历史实际值 (灰色)
//   - 虚线: 预测 yhat (琥珀)
//   - 阴影: 置信区间 yhat_lower ~ yhat_upper (淡琥珀)
//   - MAPE 显示在底部信息条, warning 训练数据不足时显示警告条
// ============================================================================

import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import * as echarts from 'echarts'
import type { EChartsOption } from 'echarts'
import { useContextStore } from '@/stores/context'
import { useAnalysisData } from '@/composables/useAnalysisData'
import { predictionApi, type PredictionJob, type PredictionModel } from '@/api/prediction'
import ChartCard from './ChartCard.vue'
import {
  ECHARTS_THEME,
  ECHARTS_COLORS,
  shortBuildingName,
  formatNumber,
} from '@/styles/echarts-theme'
import { TrendingUp, AlertTriangle, Cpu, Sparkles, BarChart3 } from 'lucide-vue-next'

const context = useContextStore()
const { buildings } = useAnalysisData()

// ---- 选中建筑 + 模型 + horizon ----
// 默认选第一栋楼 (buildings 加载后 watch 设)
const selectedBuildingId = ref<string>('')
const selectedModel = ref<PredictionModel>('prophet')
const horizonDays = ref<number>(30)

// 模型选项 (跟后端 mart.prediction_job.model_type CHECK 约束对齐)
const MODEL_OPTIONS: Array<{
  value: PredictionModel
  label: string
  desc: string
  icon: typeof Cpu
}> = [
  { value: 'prophet', label: 'Prophet', desc: 'Facebook 时序主力', icon: Sparkles },
  { value: 'lstm', label: 'LSTM', desc: 'PyTorch demo', icon: Cpu },
  { value: 'linear', label: 'Linear', desc: '线性基线', icon: BarChart3 },
  { value: 'gbm', label: 'GBM', desc: '天气驱动 (ML)', icon: Cpu },
]

// gbm 特征 -> 中文名 (前端展示用)
const FEATURE_LABEL: Record<string, string> = {
  day_of_week: '星期几',
  is_weekend: '是否周末',
  month: '月份(季节)',
  day_of_year: '年内第几天',
  air_temp_c: '气温',
  dew_temp_c: '露点温度',
  wind_speed_mps: '风速',
  cloud_cover_pct: '云量',
}

const HORIZON_OPTIONS = [7, 14, 30, 60, 90]

// ---- 数据状态 ----
const job = ref<PredictionJob | null>(null)
const submitting = ref(false)
const polling = ref(false)
const errorMsg = ref<string | null>(null)
let pollTimer: ReturnType<typeof setTimeout> | null = null

// 楼列表 (按 building_code 排序方便找)
const buildingList = computed(() => {
  const list = buildings.value?.buildings ?? []
  return [...list].sort((a, b) => a.building_code.localeCompare(b.building_code))
})

// 默认选中第一栋楼
watch(buildingList, (list) => {
  if (!selectedBuildingId.value && list.length > 0) {
    selectedBuildingId.value = list[0].building_id
  }
}, { immediate: true })

// 楼切换时自动拉缓存 (拿当前模型的 latest, 没有就清空)
watch([selectedBuildingId, selectedModel], async () => {
  if (!selectedBuildingId.value) return
  await loadLatest()
})

// ---- 拉 latest 缓存 ----
async function loadLatest() {
  if (!selectedBuildingId.value) return
  errorMsg.value = null
  try {
    const data = await predictionApi.getLatest(selectedBuildingId.value, {
      model_type: selectedModel.value,
    })
    // 后端返 null = 该楼没跑过该模型的预测, 清空让用户手动触发
    job.value = data
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : '加载预测结果失败'
  }
}

// ---- 提交预测 + 轮询 ----
async function submitForecast() {
  if (!selectedBuildingId.value) {
    errorMsg.value = '请先选择建筑'
    return
  }
  submitting.value = true
  polling.value = false
  errorMsg.value = null

  try {
    const { job_id } = await predictionApi.createForecast(
      selectedBuildingId.value,
      { horizon_days: horizonDays.value, model_type: selectedModel.value },
    )
    // 拿到 job_id 后开始轮询
    polling.value = true
    await pollJobStatus(job_id)
  } catch (err) {
    errorMsg.value = err instanceof Error ? err.message : '提交预测失败'
  } finally {
    submitting.value = false
    polling.value = false
  }
}

// 轮询 job 状态, 每 2s 一次直到 SUCCEEDED/FAILED
async function pollJobStatus(jobId: string) {
  if (pollTimer) {
    clearTimeout(pollTimer)
    pollTimer = null
  }

  const POLL_INTERVAL = 2000
  const MAX_POLL_MS = 5 * 60 * 1000  // 5 分钟超时 (LSTM 50 epoch 单楼最多 60s, 留余量)
  const startTs = Date.now()

  return new Promise<void>((resolve, reject) => {
    const checkOnce = async () => {
      if (Date.now() - startTs > MAX_POLL_MS) {
        errorMsg.value = '预测超时, 请稍后重试'
        reject(new Error('poll timeout'))
        return
      }
      try {
        const data = await predictionApi.getJob(jobId)
        job.value = data
        if (data.status === 'SUCCEEDED') {
          resolve()
          return
        }
        if (data.status === 'FAILED') {
          errorMsg.value = data.error_message || '预测失败'
          reject(new Error(data.error_message || 'prediction failed'))
          return
        }
        // PENDING / RUNNING: 继续轮询
        pollTimer = setTimeout(checkOnce, POLL_INTERVAL)
      } catch (err) {
        errorMsg.value = err instanceof Error ? err.message : '轮询任务状态失败'
        reject(err)
      }
    }
    checkOnce()
  })
}

onBeforeUnmount(() => {
  if (pollTimer) clearTimeout(pollTimer)
})

// ---- ECharts 实例 ----
const chartRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const chartOption = computed<EChartsOption | null>(() => {
  if (!job.value || !job.value.result) return null
  const result = job.value.result

  // 合并历史 + 预测日期轴 (历史最后一天和预测第一天之间画一条虚线分隔)
  const historyDates = result.history.map(p => p.date)
  const forecastDates = result.forecast.map(p => p.date)
  const allDates = [...historyDates, ...forecastDates]

  // 历史值在合并轴上对齐位置 (前面 history.length 个, 后面填 null 不画)
  const historyValues = result.history.map(p => p.value)
  const historyAligned = [
    ...historyValues,
    ...new Array(forecastDates.length).fill(null),
  ]

  // 预测值在合并轴上对齐 (前面 history.length - 1 个 null, 然后接 forecast[0] 让线连起来)
  // 第一个预测点接到历史最后一个点上, 视觉上没有断口
  const forecastAligned = [
    ...new Array(Math.max(0, historyDates.length - 1)).fill(null),
    result.history.length > 0 ? result.history[result.history.length - 1].value : null,
    ...result.forecast.map(p => p.yhat),
  ]

  // 置信区间 (Prophet 才有, LSTM/linear 都 null)
  // 画两条线, 上界和下界之间用 stack + areaStyle 填色
  const hasInterval = result.forecast.some(p => p.yhat_lower !== null && p.yhat_upper !== null)
  const lowerAligned = hasInterval ? [
    ...new Array(Math.max(0, historyDates.length - 1)).fill(null),
    result.history.length > 0 ? result.history[result.history.length - 1].value : null,
    ...result.forecast.map(p => p.yhat_lower),
  ] : []
  // upper 减去 lower 后做 stack (ECharts stack 是累加, 上界要减下界才对)
  const upperDelta = hasInterval ? [
    ...new Array(Math.max(0, historyDates.length - 1)).fill(null),
    0,
    ...result.forecast.map(p => {
      if (p.yhat_lower === null || p.yhat_upper === null) return 0
      return p.yhat_upper - p.yhat_lower
    }),
  ] : []

  const series: EChartsOption['series'] = [
    {
      name: '历史',
      type: 'line',
      data: historyAligned,
      smooth: true,
      symbol: 'none',
      showSymbol: false,
      lineStyle: { width: 2, color: ECHARTS_COLORS.concrete, type: 'solid' },
      itemStyle: { color: ECHARTS_COLORS.concrete },
    },
    {
      name: '预测',
      type: 'line',
      data: forecastAligned,
      smooth: true,
      symbol: 'circle',
      symbolSize: 4,
      showSymbol: false,
      lineStyle: { width: 2, color: ECHARTS_COLORS.amber, type: 'dashed' },
      itemStyle: { color: ECHARTS_COLORS.amber },
    },
  ]

  if (hasInterval) {
    // 置信区间: 下界先画一条透明线, 上界减下界后 stack 上去, 中间 areaStyle 填色
    series.push({
      name: '置信区间下界',
      type: 'line',
      data: lowerAligned,
      stack: 'confidence-band',
      symbol: 'none',
      showSymbol: false,
      lineStyle: { opacity: 0 },
      areaStyle: { color: 'transparent' },
      z: 1,
    } as any)
    series.push({
      name: '置信区间',
      type: 'line',
      data: upperDelta,
      stack: 'confidence-band',
      symbol: 'none',
      showSymbol: false,
      lineStyle: { opacity: 0 },
      areaStyle: { color: 'rgba(212, 155, 59, 0.15)' },
      z: 1,
    } as any)
  }

  return {
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'line' },
      formatter: (params: any) => {
        if (!params || params.length === 0) return ''
        const date = params[0]?.axisValue ?? ''
        let html = `<b>${date}</b>`
        for (const p of params) {
          const v = p.value
          if (v === null || v === undefined) continue
          const val = typeof v === 'number' ? formatNumber(v) : v
          html += `<br/>${p.marker} ${p.seriesName}: <b>${val}</b>`
        }
        return html
      },
    },
    legend: {
      data: ['历史', '预测'],
      top: 0,
      right: 8,
      textStyle: { fontSize: 11, color: ECHARTS_COLORS.textSecondary },
      icon: 'roundRect',
      itemWidth: 12,
      itemHeight: 6,
    },
    grid: { left: 12, right: 16, top: 28, bottom: 24, containLabel: true },
    xAxis: {
      type: 'category',
      data: allDates,
      boundaryGap: false,
      axisLabel: {
        formatter: (val: string) => val.length >= 10 ? val.slice(5, 10) : val,
      },
    },
    yAxis: {
      type: 'value',
      name: '日能耗 (kWh)',
      nameTextStyle: { fontSize: 11, color: ECHARTS_COLORS.textSecondary },
    },
    series,
  }
})

function initChart() {
  if (!chartRef.value) return
  chart = echarts.init(chartRef.value, undefined, { renderer: 'svg' })
  chart.setOption(ECHARTS_THEME as EChartsOption)
  if (chartOption.value) {
    chart.setOption(chartOption.value as EChartsOption, { replaceMerge: ['series'] })
  }
  resizeObserver = new ResizeObserver(() => chart?.resize())
  resizeObserver.observe(chartRef.value)
}

function updateChart() {
  if (!chart) return
  if (chartOption.value) {
    chart.setOption(chartOption.value as EChartsOption, { replaceMerge: ['series'] })
  } else {
    chart.clear()
  }
}

onMounted(() => {
  nextTick(initChart)
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
  chart = null
})

watch(chartOption, updateChart, { deep: true })

// ---- 三态 ----
const isLoading = computed(() => submitting.value || polling.value)
const loadingText = computed(() => {
  if (submitting.value) return '提交预测任务中...'
  if (polling.value) {
    const status = job.value?.status
    if (status === 'PENDING') return '排队等待 worker 拾取...'
    if (status === 'RUNNING') return `${selectedModel.value} 训练中...`
    return '预测运行中...'
  }
  return '加载中...'
})
const isEmpty = computed(() => !job.value || !job.value.result)
const emptyText = computed(() => {
  if (!selectedBuildingId.value) return '请选择建筑'
  return `尚无 ${selectedModel.value} 模型预测结果, 点击右上角按钮开始预测`
})

// MAPE 评级: <20% 优秀, 20-50% 可用, >50% 偏差较大
function mapeLevel(mape: number | null): { label: string; color: string } {
  if (mape === null) return { label: '-', color: ECHARTS_COLORS.textSecondary }
  if (mape < 20) return { label: '优秀', color: ECHARTS_COLORS.green }
  if (mape < 50) return { label: '可用', color: ECHARTS_COLORS.amber }
  return { label: '偏差较大', color: ECHARTS_COLORS.red }
}

const mapeInfo = computed(() => {
  const mape = job.value?.result?.mape ?? null
  return { value: mape, ...mapeLevel(mape) }
})

// 副标题 (建筑名 + 模型 + horizon)
const subtitle = computed(() => {
  const b = buildingList.value.find(x => x.building_id === selectedBuildingId.value)
  if (!b) return '请选择建筑'
  const modelName = MODEL_OPTIONS.find(m => m.value === selectedModel.value)?.label ?? selectedModel.value
  return `${shortBuildingName(b.building_code)} · ${modelName} · ${horizonDays.value} 天`
})

// 当前 job 状态文案
const jobStatusText = computed(() => {
  if (!job.value) return ''
  const map: Record<string, string> = {
    PENDING: '排队中',
    RUNNING: '训练中',
    SUCCEEDED: '已完成',
    FAILED: '失败',
  }
  return map[job.value.status] ?? job.value.status
})
</script>

<template>
  <ChartCard
    title="能耗预测"
    :subtitle="subtitle"
    :loading="isLoading"
    :loading-text="loadingText"
    :error="errorMsg"
    :empty="isEmpty"
    :empty-text="emptyText"
    :height="480"
    @retry="loadLatest"
  >
    <template #extra>
      <div class="pred-controls">
        <!-- 建筑选择 -->
        <select
          v-model="selectedBuildingId"
          class="pred-select pred-select--building"
          :disabled="isLoading"
        >
          <option v-for="b in buildingList" :key="b.building_id" :value="b.building_id">
            {{ shortBuildingName(b.building_code) }}
          </option>
        </select>

        <!-- 模型选择 (segmented) -->
        <div class="pred-model-group">
          <button
            v-for="opt in MODEL_OPTIONS"
            :key="opt.value"
            type="button"
            class="pred-model-btn"
            :class="{ 'is-active': selectedModel === opt.value }"
            :disabled="isLoading"
            :title="opt.desc"
            @click="selectedModel = opt.value"
          >
            <component :is="opt.icon" :size="12" />
            <span>{{ opt.label }}</span>
          </button>
        </div>

        <!-- horizon 选择 -->
        <select
          v-model.number="horizonDays"
          class="pred-select pred-select--horizon"
          :disabled="isLoading"
        >
          <option v-for="h in HORIZON_OPTIONS" :key="h" :value="h">{{ h }} 天</option>
        </select>

        <!-- 预测按钮 -->
        <button
          type="button"
          class="pred-submit-btn"
          :disabled="isLoading || !selectedBuildingId"
          @click="submitForecast"
        >
          <TrendingUp :size="12" />
          <span>{{ polling ? jobStatusText : '预测' }}</span>
        </button>
      </div>
    </template>

    <div class="pred">
      <!-- 图表区 -->
      <div ref="chartRef" class="pred__chart" />

      <!-- 底部信息条 -->
      <div v-if="job?.result" class="pred__footer">
        <div class="pred-metric">
          <span class="pred-metric__label">MAPE</span>
          <span class="pred-metric__value" :style="{ color: mapeInfo.color }">
            {{ mapeInfo.value !== null ? mapeInfo.value.toFixed(2) + '%' : '-' }}
          </span>
          <span class="pred-metric__badge" :style="{ color: mapeInfo.color, borderColor: mapeInfo.color }">
            {{ mapeInfo.label }}
          </span>
        </div>
        <div class="pred-metric">
          <span class="pred-metric__label">训练数据</span>
          <span class="pred-metric__value">
            {{ job.result.history.length }} 天
          </span>
        </div>
        <div class="pred-metric">
          <span class="pred-metric__label">预测区间</span>
          <span class="pred-metric__value">
            {{ horizonDays }} 天
          </span>
        </div>
        <div v-if="job.finished_at" class="pred-metric">
          <span class="pred-metric__label">完成时间</span>
          <span class="pred-metric__value pred-metric__value--time">
            {{ new Date(job.finished_at).toLocaleString('zh-CN', { hour12: false }) }}
          </span>
        </div>
      </div>

      <!-- warning 警告条 -->
      <div v-if="job?.result?.warning" class="pred-warning">
        <AlertTriangle :size="12" />
        <span>{{ job.result.warning }}</span>
      </div>

      <!-- 特征重要性 (gbm 模型) -->
      <div v-if="job?.result?.feature_importances?.length" class="pred-importance">
        <div class="pred-importance__title">能耗驱动因子 (GBM 特征重要性)</div>
        <div class="pred-importance__list">
          <div
            v-for="f in job.result.feature_importances"
            :key="f.feature"
            class="pred-importance__row"
          >
            <span class="pred-importance__label">{{ FEATURE_LABEL[f.feature] ?? f.feature }}</span>
            <div class="pred-importance__track">
              <div class="pred-importance__bar" :style="{ width: `${Math.round(f.importance * 100)}%` }" />
            </div>
            <span class="pred-importance__val">{{ (f.importance * 100).toFixed(1) }}%</span>
          </div>
        </div>
      </div>
    </div>
  </ChartCard>
</template>

<style scoped lang="scss">
.pred {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  gap: $space-2;

  &__chart {
    flex: 1;
    min-height: 0;
    width: 100%;
  }

  &__footer {
    display: flex;
    flex-wrap: wrap;
    gap: $space-3;
    padding: $space-2 $space-1 0;
    border-top: 1px dashed $gray-200;
    flex-shrink: 0;
  }

  &__warning {
    display: flex;
    align-items: center;
    gap: $space-1;
    padding: $space-1 $space-2;
    background: $color-amber-soft;
    border: 1px solid rgba($color-amber, 0.4);
    border-radius: $radius-xs;
    color: $color-amber-deep;
    font-size: $fs-xs;
    flex-shrink: 0;
  }
}

.pred-importance {
  flex-shrink: 0;
  padding-top: $space-2;
  border-top: 1px dashed $gray-200;

  &__title {
    font-size: $fs-xs;
    font-weight: $fw-medium;
    color: $color-concrete;
    margin-bottom: $space-2;
  }

  &__list {
    display: flex;
    flex-direction: column;
    gap: 5px;
  }

  &__row {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-xs;
  }

  &__label {
    width: 72px;
    flex-shrink: 0;
    color: $color-text-secondary;
  }

  &__track {
    flex: 1;
    height: 8px;
    background: $gray-100;
    border-radius: $radius-pill;
    overflow: hidden;
  }

  &__bar {
    height: 100%;
    background: linear-gradient(90deg, $color-amber, $color-amber-deep);
    border-radius: $radius-pill;
  }

  &__val {
    width: 44px;
    flex-shrink: 0;
    text-align: right;
    font-family: $font-mono;
    color: $color-concrete;
  }
}

.pred-controls {
  display: flex;
  align-items: center;
  gap: $space-1;
  flex-wrap: nowrap;
}

.pred-select {
  height: 24px;
  padding: 0 8px;
  border: 1px solid $gray-200;
  border-radius: $radius-xs;
  background: $color-card;
  font-size: $fs-xs;
  color: $color-concrete;
  font-family: inherit;
  cursor: pointer;
  transition: border-color $transition-fast;

  &:hover:not(:disabled) {
    border-color: $color-amber;
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  &--building {
    min-width: 90px;
  }

  &--horizon {
    min-width: 64px;
  }
}

.pred-model-group {
  display: inline-flex;
  border: 1px solid $gray-200;
  border-radius: $radius-xs;
  overflow: hidden;
  background: $color-card;
}

.pred-model-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 8px;
  height: 24px;
  border: none;
  background: transparent;
  font-size: $fs-xs;
  color: $color-text-secondary;
  cursor: pointer;
  transition: all $transition-fast;
  font-family: inherit;

  &:not(:last-child) {
    border-right: 1px solid $gray-200;
  }

  &:hover:not(:disabled):not(.is-active) {
    background: $gray-50;
    color: $color-concrete;
  }

  &.is-active {
    background: $color-amber;
    color: white;
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}

.pred-submit-btn {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 0 12px;
  height: 24px;
  background: $color-amber;
  border: none;
  border-radius: $radius-xs;
  color: white;
  font-size: $fs-xs;
  font-weight: $fw-medium;
  cursor: pointer;
  transition: background $transition-fast, transform $transition-fast;
  font-family: inherit;
  white-space: nowrap;

  &:hover:not(:disabled) {
    background: $color-amber-hover;
  }

  &:active:not(:disabled) {
    transform: translateY(1px);
  }

  &:disabled {
    background: $gray-300;
    cursor: not-allowed;
  }
}

.pred-metric {
  display: inline-flex;
  align-items: baseline;
  gap: 4px;
  font-size: $fs-xs;

  &__label {
    color: $color-text-secondary;
  }

  &__value {
    font-family: $font-mono;
    font-weight: $fw-semibold;
    color: $color-concrete;
    font-size: $fs-sm;

    &--time {
      font-size: $fs-xs;
      font-weight: $fw-medium;
      color: $color-text-secondary;
    }
  }

  &__badge {
    margin-left: 4px;
    padding: 1px 6px;
    border: 1px solid;
    border-radius: $radius-pill;
    font-size: 10px;
    font-weight: $fw-medium;
  }
}
</style>
