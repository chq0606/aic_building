<script setup lang="ts">
// ============================================================================
// ChartCard - 分析中心通用图表卡片
// ----------------------------------------------------------------------------
// 6 个图表组件 (BuildingRanking / BuildingCompare / EuiBaseline / AnomalyOverview
// / WeatherCorrelation / DataQualityPanel) 的统一外壳:
//
//   ┌────────────────────────────────────────────┐
//   │ 标题                  [extra slot 选楼/筛选]│
//   │ 副标题 (说明)                              │
//   ├────────────────────────────────────────────┤
//   │                                            │
//   │            ECharts 容器 (360px)            │
//   │                                            │
//   ├────────────────────────────────────────────┤
//   │ footer slot (小字说明, 可选)               │
//   └────────────────────────────────────────────┘
//
// 三态切换 (loading / error / empty):
//   - loading: 顶部琥珀进度条 + 居中 spinner + 骨架占位
//   - error:   红色 AlertCircle + 错误文案 + 重试按钮
//   - empty:   淡灰占位 + "暂无数据" 提示
//   - 正常:    ECharts 容器显示, 自动 ResizeObserver 跟随父容器尺寸
//
// ECharts 实例管理:
//   - props.option 变化时 setOption(replaceMerge: ['series']), 替换 series 避免老数据残留
//   - 主题在 init 时一次性 setOption, 后续局部 option 走 deep merge (保留主题 nested 样式)
//   - ResizeObserver 监听容器尺寸, 触发 chart.resize() (容器 flex/grid 变化时自动适配)
//   - onBeforeUnmount 必须 chart.dispose() 释放, 否则切页面内存泄漏
//
// 设计风格:
//   - 跟 tokens.scss 对齐 (混凝土色文本 + 琥珀强调 + 纸张背景)
//   - 卡片白底 + 1px 细线边框, hover 抬升阴影 (克制, 不夸张)
//   - 顶部琥珀进度条 (loading 时显示, 2s 循环) 视觉锚点
// ============================================================================

import { ref, onMounted, onBeforeUnmount, watch, nextTick } from 'vue'
import * as echarts from 'echarts'
import type { EChartsOption } from 'echarts'
import { Loader2, AlertCircle, Inbox } from 'lucide-vue-next'
import { ECHARTS_THEME } from '@/styles/echarts-theme'

const props = withDefaults(defineProps<{
  title: string
  subtitle?: string
  // 卡片高度 (图表容器高度), 默认 360 跟设计稿对齐
  height?: number
  loading?: boolean
  // loading 覆盖层文案, 默认 "数据加载中"
  // PredictionPanel 用这个区分 "提交中/排队中/训练中" 三态
  loadingText?: string
  error?: string | null
  empty?: boolean
  emptyText?: string
  // ECharts option, null 时不渲染图表
  option?: EChartsOption | null
}>(), {
  height: 360,
  loading: false,
  loadingText: '数据加载中',
  error: null,
  empty: false,
  emptyText: '暂无数据',
  option: null,
})

const emit = defineEmits<{ retry: [] }>()

const chartContainerRef = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

function initChart() {
  if (!chartContainerRef.value) return
  // 用 svg 渲染: 印刷质感 + 缩放不糊, 跟 Swiss design 图纸气质对齐
  // 比 canvas 慢一点但 demo 数据量小, 不影响性能
  chart = echarts.init(chartContainerRef.value, undefined, { renderer: 'svg' })
  // 先 setOption 主题 (tooltip / textStyle / grid 等), 后续 setOption 默认走 deep merge,
  // 不会丢主题的 nested 字段 (比如 tooltip.backgroundColor 不会被局部 option 覆盖掉)
  chart.setOption(ECHARTS_THEME as EChartsOption)
  if (props.option) {
    // replaceMerge: ['series'] 替换 series 数组, 避免 metric 切换时老 series 残留
    chart.setOption(props.option as EChartsOption, { replaceMerge: ['series'] })
  }
  resizeObserver = new ResizeObserver(() => {
    chart?.resize()
  })
  resizeObserver.observe(chartContainerRef.value)
}

function updateChart() {
  if (!chart) return
  if (props.option) {
    // 默认 deep merge (跟 init 时的 ECHARTS_THEME 合并), 但 series 数组替换避免残留
    chart.setOption(props.option as EChartsOption, { replaceMerge: ['series'] })
  } else {
    chart.clear()
  }
}

onMounted(() => {
  nextTick(initChart)
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  resizeObserver = null
  chart?.dispose()
  chart = null
})

watch(
  () => props.option,
  () => updateChart(),
  { deep: true },
)

// loading/error/empty 切换为 false 时, 确保图表容器尺寸正确 (display:none -> block 后 resize)
watch(
  () => [props.loading, props.error, props.empty],
  () => {
    nextTick(() => chart?.resize())
  },
)
</script>

