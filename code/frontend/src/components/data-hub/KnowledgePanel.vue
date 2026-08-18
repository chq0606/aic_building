<script setup lang="ts">
// ============================================================================
// KnowledgePanel - 知识库文档管理 (上传 / 解析 / 向量化 / 删除)
// ----------------------------------------------------------------------------
// 数据流:
//   1. 拖拽 PDF -> POST /knowledge/documents (上传, status=pending)
//   2. 上传成功后立即 POST /knowledge/documents/{id}/parse (后台解析)
//   3. 后台解析: PDF -> 文本 -> 切 chunk -> 写库, status: pending -> parsing -> parsed/failed
//   4. 用户在列表点 "向量化" -> POST /knowledge/documents/{id}/embed
//      status: parsed -> embedding -> parsed (embedded_chunk_count > 0)
//   5. 失败任意环节: status=failed, parse_error 记原因
//
// 轮询: 列表里有 parsing / embedding 状态时, 每 3 秒拉一次 GET /documents
//   全部终态 (parsed/failed/pending) 停止轮询. 组件卸载也停.
//
// 状态展示 (5 状态 + 向量化进度):
//   pending   待解析   灰    刚上传, 还没开始
//   parsing   解析中   琥珀  旋转图标
//   parsed    已解析   绿    chunk 已入库, 可走 BM25 检索
//            (embedded_chunk_count=0 时: 未向量化, "向量化"按钮可点)
//            (embedded_chunk_count>0 时: 已向量化, 可走向量+BM25 混检)
//   embedding 向量化中 琥珀  旋转图标 + embedded_chunk_count/N 进度
//   failed    失败     红    点击查看 parse_error
// ============================================================================

import { ref, computed, onMounted, onBeforeUnmount, watch } from 'vue'
import { message, Modal, Tooltip, Progress } from 'ant-design-vue'
import {
  UploadCloud, FileText, Loader2, CheckCircle2, AlertCircle,
  Sparkles, Trash2, RefreshCw, BookOpen,
} from 'lucide-vue-next'
import { knowledgeApi, type KnowledgeDocument, type KnowledgeDocStatus } from '@/api/knowledge'
import { ApiError } from '@/api/client'

// 文档列表
const docs = ref<KnowledgeDocument[]>([])
const loading = ref(false)
const errorMsg = ref<string | null>(null)
// 单个文档操作 loading (parse/embed/delete)
const actionLoading = ref<Record<string, boolean>>({})

// ---- 上传 ----
const dragOver = ref(false)
const uploading = ref(false)
const uploadProgress = ref(0)
const fileInputRef = ref<HTMLInputElement | null>(null)

// ---- 状态元数据 ----
interface StatusMeta {
  color: string  // Tag color: default/processing/success/error
  label: string
  icon: typeof CheckCircle2
  spinning: boolean
}

const STATUS_META: Record<KnowledgeDocStatus, StatusMeta> = {
  pending: { color: 'default', label: '待解析', icon: FileText, spinning: false },
  parsing: { color: 'processing', label: '解析中', icon: Loader2, spinning: true },
  parsed: { color: 'success', label: '已解析', icon: CheckCircle2, spinning: false },
  embedding: { color: 'processing', label: '向量化中', icon: Loader2, spinning: true },
  failed: { color: 'error', label: '失败', icon: AlertCircle, spinning: false },
}

// ---- 计算属性 ----
const hasDoc = computed(() => docs.value.length > 0)
// 有 parsing 或 embedding 状态时需要轮询
const hasPendingWork = computed(() =>
  docs.value.some((d) => d.status === 'parsing' || d.status === 'embedding'),
)

// 是否已向量化 (status=parsed 且 embedded_chunk_count > 0)
function isEmbedded(d: KnowledgeDocument): boolean {
  return d.status === 'parsed' && d.embedded_chunk_count > 0
}

// 是否可向量化 (status=parsed 且当前没在向量化)
function canEmbed(d: KnowledgeDocument): boolean {
  return d.status === 'parsed'
}

