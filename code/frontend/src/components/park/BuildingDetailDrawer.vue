<script setup lang="ts">
// ============================================================================
// BuildingDetailDrawer - 建筑详情右侧抽屉
// ----------------------------------------------------------------------------
// 4 个 tab:
//   概览: 建筑基本信息卡 + 3 个关键指标卡 (总能耗 / EUI / 异常数)
//   能耗: 日能耗时序折线 + 能源构成饼图 + 24h×星期热力图 (典型周) + 能耗-气温散点
//   异常: 异常事件列表 (调 GET /anomalies/buildings/{id}) + 严重度徽章
//   助手: 占位 + 按钮"在 AI 抽屉继续问"
//
// 抽屉从右侧滑出, 宽度 480px (比标准 400 略宽, 装下 ECharts 图表)。
// 关闭时不清数据 (下次打开同样 building 不重新拉)。
// ============================================================================

import { ref, computed, watch, onMounted, onBeforeUnmount, nextTick } from 'vue'
import { useRouter } from 'vue-router'
import { Drawer as ADrawer, Tabs as ATabs, TabPane as ATabPane, Empty as AEmpty, Spin as ASpin, Tag as ATag, Button as AButton, Slider as ASlider, message as AMessage } from 'ant-design-vue'
import * as echarts from 'echarts'
import { Building2, Zap, Gauge, AlertTriangle, Bot, X, ChevronRight, RotateCw, TrendingUp, Sparkles } from 'lucide-vue-next'
import { useParkStore } from '@/stores/park'
import { useContextStore } from '@/stores/context'
import { useAiStore } from '@/stores/ai'
import { anomalyApi, type AnomalyEvent, type BuildingAnomalyList } from '@/api/anomaly'
import { queryApi } from '@/api/query'
import { visualApi } from '@/api/visual'
import { predictionApi, type PredictionJob } from '@/api/prediction'
import OptimizationPlanCard from '@/components/ai-drawer/OptimizationPlanCard.vue'
import type { OptimizationPlan } from '@/api/assistant'
import { levelToColor, LEVEL_LABEL, severityToColor } from '@/utils/sceneTransform'
import dayjs from 'dayjs'

const props = defineProps<{
  open: boolean
}>()

const emit = defineEmits<{
  (e: 'update:open', v: boolean): void
  (e: 'openAiDrawer'): void
}>()

const park = useParkStore()
const context = useContextStore()
const router = useRouter()
const aiStore = useAiStore()

const activeTab = ref<'overview' | 'energy' | 'prediction' | 'anomaly' | 'optimization'>('overview')
const anomalyLoading = ref(false)
const anomalyList = ref<BuildingAnomalyList | null>(null)
const energyLoading = ref(false)
const energyTimeseries = ref<Array<{ ts: string; value: number }>>([])
// 小时级时序 (热力图用) + 天气读数 (气温散点用), 跟 energyTimeseries 一起拉
const hourlyTimeseries = ref<Array<{ ts: string; value: number }>>([])
const weatherPoints = ref<Array<{ ts: string; air_temp_c: number | null }>>([])
const weatherR = ref<number | null>(null)  // 能耗-气温 Pearson 相关系数, 渲染时算
// GBM 能耗驱动因子 (特征重要性), 从最近一次 gbm 预测结果拿, 直接展示在能耗 tab
const energyDrivers = ref<Array<{ feature: string; importance: number }> | null>(null)

const WEEKDAYS = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']

const building = computed(() => park.selectedBuilding)
const visible = computed({
  get: () => props.open,
  set: (v) => emit('update:open', v),
})

// ---- 水平旋转 (yaw_deg) ----
// 滑块拖动时本地 optimistic update (park.setBuildingYaw), BuildingSplat
// watch 触发重渲毫秒级响应; 同时 debounce 400ms 后调 PATCH /yaw 落库,
// 避免拖动过程狂调 API。失败回滚 yaw 并提示。
const yawSaving = ref(false)
const yawSavingTimer = ref<number | null>(null)
const yawDisplay = ref(0)  // 滑块显示值 (跟 store 同步)

const canRotate = computed(() => {
  // 只在 splat 模式显示旋转: BuildingBlock 灰盒 axis-aligned 不旋转,
  // estimated 模式没真实模型也不需要。splat 模式才有"朝向不对要调"的需求。
  return building.value?.model_kind === 'splat'
})

// 同步 store 的 yaw_deg 到本地 yawDisplay (building 切换 / drawer 重开时)
watch(() => building.value?.building_id, () => {
  yawDisplay.value = building.value?.position?.yaw_deg ?? 0
}, { immediate: true })
watch(() => props.open, (open) => {
  if (open) yawDisplay.value = building.value?.position?.yaw_deg ?? 0
})

function onYawChange(val: number) {
  if (!building.value) return
  yawDisplay.value = val
  // 立即本地更新 (BuildingSplat watch 触发重渲)
  park.setBuildingYaw(building.value.building_id, val)
  // debounce 400ms 落库
  if (yawSavingTimer.value !== null) {
    window.clearTimeout(yawSavingTimer.value)
  }
  yawSavingTimer.value = window.setTimeout(async () => {
    if (!building.value) return
    yawSaving.value = true
    try {
      await visualApi.updateBuildingYaw(building.value.building_id, val)
    } catch (err: any) {
      // 失败回滚: 把 store 的 yaw 改回原值
      const original = building.value?.position?.yaw_deg ?? 0
      park.setBuildingYaw(building.value!.building_id, original)
      yawDisplay.value = original
      AMessage.error(err?.response?.data?.detail ?? '保存朝向失败, 已回滚')
    } finally {
      yawSaving.value = false
      yawSavingTimer.value = null
    }
  }, 400)
}

function onYawReset() {
  onYawChange(0)
}

// 跳楼层分析页 (FloorView), 带上 buildingId, 同时关抽屉
function goFloorView() {
  if (!building.value) return
  const buildingId = building.value.building_id
  visible.value = false
  router.push({ name: 'floor-view', params: { buildingId } })
}

onBeforeUnmount(() => {
  if (yawSavingTimer.value !== null) {
    window.clearTimeout(yawSavingTimer.value)
  }
})

// ---- 拉异常数据 ----
async function loadAnomalies() {
  if (!building.value) return
  anomalyLoading.value = true
  try {
    const range = context.currentRange
    anomalyList.value = await anomalyApi.listBuildingAnomalies(
      building.value.building_id,
      {
        start: range.start.toISOString(),
        end: range.end.toISOString(),
        limit: 100,
      },
    )
  } catch (err) {
    console.error('load anomalies failed', err)
  } finally {
    anomalyLoading.value = false
  }
}

