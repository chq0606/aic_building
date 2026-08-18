<script setup lang="ts">
// ============================================================================
// ContextIndicator - 当前上下文显示
// ----------------------------------------------------------------------------
// 显示用户当前正在查看的: 园区 / 建筑 / 时间范围 / 指标
// 数据来源: Pinia contextStore (通过 aiStore.currentContext 透传)
// 点 × 可清除上下文 (变成 "全局提问" 模式, 后端不注入上下文到 system prompt)
// ============================================================================

import { computed } from 'vue'
import { Building2, Calendar, Gauge, MapPin, X } from 'lucide-vue-next'
import { useAiStore } from '@/stores/ai'

const ai = useAiStore()

const ctx = computed(() => ai.currentContext)

const isEmpty = computed(() =>
  !ctx.value?.site_id && !ctx.value?.building_id,
)

const timeRange = computed(() => {
  if (!ctx.value?.time_range) return ''
  const start = new Date(ctx.value.time_range.start)
  const end = new Date(ctx.value.time_range.end)
  const fmt = (d: Date) => `${d.getMonth() + 1}/${d.getDate()}`
  return `${fmt(start)} ~ ${fmt(end)}`
})

const metricLabel = computed(() => {
  const m = ctx.value?.metric
  if (!m) return ''
  return ({ total_kwh: '总能耗', eui: 'EUI', anomaly_count: '异常数', cost: '费用' } as Record<string, string>)[m] || m
})
</script>

<template>
  <div v-if="!isEmpty" class="ctx-bar">
    <div class="ctx-bar__label">当前上下文</div>
    <div class="ctx-bar__chips">
      <div v-if="ctx?.site_id" class="ctx-chip">
        <MapPin :size="12" />
        <span>园区</span>
      </div>
      <div v-if="ctx?.building_id" class="ctx-chip ctx-chip--primary">
        <Building2 :size="12" />
        <span>建筑</span>
      </div>
      <div v-if="timeRange" class="ctx-chip">
        <Calendar :size="12" />
        <span>{{ timeRange }}</span>
      </div>
      <div v-if="metricLabel" class="ctx-chip">
        <Gauge :size="12" />
        <span>{{ metricLabel }}</span>
      </div>
    </div>
  </div>
  <div v-else class="ctx-bar ctx-bar--empty">
    <div class="ctx-bar__label">全局提问</div>
    <span class="ctx-bar__hint">未绑定上下文, AI 将基于通用知识回答</span>
  </div>
</template>

<style scoped lang="scss">
.ctx-bar {
  display: flex;
  align-items: center;
  gap: $space-2;
  padding: $space-2 $space-4;
  background: $color-amber-soft;
  border-bottom: 1px solid rgba($color-amber, 0.2);
  flex-shrink: 0;
  font-size: $fs-xs;
  flex-wrap: wrap;

  &--empty {
    background: $gray-50;
    border-bottom-color: $gray-200;
  }

  &__label {
    color: $color-amber-deep;
    font-weight: $fw-semibold;
    flex-shrink: 0;
  }

  &__hint {
    color: $color-text-secondary;
    opacity: 0.7;
  }

  &__chips {
    display: flex;
    gap: $space-1;
    flex-wrap: wrap;
  }
}

.ctx-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  padding: 2px 8px;
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-pill;
  color: $color-stone;
  font-size: $fs-xs;
  font-weight: $fw-medium;

  svg {
    color: $color-text-secondary;
  }

  &--primary {
    background: $color-amber;
    border-color: $color-amber;
    color: $color-card;

    svg {
      color: $color-card;
    }
  }
}
</style>