// 向量化进度百分比 (embedding 状态时显示)
function embedProgressPct(d: KnowledgeDocument): number {
  if (d.chunk_count === 0) return 0
  return Math.min(100, Math.round((d.embedded_chunk_count / d.chunk_count) * 100))
}

// ---- 轮询 ----
let pollTimer: ReturnType<typeof setInterval> | null = null

function schedulePolling() {
  if (hasPendingWork.value) {
    if (!pollTimer) {
      pollTimer = setInterval(pollPending, 3000)
    }
  } else {
    stopPolling()
  }
}

function stopPolling() {
  if (pollTimer) {
    clearInterval(pollTimer)
    pollTimer = null
  }
}

// 只拉 parsing / embedding 状态的文档, 减少带宽
async function pollPending() {
  const pending = docs.value.filter((d) => d.status === 'parsing' || d.status === 'embedding')
  if (pending.length === 0) {
    stopPolling()
    return
  }
  try {
    const all = await knowledgeApi.list()
    // 用 all 整体替换 (列表本身可能因为状态变化顺序变, 简单点直接覆盖)
    // 但保留用户当前操作中的 actionLoading 状态
    docs.value = all
    if (!hasPendingWork.value) {
      stopPolling()
    }
  } catch (e) {
    // 单次轮询失败不致命, 下次再试
    console.warn('[KnowledgePanel] 轮询失败:', e)
  }
}

// ---- 加载列表 ----
async function loadDocs() {
  loading.value = true
  errorMsg.value = null
  try {
    docs.value = await knowledgeApi.list()
    schedulePolling()
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '加载文档列表失败'
  } finally {
    loading.value = false
  }
}

// ---- 上传 ----
function onDragEnter(e: DragEvent) {
  e.preventDefault()
  if (!uploading.value) dragOver.value = true
}
function onDragOver(e: DragEvent) {
  e.preventDefault()
  if (e.dataTransfer) e.dataTransfer.dropEffect = 'copy'
}
function onDragLeave(e: DragEvent) {
  e.preventDefault()
  if (e.currentTarget === e.target) dragOver.value = false
}
function onDrop(e: DragEvent) {
  e.preventDefault()
  dragOver.value = false
  if (uploading.value) return
  const files = e.dataTransfer?.files
  if (!files || files.length === 0) return
  void uploadFiles(Array.from(files))
}

function triggerPick() {
  if (uploading.value) return
  fileInputRef.value?.click()
}

function onFileInputChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files || input.files.length === 0) return
  void uploadFiles(Array.from(input.files))
  input.value = ''
}

async function uploadFiles(files: File[]) {
  // 只接受 PDF
  const pdfs = files.filter((f) => f.name.toLowerCase().endsWith('.pdf'))
  if (pdfs.length === 0) {
    message.warning('只支持 PDF 文件')
    return
  }
  if (pdfs.length < files.length) {
    message.warning(`已过滤 ${files.length - pdfs.length} 个非 PDF 文件`)
  }

  // demo 账号也允许上传/解析/向量化/删除文档 - 知识库不属源数据 (能耗数据),
  // 跟其他非源数据写操作 (3D 重建 / 异常检测) 一样对 demo 开放
  uploading.value = true
  uploadProgress.value = 0
  try {
    for (const file of pdfs) {
      // 上传 (单文件, 走 api.upload)
      const doc = await knowledgeApi.uploadDocument(file)
      message.success(`${file.name} 上传成功, 自动触发解析`)
      // 立即触发 parse (无需用户手点)
      // 兜底: 后端老版本 return {doc_id}, 新版本 return {id, doc_id}, 都兼容
      const docId = (doc as KnowledgeDocument & { doc_id?: string }).id
        ?? (doc as { doc_id?: string }).doc_id
      if (!docId) {
        message.error(`${file.name}: 上传响应缺文档 ID, 无法触发解析`)
        continue
      }
      try {
        await knowledgeApi.parse(docId)
      } catch (e) {
        // parse 触发失败不致命, 用户可在列表里手动重试
        const msg = e instanceof ApiError ? e.message : '触发解析失败'
        message.error(`${file.name}: ${msg}`)
      }
    }
    // 刷新列表
    await loadDocs()
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : '上传失败'
    message.error(msg)
  } finally {
    uploading.value = false
    setTimeout(() => { uploadProgress.value = 0 }, 1000)
  }
}

