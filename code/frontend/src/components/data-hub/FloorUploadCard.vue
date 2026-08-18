<script setup lang="ts">
// ============================================================================
// FloorUploadCard - 楼层信息上传卡片 (Step 11b)
// ----------------------------------------------------------------------------
// 跟 POINT 上传不一样, FLOOR 上传:
//   - 字段固定 (building_id/floor_number/floor_name/floor_type/area_sqm/is_rooftop)
//   - 不走 MappingWizard 4 步列映射, 上传后直接 commit
//   - commit 后直接落 core.floor, 不写 staging_reading
//
// 卡片流程: 下模板 -> 选文件 -> 上传 (POST /uploads/single?target_type=FLOOR)
//                -> 自动 commit (POST /uploads/{id}/commit) -> 显示结果
//
// demo 用户: 后端 commit 会拦 403, 前端先 disable 上传按钮 + 提示
// ============================================================================

import { ref, computed } from 'vue'
import { UploadCloud, FileDown, Loader2, CheckCircle2, AlertCircle, Building2 } from 'lucide-vue-next'
import { uploadApi } from '@/api/upload'
import { ApiError } from '@/api/client'
import { useAuthStore } from '@/stores/auth'

const emit = defineEmits<{
  committed: [payload: { session_id: string; batch_id: string }]
}>()

const auth = useAuthStore()

// 各阶段状态机: idle -> uploading -> committing -> done / error
type Phase = 'idle' | 'uploading' | 'committing' | 'done' | 'error'
const phase = ref<Phase>('idle')
const errorMsg = ref<string | null>(null)
const successMsg = ref<string | null>(null)

// 上传进度 0-100 (上传阶段才用, commit 阶段转圈圈)
const progress = ref(0)
const fileInputRef = ref<HTMLInputElement | null>(null)
const lastFilename = ref<string | null>(null)

const isDemo = computed(() => auth.isDemo)
const isBusy = computed(() => phase.value === 'uploading' || phase.value === 'committing')

async function downloadTemplate() {
  try {
    const { blob, filename } = await uploadApi.downloadTemplate('floors')
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '下载模板失败, 请重试'
    phase.value = 'error'
  }
}

function triggerPick() {
  if (isBusy.value || isDemo.value) return
  fileInputRef.value?.click()
}

async function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files || input.files.length === 0) return
  const file = input.files[0]
  input.value = ''  // 清空让用户能再次选同一个文件
  await uploadAndCommit(file)
}

async function uploadAndCommit(file: File) {
  phase.value = 'uploading'
  progress.value = 0
  errorMsg.value = null
  successMsg.value = null
  lastFilename.value = file.name

  try {
    // 1. 上传拿 session_id
    const session = await uploadFileWithProgress(file)
    // 2. 自动 commit (FLOOR commit 直接落 core.floor, 不需要 mapping/validate)
    phase.value = 'committing'
    const result = await uploadApi.commit(session.session_id)
    successMsg.value = `已导入 ${result.row_count_inserted} 层楼层数据`
    phase.value = 'done'
    // 通知父组件刷新 (session 列表 + batch 列表)
    emit('committed', { session_id: session.session_id, batch_id: result.batch_id })
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '导入失败, 请重试'
    phase.value = 'error'
  } finally {
    progress.value = 0
  }
}

// 上传单文件, 带 axios onUploadProgress 监听进度
// (复用 FileUpload.vue 的实现思路, 但 FLOOR 上传只走单文件模式)
async function uploadFileWithProgress(file: File) {
  const fd = new FormData()
  fd.append('file', file)
  const clientMod = await import('@/api/client')
  const client = clientMod.default
  const resp = await client.post(`/uploads/single?target_type=FLOOR`, fd, {
    headers: { 'Content-Type': 'multipart/form-data' },
    onUploadProgress: (ev: { loaded: number; total?: number }) => {
      if (ev.total) {
        progress.value = Math.round((ev.loaded / ev.total) * 100)
      }
    },
  })
  if (resp.data?.code !== 0) {
    throw new ApiError(resp.data?.code ?? -1, resp.data?.message ?? '上传失败')
  }
  return resp.data.data as { session_id: string; upload_file_id: string }
}
</script>

