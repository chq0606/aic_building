<script setup lang="ts">
// ============================================================================
// GlobalAiFab - 全局浮动按钮
// ----------------------------------------------------------------------------
// 固定在右下角的圆形按钮, 点击打开 AI 抽屉。挂到 App.vue 根节点, 所有
// 路由都能看到。仅在已登录状态下显示 (访客看不到)。
//
// 设计:
//   - 圆形 56x56px, 琥珀色背景, Sparkles 图标
//   - 悬停时: 略微放大 + 显示 tooltip "AI 助手"
//   - 流式进行中: 图标变成 Loader2 旋转动画, 颜色变深
//   - 已打开抽屉时: 按钮变深色 (active 状态)
//
// 位置: 右下角, bottom: 80px (避开浏览器自带的 cookie 通知等), right: 24px
// 移动端: 缩到 48x48px, 避开底部 tab bar
// ============================================================================

import { computed } from 'vue'
import { Sparkles, Loader2 } from 'lucide-vue-next'
import { useAiStore } from '@/stores/ai'
import { useAuthStore } from '@/stores/auth'

const ai = useAiStore()
const auth = useAuthStore()

const visible = computed(() => auth.isAuthenticated)
const isStreaming = computed(() => ai.isStreaming)
const isActive = computed(() => ai.drawerOpen)
</script>

<template>
  <Transition name="fab">
    <button
      v-if="visible"
      class="ai-fab"
      :class="{ 'ai-fab--active': isActive, 'ai-fab--streaming': isStreaming }"
      :title="isActive ? '关闭 AI 助手' : '打开 AI 助手'"
      @click="ai.toggleDrawer()"
    >
      <Loader2 v-if="isStreaming" :size="22" class="spin" />
      <Sparkles v-else :size="22" />
    </button>
  </Transition>
</template>

<style scoped lang="scss">
.ai-fab {
  position: fixed;
  bottom: 80px;
  right: 24px;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  background: $color-amber;
  color: $color-card;
  border: none;
  cursor: pointer;
  display: grid;
  place-items: center;
  box-shadow: 0 6px 20px rgba($color-amber, 0.4),
              0 2px 6px rgba($color-concrete, 0.1);
  z-index: 1000;
  transition: all $transition-base;

  &:hover {
    transform: scale(1.08);
    box-shadow: 0 8px 28px rgba($color-amber, 0.5),
                0 4px 10px rgba($color-concrete, 0.15);
  }

  &--active {
    background: $color-amber-deep;
    transform: scale(0.95);
  }

  &--streaming {
    background: $color-amber-deep;
    animation: pulse 1.5s ease-in-out infinite;
  }

  @media (max-width: 768px) {
    width: 48px;
    height: 48px;
    bottom: 80px;
    right: 16px;
  }
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@keyframes pulse {
  0%, 100% {
    box-shadow: 0 6px 20px rgba($color-amber-deep, 0.5),
                0 0 0 0 rgba($color-amber, 0.4);
  }
  50% {
    box-shadow: 0 6px 20px rgba($color-amber-deep, 0.5),
                0 0 0 12px rgba($color-amber, 0);
  }
}

.fab-enter-active,
.fab-leave-active {
  transition: all 0.3s ease;
}

.fab-enter-from,
.fab-leave-to {
  opacity: 0;
  transform: translateY(20px) scale(0.8);
}
</style>
