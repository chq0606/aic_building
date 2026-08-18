<script setup lang="ts">
// ============================================================================
// FileUpload - 拖拽 + 点击上传 CSV/XLSX
// ----------------------------------------------------------------------------
// 支持单文件 (target_type 单一) 和多文件 (批量上传) 两种模式:
//   - 单文件: 调 POST /uploads/single, 返回一个 session_id
//   - 多文件: 调 POST /uploads/multi, 返回多个 session_id + 错误列表
//
// 上传进度用 axios onUploadProgress 监听, 后端是单次接收完整文件所以进度
// 只反映上传阶段, validate / commit 阶段不算在内 (那些是独立 API 调用).
//
// emit:
//   - uploaded: 单文件模式返 { session_id, upload_file_id, filename }
//               多文件模式返 { sessions: [...], errors: [...] }
//   - failed: 上传失败的错误信息
//
// 父组件 MappingWizard 监听 uploaded 拿 session_id 进入下一步 mapping-suggest.
// ============================================================================

import { ref, computed } from 'vue'
import { UploadCloud, File as FileIcon, Loader2, X, AlertCircle } from 'lucide-vue-next'
import {
  uploadApi,
  type UploadTargetType,
  type UploadSingleResult,
  type UploadMultiResult,
  type UploadedPayload,
} from '@/api/upload'
import { ApiError } from '@/api/client'

const props = withDefaults(defineProps<{
  // 上传到哪张表: POINT (能耗读数, 默认) / WEATHER / BUILDING
  targetType?: UploadTargetType
  // 多文件模式 (一次拖多个文件, 各建一个 session)
  multiple?: boolean
  // 接受的文件类型, 默认 CSV + Excel
  accept?: string
  // 标题副文案, 给父组件自定义展示
  title?: string
  subtitle?: string
}>(), {
  targetType: 'POINT',
  multiple: false,
  accept: '.csv,.xlsx,.xls',
  title: '上传数据文件',
  subtitle: '支持 .csv / .xlsx, 单文件最大 50MB',
})

const emit = defineEmits<{
  uploaded: [payload: UploadedPayload]
  failed: [message: string]
}>()

// 拖拽状态
const dragOver = ref(false)
// 上传进度 0-100
const progress = ref(0)
// 上传中
const uploading = ref(false)
// 已上传的文件列表 (展示用)
const uploadedFiles = ref<Array<{ name: string; size: number; sessionId?: string; error?: string }>>([])
// 错误信息
const errorMsg = ref<string | null>(null)

// 文件输入 ref, 给点击触发用
const fileInputRef = ref<HTMLInputElement | null>(null)

const hasUploaded = computed(() => uploadedFiles.value.length > 0)

// 拖拽进入/离开
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
  // 子元素之间触发 dragleave 也要算离开, 用 relatedTarget 判断
  if (e.currentTarget === e.target) dragOver.value = false
}

function onDrop(e: DragEvent) {
  e.preventDefault()
  dragOver.value = false
  if (uploading.value) return
  const files = e.dataTransfer?.files
  if (!files || files.length === 0) return

  // 多文件模式收全部, 单文件模式只收第一个
  const list = props.multiple ? Array.from(files) : [files[0]]
  uploadFiles(list)
}

// 点击触发文件选择
function triggerPick() {
  if (uploading.value) return
  fileInputRef.value?.click()
}

function onFileInputChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files || input.files.length === 0) return
  const list = Array.from(input.files)
  uploadFiles(list)
  // 清空 input value 让用户能再次选同一个文件 (onChange 同名不触发)
  input.value = ''
}

// 文件大小格式化
function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

