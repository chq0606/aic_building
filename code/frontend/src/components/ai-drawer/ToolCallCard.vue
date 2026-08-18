<script setup lang="ts">
// ============================================================================
// ToolCallCard - 工具调用卡片
// ----------------------------------------------------------------------------
// 显示 LLM 调了哪些工具 + 当前状态 (进行中 / 成功 / 失败)。
// 在 assistant 消息气泡内, 在内容前面展开。
//
// 工具名映射中文:
//   query_park_overview     -> 园区总览
//   query_building_energy   -> 单楼能耗
//   query_anomalies         -> 异常事件
//   search_knowledge        -> 知识检索
// ============================================================================

import { computed } from 'vue'
import { CheckCircle2, AlertCircle, Loader2 } from 'lucide-vue-next'
import type { ToolCall } from '@/api/assistant'

const props = defineProps<{
  tool: ToolCall
  status: 'pending' | 'ok' | 'error'
}>()

const TOOL_LABELS: Record<string, string> = {
  query_park_overview: '园区总览',
  query_building_energy: '单楼能耗',
  query_anomalies: '异常事件',
  search_knowledge: '知识检索',
}

const TOOL_ICONS: Record<string, string> = {
  query_park_overview: '🏢',
  query_building_energy: '⚡',
  query_anomalies: '⚠️',
  search_knowledge: '📚',
}

const label = computed(() => TOOL_LABELS[props.tool.name] || props.tool.name)
const icon = computed(() => TOOL_ICONS[props.tool.name] || '🔧')

const argsSummary = computed(() => {
  const a = props.tool.arguments || {}
  const parts: string[] = []
  if (a.building_id) parts.push('building')
  if (a.site_id) parts.push('site')
  if (a.start && a.end) parts.push('time')
  if (a.query) parts.push(`"${String(a.query).slice(0, 16)}${String(a.query).length > 16 ? '...' : ''}"`)
  if (a.granularity) parts.push(a.granularity as string)
  return parts.join(' · ')
})
</script>

<template>
  <div class="tool-card" :class="`tool-card--${status}`">
    <div class="tool-card__header">
      <span class="tool-card__icon">{{ icon }}</span>
      <span class="tool-card__name">{{ label }}</span>
      <span class="tool-card__args">{{ argsSummary }}</span>
      <span class="tool-card__status">
        <Loader2 v-if="status === 'pending'" :size="12" class="spin" />
        <CheckCircle2 v-else-if="status === 'ok'" :size="12" />
        <AlertCircle v-else :size="12" />
      </span>
    </div>
  </div>
</template>

<style scoped lang="scss">
.tool-card {
  display: flex;
  flex-direction: column;
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-sm;
  padding: $space-2 $space-3;
  margin-bottom: $space-1;
  font-size: $fs-xs;
  transition: all $transition-base;

  &--pending {
    border-color: $color-amber;
    background: $color-amber-soft;
  }

  &--ok {
    border-color: $color-green-soft;
  }

  &--error {
    border-color: rgba($color-red, 0.4);
    background: rgba($color-red, 0.04);
  }

  &__header {
    display: flex;
    align-items: center;
    gap: $space-2;
  }

  &__icon {
    font-size: 14px;
    flex-shrink: 0;
  }

  &__name {
    color: $color-concrete;
    font-weight: $fw-semibold;
    flex-shrink: 0;
  }

  &__args {
    color: $color-text-secondary;
    flex: 1;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__status {
    flex-shrink: 0;
    display: grid;
    place-items: center;
  }
}

.tool-card--pending .tool-card__status {
  color: $color-amber;
}

.tool-card--ok .tool-card__status {
  color: $color-green;
}

.tool-card--error .tool-card__status {
  color: $color-red;
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