// ---- 操作: 重新解析 ----
async function reparse(d: KnowledgeDocument) {
  if (actionLoading.value[d.id]) return
  actionLoading.value[d.id] = true
  try {
    await knowledgeApi.parse(d.id)
    message.success(`已重新触发解析: ${d.title}`)
    // 乐观更新本地状态, 等 3 秒轮询会拉到 parsing
    const idx = docs.value.findIndex((x) => x.id === d.id)
    if (idx >= 0) {
      docs.value[idx].status = 'parsing'
      docs.value[idx].parse_error = null
    }
    schedulePolling()
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : '触发解析失败'
    message.error(msg)
  } finally {
    actionLoading.value[d.id] = false
  }
}

// ---- 操作: 向量化 ----
async function embed(d: KnowledgeDocument) {
  if (actionLoading.value[d.id]) return
  actionLoading.value[d.id] = true
  try {
    await knowledgeApi.embed(d.id)
    message.success(`已触发向量化: ${d.title}`)
    // 乐观更新, 等轮询拉进度
    const idx = docs.value.findIndex((x) => x.id === d.id)
    if (idx >= 0) {
      docs.value[idx].status = 'embedding'
    }
    schedulePolling()
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : '触发向量化失败'
    message.error(msg)
  } finally {
    actionLoading.value[d.id] = false
  }
}

// ---- 操作: 删除 ----
function confirmDelete(d: KnowledgeDocument) {
  Modal.confirm({
    title: '删除文档',
    content: `确定删除 "${d.title}"? 此操作会同时删除所有 chunk 和向量, 不可恢复.`,
    okText: '删除',
    okType: 'danger',
    cancelText: '取消',
    async onOk() {
      actionLoading.value[d.id] = true
      try {
        await knowledgeApi.delete(d.id)
        message.success('文档已删除')
        await loadDocs()
      } catch (e) {
        const msg = e instanceof ApiError ? e.message : '删除失败'
        message.error(msg)
      } finally {
        actionLoading.value[d.id] = false
      }
    },
  })
}

// ---- 刷新 ----
async function refresh() {
  await loadDocs()
}

// ---- 工具: 格式化 ----
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

// file_hash 前 8 位 (展示用, 让用户能区分同名文件)
function shortHash(hash: string): string {
  return hash.slice(0, 8)
}

function formatTime(iso: string | null): string {
  if (!iso) return '-'
  return new Date(iso).toLocaleString('zh-CN', {
    month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit',
  })
}

// doc_type 中文标签
const DOC_TYPE_LABELS: Record<string, string> = {
  standard: '标准',
  manual: '手册',
  sop: 'SOP',
  report: '报告',
  paper: '论文',
  other: '其他',
}

// ---- 生命周期 ----
onMounted(loadDocs)
onBeforeUnmount(stopPolling)

// 父组件可通过 refreshSignal 触发刷新 (跟 ImportBatchList 同款约定)
const props = defineProps<{ refreshSignal?: number }>()
watch(() => props.refreshSignal, (n, old) => {
  if (n && n !== old) loadDocs()
})
</script>

