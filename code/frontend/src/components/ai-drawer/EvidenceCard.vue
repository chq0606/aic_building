<script setup lang="ts">
// ============================================================================
// EvidenceCard - 数据证据 / 文档引用卡片
// ----------------------------------------------------------------------------
// 显示工具返回的关键数据 / 引用的文档片段, 在 assistant 回答末尾展开。
// 三种类型:
//   - data:    数据证据 (EUI / 总能耗 / 异常数 等数字)
//   - anomaly: 异常事件证据 (含 event_type / severity / observed/baseline 对比)
//   - document: 文档引用 (标准号 / 条款号 / 原文片段)
// ============================================================================

import { computed } from 'vue'
import { FileText, AlertTriangle, BarChart3 } from 'lucide-vue-next'
import type { Citation } from '@/api/assistant'

const props = defineProps<{
  citation: Citation
}>()

const type = computed(() => props.citation.type as string)

const typeIcon = computed(() => {
  if (type.value === 'data') return BarChart3
  if (type.value === 'anomaly') return AlertTriangle
  if (type.value === 'document') return FileText
  return FileText
})

const typeLabel = computed(() => {
  if (type.value === 'data') {
    return (props.citation as { scope?: string }).scope === 'park' ? '园区数据' : '建筑数据'
  }
  if (type.value === 'anomaly') return '异常证据'
  if (type.value === 'document') return '文档引用'
  return '证据'
})

const dataItems = computed<Array<[string, unknown]>>(() => {
  if (type.value !== 'data') return []
  const c = props.citation as Record<string, unknown>
  const items: Array<[string, unknown]> = []
  if (c.building_name) items.push(['建筑', c.building_name])
  if (c.building_count != null) items.push(['建筑数', `${c.building_count} 栋`])
  if (c.total_kwh != null) items.push(['总能耗', `${Number(c.total_kwh).toFixed(1)} kWh`])
  if (c.avg_eui != null) items.push(['平均 EUI', `${Number(c.avg_eui).toFixed(2)} kWh/m²`])
  if (c.anomaly_count != null) items.push(['异常数', `${c.anomaly_count} 条`])
  if (c.metric) items.push(['指标', c.metric])
  if (c.granularity) items.push(['粒度', c.granularity])
  const stats = c.stats as Record<string, unknown> | undefined
  if (stats) {
    if (stats.total_kwh != null) items.push(['统计总能耗', `${Number(stats.total_kwh).toFixed(1)} kWh`])
    if (stats.peak_kwh != null) items.push(['峰值', `${Number(stats.peak_kwh).toFixed(1)} kWh`])
    if (stats.avg_kwh != null) items.push(['日均', `${Number(stats.avg_kwh).toFixed(1)} kWh`])
  }
  return items
})

const anomalyItems = computed(() => {
  if (type.value !== 'anomaly') return null
  const c = props.citation as Record<string, unknown>
  return {
    eventType: c.event_type,
    severity: c.severity,
    observed: c.observed_value != null ? Number(c.observed_value).toFixed(2) : null,
    baseline: c.baseline_value != null ? Number(c.baseline_value).toFixed(2) : null,
    evidence: c.evidence as string,
  }
})

const docInfo = computed(() => {
  if (type.value !== 'document') return null
  const c = props.citation as Record<string, unknown>
  return {
    standardNo: c.standard_no as string,
    sectionTitle: c.section_title as string,
    contentSnippet: c.content_snippet as string,
    score: c.score as number | undefined,
  }
})
</script>