// ---- 拉能耗 tab 数据 ----
// 进 tab 一次并行拉四份: 日时序 (折线) + 小时时序 (热力图) + 天气 (气温散点)
// + 最近一次 gbm 预测的能耗驱动因子 (特征重要性)
async function loadEnergyTabData() {
  if (!building.value) return
  energyLoading.value = true
  const range = context.currentRange
  const buildingId = building.value.building_id
  const start = range.start.toISOString()
  const end = range.end.toISOString()
  try {
    const [dayRes, hourRes, weatherRes, gbmJob] = await Promise.all([
      queryApi.buildingTimeseries(buildingId, { granularity: 'day', start, end }),
      queryApi.buildingTimeseries(buildingId, { granularity: 'hour', start, end }),
      queryApi.buildingWeather(buildingId, { start, end }),
      predictionApi.getLatest(buildingId, { model_type: 'gbm' }),
    ])
    energyTimeseries.value = dayRes.points ?? []
    hourlyTimeseries.value = hourRes.points ?? []
    weatherPoints.value = weatherRes.points ?? []
    // gbm 预测没跑过时 job 为 null, 驱动因子显示"去生成预测"提示
    energyDrivers.value = gbmJob?.result?.feature_importances ?? null
  } catch (err) {
    console.error('load energy tab data failed', err)
    energyTimeseries.value = []
    hourlyTimeseries.value = []
    weatherPoints.value = []
    energyDrivers.value = null
  } finally {
    energyLoading.value = false
  }
}

// ---- 预测 tab: 单楼能耗预测 ----
// 进 tab 先拉最近一次 SUCCEEDED 预测, 没有就给"生成预测"按钮 -> 提交 job -> 轮询。
const predictionLoading = ref(false)
const predicting = ref(false)  // 正在生成 (轮询中)
const predictionJob = ref<PredictionJob | null>(null)
const predictionError = ref<string | null>(null)
const predictionModelType = ref<'prophet' | 'lstm' | 'linear' | 'gbm'>('gbm')
let predictionPollTimer: number | null = null

// 模型类型中文名
const MODEL_TYPE_LABEL: Record<string, string> = {
  prophet: 'Prophet 时序',
  lstm: 'LSTM 神经网络',
  linear: '线性回归',
  gbm: 'GBM 天气驱动',
}

// gbm 特征 -> 中文名 (前端展示用)
const FEATURE_LABEL: Record<string, string> = {
  day_of_week: '星期几',
  is_weekend: '是否周末',
  month: '月份',
  day_of_year: '年内第几天',
  air_temp_c: '气温',
  dew_temp_c: '露点温度',
  wind_speed_mps: '风速',
  cloud_cover_pct: '云量',
}

async function loadPrediction() {
  if (!building.value) return
  predictionLoading.value = true
  predictionError.value = null
  try {
    predictionJob.value = await predictionApi.getLatest(building.value.building_id, {
      model_type: predictionModelType.value,
    })
  } catch (err) {
    predictionError.value = err instanceof Error ? err.message : '加载预测失败'
  } finally {
    predictionLoading.value = false
    await nextTick()
    renderPredictionChart()
  }
}

// 切换模型: 清掉当前结果, 拉该模型最近一次预测 (没有则显示"生成预测")
function onPredictionModelChange() {
  predictionJob.value = null
  predictionError.value = null
  loadPrediction()
}

async function generatePrediction() {
  if (!building.value) return
  predicting.value = true
  predictionError.value = null
  try {
    const { job_id } = await predictionApi.createForecast(building.value.building_id, {
      horizon_days: 30,
      model_type: predictionModelType.value,
    })
    pollPrediction(job_id)
  } catch (err) {
    predictionError.value = err instanceof Error ? err.message : '提交预测失败'
    predicting.value = false
  }
}

function pollPrediction(jobId: string) {
  const tick = async () => {
    try {
      const job = await predictionApi.getJob(jobId)
      if (job.status === 'SUCCEEDED') {
        predictionJob.value = job
        predicting.value = false
        await nextTick()
        renderPredictionChart()
        return
      }
      if (job.status === 'FAILED') {
        predictionError.value = job.error_message ?? '预测失败'
        predicting.value = false
        return
      }
      predictionPollTimer = window.setTimeout(tick, 2000)
    } catch (err) {
      predictionError.value = err instanceof Error ? err.message : '查询预测状态失败'
      predicting.value = false
    }
  }
  tick()
}

// ---- 节能建议 tab: 内嵌式生成四段方案 (不跳 AI 抽屉) ----
const optimizing = ref(false)
const optimizationError = ref<string | null>(null)
const optimizationResult = ref<{ plan: OptimizationPlan; messageId: string } | null>(null)

async function generateOptimizationPlan() {
  if (!building.value) return
  optimizing.value = true
  optimizationError.value = null
  try {
    const result = await aiStore.generateOptimization(
      building.value.building_id,
      building.value.display_name,
    )
    if (result) {
      optimizationResult.value = result
    } else {
      // store 只在"没 plan 也没任何流文本"时返 null (流被掐断等罕见情况),
      // AI 拒答 / GLM 报错的真实原因走 catch 分支显示
      optimizationError.value = '未能生成节能建议，请稍后重试，或到 AI 抽屉查看该次会话'
    }
  } catch (err) {
    optimizationError.value = err instanceof Error ? err.message : '生成节能建议失败'
  } finally {
    optimizing.value = false
  }
}

// ---- ECharts 初始化 ----
const energyChartRef = ref<HTMLDivElement | null>(null)
const compositionChartRef = ref<HTMLDivElement | null>(null)
const hourlyChartRef = ref<HTMLDivElement | null>(null)
const weatherChartRef = ref<HTMLDivElement | null>(null)
const predictionChartRef = ref<HTMLDivElement | null>(null)
let energyChart: echarts.ECharts | null = null
let compositionChart: echarts.ECharts | null = null
let hourlyChart: echarts.ECharts | null = null
let weatherChart: echarts.ECharts | null = null
let predictionChart: echarts.ECharts | null = null

function renderEnergyChart() {
  if (!energyChartRef.value || !building.value) return
  if (energyChart && energyChart.getDom() !== energyChartRef.value) {
    energyChart.dispose()
    energyChart = null
  }
  if (!energyChart) {
    energyChart = echarts.init(energyChartRef.value)
  }
  const data = energyTimeseries.value.map(p => [p.ts, p.value])
  energyChart.setOption({
    grid: { left: 48, right: 16, top: 24, bottom: 32 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const p = params[0]
        return `${dayjs(p.value[0]).format('MM-DD')}<br/>${p.value[1].toFixed(1)} kWh`
      },
    },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: '#D1CEC5' } },
      axisLabel: { color: '#8A8275', fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      name: 'kWh',
      nameTextStyle: { color: '#8A8275', fontSize: 11 },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: '#F2F0EA' } },
      axisLabel: { color: '#8A8275', fontSize: 11 },
    },
    series: [{
      type: 'line',
      data,
      smooth: true,
      symbol: 'none',
      lineStyle: { color: '#D49B3B', width: 2 },
      areaStyle: {
        color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
          { offset: 0, color: 'rgba(212, 155, 59, 0.25)' },
          { offset: 1, color: 'rgba(212, 155, 59, 0.02)' },
        ]),
      },
    }],
  })
}