<template>
  <div class="kp">
    <!-- 上半屏: 上传区 -->
    <section class="kp__upload">
      <div
        class="kp__dropzone"
        :class="{ 'kp__dropzone--over': dragOver, 'kp__dropzone--uploading': uploading }"
        @dragenter="onDragEnter"
        @dragover="onDragOver"
        @dragleave="onDragLeave"
        @drop="onDrop"
        @click="triggerPick"
      >
        <input
          ref="fileInputRef"
          type="file"
          accept=".pdf,application/pdf"
          multiple
          class="kp__file-input"
          @change="onFileInputChange"
        >
        <div class="kp__dropzone-content">
          <Loader2 v-if="uploading" :size="32" class="spin" />
          <UploadCloud v-else :size="32" />
          <div class="kp__dropzone-title">
            {{ uploading ? '上传中...' : '拖拽 PDF 到此处, 或点击选择' }}
          </div>
          <div class="kp__dropzone-hint">
            支持国标 / 行标 / 技术手册等 PDF 文档 · 单文件最大 200MB
          </div>
          <div v-if="uploading && uploadProgress > 0" class="kp__upload-progress">
            <Progress :percent="uploadProgress" :show-info="false" size="small" />
          </div>
        </div>
      </div>
      <div class="kp__upload-tip">
        <Sparkles :size="12" />
        <span>上传后自动解析 (PDF → 文本 → 切 chunk), 解析完成后可在列表手动触发向量化</span>
      </div>
    </section>

    <!-- 下半屏: 文档列表 -->
    <section class="kp__list">
      <header class="kp__list-header">
        <div class="kp__list-title">
          <BookOpen :size="16" />
          <span>已上传文档</span>
          <span v-if="hasDoc" class="kp__list-count">{{ docs.length }} 个</span>
        </div>
        <button class="kp__refresh-btn" :disabled="loading" @click="refresh">
          <RefreshCw :size="14" :class="{ spin: loading }" />
          <span>刷新</span>
        </button>
      </header>

      <div v-if="errorMsg" class="kp__error">
        <AlertCircle :size="14" />
        <span>{{ errorMsg }}</span>
      </div>

      <div v-if="loading && !hasDoc" class="kp__empty">
        <Loader2 :size="24" class="spin" />
        <span>加载中...</span>
      </div>

      <div v-else-if="!hasDoc" class="kp__empty">
        <FileText :size="32" />
        <span>暂无文档, 上传 PDF 后此处显示解析进度</span>
      </div>

      <div v-else class="kp__table-wrap">
        <table class="kp__table">
          <thead>
            <tr>
              <th class="kp__th kp__th--title">文件名</th>
              <th class="kp__th kp__th--type">类型</th>
              <th class="kp__th kp__th--status">状态</th>
              <th class="kp__th kp__th--chunks">Chunk</th>
              <th class="kp__th kp__th--pages">页数</th>
              <th class="kp__th kp__th--time">上传时间</th>
              <th class="kp__th kp__th--actions">操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="d in docs" :key="d.id" class="kp__row" :class="{ 'kp__row--failed': d.status === 'failed' }">
              <!-- 文件名 -->
              <td class="kp__td kp__td--title">
                <div class="kp__file">
                  <FileText :size="14" class="kp__file-icon" />
                  <div class="kp__file-info">
                    <div class="kp__file-name" :title="d.title">{{ d.title }}</div>
                    <div class="kp__file-meta">
                      <span class="kp__file-hash">#{{ shortHash(d.file_hash) }}</span>
                      <span v-if="d.metadata.standard_no" class="kp__file-std">{{ d.metadata.standard_no }}</span>
                    </div>
                  </div>
                </div>
              </td>

              <!-- 类型 -->
              <td class="kp__td kp__td--type">
                <span class="kp__type-tag">{{ DOC_TYPE_LABELS[d.doc_type] ?? d.doc_type }}</span>
              </td>

              <!-- 状态 -->
              <td class="kp__td kp__td--status">
                <div class="kp__status">
                  <component
                    :is="STATUS_META[d.status].icon"
                    :size="13"
                    :class="{ spin: STATUS_META[d.status].spinning }"
                    :style="{ color: `var(--status-color-${d.status})` }"
                  />
                  <span class="kp__status-label">{{ STATUS_META[d.status].label }}</span>
                  <!-- 向量化进度 (embedding 状态时显示) -->
                  <span v-if="d.status === 'embedding'" class="kp__status-progress">
                    {{ d.embedded_chunk_count }}/{{ d.chunk_count }}
                  </span>
                  <!-- 已解析已向量化标记 -->
                  <span v-else-if="isEmbedded(d)" class="kp__status-extra">已向量化</span>
                  <!-- 失败: 点击查看错误 -->
                  <Tooltip v-else-if="d.status === 'failed' && d.parse_error" :title="d.parse_error">
                    <span class="kp__status-error-hint">查看原因</span>
                  </Tooltip>
                </div>
                <!-- 向量化进度条 (embedding 状态时显示) -->
                <Progress
                  v-if="d.status === 'embedding' && d.chunk_count > 0"
                  :percent="embedProgressPct(d)"
                  :show-info="false"
                  size="small"
                  class="kp__embed-progress"
                />
              </td>

              <!-- Chunk 数 -->
              <td class="kp__td kp__td--chunks">
                <span v-if="d.chunk_count > 0" class="kp__num">{{ d.chunk_count }}</span>
                <span v-else class="kp__num kp__num--muted">-</span>
                <span v-if="isEmbedded(d)" class="kp__num-sub">/{{ d.embedded_chunk_count }} 已向量化</span>
              </td>

              <!-- 页数 -->
              <td class="kp__td kp__td--pages">
                <span v-if="d.page_count" class="kp__num">{{ d.page_count }}</span>
                <span v-else class="kp__num kp__num--muted">-</span>
              </td>

              <!-- 上传时间 -->
              <td class="kp__td kp__td--time">{{ formatTime(d.uploaded_at) }}</td>

              <!-- 操作 -->
              <td class="kp__td kp__td--actions">
                <div class="kp__actions">
                  <!-- 向量化: parsed 状态可点 (已向量化的也可重跑) -->
                  <Tooltip :title="canEmbed(d) ? '触发 BGE 向量化' : '需先解析完成'">
                    <button
                      class="kp__action-btn kp__action-btn--embed"
                      :disabled="!canEmbed(d) || actionLoading[d.id]"
                      @click="embed(d)"
                    >
                      <Loader2 v-if="actionLoading[d.id] && d.status === 'embedding'" :size="12" class="spin" />
                      <Sparkles v-else :size="12" />
                      <span>{{ isEmbedded(d) ? '重新向量化' : '向量化' }}</span>
                    </button>
                  </Tooltip>

                  <!-- 重新解析: 失败或已解析时可重跑 -->
                  <Tooltip :title="d.status === 'parsing' ? '解析中...' : '重新解析'">
                    <button
                      class="kp__action-btn"
                      :disabled="d.status === 'parsing' || d.status === 'embedding' || actionLoading[d.id]"
                      @click="reparse(d)"
                    >
                      <RefreshCw :size="12" />
                      <span>重解析</span>
                    </button>
                  </Tooltip>

                  <!-- 删除 -->
                  <Tooltip title="删除文档">
                    <button
                      class="kp__action-btn kp__action-btn--danger"
                      :disabled="actionLoading[d.id]"
                      @click="confirmDelete(d)"
                    >
                      <Trash2 :size="12" />
                    </button>
                  </Tooltip>
                </div>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>