async function uploadFiles(files: File[]) {
  if (files.length === 0) return
  uploading.value = true
  progress.value = 0
  errorMsg.value = null

  // 先把待上传文件预填到列表 (size 已知, sessionId 待填)
  // 显式标 sessionId/error 可选, 否则 TS 推断成 undefined 字面量无法后续赋值
  const pendingFiles: Array<{ name: string; size: number; sessionId?: string; error?: string }> =
    files.map((f) => ({ name: f.name, size: f.size }))
  uploadedFiles.value = pendingFiles

  try {
    if (props.multiple) {
      // 多文件一次性 POST /uploads/multi
      const result = await uploadApi.uploadMulti(files, props.targetType)
      // 把后端返的 session_id / errors 回填到 pendingFiles
      result.sessions.forEach((s) => {
        const idx = pendingFiles.findIndex((p) => p.name === s.filename)
        if (idx >= 0) pendingFiles[idx].sessionId = s.session_id
      })
      result.errors.forEach((e) => {
        const idx = pendingFiles.findIndex((p) => p.name === e.filename)
        if (idx >= 0) pendingFiles[idx].error = e.reason
      })
      const payload: UploadedPayload = { mode: 'multi', result }
      emit('uploaded', payload)
    } else {
      // 单文件 POST /uploads/single, axios onUploadProgress 监听上传进度
      const file = files[0]
      const result = await uploadSingleWithProgress(file)
      pendingFiles[0].sessionId = result.session_id
      const payload: UploadedPayload = {
        mode: 'single',
        session_id: result.session_id,
        upload_file_id: result.upload_file_id,
        filename: file.name,
      }
      emit('uploaded', payload)
    }
    progress.value = 100
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : '上传失败, 请重试'
    errorMsg.value = msg
    // 标记 pendingFiles 错误状态
    pendingFiles.forEach((p) => {
      if (!p.sessionId) p.error = msg
    })
    emit('failed', msg)
  } finally {
    uploading.value = false
    // 1s 后清进度条, 让用户看到 100% 的视觉确认
    setTimeout(() => { progress.value = 0 }, 1000)
  }
}

// 单文件上传, 带 axios onUploadProgress 监听进度
async function uploadSingleWithProgress(file: File): Promise<UploadSingleResult> {
  const fd = new FormData()
  fd.append('file', file)
  // 直接调 axios (而非 api.upload) 拿 progress 事件
  // client 是 default export, 动态 import 拿 default
  const clientMod = await import('@/api/client')
  const client = clientMod.default
  const resp = await client.post(`/uploads/single?target_type=${props.targetType}`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (ev: { loaded: number; total?: number }) => {
      if (ev.total) {
        progress.value = Math.round((ev.loaded / ev.total) * 100)
      }
    },
  })
  // 后端 envelope: { code, message, data }
  if (resp.data?.code !== 0) {
    throw new ApiError(resp.data?.code ?? -1, resp.data?.message ?? '上传失败')
  }
  return resp.data.data as UploadSingleResult
}

// 清空已上传列表 (父组件触发 reset 时调)
function reset() {
  uploadedFiles.value = []
  progress.value = 0
  errorMsg.value = null
}

defineExpose({ reset })
</script>

<template>
  <section class="fu">
    <header class="fu__header">
      <div class="fu__title-wrap">
        <h3 class="fu__title">{{ title }}</h3>
        <p class="fu__subtitle">{{ subtitle }}</p>
      </div>
    </header>

    <div
      class="fu__dropzone"
      :class="{ 'fu__dropzone--over': dragOver, 'fu__dropzone--uploading': uploading }"
      @dragenter="onDragEnter"
      @dragover="onDragOver"
      @dragleave="onDragLeave"
      @drop="onDrop"
      @click="triggerPick"
    >
      <input
        ref="fileInputRef"
        type="file"
        class="fu__input"
        :accept="accept"
        :multiple="multiple"
        @change="onFileInputChange"
      >

      <div class="fu__dropzone-content">
        <Loader2 v-if="uploading" :size="32" class="fu__spin" />
        <UploadCloud v-else :size="32" class="fu__dropzone-icon" />
        <p class="fu__dropzone-hint">
          {{ uploading ? '上传中...' : '拖拽文件到此处, 或点击选择' }}
        </p>
        <p class="fu__dropzone-sub">
          {{ multiple ? '支持多文件批量上传' : `仅 ${targetType} 类型` }}
        </p>
      </div>

      <!-- 上传进度条 -->
      <div v-if="uploading || progress > 0" class="fu__progress">
        <div class="fu__progress-bar" :style="{ width: `${progress}%` }" />
      </div>
    </div>

    <!-- 已上传文件列表 -->
    <div v-if="hasUploaded" class="fu__list">
      <div
        v-for="(f, i) in uploadedFiles"
        :key="i"
        class="fu__file"
        :class="{ 'fu__file--error': !!f.error, 'fu__file--ok': !!f.sessionId }"
      >
        <FileIcon :size="16" class="fu__file-icon" />
        <div class="fu__file-body">
          <div class="fu__file-name">{{ f.name }}</div>
          <div class="fu__file-meta">
            <span>{{ formatSize(f.size) }}</span>
            <span v-if="f.sessionId" class="fu__file-tag fu__file-tag--ok">已上传</span>
            <span v-if="f.error" class="fu__file-tag fu__file-tag--err">{{ f.error }}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- 错误信息 -->
    <p v-if="errorMsg" class="fu__error">
      <AlertCircle :size="14" />
      {{ errorMsg }}
    </p>
  </section>