function renderCompositionChart() {
  if (!compositionChartRef.value || !building.value) return
  if (compositionChart && compositionChart.getDom() !== compositionChartRef.value) {
    compositionChart.dispose()
    compositionChart = null
  }
  if (!compositionChart) {
    compositionChart = echarts.init(compositionChartRef.value)
  }
  const composition = building.value.energy_composition?.composition ?? []
  const data = composition.map(c => ({
    name: c.type,
    value: c.kwh,
    pct: c.pct,
  }))
  const ENERGY_COLORS: Record<string, string> = {
    electricity: '#D49B3B',
    hotwater: '#B84A3C',
    chilledwater: '#5A6B7C',
    gas: '#4A4A4A',
    water: '#3D7E6A',
    solar: '#7CB34A',
  }
  compositionChart.setOption({
    tooltip: {
      trigger: 'item',
      formatter: (p: any) => `${p.name}<br/>${(p.data.pct * 100).toFixed(1)}% (${p.value.toFixed(0)} kWh)`,
    },
    series: [{
      type: 'pie',
      radius: ['45%', '70%'],
      center: ['50%', '50%'],
      data,
      itemStyle: {
        borderColor: '#FFFFFF',
        borderWidth: 2,
      },
      color: composition.map(c => ENERGY_COLORS[c.type.toLowerCase()] ?? '#A8A294'),
      label: {
        formatter: '{b}\n{d}%',
        fontSize: 11,
        color: '#5A6B7C',
      },
    }],
  })
}

function renderHourlyHeatmap() {
  if (!hourlyChartRef.value || !building.value) return
  if (hourlyChart && hourlyChart.getDom() !== hourlyChartRef.value) {
    hourlyChart.dispose()
    hourlyChart = null
  }
  if (!hourlyChart) {
    hourlyChart = echarts.init(hourlyChartRef.value)
  }
  // 把范围内小时数据折叠成典型周: 每格 = 该星期几该小时的均值
  const sum = Array.from({ length: 7 }, () => Array(24).fill(0))
  const cnt = Array.from({ length: 7 }, () => Array(24).fill(0))
  for (const p of hourlyTimeseries.value) {
    const d = dayjs(p.ts)
    const dow = (d.day() + 6) % 7  // day() 0=Sunday, 转成 0=Monday
    sum[dow][d.hour()] += p.value
    cnt[dow][d.hour()] += 1
  }
  const matrix: Array<[number, number, number]> = []
  let max = 0
  for (let dow = 0; dow < 7; dow++) {
    for (let h = 0; h < 24; h++) {
      const avg = cnt[dow][h] > 0 ? sum[dow][h] / cnt[dow][h] : 0
      matrix.push([h, dow, Number(avg.toFixed(3))])
      if (avg > max) max = avg
    }
  }
  hourlyChart.setOption({
    grid: { left: 8, right: 8, top: 8, bottom: 56, containLabel: true },
    tooltip: {
      position: 'top',
      formatter: (p: any) => {
        const [h, dow, v] = p.value as [number, number, number]
        return `${WEEKDAYS[dow]} ${String(h).padStart(2, '0')}:00<br/>${v.toFixed(2)} kWh`
      },
    },
    xAxis: {
      type: 'category',
      data: Array.from({ length: 24 }, (_, i) => String(i).padStart(2, '0')),
      axisLine: { lineStyle: { color: '#D1CEC5' } },
      axisTick: { show: false },
      axisLabel: { color: '#8A8275', fontSize: 10, interval: 2 },
    },
    yAxis: {
      type: 'category',
      data: WEEKDAYS,
      axisLine: { lineStyle: { color: '#D1CEC5' } },
      axisTick: { show: false },
      axisLabel: { color: '#8A8275', fontSize: 11 },
    },
    visualMap: {
      min: 0,
      max: max > 0 ? max : 1,
      orient: 'horizontal',
      left: 'center',
      bottom: 0,
      itemWidth: 10,
      itemHeight: 100,
      textStyle: { color: '#8A8275', fontSize: 10 },
      inRange: { color: ['#F5F2EB', '#F5E6C8', '#D49B3B', '#8B5A1F'] },
    },
    series: [{
      type: 'heatmap',
      data: matrix,
      label: { show: false },
      itemStyle: { borderColor: '#FFFFFF', borderWidth: 1 },
    }],
  })
}

function renderWeatherScatter() {
  if (!weatherChartRef.value || !building.value) return
  if (weatherChart && weatherChart.getDom() !== weatherChartRef.value) {
    weatherChart.dispose()
    weatherChart = null
  }
  if (!weatherChart) {
    weatherChart = echarts.init(weatherChartRef.value)
  }
  // 小时气温按日期聚成日均温, 跟日能耗配成 (温度, 能耗) 点对
  const tempByDay = new Map<string, { sum: number; cnt: number }>()
  for (const p of weatherPoints.value) {
    if (p.air_temp_c == null) continue
    const key = dayjs(p.ts).format('YYYY-MM-DD')
    const t = tempByDay.get(key) ?? { sum: 0, cnt: 0 }
    t.sum += p.air_temp_c
    t.cnt += 1
    tempByDay.set(key, t)
  }
  const pairs: Array<[number, number]> = []
  for (const p of energyTimeseries.value) {
    const t = tempByDay.get(dayjs(p.ts).format('YYYY-MM-DD'))
    if (t && t.cnt > 0) pairs.push([t.sum / t.cnt, p.value])
  }

  // 最小二乘拟合 y = a + b·x 当趋势线; Pearson r 放进标题
  const series: any[] = [{
    type: 'scatter',
    data: pairs,
    symbolSize: 6,
    itemStyle: { color: 'rgba(212, 155, 59, 0.7)', borderColor: '#D49B3B' },
  }]
  weatherR.value = null
  if (pairs.length >= 2) {
    const n = pairs.length
    const sx = pairs.reduce((s, p) => s + p[0], 0)
    const sy = pairs.reduce((s, p) => s + p[1], 0)
    const sxx = pairs.reduce((s, p) => s + p[0] * p[0], 0)
    const sxy = pairs.reduce((s, p) => s + p[0] * p[1], 0)
    const syy = pairs.reduce((s, p) => s + p[1] * p[1], 0)
    const b = (n * sxy - sx * sy) / (n * sxx - sx * sx)
    const a = (sy - b * sx) / n
    const rNum = (n * sxy - sx * sy) / Math.sqrt((n * sxx - sx * sx) * (n * syy - sy * sy))
    if (Number.isFinite(b) && Number.isFinite(rNum)) {
      weatherR.value = rNum
      const minX = Math.min(...pairs.map((p) => p[0]))
      const maxX = Math.max(...pairs.map((p) => p[0]))
      series.push({
        type: 'line',
        data: [[minX, a + b * minX], [maxX, a + b * maxX]],
        symbol: 'none',
        silent: true,
        lineStyle: { color: '#5A6B7C', width: 1.5, type: 'dashed' },
      })
    }
  }

  weatherChart.setOption({
    grid: { left: 48, right: 16, top: 24, bottom: 32 },
    tooltip: {
      trigger: 'item',
      formatter: (p: any) => {
        if (p.seriesType !== 'scatter') return ''
        const [t, v] = p.value as [number, number]
        return `日均温 ${t.toFixed(1)} °C<br/>日能耗 ${v.toFixed(0)} kWh`
      },
    },
    xAxis: {
      type: 'value',
      name: '°C',
      nameTextStyle: { color: '#8A8275', fontSize: 11 },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: '#F2F0EA' } },
      axisLabel: { color: '#8A8275', fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      name: 'kWh',
      nameTextStyle: { color: '#8A8275', fontSize: 11 },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: '#F2F0EA' } },
      axisLabel: { color: '#8A8275', fontSize: 11 },
    },
    series,
  })
}