<style scoped lang="scss">
.kp {
  display: flex;
  flex-direction: column;
  gap: $space-3;
  height: 100%;
  min-height: 0;
}

// ---- 上传区 ----
.kp__upload {
  display: flex;
  flex-direction: column;
  gap: $space-2;
  flex-shrink: 0;
}

.kp__dropzone {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: $space-5 $space-4;
  border: 2px dashed $color-line;
  border-radius: $radius-md;
  background: $gray-50;
  cursor: pointer;
  transition: all $transition-fast;
  min-height: 110px;

  &:hover {
    border-color: $color-amber;
    background: $color-amber-soft;
  }

  &--over {
    border-color: $color-amber;
    background: $color-amber-soft;
    transform: scale(1.005);
  }

  &--uploading {
    cursor: progress;
    border-color: $color-amber;
  }
}

.kp__file-input {
  position: absolute;
  width: 0;
  height: 0;
  opacity: 0;
  pointer-events: none;
}

.kp__dropzone-content {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: $space-1;
  color: $color-stone;

  :deep(svg) {
    color: $color-amber;
  }
}

.kp__dropzone-title {
  font-size: $fs-sm;
  font-weight: $fw-medium;
  color: $color-concrete;
  margin-top: $space-1;
}

.kp__dropzone-hint {
  font-size: $fs-xs;
  color: $color-text-secondary;
}