<template>
  <section class="fuc" :class="{ 'fuc--disabled': isDemo }">
    <header class="fuc__header">
      <div class="fuc__title-wrap">
        <h3 class="fuc__title">
          <Building2 :size="16" />
          楼层信息上传
        </h3>
        <p class="fuc__subtitle">
          楼层实体数据 (用途/面积/顶层标记). 上传后直接落库, 不需要列映射
        </p>
      </div>
    </header>

    <!-- demo 拦截提示 -->
    <div v-if="isDemo" class="fuc__demo-hint">
      <AlertCircle :size="14" />
      <span>demo 账号只读, 切换到管理员账号可上传楼层数据</span>
    </div>

    <!-- 操作区: 下载模板 + 上传按钮 -->
    <div class="fuc__actions">
      <button
        class="fuc__btn fuc__btn--ghost"
        :disabled="isBusy"
        @click="downloadTemplate"
      >
        <FileDown :size="14" />
        下载 floors.csv 模板
      </button>

      <button
        class="fuc__btn fuc__btn--primary"
        :disabled="isBusy || isDemo"
        @click="triggerPick"
      >
        <Loader2 v-if="phase === 'uploading'" :size="14" class="fuc__spin" />
        <UploadCloud v-else :size="14" />
        {{ phase === 'uploading' ? '上传中...' : phase === 'committing' ? '导入中...' : '选择文件上传' }}
      </button>

      <input
        ref="fileInputRef"
        type="file"
        class="fuc__input"
        accept=".csv,.xlsx"
        @change="onFileChange"
      >
    </div>

    <!-- 上传进度条 -->
    <div v-if="phase === 'uploading' && progress > 0" class="fuc__progress">
      <div class="fuc__progress-bar" :style="{ width: `${progress}%` }" />
    </div>

    <!-- commit 阶段提示 -->
    <div v-if="phase === 'committing'" class="fuc__committing">
      <Loader2 :size="12" class="fuc__spin" />
      <span>正在写入楼层表...</span>
    </div>

    <!-- 成功态 -->
    <div v-if="phase === 'done' && successMsg" class="fuc__success">
      <CheckCircle2 :size="14" />
      <span>{{ successMsg }}</span>
      <span v-if="lastFilename" class="fuc__filename">{{ lastFilename }}</span>
    </div>

    <!-- 错误态 -->
    <div v-if="phase === 'error' && errorMsg" class="fuc__error">
      <AlertCircle :size="14" />
      <span>{{ errorMsg }}</span>
    </div>
  </section>
</template>

<style scoped lang="scss">
.fuc {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-4;
  transition: box-shadow $transition-base;

  &:hover:not(.fuc--disabled) {
    box-shadow: $shadow-sm;
  }

  &--disabled {
    opacity: 0.85;
    background: $gray-50;
  }

  &__header {
    margin-bottom: $space-3;
  }

  &__title-wrap {
    flex: 1;
  }

  &__title {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: $fs-md;
    font-weight: $fw-semibold;
    color: $color-concrete;
    margin: 0;

    svg {
      color: $color-amber;
    }
  }

  &__subtitle {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin: 2px 0 0;
    line-height: $lh-snug;
  }

  &__demo-hint {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-bottom: $space-3;
    padding: $space-2 $space-3;
    background: $color-amber-soft;
    border-left: 3px solid $color-amber;
    color: $color-amber-deep;
    font-size: $fs-xs;
    border-radius: $radius-sm;
  }

  &__actions {
    display: flex;
    gap: $space-2;
    align-items: center;
    flex-wrap: wrap;
  }

  &__btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 8px 14px;
    border-radius: $radius-sm;
    font-size: $fs-sm;
    font-weight: $fw-medium;
    cursor: pointer;
    transition: all $transition-fast;
    border: 1px solid transparent;

    &--ghost {
      background: $color-card;
      border-color: $color-line;
      color: $color-stone;

      &:hover:not(:disabled) {
        border-color: $color-amber;
        color: $color-amber;
      }
    }

    &--primary {
      background: $color-amber;
      color: white;

      &:hover:not(:disabled) {
        background: $color-amber-deep;
      }
    }

    &:disabled {
      opacity: 0.5;
      cursor: not-allowed;
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

  &__committing {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: $space-3;
    color: $color-amber-deep;
    font-size: $fs-xs;
  }

  &__success {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: $space-3;
    padding: $space-2 $space-3;
    background: $color-green-soft;
    border-left: 3px solid $color-green;
    color: $color-green;
    font-size: $fs-xs;
    border-radius: $radius-sm;
  }

  &__filename {
    margin-left: auto;
    color: $color-text-secondary;
    font-family: $font-mono;
    font-size: 11px;
  }

  &__error {
    display: flex;
    align-items: center;
    gap: 6px;
    margin-top: $space-3;
    padding: $space-2 $space-3;
    background: $color-red-soft;
    border-left: 3px solid $color-red;
    color: $color-red;
    font-size: $fs-xs;
    border-radius: $radius-sm;
  }

  &__spin {
    animation: fuc-spin 1s linear infinite;
  }
}

@keyframes fuc-spin {
  to { transform: rotate(360deg); }
}
</style>