function renderPredictionChart() {
  if (!predictionChartRef.value || !building.value) return
  const result = predictionJob.value?.result
  if (!result) return
  // 切模型/切楼时 chart div 被卸载再重挂 (v-else 空态切换), ECharts 实例还绑在
  // 旧 DOM 上, setOption 会画到已脱离文档的节点 -> 显示不出图。DOM 变了就销毁重建。
  if (predictionChart && predictionChart.getDom() !== predictionChartRef.value) {
    predictionChart.dispose()
    predictionChart = null
  }
  if (!predictionChart) {
    predictionChart = echarts.init(predictionChartRef.value)
  }

  const history = result.history.map(p => [p.date, p.value])
  const forecast = result.forecast.map(p => [p.date, p.yhat])
  // 置信区间: ECharts 没有"两条线之间填充"的原生支持, 用 stack 技巧:
  // 下界 invisible + (上界-下界) 堆叠成一条面积带
  const bandLower = result.forecast.map(p => [p.date, p.yhat_lower ?? p.yhat])
  const bandUpper = result.forecast.map(p => [p.date, (p.yhat_upper ?? p.yhat) - (p.yhat_lower ?? p.yhat)])

  predictionChart.setOption({
    grid: { left: 48, right: 16, top: 24, bottom: 32 },
    tooltip: {
      trigger: 'axis',
      formatter: (params: any) => {
        const items = params.filter((p: any) => p.seriesName === '历史' || p.seriesName === '预测')
        if (items.length === 0) return ''
        return items
          .map((p: any) => `${dayjs(p.value[0]).format('MM-DD')}<br/>${p.seriesName}: ${Number(p.value[1]).toFixed(1)} kWh`)
          .join('<br/>')
      },
    },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: '#D1CEC5' } },
      axisLabel: { color: '#8A8275', fontSize: 11 },
    },
    yAxis: {
      type: 'value',
      name: 'kWh',
      nameTextStyle: { color: '#8A8275', fontSize: 11 },
      axisLine: { show: false },
      splitLine: { lineStyle: { color: '#F2F0EA' } },
      axisLabel: { color: '#8A8275', fontSize: 11 },
    },
    series: [
      {
        name: '历史',
        type: 'line',
        data: history,
        smooth: true,
        symbol: 'none',
        lineStyle: { color: '#D49B3B', width: 2 },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: 'rgba(212, 155, 59, 0.2)' },
            { offset: 1, color: 'rgba(212, 155, 59, 0.02)' },
          ]),
        },
      },
      {
        name: '置信下界',
        type: 'line',
        data: bandLower,
        stack: 'band',
        symbol: 'none',
        lineStyle: { opacity: 0 },
        silent: true,
      },
      {
        name: '置信区间',
        type: 'line',
        data: bandUpper,
        stack: 'band',
        symbol: 'none',
        lineStyle: { opacity: 0 },
        areaStyle: { color: 'rgba(90, 107, 124, 0.15)' },
        silent: true,
      },
      {
        name: '预测',
        type: 'line',
        data: forecast,
        smooth: true,
        symbol: 'none',
        lineStyle: { color: '#5A6B7C', width: 2, type: 'dashed' },
      },
    ],
  })
}

// 监听 tab 切换: 切到能耗/预测/异常才拉数据 (节能建议是按钮按需触发)
watch(activeTab, async (tab) => {
  if (tab === 'energy' && energyTimeseries.value.length === 0) {
    await loadEnergyTabData()
    await nextTick()
    renderEnergyChart()
    renderCompositionChart()
    renderHourlyHeatmap()
    renderWeatherScatter()
  }
  if (tab === 'prediction' && !predictionJob.value && !predicting.value) {
    await loadPrediction()
  }
  if (tab === 'anomaly' && !anomalyList.value) {
    await loadAnomalies()
  }
})

// 监听抽屉打开: 切到默认 tab, 重置数据
watch(() => props.open, async (open) => {
  if (open) {
    activeTab.value = 'overview'
    energyTimeseries.value = []
    hourlyTimeseries.value = []
    weatherPoints.value = []
    weatherR.value = null
    anomalyList.value = null
    predictionJob.value = null
    optimizationResult.value = null
    optimizationError.value = null
  }
})

// 监听 selected building 变化: 重置数据
watch(() => park.selectedBuildingId, () => {
  energyTimeseries.value = []
  hourlyTimeseries.value = []
  weatherPoints.value = []
  weatherR.value = null
  anomalyList.value = null
  predictionJob.value = null
  optimizationResult.value = null
  optimizationError.value = null
  if (props.open) {
    // 切换建筑后如果当前在能耗/预测/异常 tab, 重新拉
    if (activeTab.value === 'energy') {
      loadEnergyTabData().then(() => nextTick(() => {
        renderEnergyChart()
        renderCompositionChart()
        renderHourlyHeatmap()
        renderWeatherScatter()
      }))
    } else if (activeTab.value === 'prediction') {
      loadPrediction()
    } else if (activeTab.value === 'anomaly') {
      loadAnomalies()
    }
  }
})

// 窗口 resize 时 resize ECharts
function onResize() {
  energyChart?.resize()
  compositionChart?.resize()
  hourlyChart?.resize()
  weatherChart?.resize()
  predictionChart?.resize()
}
onMounted(() => window.addEventListener('resize', onResize))
onBeforeUnmount(() => {
  window.removeEventListener('resize', onResize)
  energyChart?.dispose()
  compositionChart?.dispose()
  hourlyChart?.dispose()
  weatherChart?.dispose()
  predictionChart?.dispose()
  if (predictionPollTimer !== null) {
    window.clearTimeout(predictionPollTimer)
    predictionPollTimer = null
  }
})

