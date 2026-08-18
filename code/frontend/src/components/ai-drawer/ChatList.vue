<script setup lang="ts">
// ============================================================================
// ChatList - 消息列表
// ----------------------------------------------------------------------------
// 显示当前会话的所有消息 (持久化 + 流式中)。
// 空状态: 显示欢迎卡 + 引导
// ============================================================================

import { computed } from 'vue'
import { Sparkles, MessageSquarePlus } from 'lucide-vue-next'
import { useAiStore } from '@/stores/ai'
import MessageBubble from './MessageBubble.vue'

const ai = useAiStore()

const messages = computed(() => ai.allMessages)

const isEmpty = computed(() =>
  messages.value.length === 0 && !ai.isStreaming,
)
</script>

<template>
  <div class="chat-list">
    <!-- 空状态 -->
    <div v-if="isEmpty" class="empty">
      <div class="empty__icon">
        <Sparkles :size="32" />
      </div>
      <h3 class="empty__title">AI 能效助手</h3>
      <p class="empty__desc">
        问能耗数据, 查异常原因, 检索国标条款, 还能生成结构化节能方案并导出 PDF。
      </p>
      <div class="empty__hint">
        <MessageSquarePlus :size="12" />
        <span>从下方推荐问题开始, 或直接输入你的问题</span>
      </div>
    </div>

    <!-- 消息列表 -->
    <template v-else>
      <MessageBubble
        v-for="msg in messages"
        :key="msg.id"
        :message="msg"
      />
    </template>
  </div>
</template>

<style scoped lang="scss">
.chat-list {
  min-height: 100%;
  display: flex;
  flex-direction: column;
}

.empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  text-align: center;
  padding: $space-6 $space-4;
  gap: $space-2;

  &__icon {
    width: 64px;
    height: 64px;
    display: grid;
    place-items: center;
    background: $color-amber-soft;
    color: $color-amber;
    border-radius: 50%;
    margin-bottom: $space-2;
  }

  &__title {
    font-size: $fs-lg;
    font-weight: $fw-semibold;
    color: $color-concrete;
    margin: 0;
  }

  &__desc {
    font-size: $fs-sm;
    color: $color-text-secondary;
    line-height: 1.6;
    max-width: 320px;
    margin: 0;
  }

  &__hint {
    display: inline-flex;
    align-items: center;
    gap: $space-1;
    margin-top: $space-3;
    padding: $space-1 $space-2;
    background: $color-card;
    border: 1px solid $gray-200;
    border-radius: $radius-pill;
    font-size: $fs-xs;
    color: $color-text-secondary;
  }
}
</style>
