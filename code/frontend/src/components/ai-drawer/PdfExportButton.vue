<script setup lang="ts">
// ============================================================================
// PdfExportButton - 节能方案 PDF 导出按钮
// ----------------------------------------------------------------------------
// 点击调 aiStore.exportPdf(message_id), 拿到 Blob 后用 URL.createObjectURL
// + a 标签触发下载。
//
// 导出是耗时操作 (matplotlib 画图 + weasyprint 渲染), 按钮显示 loading 状态
// ============================================================================

import { ref } from 'vue'
import { Download, Loader2, FileText } from 'lucide-vue-next'
import { message as antdMessage } from 'ant-design-vue'
import { useAiStore } from '@/stores/ai'

const props = defineProps<{
  messageId: string
}>()

const ai = useAiStore()
const loading = ref(false)

async function handleExport() {
  if (loading.value) return
  loading.value = true
  try {
    const blob = await ai.exportPdf(props.messageId)
    // 触发下载
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    // 后端返了 Content-Disposition: filename*=UTF-8''<encoded>
    // 但 Blob 下载没法读 header, 用默认文件名
    a.download = `节能方案_${new Date().toISOString().slice(0, 10)}.pdf`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
    antdMessage.success('PDF 导出成功')
  } catch (e: unknown) {
    const msg = e instanceof Error ? e.message : 'PDF 导出失败'
    antdMessage.error(msg)
  } finally {
    loading.value = false
  }
}
</script>

<template>
  <button class="pdf-btn" :disabled="loading" @click="handleExport">
    <Loader2 v-if="loading" :size="14" class="spin" />
    <Download v-else :size="14" />
    <span>{{ loading ? '生成中...' : '导出节能方案 PDF' }}</span>
    <FileText :size="12" class="pdf-btn__icon" />
  </button>
</template>

<style scoped lang="scss">
.pdf-btn {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: $space-1;
  width: 100%;
  padding: $space-2 $space-3;
  background: $color-amber;
  color: $color-card;
  border: none;
  border-radius: $radius-sm;
  font-size: $fs-sm;
  font-weight: $fw-medium;
  cursor: pointer;
  transition: all $transition-base;

  &:hover:not(:disabled) {
    background: $color-amber-deep;
  }

  &:disabled {
    opacity: 0.6;
    cursor: not-allowed;
  }

  &__icon {
    opacity: 0.6;
  }
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