// ---- 数据格式化 ----
function formatMetricValue(value: number | null, metric: string): string {
  if (value === null || value === undefined) return '-'
  if (metric === 'total_kwh') {
    if (value >= 10000) return (value / 1000).toFixed(1) + 'k'
    return value.toFixed(0)
  }
  if (metric === 'eui') return value.toFixed(1)
  return value.toFixed(0)
}

function metricUnit(metric: string): string {
  if (metric === 'eui') return 'kWh/m²'
  if (metric === 'total_kwh') return 'kWh'
  if (metric === 'anomaly_count') return '条'
  return ''
}

function anomalyTypeLabel(type: string): string {
  const map: Record<string, string> = {
    SPIKE: '瞬时尖峰',
    DRIFT: '缓慢漂移',
    PROLONGED_ZERO: '持续为零',
    MISSING_GAP: '缺测时段',
    SCHEDULE_VIOLATION: '作息违反',
    BASELINE_DEVIATION: '横向偏离',
    ML_OUTLIER: '机器学习异常',
  }
  return map[type] ?? type
}

function severityColor(s: string): string {
  return severityToColor(s as 'LOW' | 'MEDIUM' | 'HIGH')
}

function close() {
  emit('update:open', false)
}

// ---- 助手 tab: 跳到 AI 抽屉 ----
function openAiDrawer() {
  // 通过 emit 通知 DefaultLayout 打开 AiDrawer
  emit('openAiDrawer')
}
</script>

