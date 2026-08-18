<script setup lang="ts">
// ============================================================================
// AiDrawer - AI 抽屉主容器
// ----------------------------------------------------------------------------
// 三段布局:
//   ┌─────────────────────────┐
//   │ Header (上下文 + 操作)  │
//   ├─────────────────────────┤
//   │                         │
//   │ ChatList (消息列表)     │  <- 滚动区
//   │                         │
//   ├─────────────────────────┤
//   │ QuickQuestions (流式时隐藏)│
//   │ InputBar (输入框 + 发送)│
//   └─────────────────────────┘
//
// 用 a-drawer 组件, width=480, 移动端 (<768px) 全屏。
// drawerOpen 由 Pinia aiStore 控制, 跨页面持久 (关掉再开还在)。
// ============================================================================

import { computed, nextTick, ref, watch } from 'vue'
import { Plus, X, Sparkles, FileText, History } from 'lucide-vue-next'
import { useAiStore } from '@/stores/ai'
import ChatList from './ChatList.vue'
import SessionList from './SessionList.vue'
import QuickQuestions from './QuickQuestions.vue'
import ContextIndicator from './ContextIndicator.vue'

const ai = useAiStore()

const inputValue = ref('')
const showSessions = ref(false)
const scrollContainer = ref<HTMLElement | null>(null)

const isMobile = computed(() => window.innerWidth < 768)
const drawerWidth = computed(() => (isMobile.value ? '100%' : 480))

const placeholder = computed(() => {
  if (ai.isStreaming) return 'AI 正在思考中...'
  return '问点什么? (Enter 发送, Shift+Enter 换行)'
})

function handleSend() {
  const text = inputValue.value.trim()
  if (!text || ai.isStreaming) return
  inputValue.value = ''
  ai.sendMessage(text)
}

function handleKeydown(e: KeyboardEvent) {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    handleSend()
  }
}

async function newSession() {
  if (ai.isStreaming) return
  ai.currentSessionId = null
  ai.messages = []
  inputValue.value = ''
  showSessions.value = false
}

function onSelectSession() {
  showSessions.value = false
}

// 流式消息变化时自动滚到底
watch(
  () => ai.streamingMessage?.content,
  () => {
    nextTick(() => {
      if (scrollContainer.value) {
        scrollContainer.value.scrollTop = scrollContainer.value.scrollHeight
      }
    })
  },
)

// 切会话时滚到底
watch(
  () => ai.currentSessionId,
  () => {
    nextTick(() => {
      if (scrollContainer.value) {
        scrollContainer.value.scrollTop = scrollContainer.value.scrollHeight
      }
    })
  },
)

function onQuickQuestion(q: string) {
  if (ai.isStreaming) return
  ai.sendMessage(q)
}
</script>

<template>
  <a-drawer
    :open="ai.drawerOpen"
    :width="drawerWidth"
    placement="right"
    :closable="false"
    :body-style="{ padding: 0, display: 'flex', flexDirection: 'column', height: '100%' }"
    :header-style="{ display: 'none' }"
    :mask="isMobile"
    :mask-closable="true"
    @close="ai.closeDrawer()"
  >
    <div class="ai-drawer">
      <!-- ===================== Header ===================== -->
      <header class="ai-header">
        <div class="ai-header__left">
          <Sparkles :size="18" class="ai-header__icon" />
          <div>
            <div class="ai-header__title">AI 助手</div>
            <div class="ai-header__sub">建筑能耗分析 · 节能优化</div>
          </div>
        </div>
        <div class="ai-header__actions">
          <button
            class="ai-header__btn"
            title="历史会话"
            :class="{ 'ai-header__btn--active': showSessions }"
            @click="showSessions = !showSessions"
          >
            <History :size="16" />
          </button>
          <button
            class="ai-header__btn"
            title="新建会话"
            :disabled="ai.isStreaming"
            @click="newSession"
          >
            <Plus :size="16" />
          </button>
          <button class="ai-header__btn" title="关闭" @click="ai.closeDrawer()">
            <X :size="16" />
          </button>
        </div>
      </header>

      <!-- ===================== 上下文 ===================== -->
      <ContextIndicator />

      <!-- ===================== 消息列表 ===================== -->
      <div ref="scrollContainer" class="ai-body">
        <SessionList v-if="showSessions" @select="onSelectSession" />
        <ChatList v-else />
      </div>

      <!-- ===================== 快问 + 输入框 ===================== -->
      <footer class="ai-footer">
        <QuickQuestions
          v-if="!ai.isStreaming && !ai.streamingMessage"
          :questions="ai.quickQuestions"
          @select="onQuickQuestion"
        />
        <div v-if="!ai.isStreaming && !ai.streamingMessage" class="ai-tip">
          <FileText :size="12" />
          <span>提出节能优化类问题（如「给我节能优化建议」）可生成结构化方案并导出 PDF</span>
        </div>
        <div class="ai-input-bar">
          <textarea
            v-model="inputValue"
            class="ai-input"
            :placeholder="placeholder"
            :disabled="ai.isStreaming"
            rows="2"
            @keydown="handleKeydown"
          />
          <button
            class="ai-send-btn"
            :disabled="!inputValue.trim() || ai.isStreaming"
            @click="handleSend"
          >
            <Sparkles :size="16" />
            <span>{{ ai.isStreaming ? '生成中' : '发送' }}</span>
          </button>
        </div>
      </footer>
    </div>
  </a-drawer>
