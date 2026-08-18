<script setup lang="ts">
// ============================================================================
// QuickQuestions - 推荐问题
// ----------------------------------------------------------------------------
// 流式未进行 + 没有正在生成的消息时, 在输入框上方显示推荐问题。
// 问题列表从 aiStore.quickQuestions 拿 (按上下文场景动态选)。
// 点击问题直接 sendMessage。
// ============================================================================

import { Lightbulb } from 'lucide-vue-next'

defineProps<{
  questions: string[]
}>()

const emit = defineEmits<{
  select: [q: string]
}>()
</script>

<template>
  <div v-if="questions.length > 0" class="quick">
    <div class="quick__header">
      <Lightbulb :size="12" />
      <span>试试这些</span>
    </div>
    <div class="quick__list">
      <button
        v-for="(q, i) in questions"
        :key="i"
        class="quick__item"
        @click="emit('select', q)"
      >
        {{ q }}
      </button>
    </div>
  </div>
</template>

<style scoped lang="scss">
.quick {
  margin-bottom: $space-2;

  &__header {
    display: flex;
    align-items: center;
    gap: $space-1;
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-bottom: $space-1;
    padding: 0 2px;
  }

  &__list {
    display: flex;
    flex-wrap: wrap;
    gap: $space-1;
  }

  &__item {
    border: 1px solid $gray-200;
    background: $color-card;
    color: $color-stone;
    padding: 4px 10px;
    border-radius: $radius-pill;
    font-size: $fs-xs;
    cursor: pointer;
    transition: all $transition-base;

    &:hover {
      border-color: $color-amber;
      color: $color-amber-deep;
      background: $color-amber-soft;
    }
  }
}
</style>