<template>
  <ADrawer
    :open="visible"
    @update:open="(v: boolean) => (visible = v)"
    placement="right"
    :width="480"
    :closable="false"
    :mask="false"
    :body-style="{ padding: 0 }"
    :header-style="{ display: 'none' }"
    class="building-drawer"
  >
    <div v-if="building" class="drawer-content">
      <!-- 自定义 header -->
      <div class="drawer-header">
        <div class="drawer-header__left">
          <div class="drawer-header__icon">
            <Building2 :size="18" />
          </div>
          <div>
            <div class="drawer-header__name">{{ building.display_name }}</div>
            <div class="drawer-header__code">{{ building.building_code }}</div>
          </div>
        </div>
        <button class="drawer-close" @click="close" aria-label="关闭">
          <X :size="18" />
        </button>
      </div>

      <ATabs v-model:active-key="activeTab" class="drawer-tabs">
        <!-- ============ 概览 ============ -->
        <ATabPane key="overview" tab="概览">
          <div class="tab-pane">
            <!-- 建筑信息卡 -->
            <div class="info-card">
              <div class="info-card__title">建筑信息</div>
              <div class="info-grid">
                <div class="info-cell">
                  <div class="info-cell__label">楼层数</div>
                  <div class="info-cell__value">{{ building.dimensions.floors_count ?? '-' }}</div>
                </div>
                <div class="info-cell">
                  <div class="info-cell__label">长度</div>
                  <div class="info-cell__value">{{ building.dimensions.length_m ?? '-' }} <span>m</span></div>
                </div>
                <div class="info-cell">
                  <div class="info-cell__label">宽度</div>
                  <div class="info-cell__value">{{ building.dimensions.width_m ?? '-' }} <span>m</span></div>
                </div>
                <div class="info-cell">
                  <div class="info-cell__label">高度</div>
                  <div class="info-cell__value">{{ building.dimensions.height_m ?? '-' }} <span>m</span></div>
                </div>
                <div class="info-cell">
                  <div class="info-cell__label">模型</div>
                  <div class="info-cell__value">{{ building.model_kind }}</div>
                </div>
              </div>
            </div>

            <!-- 3 个关键指标 -->
            <div class="metric-cards">
              <div class="metric-card" :style="{ borderColor: levelToColor(building.color_metric.level) }">
                <div class="metric-card__icon"><Zap :size="14" /></div>
                <div class="metric-card__label">总能耗</div>
                <div class="metric-card__value">{{ formatMetricValue(building.color_metric.metric === 'total_kwh' ? building.color_metric.value : null, 'total_kwh') }}</div>
                <div class="metric-card__unit">kWh</div>
              </div>
              <div class="metric-card" :style="{ borderColor: levelToColor(building.color_metric.level) }">
                <div class="metric-card__icon"><Gauge :size="14" /></div>
                <div class="metric-card__label">EUI</div>
                <div class="metric-card__value">{{ formatMetricValue(building.color_metric.metric === 'eui' ? building.color_metric.value : null, 'eui') }}</div>
                <div class="metric-card__unit">kWh/m²</div>
              </div>
              <div class="metric-card" :style="{ borderColor: severityColor(building.anomaly_status.severity_max ?? 'LOW') }">
                <div class="metric-card__icon"><AlertTriangle :size="14" /></div>
                <div class="metric-card__label">异常数</div>
                <div class="metric-card__value">{{ building.anomaly_status.count }}</div>
                <div class="metric-card__unit">条</div>
              </div>
            </div>

            <!-- 着色等级 + severity 分布 -->
            <div class="status-row">
              <div class="status-chip" :style="{ background: levelToColor(building.color_metric.level) + '22', color: levelToColor(building.color_metric.level) }">
                {{ LEVEL_LABEL[building.color_metric.level] }}
              </div>
              <div v-if="building.anomaly_status.has_anomaly" class="severity-row">
                <span class="severity-label">严重度分布</span>
                <span class="severity-pill" v-if="building.anomaly_status.by_severity.HIGH" style="background: #B84A3C22; color: #B84A3C">
                  HIGH {{ building.anomaly_status.by_severity.HIGH }}
                </span>
                <span class="severity-pill" v-if="building.anomaly_status.by_severity.MEDIUM" style="background: #D49B3B22; color: #B8842C">
                  MED {{ building.anomaly_status.by_severity.MEDIUM }}
                </span>
                <span class="severity-pill" v-if="building.anomaly_status.by_severity.LOW" style="background: #3D7E6A22; color: #3D7E6A">
                  LOW {{ building.anomaly_status.by_severity.LOW }}
                </span>
              </div>
            </div>

            <!-- 水平旋转 (仅 splat 模式显示) -->
            <div v-if="canRotate" class="yaw-card">
              <div class="yaw-card__header">
                <div class="yaw-card__title">
                  <RotateCw :size="14" />
                  <span>建筑朝向</span>
                </div>
                <button class="yaw-card__reset" @click="onYawReset" :disabled="yawSaving">
                  重置
                </button>
              </div>
              <div class="yaw-card__body">
                <ASlider
                  v-model:value="yawDisplay"
                  :min="0"
                  :max="360"
                  :step="1"
                  :tip-formatter="(v) => (v == null ? '' : `${v}°`)"
                  :disabled="yawSaving"
                  @change="(val: number | [number, number]) => onYawChange(Array.isArray(val) ? val[0] : val)"
                />
                <div class="yaw-card__value">
                  <span class="yaw-card__number">{{ Math.round(yawDisplay) }}</span>
                  <span class="yaw-card__unit">°</span>
                  <span v-if="yawSaving" class="yaw-card__saving">保存中…</span>
                </div>
              </div>
              <div class="yaw-card__hint">
                拖动调整建筑水平朝向 (0° = 原始方向, 顺时针旋转)
              </div>
            </div>

            <!-- 跳楼层分析页 -->
            <button class="floor-view-btn" @click="goFloorView">
              <span>查看楼层分析</span>
              <ChevronRight :size="14" />
            </button>
          </div>
        </ATabPane>

        <!-- ============ 能耗 ============ -->
        <ATabPane key="energy" tab="能耗">
          <div class="tab-pane">
            <ASpin v-if="energyLoading" size="small" />
            <template v-else>
              <!-- AI 能耗驱动因子 (GBM 特征重要性) -->
              <div class="drivers-card">
                <div class="drivers-card__header">
                  <Sparkles :size="14" class="drivers-card__icon" />
                  <span class="drivers-card__title">AI 能耗驱动因子</span>
                  <span class="drivers-card__sub">GBM 天气驱动模型 · 特征重要性</span>
                </div>
                <div v-if="energyDrivers?.length" class="drivers-card__list">
                  <div v-for="f in energyDrivers" :key="f.feature" class="drivers-card__row">
                    <span class="drivers-card__label">{{ FEATURE_LABEL[f.feature] ?? f.feature }}</span>
                    <div class="drivers-card__track">
                      <div class="drivers-card__bar" :style="{ width: `${Math.round(f.importance * 100)}%` }" />
                    </div>
                    <span class="drivers-card__val">{{ (f.importance * 100).toFixed(1) }}%</span>
                  </div>
                </div>
                <div v-else class="drivers-card__empty">
                  <span>尚未运行 GBM 预测, 无法计算驱动因子</span>
                  <button class="drivers-card__link" @click="activeTab = 'prediction'">去生成预测</button>
                </div>
              </div>

              <div class="chart-block">
                <div class="chart-block__title">日能耗时序</div>
                <div ref="energyChartRef" class="chart-canvas chart-canvas--lg" />
              </div>
              <div class="chart-block">
                <div class="chart-block__title">能源构成</div>
                <div ref="compositionChartRef" class="chart-canvas chart-canvas--md" />
              </div>
              <div class="chart-block">
                <div class="chart-block__title">
                  24小时 × 星期
                  <span class="chart-block__hint">折叠为典型周 (每格 = 该星期几该小时的均值)</span>
                </div>
                <div ref="hourlyChartRef" class="chart-canvas chart-canvas--hm" />
              </div>
              <div class="chart-block">
                <div class="chart-block__title">
                  能耗-气温关联
                  <span v-if="weatherR !== null" class="chart-block__hint">r = {{ weatherR.toFixed(2) }}</span>
                </div>
                <div ref="weatherChartRef" class="chart-canvas chart-canvas--md" />
              </div>
            </template>
          </div>
        </ATabPane>

        <!-- ============ 预测 ============ -->
        <ATabPane key="prediction" tab="预测">
          <div class="tab-pane">
            <!-- 模型选择 -->
            <div class="prediction-model-select">
              <a-select
                v-model:value="predictionModelType"
                size="small"
                style="width: 160px"
                @change="onPredictionModelChange"
              >
                <a-select-option value="prophet">Prophet 时序</a-select-option>
                <a-select-option value="lstm">LSTM 神经网络</a-select-option>
                <a-select-option value="linear">线性回归</a-select-option>
                <a-select-option value="gbm">GBM 天气驱动</a-select-option>
              </a-select>
            </div>

            <ASpin v-if="predictionLoading" size="small" />
            <template v-else-if="predictionJob?.result">
              <div class="prediction-meta">
                <span class="prediction-meta__label">模型</span>
                <span class="prediction-meta__value">{{ MODEL_TYPE_LABEL[predictionJob.model_type] ?? predictionJob.model_type }}</span>
                <span v-if="predictionJob.mape != null" class="prediction-meta__label">MAPE</span>
                <span v-if="predictionJob.mape != null" class="prediction-meta__value">{{ predictionJob.mape.toFixed(1) }}%</span>
              </div>
              <div class="chart-block">
                <div class="chart-block__title">能耗预测 (30 天)</div>
                <div ref="predictionChartRef" class="chart-canvas chart-canvas--lg" />
              </div>

              <!-- 特征重要性 (gbm 模型) -->
              <div v-if="predictionJob.result.feature_importances?.length" class="chart-block">
                <div class="chart-block__title">
                  能耗驱动因子
                  <span class="chart-block__hint">GBM 特征重要性 (谁在驱动能耗)</span>
                </div>
                <div class="feat-imp">
                  <div
                    v-for="f in predictionJob.result.feature_importances"
                    :key="f.feature"
                    class="feat-imp__row"
                  >
                    <span class="feat-imp__label">{{ FEATURE_LABEL[f.feature] ?? f.feature }}</span>
                    <div class="feat-imp__track">
                      <div class="feat-imp__bar" :style="{ width: `${Math.round(f.importance * 100)}%` }" />
                    </div>
                    <span class="feat-imp__val">{{ (f.importance * 100).toFixed(1) }}%</span>
                  </div>
                </div>
              </div>
            </template>
            <div v-else class="prediction-empty">
              <TrendingUp :size="40" :stroke-width="1.2" />
              <p>暂无预测结果</p>
              <AButton type="primary" :loading="predicting" @click="generatePrediction">
                {{ predicting ? '预测中...' : '生成预测' }}
              </AButton>
            </div>
            <div v-if="predictionError" class="tab-error">{{ predictionError }}</div>
          </div>
        </ATabPane>

        <!-- ============ 异常 ============ -->
        <ATabPane key="anomaly" tab="异常">
          <div class="tab-pane">
            <ASpin v-if="anomalyLoading" size="small" />
            <template v-else-if="anomalyList && anomalyList.anomalies.length > 0">
              <div class="anomaly-summary">
                共 {{ anomalyList.total }} 条
                <span v-for="(cnt, sev) in anomalyList.by_severity" :key="sev" class="severity-pill" :style="{ background: severityColor(sev) + '22', color: severityColor(sev) }">
                  {{ sev }} {{ cnt }}
                </span>
              </div>
              <div class="anomaly-list">
                <div v-for="a in anomalyList.anomalies" :key="a.id" class="anomaly-item">
                  <div class="anomaly-item__header">
                    <ATag :color="severityColor(a.severity)" class="anomaly-item__sev">{{ a.severity }}</ATag>
                    <span class="anomaly-item__type">{{ anomalyTypeLabel(a.event_type) }}</span>
                    <span class="anomaly-item__time">{{ dayjs(a.start_ts).format('MM-DD HH:mm') }}</span>
                  </div>
                  <div class="anomaly-item__body">
                    <div v-if="a.point_code" class="anomaly-item__point">{{ a.point_code }}</div>
                    <div class="anomaly-item__values">
                      <span>观测 {{ a.observed_value?.toFixed(1) ?? '-' }}</span>
                      <span>基准 {{ a.baseline_value?.toFixed(1) ?? '-' }}</span>
                    </div>
                  </div>
                </div>
              </div>
            </template>
            <AEmpty v-else description="无异常记录" />
          </div>
        </ATabPane>

        <!-- ============ 节能建议 ============ -->
        <ATabPane key="optimization" tab="节能建议">
          <div class="tab-pane">
            <div v-if="optimizationResult" class="optimization-result">
              <OptimizationPlanCard
                :plan="optimizationResult.plan"
                :message-id="optimizationResult.messageId"
              />
              <button class="assistant-link" @click="openAiDrawer">
                <Bot :size="14" />
                <span>在 AI 抽屉继续问</span>
              </button>
            </div>
            <div v-else-if="optimizing" class="optimization-placeholder">
              <ASpin size="small" />
              <p>正在生成节能建议…</p>
            </div>
            <div v-else class="optimization-placeholder">
              <Sparkles :size="40" :stroke-width="1.2" />
              <p>基于能耗数据和国标条款，生成该楼的节能优化方案</p>
              <AButton type="primary" @click="generateOptimizationPlan">
                生成节能建议
              </AButton>
            </div>
            <div v-if="optimizationError" class="tab-error">{{ optimizationError }}</div>
          </div>
        </ATabPane>
      </ATabs>
    </div>
  </ADrawer>