<template>
  <div class="chart-card" :class="{ 'is-loading': loading }">
    <!-- 顶部琥珀进度条 (loading 时显示) -->
    <div v-if="loading" class="chart-card__progress" />

    <header class="chart-card__header">
      <div class="chart-card__title-wrap">
        <h3 class="chart-card__title">{{ title }}</h3>
        <p v-if="subtitle" class="chart-card__subtitle">{{ subtitle }}</p>
      </div>
      <div v-if="$slots.extra" class="chart-card__extra">
        <slot name="extra" />
      </div>
    </header>

    <div class="chart-card__body" :style="{ height: `${height}px` }">
      <!-- 默认 chart 容器 (始终在 DOM, 让 ECharts 实例不丢); 父组件传 default slot
           则渲染 slot 内容 (如 AnomalyOverview 左饼右柱双 chart) -->
      <slot>
        <div ref="chartContainerRef" class="chart-card__chart" />
      </slot>

      <!-- 三态用 absolute overlay 覆盖在 chart 容器之上, 不破坏 chart DOM 引用 -->
      <div v-if="loading" class="chart-card__overlay chart-card__state--loading">
        <Loader2 class="chart-card__spinner" :size="24" />
        <span class="chart-card__state-text">{{ loadingText }}</span>
      </div>

      <div v-else-if="error" class="chart-card__overlay chart-card__state--error">
        <AlertCircle :size="24" />
        <span class="chart-card__state-text">{{ error }}</span>
        <button class="chart-card__retry" @click="emit('retry')">
          重试
        </button>
      </div>

      <div v-else-if="empty" class="chart-card__overlay chart-card__state--empty">
        <Inbox :size="28" />
        <span class="chart-card__state-text">{{ emptyText }}</span>
      </div>
    </div>

    <footer v-if="$slots.footer" class="chart-card__footer">
      <slot name="footer" />
    </footer>
  </div>
</template>

<style scoped lang="scss">
.chart-card {
  position: relative;
  display: flex;
  flex-direction: column;
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  overflow: hidden;
  transition: box-shadow $transition-base, border-color $transition-base;

  &:hover {
    box-shadow: $shadow-md;
    border-color: $color-line;
  }

  &.is-loading {
    // loading 时降低整体不透明度, 让进度条成为视觉焦点
    opacity: 0.92;
  }

  // 顶部琥珀进度条 (loading 时显示)
  &__progress {
    position: absolute;
    top: 0;
    left: 0;
    right: 0;
    height: 2px;
    background: linear-gradient(
      90deg,
      transparent 0%,
      $color-amber 30%,
      $color-amber-deep 50%,
      $color-amber 70%,
      transparent 100%
    );
    background-size: 200% 100%;
    animation: chart-card-progress 1.6s linear infinite;
    z-index: 1;
  }
}

.chart-card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: $space-3;
  padding: $space-3 $space-4;
  border-bottom: 1px solid $gray-100;
}

.chart-card__title-wrap {
  flex: 1;
  min-width: 0;
}

.chart-card__title {
  font-size: $fs-md;
  font-weight: $fw-semibold;
  color: $color-concrete;
  margin: 0;
  line-height: $lh-tight;
  // 超长标题省略, 不挤压图表
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chart-card__subtitle {
  font-size: $fs-xs;
  color: $color-text-secondary;
  margin: 2px 0 0;
  line-height: $lh-snug;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.chart-card__extra {
  flex-shrink: 0;
  // 让 slot 里的 Select / Segmented 紧凑对齐
  display: flex;
  align-items: center;
  gap: $space-2;
}

.chart-card__body {
  position: relative;
  padding: $space-3 $space-4;
}

.chart-card__chart {
  width: 100%;
  height: 100%;
}

// 三态覆盖层 - absolute inset 0, 盖在 chart 容器或 slot 内容之上
// 用 absolute 而非 v-if 切换 chart 容器, 是为了让 ECharts 实例的 DOM 引用不丢,
// loading 切换时不需重新 init
.chart-card__overlay {
  position: absolute;
  inset: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: $space-2;
  background: $color-card;
  color: $color-text-secondary;
  border-radius: $radius-sm;
  // 偶尔 chart 还没 setOption 时半透明背景挡一下空容器, 避免"先看到空 chart 再盖遮罩"的闪屏
  animation: chart-card-overlay-fade 200ms ease-out;

  &--loading {
    color: $color-amber;
  }

  &--error {
    color: $color-red;
  }

  &--empty {
    color: $gray-400;
  }
}

.chart-card__spinner {
  animation: chart-card-spin 1.1s linear infinite;
}

.chart-card__state-text {
  font-size: $fs-sm;
  color: $color-text-secondary;
}

.chart-card__retry {
  margin-top: $space-1;
  padding: 4px 12px;
  background: transparent;
  border: 1px solid $color-amber;
  color: $color-amber;
  border-radius: $radius-sm;
  font-size: $fs-xs;
  font-weight: $fw-medium;
  cursor: pointer;
  transition: background $transition-fast, color $transition-fast;

  &:hover {
    background: $color-amber;
    color: white;
  }

  &:active {
    transform: translateY(1px);
  }
}

.chart-card__footer {
  padding: $space-2 $space-4;
  border-top: 1px solid $gray-100;
  font-size: $fs-xs;
  color: $color-text-secondary;
  background: $gray-50;
  line-height: $lh-snug;
}

@keyframes chart-card-progress {
  0% {
    background-position: 100% 0;
  }
  100% {
    background-position: -100% 0;
  }
}

@keyframes chart-card-spin {
  from {
    transform: rotate(0deg);
  }
  to {
    transform: rotate(360deg);
  }
}

@keyframes chart-card-overlay-fade {
  from {
    opacity: 0;
  }
  to {
    opacity: 1;
  }
}
</style>
