<script setup lang="ts">
// ============================================================================
// SessionList - 历史会话列表
// ----------------------------------------------------------------------------
// 覆盖在聊天区上方, 点 Header 的 History 按钮展开, 点某会话切换并收起。
// 数据源: ai.sessions (打开抽屉时 loadSessions 拉取)。
// ============================================================================

import { useAiStore } from '@/stores/ai'
import { MessageSquare } from 'lucide-vue-next'

const ai = useAiStore()

const emit = defineEmits<{
  (e: 'select', id: string): void
}>()

function fmtTime(iso: string | undefined): string {
  if (!iso) return ''
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return ''
  const diff = Date.now() - d.getTime()
  const min = Math.floor(diff / 60000)
  if (min < 1) return '刚刚'
  if (min < 60) return `${min} 分钟前`
  const hr = Math.floor(min / 60)
  if (hr < 24) return `${hr} 小时前`
  const day = Math.floor(hr / 24)
  if (day < 7) return `${day} 天前`
  return d.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
}

function onSelect(id: string) {
  ai.selectSession(id)
  emit('select', id)
}
</script>

<template>
  <div class="session-list">
    <div class="session-list__header">
      <span>历史会话</span>
      <span class="session-list__count">{{ ai.sessions.length }}</span>
    </div>

    <div v-if="ai.sessions.length === 0" class="session-list__empty">
      暂无历史会话
    </div>

    <button
      v-for="s in ai.sessions"
      :key="s.id"
      class="session-item"
      :class="{ 'session-item--active': s.id === ai.currentSessionId }"
      @click="onSelect(s.id)"
    >
      <MessageSquare :size="14" class="session-item__icon" />
      <div class="session-item__body">
        <div class="session-item__title">{{ s.title || '新会话' }}</div>
        <div class="session-item__meta">
          {{ s.message_count ?? 0 }} 条 · {{ fmtTime(s.updated_at || s.created_at) }}
        </div>
      </div>
    </button>
  </div>
</template>

<style scoped lang="scss">
.session-list {
  display: flex;
  flex-direction: column;
  gap: $space-2;
  padding: $space-3 $space-4;

  &__header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    font-size: $fs-xs;
    font-weight: $fw-semibold;
    color: $color-text-secondary;
    text-transform: uppercase;
    letter-spacing: 0.04em;
    padding-bottom: $space-2;
    border-bottom: 1px solid $gray-200;
  }

  &__count {
    display: inline-grid;
    place-items: center;
    min-width: 20px;
    height: 20px;
    padding: 0 $space-1;
    background: $gray-100;
    border-radius: $radius-pill;
    font-size: $fs-xs;
    color: $color-stone;
  }

  &__empty {
    padding: $space-6 $space-2;
    text-align: center;
    font-size: $fs-sm;
    color: $color-text-secondary;
  }
}

.session-item {
  display: flex;
  align-items: center;
  gap: $space-2;
  width: 100%;
  padding: $space-2 $space-3;
  border: 1px solid transparent;
  border-radius: $radius-md;
  background: transparent;
  cursor: pointer;
  text-align: left;
  transition: all $transition-base;

  &:hover {
    background: $gray-100;
  }

  &--active {
    background: $color-amber-soft;
    border-color: $color-amber;

    .session-item__icon {
      color: $color-amber;
    }

    .session-item__title {
      color: $color-amber-deep;
    }
  }

  &__icon {
    flex-shrink: 0;
    color: $color-stone;
  }

  &__body {
    flex: 1;
    min-width: 0;
  }

  &__title {
    font-size: $fs-sm;
    font-weight: $fw-medium;
    color: $color-concrete;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  &__meta {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-top: 1px;
  }
}
</style>