</template>

<style scoped lang="scss">
.drawer-content {
  display: flex;
  flex-direction: column;
  height: 100%;
}

.drawer-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: $space-4 $space-5;
  border-bottom: 1px solid $gray-200;
  background: $color-card;

  &__left {
    display: flex;
    align-items: center;
    gap: $space-3;
  }

  &__icon {
    width: 36px;
    height: 36px;
    background: $color-amber-soft;
    color: $color-amber-deep;
    border-radius: $radius-md;
    display: grid;
    place-items: center;
  }

  &__name {
    font-size: $fs-md;
    font-weight: $fw-bold;
    color: $color-concrete;
  }

  &__code {
    font-size: $fs-xs;
    color: $color-text-secondary;
    font-family: $font-mono;
  }
}

.drawer-close {
  display: grid;
  place-items: center;
  width: 32px;
  height: 32px;
  border-radius: $radius-sm;
  color: $color-stone;
  transition: all $transition-base;

  &:hover {
    background: $gray-100;
    color: $color-concrete;
  }
}

.drawer-tabs {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;

  :deep(.ant-tabs-content) {
    flex: 1;
    overflow-y: auto;
  }
  :deep(.ant-tabs-tab) {
    padding: 12px 16px;
    font-size: 14px;
  }
  :deep(.ant-tabs-tab-active) {
    color: $color-amber-deep !important;
    font-weight: $fw-semibold;
  }
  :deep(.ant-tabs-ink-bar) {
    background: $color-amber;
  }
}

.tab-pane {
  padding: $space-4 $space-5;
}

.info-card {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-4;
  margin-bottom: $space-4;

  &__title {
    font-size: $fs-xs;
    color: $color-text-secondary;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: $space-3;
  }
}

.info-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: $space-3;
}

.info-cell {
  &__label {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-bottom: 4px;
  }
  &__value {
    font-size: $fs-md;
    font-weight: $fw-semibold;
    color: $color-concrete;
    font-family: $font-mono;
    span {
      font-size: $fs-xs;
      color: $color-text-secondary;
      font-weight: $fw-regular;
    }
  }
}

.metric-cards {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: $space-3;
  margin-bottom: $space-4;
}

.metric-card {
  background: $color-card;
  border: 1px solid $gray-200;
  border-left: 3px solid $color-amber;
  border-radius: $radius-md;
  padding: $space-3;
  display: flex;
  flex-direction: column;
  gap: 4px;

  &__icon {
    color: $color-stone;
    margin-bottom: 4px;
  }
  &__label {
    font-size: $fs-xs;
    color: $color-text-secondary;
  }
  &__value {
    font-size: $fs-xl;
    font-weight: $fw-bold;
    color: $color-concrete;
    font-family: $font-mono;
    line-height: 1.1;
  }
  &__unit {
    font-size: $fs-xs;
    color: $color-text-secondary;
  }
}

.status-row {
  display: flex;
  align-items: center;
  gap: $space-2;
  flex-wrap: wrap;
}

.status-chip {
  padding: 4px 12px;
  border-radius: $radius-pill;
  font-size: $fs-xs;
  font-weight: $fw-medium;
}

.severity-row {
  display: flex;
  align-items: center;
  gap: $space-2;
  flex-wrap: wrap;
}

.severity-label {
  font-size: $fs-xs;
  color: $color-text-secondary;
  margin-right: 4px;
}

.severity-pill {
  padding: 2px 8px;
  border-radius: $radius-xs;
  font-size: $fs-xs;
  font-family: $font-mono;
  font-weight: $fw-semibold;
}

.yaw-card {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-4;
  margin-top: $space-4;

  &__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: $space-3;
  }

  &__title {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-sm;
    font-weight: $fw-semibold;
    color: $color-concrete;
  }

  &__reset {
    background: none;
    border: 1px solid $gray-200;
    border-radius: $radius-xs;
    padding: 2px 10px;
    font-size: $fs-xs;
    color: $color-text-secondary;
    cursor: pointer;
    transition: all $transition-base;

    &:hover:not(:disabled) {
      border-color: $color-amber;
      color: $color-amber-deep;
    }
    &:disabled {
      opacity: 0.5;
      cursor: not-allowed;
    }
  }

  &__body {
    display: flex;
    align-items: center;
    gap: $space-3;
  }

  &__value {
    display: flex;
    align-items: baseline;
    gap: 2px;
    min-width: 80px;
    font-family: $font-mono;
  }

  &__number {
    font-size: $fs-xl;
    font-weight: $fw-bold;
    color: $color-amber-deep;
  }

  &__unit {
    font-size: $fs-md;
    color: $color-text-secondary;
  }

  &__saving {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-left: $space-2;
    font-family: $font-sans;
  }

  &__hint {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-top: $space-2;
  }

  :deep(.ant-slider) {
    flex: 1;
    margin: 0;

    .ant-slider-track {
      background: $color-amber;
    }
    .ant-slider-handle::after {
      box-shadow: 0 0 0 2px $color-amber;
    }
    &:hover .ant-slider-track {
      background: $color-amber-deep;
    }
  }
}