.kp__upload-progress {
  width: 200px;
  margin-top: $space-1;
}

.kp__upload-tip {
  display: flex;
  align-items: center;
  gap: $space-1;
  font-size: $fs-xs;
  color: $color-text-secondary;

  :deep(svg) {
    color: $color-amber;
  }
}

// ---- 列表 ----
.kp__list {
  flex: 1;
  min-height: 0;
  display: flex;
  flex-direction: column;
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  overflow: hidden;
}

.kp__list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: $space-2 $space-3;
  border-bottom: 1px solid $gray-200;
  background: $gray-50;
  flex-shrink: 0;
}

.kp__list-title {
  display: flex;
  align-items: center;
  gap: $space-2;
  font-size: $fs-sm;
  font-weight: $fw-medium;
  color: $color-concrete;

  :deep(svg) {
    color: $color-amber;
  }
}

.kp__list-count {
  padding: 1px 6px;
  background: $color-amber-soft;
  color: $color-amber-deep;
  border-radius: $radius-xs;
  font-size: $fs-xs;
  font-family: $font-mono;
}

.kp__refresh-btn {
  display: flex;
  align-items: center;
  gap: 4px;
  padding: 4px 8px;
  border: 1px solid $color-line;
  background: $color-card;
  border-radius: $radius-sm;
  font-size: $fs-xs;
  color: $color-concrete;
  cursor: pointer;
  transition: all $transition-fast;

  &:hover:not(:disabled) {
    border-color: $color-amber;
    color: $color-amber;
  }

  &:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }
}

.kp__error {
  display: flex;
  align-items: center;
  gap: $space-1;
  padding: $space-2 $space-3;
  background: rgba($color-red, 0.05);
  color: $color-red;
  font-size: $fs-xs;
  border-bottom: 1px solid rgba($color-red, 0.2);
}

.kp__empty {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: $space-2;
  color: $color-text-secondary;
  font-size: $fs-sm;

  :deep(svg) {
    color: $color-stone;
    opacity: 0.6;
  }
}

// ---- 表格 ----
.kp__table-wrap {
  flex: 1;
  overflow: auto;
  min-height: 0;
}

.kp__table {
  width: 100%;
  border-collapse: collapse;
  font-size: $fs-sm;
}

.kp__th {
  padding: $space-2 $space-3;
  text-align: left;
  font-weight: $fw-medium;
  color: $color-text-secondary;
  font-size: $fs-xs;
  background: $gray-50;
  border-bottom: 1px solid $gray-200;
  white-space: nowrap;
  position: sticky;
  top: 0;
  z-index: 1;

  &--title { min-width: 200px; }
  &--type { width: 60px; }
  &--status { width: 140px; }
  &--chunks { width: 100px; }
  &--pages { width: 60px; }
  &--time { width: 100px; }
  &--actions { width: 220px; }
}

.kp__row {
  border-bottom: 1px solid $gray-100;
  transition: background $transition-fast;

  &:hover {
    background: $gray-50;
  }

  &--failed {
    background: rgba($color-red, 0.02);
  }
}

.kp__td {
  padding: $space-2 $space-3;
  vertical-align: middle;
  color: $color-concrete;

  &--title { min-width: 200px; }
}

