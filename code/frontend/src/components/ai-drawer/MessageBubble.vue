<script setup lang="ts">
// ============================================================================
// MessageBubble - 消息气泡
// ----------------------------------------------------------------------------
// 区分 user / assistant 两种气泡:
//   - user 气泡靠右, 琥珀色背景
//   - assistant 气泡靠左, 白色背景, 内含:
//     - 工具调用卡片列表 (ToolCallCard)
//     - markdown 渲染的回答 (delta 流式累积)
//     - 引用证据卡片 (EvidenceCard)
//     - 节能优化四段卡片 (OptimizationPlanCard)
//     - PDF 导出按钮 (有 optimization_plan 时显示)
//
// 流式时 (is_streaming=true): 末尾显示闪烁光标, 表示还在生成
// ============================================================================

import { computed } from 'vue'
import { User, Sparkles, Loader2 } from 'lucide-vue-next'
import type { ChatMessage, ToolCall, Citation, OptimizationPlan } from '@/api/assistant'
import { useMarkdown } from '@/utils/markdown'
import ToolCallCard from './ToolCallCard.vue'
import EvidenceCard from './EvidenceCard.vue'
import OptimizationPlanCard from './OptimizationPlanCard.vue'

const props = defineProps<{
  message: ChatMessage & {
    is_streaming?: boolean
    tool_results?: Array<{ tool_call_id: string; ok: boolean }>
    thinking_text?: string | null
    error?: string
  }
}>()

const isUser = computed(() => props.message.role === 'user')
const isAssistant = computed(() => props.message.role === 'assistant')

const renderedContent = useMarkdown(() => props.message.content || '')

const toolCalls = computed<ToolCall[]>(() => props.message.tool_calls || [])
const citations = computed<Citation[]>(() => props.message.citations || [])
const optPlan = computed<OptimizationPlan | null>(() => props.message.optimization_plan ?? null)

// 工具状态: 流式中可能只有 tool_calls 没有 tool_results, 全 pending
const toolStatus = (tcId: string): 'pending' | 'ok' | 'error' => {
  const results = props.message.tool_results
  if (!results || results.length === 0) return 'pending'
  const r = results.find(tr => tr.tool_call_id === tcId)
  if (!r) return 'pending'
  return r.ok ? 'ok' : 'error'
}

const isStreaming = computed(() => props.message.is_streaming && !props.message.error)
const hasError = computed(() => !!props.message.error)
</script>

<template>
  <div class="msg" :class="isUser ? 'msg--user' : 'msg--assistant'">
    <!-- 头像 -->
    <div class="msg__avatar">
      <User v-if="isUser" :size="14" />
      <Sparkles v-else :size="14" />
    </div>

    <!-- 气泡 -->
    <div class="msg__bubble">
      <!-- 工具调用卡片 -->
      <div v-if="isAssistant && toolCalls.length > 0" class="msg__tools">
        <ToolCallCard
          v-for="tc in toolCalls"
          :key="tc.id"
          :tool="tc"
          :status="toolStatus(tc.id)"
        />
      </div>

      <!-- 内容 (markdown) -->
      <div v-if="renderedContent" class="msg__content markdown-body" v-html="renderedContent" />

      <!-- 流式指示器 -->
      <!-- content 为空: thinking_text 占据回答位置 (大字 + 旋转图标) -->
      <span v-if="isStreaming && !renderedContent" class="msg__thinking">
        <Loader2 :size="12" class="spin" />
        <span>{{ message.thinking_text || '思考中...' }}</span>
      </span>
      <!-- content 非空 + thinking_text: 输出停顿心跳, 在 content 下方小字显示"正在继续..." -->
      <div v-else-if="isStreaming && renderedContent && message.thinking_text" class="msg__thinking-inline">
        <Loader2 :size="11" class="spin" />
        <span>{{ message.thinking_text }}</span>
      </div>
      <!-- content 非空 + 无 thinking_text: 正在输出, 显示闪烁光标 -->
      <span v-else-if="isStreaming" class="msg__cursor" />

      <!-- 引用证据 -->
      <div v-if="isAssistant && citations.length > 0" class="msg__evidence">
        <div class="msg__evidence-label">引用证据 ({{ citations.length }})</div>
        <EvidenceCard
          v-for="(c, i) in citations"
          :key="i"
          :citation="c"
        />
      </div>

      <!-- 节能优化四段卡片 -->
      <OptimizationPlanCard
        v-if="isAssistant && optPlan"
        :plan="optPlan"
        :message-id="message.id"
      />

      <!-- 错误 -->
      <div v-if="hasError" class="msg__error">
        {{ message.error }}
      </div>
    </div>
  </div>