</template>

<style scoped lang="scss">
.ai-drawer {
  display: flex;
  flex-direction: column;
  height: 100%;
  background: $color-paper;
}

.ai-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: $space-3 $space-4;
  background: $color-card;
  border-bottom: 1px solid $gray-200;
  flex-shrink: 0;

  &__left {
    display: flex;
    align-items: center;
    gap: $space-2;
  }

  &__icon {
    color: $color-amber;
  }

  &__title {
    font-size: $fs-md;
    font-weight: $fw-semibold;
    color: $color-concrete;
    line-height: 1.2;
  }

  &__sub {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-top: 2px;
  }

  &__actions {
    display: flex;
    gap: $space-1;
  }

  &__btn {
    display: grid;
    place-items: center;
    width: 30px;
    height: 30px;
    border: 1px solid $gray-200;
    border-radius: $radius-sm;
    background: transparent;
    cursor: pointer;
    color: $color-stone;
    transition: all $transition-base;

    &:hover:not(:disabled) {
      border-color: $color-amber;
      color: $color-amber;
      background: $color-amber-soft;
    }

    &--active {
      border-color: $color-amber;
      color: $color-amber;
      background: $color-amber-soft;
    }

    &:disabled {
      opacity: 0.4;
      cursor: not-allowed;
    }
  }
}

.ai-body {
  flex: 1;
  overflow-y: auto;
  padding: $space-3 $space-4;
  scroll-behavior: smooth;
}

.ai-footer {
  flex-shrink: 0;
  background: $color-card;
  border-top: 1px solid $gray-200;
  padding: $space-3 $space-4 $space-4;
}

// PDF 导出提示条: 跟 QuickQuestions 同条件显示, 流式时隐藏
// 节能优化场景才会出现 PDF 按钮, 这里提示用户问什么问题能触发
.ai-tip {
  display: flex;
  align-items: center;
  gap: $space-1;
  margin-bottom: $space-2;
  padding: $space-1 $space-2;
  background: $color-amber-soft;
  border-radius: $radius-sm;
  font-size: $fs-xs;
  color: $color-amber-deep;
  line-height: 1.4;

  svg {
    flex-shrink: 0;
    color: $color-amber;
  }
}

.ai-input-bar {
  display: flex;
  align-items: flex-end;
  gap: $space-2;
  background: $color-paper;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-2;
  transition: border-color $transition-base;

  &:focus-within {
    border-color: $color-amber;
  }
}

.ai-input {
  flex: 1;
  border: none;
  outline: none;
  background: transparent;
  resize: none;
  font-family: $font-sans;
  font-size: $fs-sm;
  color: $color-concrete;
  line-height: 1.5;
  padding: $space-1 $space-2;
  max-height: 120px;

  &::placeholder {
    color: $color-text-secondary;
    opacity: 0.7;
  }

  &:disabled {
    cursor: not-allowed;
  }
}

.ai-send-btn {
  display: flex;
  align-items: center;
  gap: $space-1;
  padding: $space-2 $space-3;
  border: none;
  border-radius: $radius-sm;
  background: $color-amber;
  color: $color-card;
  font-size: $fs-sm;
  font-weight: $fw-medium;
  cursor: pointer;
  transition: all $transition-base;
  flex-shrink: 0;

  &:hover:not(:disabled) {
    background: $color-amber-deep;
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}
</style>