// 文件名单元格
.kp__file {
  display: flex;
  align-items: center;
  gap: $space-2;
  min-width: 0;
}

.kp__file-icon {
  color: $color-amber;
  flex-shrink: 0;
}

.kp__file-info {
  display: flex;
  flex-direction: column;
  gap: 2px;
  min-width: 0;
}

.kp__file-name {
  font-size: $fs-sm;
  color: $color-concrete;
  font-weight: $fw-medium;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  max-width: 280px;
}

.kp__file-meta {
  display: flex;
  align-items: center;
  gap: $space-1;
  font-size: 10px;
  color: $color-text-secondary;
  font-family: $font-mono;
}

.kp__file-hash {
  opacity: 0.7;
}

.kp__file-std {
  padding: 0 4px;
  background: $color-amber-soft;
  color: $color-amber-deep;
  border-radius: $radius-xs;
  font-weight: $fw-medium;
}

// 类型 tag
.kp__type-tag {
  display: inline-block;
  padding: 1px 6px;
  background: $gray-100;
  color: $color-text-secondary;
  border-radius: $radius-xs;
  font-size: $fs-xs;
  font-weight: $fw-medium;
}

// 状态
.kp__status {
  display: flex;
  align-items: center;
  gap: $space-1;

  // 状态颜色 (跟 STATUS_META 对应, 用 CSS var 方便 icon 复用)
  --status-color-pending: #{$color-stone};
  --status-color-parsing: #{$color-amber};
  --status-color-parsed: #{$color-green};
  --status-color-embedding: #{$color-amber};
  --status-color-failed: #{$color-red};
}

.kp__status-label {
  font-size: $fs-xs;
  color: $color-concrete;
  font-weight: $fw-medium;
}

.kp__status-progress {
  font-size: 10px;
  color: $color-amber-deep;
  font-family: $font-mono;
  padding: 0 4px;
  background: $color-amber-soft;
  border-radius: $radius-xs;
}

.kp__status-extra {
  font-size: 10px;
  color: $color-green;
  padding: 0 4px;
  background: rgba($color-green, 0.1);
  border-radius: $radius-xs;
}

.kp__status-error-hint {
  font-size: 10px;
  color: $color-red;
  text-decoration: underline dotted;
  cursor: help;
}

.kp__embed-progress {
  margin-top: 4px;
  margin-right: $space-3;
}

// 数字
.kp__num {
  font-family: $font-mono;
  font-size: $fs-sm;
  color: $color-concrete;
  font-weight: $fw-medium;

  &--muted {
    color: $color-text-secondary;
    opacity: 0.5;
  }
}

.kp__num-sub {
  display: block;
  font-size: 10px;
  color: $color-green;
  margin-top: 2px;
}

// 操作
.kp__actions {
  display: flex;
  align-items: center;
  gap: $space-1;
}

.kp__action-btn {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  padding: 3px 8px;
  border: 1px solid $color-line;
  background: $color-card;
  border-radius: $radius-sm;
  font-size: $fs-xs;
  color: $color-concrete;
  cursor: pointer;
  transition: all $transition-fast;

  &:hover:not(:disabled) {
    border-color: $color-amber;
    color: $color-amber;
    background: $color-amber-soft;
  }

  &:disabled {
    opacity: 0.4;
    cursor: not-allowed;
  }

  &--embed {
    border-color: $color-amber;
    color: $color-amber-deep;
    background: $color-amber-soft;

    &:hover:not(:disabled) {
      background: $color-amber;
      color: $color-card;
      border-color: $color-amber;
    }
  }

  &--danger {
    padding: 3px 6px;

    &:hover:not(:disabled) {
      border-color: $color-red;
      color: $color-red;
      background: rgba($color-red, 0.05);
    }
  }
}

.spin {
  animation: spin 1s linear infinite;
}

@keyframes spin {
  to { transform: rotate(360deg); }
}
</style>