</template>

<style scoped lang="scss">
.msg {
  display: flex;
  gap: $space-2;
  margin-bottom: $space-4;
  align-items: flex-start;

  &--user {
    flex-direction: row-reverse;

    .msg__bubble {
      background: $color-amber;
      color: $color-card;
      border-color: $color-amber;
    }

    .msg__avatar {
      background: $color-amber;
      color: $color-card;
    }
  }

  &--assistant {
    .msg__bubble {
      background: $color-card;
      color: $color-concrete;
      border: 1px solid $gray-200;
    }

    .msg__avatar {
      background: $color-amber-soft;
      color: $color-amber-deep;
    }
  }

  &__avatar {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    display: grid;
    place-items: center;
    flex-shrink: 0;
  }

  &__bubble {
    flex: 1;
    min-width: 0;
    padding: $space-2 $space-3;
    border-radius: $radius-md;
    font-size: $fs-sm;
    line-height: 1.6;
    max-width: calc(100% - 40px);
    word-break: break-word;
  }

  &--user &__bubble {
    max-width: 80%;
  }

  &__tools {
    margin-bottom: $space-2;
  }

  &__content {
    :deep(p) {
      margin: 0 0 $space-1 0;
      &:last-child { margin-bottom: 0; }
    }
    :deep(ul), :deep(ol) {
      margin: $space-1 0;
      padding-left: $space-4;
    }
    :deep(li) {
      margin: 2px 0;
    }
    :deep(strong) {
      font-weight: $fw-semibold;
      color: $color-amber-deep;
    }
    :deep(code) {
      background: $gray-100;
      padding: 1px 4px;
      border-radius: $radius-xs;
      font-family: $font-mono;
      font-size: 0.85em;
    }
    :deep(pre) {
      background: $gray-100;
      padding: $space-2;
      border-radius: $radius-sm;
      overflow-x: auto;
      margin: $space-1 0;
    }
    :deep(table) {
      border-collapse: collapse;
      width: 100%;
      margin: $space-1 0;
    }
    :deep(th), :deep(td) {
      border: 1px solid $gray-200;
      padding: 4px 8px;
      font-size: 0.9em;
    }
    :deep(th) {
      background: $gray-50;
      font-weight: $fw-semibold;
    }
    :deep(a) {
      color: $color-amber-deep;
      text-decoration: underline;
    }
  }

  &__thinking {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: $color-text-secondary;
    font-size: $fs-xs;
    font-style: italic;
  }

  &__thinking-inline {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    margin-top: $space-1;
    color: $color-text-secondary;
    font-size: 11px;
    font-style: italic;
    opacity: 0.85;
  }

  &__cursor {
    display: inline-block;
    width: 6px;
    height: 14px;
    background: $color-amber;
    margin-left: 2px;
    vertical-align: text-bottom;
    animation: blink 1s steps(2) infinite;
  }

  &__evidence {
    margin-top: $space-2;
    padding-top: $space-2;
    border-top: 1px dashed $gray-200;
  }

  &__evidence-label {
    font-size: 10px;
    color: $color-text-secondary;
    text-transform: uppercase;
    letter-spacing: 0.5px;
    margin-bottom: $space-1;
    font-weight: $fw-medium;
  }

  &__error {
    margin-top: $space-2;
    padding: $space-2;
    background: rgba($color-red, 0.08);
    border: 1px solid rgba($color-red, 0.3);
    border-radius: $radius-sm;
    color: $color-red;
    font-size: $fs-xs;
  }
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}

@keyframes blink {
  50% { opacity: 0; }
}
</style>