</template>

<style scoped lang="scss">
.fu {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-4;
  transition: box-shadow $transition-base;

  &:hover {
    box-shadow: $shadow-sm;
  }

  &__header {
    margin-bottom: $space-3;
  }

  &__title {
    font-size: $fs-md;
    font-weight: $fw-semibold;
    color: $color-concrete;
    margin: 0;
  }

  &__subtitle {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin: 2px 0 0;
  }

  &__dropzone {
    position: relative;
    border: 2px dashed $color-line;
    border-radius: $radius-md;
    padding: $space-5 $space-4;
    text-align: center;
    cursor: pointer;
    background: $gray-50;
    transition: all $transition-base;

    &:hover:not(.fu__dropzone--uploading) {
      border-color: $color-amber;
      background: $color-amber-soft;
    }

    &--over {
      border-color: $color-amber;
      background: $color-amber-soft;
      transform: scale(1.005);
    }

    &--uploading {
      cursor: wait;
    }
  }

  &__input {
    position: absolute;
    width: 1px;
    height: 1px;
    opacity: 0;
    overflow: hidden;
    pointer-events: none;
  }

  &__dropzone-content {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: $space-2;
  }

  &__dropzone-icon {
    color: $color-amber;
  }

  &__dropzone-hint {
    font-size: $fs-sm;
    color: $color-concrete;
    font-weight: $fw-medium;
    margin: 0;
  }

  &__dropzone-sub {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin: 0;
  }

  &__spin {
    color: $color-amber;
    animation: fu-spin 1s linear infinite;
  }

  &__progress {
    margin-top: $space-3;
    height: 4px;
    background: $gray-200;
    border-radius: $radius-pill;
    overflow: hidden;
  }

  &__progress-bar {
    height: 100%;
    background: linear-gradient(90deg, $color-amber, $color-amber-deep);
    transition: width 200ms ease-out;
  }

  &__list {
    margin-top: $space-3;
    display: flex;
    flex-direction: column;
    gap: $space-1;
  }

  &__file {
    display: flex;
    align-items: center;
    gap: $space-2;
    padding: $space-2;
    background: $gray-50;
    border: 1px solid $gray-200;
    border-radius: $radius-sm;
    transition: all $transition-fast;

    &--ok {
      background: $color-green-soft;
      border-color: $color-green;
    }

    &--error {
      background: $color-red-soft;
      border-color: $color-red;
    }

    &-icon {
      color: $color-stone;
      flex-shrink: 0;
    }

    &-body {
      flex: 1;
      min-width: 0;
    }

    &-name {
      font-size: $fs-sm;
      color: $color-concrete;
      font-weight: $fw-medium;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    &-meta {
      display: flex;
      align-items: center;
      gap: $space-2;
      font-size: $fs-xs;
      color: $color-text-secondary;
      margin-top: 2px;
    }

    &-tag {
      padding: 1px 6px;
      border-radius: $radius-xs;
      font-size: 11px;
      font-weight: $fw-medium;

      &--ok {
        background: $color-green;
        color: white;
      }

      &--err {
        background: $color-red;
        color: white;
      }
    }
  }

  &__error {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: $space-2;
    padding: $space-2;
    background: $color-red-soft;
    border-left: 3px solid $color-red;
    color: $color-red;
    font-size: $fs-xs;
    border-radius: $radius-sm;
  }
}

@keyframes fu-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