<template>
  <div class="evidence" :class="`evidence--${type}`">
    <div class="evidence__header">
      <component :is="typeIcon" :size="12" />
      <span class="evidence__type">{{ typeLabel }}</span>
    </div>

    <!-- 数据证据 -->
    <div v-if="type === 'data'" class="evidence__data">
      <div v-for="[k, v] in dataItems" :key="k" class="evidence__row">
        <span class="evidence__k">{{ k }}</span>
        <span class="evidence__v">{{ v }}</span>
      </div>
    </div>

    <!-- 异常证据 -->
    <div v-else-if="type === 'anomaly' && anomalyItems" class="evidence__anomaly">
      <div class="evidence__row">
        <span class="evidence__k">类型</span>
        <span class="evidence__v">{{ anomalyItems.eventType }}</span>
      </div>
      <div class="evidence__row">
        <span class="evidence__k">严重度</span>
        <span class="evidence__v" :class="`sev-${anomalyItems.severity}`">{{ anomalyItems.severity }}</span>
      </div>
      <div v-if="anomalyItems.observed && anomalyItems.baseline" class="evidence__row">
        <span class="evidence__k">观测 / 基线</span>
        <span class="evidence__v">
          <span class="num">{{ anomalyItems.observed }}</span>
          <span class="evidence__sep">/</span>
          <span class="num num--muted">{{ anomalyItems.baseline }}</span>
        </span>
      </div>
      <div v-if="anomalyItems.evidence" class="evidence__evidence">
        {{ anomalyItems.evidence }}
      </div>
    </div>

    <!-- 文档引用 -->
    <div v-else-if="type === 'document' && docInfo" class="evidence__doc">
      <div v-if="docInfo.standardNo || docInfo.sectionTitle" class="evidence__doc-header">
        <span v-if="docInfo.standardNo" class="evidence__std">{{ docInfo.standardNo }}</span>
        <span v-if="docInfo.sectionTitle" class="evidence__section">{{ docInfo.sectionTitle }}</span>
        <span v-if="docInfo.score != null" class="evidence__score">
          相关度 {{ (Number(docInfo.score) * 100).toFixed(0) }}%
        </span>
      </div>
      <div v-if="docInfo.contentSnippet" class="evidence__quote">
        "{{ docInfo.contentSnippet }}"
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.evidence {
  background: $color-card;
  border: 1px solid $gray-200;
  border-left: 3px solid $color-amber;
  border-radius: $radius-sm;
  padding: $space-2 $space-3;
  margin-bottom: $space-1;
  font-size: $fs-xs;

  &--anomaly {
    border-left-color: $color-amber;
  }

  &--document {
    border-left-color: $color-green;
  }

  &__header {
    display: flex;
    align-items: center;
    gap: $space-1;
    color: $color-text-secondary;
    font-weight: $fw-medium;
    margin-bottom: $space-1;
    font-size: 10px;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  &__row {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    padding: 2px 0;
  }

  &__k {
    color: $color-text-secondary;
  }

  &__v {
    color: $color-concrete;
    font-weight: $fw-medium;
    text-align: right;
  }

  &__sep {
    color: $color-text-secondary;
    margin: 0 4px;
  }
}

.evidence__anomaly .sev-high { color: $color-red; font-weight: 600; }
.evidence__anomaly .sev-medium { color: $color-amber; font-weight: 600; }
.evidence__anomaly .sev-low { color: $color-green; font-weight: 600; }

.num {
  font-variant-numeric: tabular-nums;
  font-weight: 600;
  color: $color-amber-deep;

  &--muted {
    color: $color-text-secondary;
    font-weight: normal;
  }
}

.evidence__evidence {
  margin-top: $space-1;
  padding-top: $space-1;
  border-top: 1px dashed $gray-200;
  color: $color-text-secondary;
  line-height: 1.5;
  font-style: italic;
}

.evidence__doc-header {
  display: flex;
  align-items: center;
  gap: $space-1;
  margin-bottom: $space-1;
  flex-wrap: wrap;
}

.evidence__std {
  background: $color-amber-soft;
  color: $color-amber-deep;
  padding: 1px 6px;
  border-radius: $radius-xs;
  font-weight: $fw-semibold;
  font-size: 10px;
  font-family: $font-mono;
}

.evidence__section {
  color: $color-concrete;
  font-weight: $fw-medium;
}

.evidence__score {
  margin-left: auto;
  color: $color-text-secondary;
  font-size: 10px;
}

.evidence__quote {
  color: $color-stone;
  line-height: 1.6;
  font-style: italic;
  padding: $space-1;
  background: $gray-50;
  border-radius: $radius-xs;
  border-left: 2px solid $color-green-soft;
}
</style>