// 跳楼层分析页按钮 (概览 Tab 末尾)
.floor-view-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: $space-1;
  width: 100%;
  margin-top: $space-4;
  padding: $space-3 $space-4;
  background: $color-amber;
  color: $color-card;
  border: none;
  border-radius: $radius-sm;
  font-size: $fs-sm;
  font-weight: $fw-semibold;
  font-family: inherit;
  cursor: pointer;
  transition: all $transition-base;

  &:hover {
    background: $color-amber-deep;
    transform: translateX(2px);
  }

  &:active {
    transform: translateX(0);
  }
}

.drivers-card {
  margin-bottom: $space-5;
  padding: $space-3;
  background: $color-amber-soft;
  border: 1px solid rgba($color-amber, 0.35);
  border-radius: $radius-md;

  &__header {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: $space-2;
  }

  &__icon {
    color: $color-amber;
  }

  &__title {
    font-size: $fs-sm;
    font-weight: $fw-semibold;
    color: $color-concrete;
  }

  &__sub {
    margin-left: auto;
    font-size: $fs-xs;
    color: $color-text-secondary;
  }

  &__list {
    display: flex;
    flex-direction: column;
    gap: 6px;
  }

  &__row {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-xs;
  }

  &__label {
    width: 64px;
    flex-shrink: 0;
    color: $color-text-secondary;
  }

  &__track {
    flex: 1;
    height: 10px;
    background: rgba(255, 255, 255, 0.6);
    border-radius: $radius-pill;
    overflow: hidden;
  }

  &__bar {
    height: 100%;
    background: linear-gradient(90deg, $color-amber, $color-amber-deep);
    border-radius: $radius-pill;
  }

  &__val {
    width: 48px;
    flex-shrink: 0;
    text-align: right;
    font-family: $font-mono;
    color: $color-amber-deep;
    font-weight: $fw-semibold;
  }

  &__empty {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-xs;
    color: $color-text-secondary;
  }

  &__link {
    background: none;
    border: none;
    padding: 0;
    color: $color-amber-deep;
    font-size: $fs-xs;
    font-weight: $fw-medium;
    cursor: pointer;

    &:hover {
      text-decoration: underline;
    }
  }
}

.chart-block {
  margin-bottom: $space-5;

  &__title {
    font-size: $fs-sm;
    font-weight: $fw-semibold;
    color: $color-concrete;
    margin-bottom: $space-2;
  }

  &__hint {
    font-size: $fs-xs;
    font-weight: $fw-regular;
    color: $color-text-secondary;
    margin-left: $space-2;
    font-family: $font-mono;
  }
}

.chart-canvas {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-3;

  &--lg {
    height: 240px;
  }
  &--md {
    height: 200px;
  }
  // 热力图要给底部 visualMap 留一行
  &--hm {
    height: 260px;
  }
}

.anomaly-summary {
  display: flex;
  align-items: center;
  gap: $space-2;
  margin-bottom: $space-3;
  font-size: $fs-sm;
  color: $color-concrete;
  flex-wrap: wrap;
}

.anomaly-list {
  display: flex;
  flex-direction: column;
  gap: $space-2;
}

.anomaly-item {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-3;

  &__header {
    display: flex;
    align-items: center;
    gap: $space-2;
    margin-bottom: $space-2;
  }
  &__sev {
    font-family: $font-mono;
    font-weight: $fw-bold;
    border: none !important;
  }
  &__type {
    font-size: $fs-sm;
    font-weight: $fw-medium;
    color: $color-concrete;
  }
  &__time {
    margin-left: auto;
    font-size: $fs-xs;
    color: $color-text-secondary;
    font-family: $font-mono;
  }
  &__body {
    display: flex;
    justify-content: space-between;
    align-items: center;
    font-size: $fs-xs;
  }
  &__point {
    color: $color-text-secondary;
    font-family: $font-mono;
  }
  &__values {
    display: flex;
    gap: $space-3;
    color: $color-concrete;
    font-family: $font-mono;
  }
}

// ---- 预测 tab ----
.prediction-model-select {
  margin-bottom: $space-3;
}

.feat-imp {
  display: flex;
  flex-direction: column;
  gap: 6px;

  &__row {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-xs;
  }

  &__label {
    width: 64px;
    flex-shrink: 0;
    color: $color-text-secondary;
  }

  &__track {
    flex: 1;
    height: 10px;
    background: $gray-100;
    border-radius: $radius-pill;
    overflow: hidden;
  }

  &__bar {
    height: 100%;
    background: linear-gradient(90deg, $color-amber, $color-amber-deep);
    border-radius: $radius-pill;
    transition: width 300ms ease-out;
  }

  &__val {
    width: 48px;
    flex-shrink: 0;
    text-align: right;
    font-family: $font-mono;
    color: $color-concrete;
  }
}

.prediction-meta {
  display: flex;
  align-items: baseline;
  gap: $space-2;
  margin-bottom: $space-3;
  padding: $space-2 $space-3;
  background: $gray-50;
  border: 1px solid $gray-200;
  border-radius: $radius-sm;

  &__label {
    font-size: $fs-xs;
    color: $color-text-secondary;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  &__value {
    font-family: $font-mono;
    font-size: $fs-sm;
    font-weight: $fw-semibold;
    color: $color-amber-deep;
    margin-right: $space-2;
  }
}

.prediction-empty {
  text-align: center;
  padding: $space-8 $space-4;
  color: $color-text-secondary;

  svg {
    color: $color-stone;
    margin-bottom: $space-3;
  }

  p {
    font-size: $fs-sm;
    margin-bottom: $space-4;
  }
}

// ---- 节能建议 tab ----
.optimization-placeholder {
  text-align: center;
  padding: $space-8 $space-4;
  color: $color-text-secondary;

  svg {
    color: $color-amber;
    margin-bottom: $space-3;
  }

  p {
    font-size: $fs-sm;
    margin-bottom: $space-4;
    color: $color-text-secondary;
  }
}

.optimization-result {
  // OptimizationPlanCard 自带卡片样式, 这里只做容器留白
}

.assistant-link {
  display: flex;
  align-items: center;
  gap: $space-1;
  margin-top: $space-3;
  padding: $space-1 0;
  background: transparent;
  border: none;
  color: $color-stone;
  font-size: $fs-sm;
  cursor: pointer;
  transition: color $transition-base;

  svg {
    color: $color-amber;
  }

  &:hover {
    color: $color-amber-deep;
  }
}

// ---- tab 内错误提示 ----
.tab-error {
  margin-top: $space-3;
  padding: $space-2 $space-3;
  background: $color-red-soft;
  border: 1px solid rgba(184, 74, 60, 0.3);
  border-radius: $radius-sm;
  color: $color-red;
  font-size: $fs-sm;
}
</style>
