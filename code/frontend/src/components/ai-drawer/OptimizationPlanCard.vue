<script setup lang="ts">
// ============================================================================
// OptimizationPlanCard - 节能优化四段结构化卡片
// ----------------------------------------------------------------------------
// 当 assistant 消息含 optimization_plan 时, 在气泡末尾展开这个卡片。
// 四段: summary / problems[] / measures[] / priorities[]
// 每段用独立的子组件渲染, 容器只负责编排 + 折叠展开
//
// 折叠逻辑: 默认展开 summary + priorities, problems/measures 折叠成"展开 N 条"
// (节能方案通常内容多, 全展开会让气泡很长)
// ============================================================================

import { ref, computed } from 'vue'
import { FileBarChart, ChevronDown, ChevronRight } from 'lucide-vue-next'
import type { OptimizationPlan } from '@/api/assistant'
import ProblemItem from './ProblemItem.vue'
import MeasureItem from './MeasureItem.vue'
import PriorityList from './PriorityList.vue'
import PdfExportButton from './PdfExportButton.vue'

const props = defineProps<{
  plan: OptimizationPlan
  messageId: string
}>()

const problemsExpanded = ref(false)
const measuresExpanded = ref(true)  // measures 默认展开 (核心内容)

const toggleProblems = () => { problemsExpanded.value = !problemsExpanded.value }
const toggleMeasures = () => { measuresExpanded.value = !measuresExpanded.value }

// 总节能量 / 总 CO2 减排 / 平均回收期
const totalSavedKwh = computed(() =>
  props.plan.measures.reduce((s, m) => s + (m.saved_kwh || 0), 0),
)
const totalSavedCo2 = computed(() =>
  props.plan.measures.reduce((s, m) => s + (m.saved_co2 || 0), 0),
)
const avgPayback = computed(() => {
  if (props.plan.measures.length === 0) return 0
  return props.plan.measures.reduce((s, m) => s + (m.payback_months || 0), 0) / props.plan.measures.length
})
</script>

<template>
  <div class="opt-card">
    <div class="opt-card__header">
      <FileBarChart :size="14" />
      <span>节能优化方案</span>
    </div>

    <!-- 顶部统计卡 -->
    <div class="opt-card__stats">
      <div class="stat">
        <div class="stat__label">总节能量</div>
        <div class="stat__value">{{ totalSavedKwh.toFixed(0) }}</div>
        <div class="stat__unit">kWh</div>
      </div>
      <div class="stat">
        <div class="stat__label">CO₂ 减排</div>
        <div class="stat__value">{{ totalSavedCo2.toFixed(0) }}</div>
        <div class="stat__unit">kg</div>
      </div>
      <div class="stat">
        <div class="stat__label">平均回收期</div>
        <div class="stat__value">{{ avgPayback.toFixed(1) }}</div>
        <div class="stat__unit">月</div>
      </div>
    </div>

    <!-- 1. Summary -->
    <div class="opt-section opt-section--summary">
      <div class="opt-section__title">整体评价</div>
      <div class="opt-section__body">{{ plan.summary }}</div>
    </div>

    <!-- 2. Problems -->
    <div v-if="plan.problems.length > 0" class="opt-section">
      <button class="opt-section__toggle" @click="toggleProblems">
        <component :is="problemsExpanded ? ChevronDown : ChevronRight" :size="12" />
        <span class="opt-section__title">主要问题</span>
        <span class="opt-section__count">{{ plan.problems.length }} 条</span>
      </button>
      <div v-if="problemsExpanded" class="opt-section__body">
        <ProblemItem
          v-for="(p, i) in plan.problems"
          :key="i"
          :problem="p"
          :index="i + 1"
        />
      </div>
    </div>

    <!-- 3. Measures -->
    <div v-if="plan.measures.length > 0" class="opt-section">
      <button class="opt-section__toggle" @click="toggleMeasures">
        <component :is="measuresExpanded ? ChevronDown : ChevronRight" :size="12" />
        <span class="opt-section__title">节能措施</span>
        <span class="opt-section__count">{{ plan.measures.length }} 条</span>
      </button>
      <div v-if="measuresExpanded" class="opt-section__body">
        <MeasureItem
          v-for="(m, i) in plan.measures"
          :key="i"
          :measure="m"
          :index="i + 1"
        />
      </div>
    </div>

    <!-- 4. Priorities -->
    <PriorityList v-if="plan.priorities.length > 0" :priorities="plan.priorities" />

    <!-- PDF 导出 -->
    <div class="opt-card__footer">
      <PdfExportButton :message-id="messageId" />
    </div>
  </div>
</template>

<style scoped lang="scss">
.opt-card {
  margin-top: $space-3;
  background: linear-gradient(135deg, $color-amber-soft 0%, $color-paper 100%);
  border: 1px solid rgba($color-amber, 0.3);
  border-radius: $radius-md;
  padding: $space-3;
  font-size: $fs-sm;
}

.opt-card__header {
  display: flex;
  align-items: center;
  gap: $space-1;
  font-size: $fs-sm;
  font-weight: $fw-semibold;
  color: $color-amber-deep;
  margin-bottom: $space-2;
  padding-bottom: $space-2;
  border-bottom: 1px solid rgba($color-amber, 0.2);

  svg { color: $color-amber; }
}

.opt-card__stats {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: $space-1;
  margin-bottom: $space-3;
}

.stat {
  background: $color-card;
  padding: $space-2;
  border-radius: $radius-sm;
  text-align: center;
  border: 1px solid $gray-200;

  &__label {
    font-size: 10px;
    color: $color-text-secondary;
    text-transform: uppercase;
    letter-spacing: 0.3px;
  }

  &__value {
    font-size: $fs-lg;
    font-weight: $fw-bold;
    color: $color-amber-deep;
    font-variant-numeric: tabular-nums;
    margin: 2px 0;
  }

  &__unit {
    font-size: 10px;
    color: $color-text-secondary;
  }
}

.opt-section {
  margin-bottom: $space-3;

  &:last-child {
    margin-bottom: 0;
  }

  &--summary {
    background: $color-card;
    border-left: 3px solid $color-amber;
    border-radius: $radius-sm;
    padding: $space-2 $space-3;
  }

  &__toggle {
    display: flex;
    align-items: center;
    gap: $space-1;
    width: 100%;
    background: transparent;
    border: none;
    padding: 4px 0;
    cursor: pointer;
    color: $color-concrete;
    font-size: $fs-sm;
    font-weight: $fw-semibold;
    text-align: left;
    transition: color $transition-base;

    &:hover {
      color: $color-amber-deep;
    }
  }

  &__title {
    flex: 1;
  }

  &__count {
    font-size: $fs-xs;
    color: $color-text-secondary;
    font-weight: $fw-regular;
  }

  &__body {
    margin-top: $space-1;
  }
}

.opt-card__footer {
  margin-top: $space-3;
  padding-top: $space-2;
  border-top: 1px solid rgba($color-amber, 0.2);
}
</style>
