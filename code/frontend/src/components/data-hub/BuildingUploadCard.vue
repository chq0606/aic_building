<script setup lang="ts">
// ============================================================================
// BuildingUploadCard - 建筑基础信息上传卡片
// ----------------------------------------------------------------------------
// 跟 FLOOR 上传一样走"直接 commit"路径 (不走 MappingWizard):
//   - 字段固定 (building_id/building_name/site_code/primary_use/sqm/floors_count)
//   - 上传后直接 commit, 落 core.building (带 sqm/用途/层数, 3D 页能估出真实尺寸)
//
// 卡片流程: 下模板 -> 选文件 -> 上传 (POST /uploads/single?target_type=BUILDING)
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

type Phase = 'idle' | 'uploading' | 'committing' | 'done' | 'error'
const phase = ref<Phase>('idle')
const errorMsg = ref<string | null>(null)
const successMsg = ref<string | null>(null)

const progress = ref(0)
const fileInputRef = ref<HTMLInputElement | null>(null)
const lastFilename = ref<string | null>(null)

const isDemo = computed(() => auth.isDemo)
const isBusy = computed(() => phase.value === 'uploading' || phase.value === 'committing')

async function downloadTemplate() {
  try {
    const { blob, filename } = await uploadApi.downloadTemplate('buildings')
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
  input.value = ''
  await uploadAndCommit(file)
}

async function uploadAndCommit(file: File) {
  phase.value = 'uploading'
  progress.value = 0
  errorMsg.value = null
  successMsg.value = null
  lastFilename.value = file.name

  try {
    const session = await uploadFileWithProgress(file)
    phase.value = 'committing'
    const result = await uploadApi.commit(session.session_id)
    successMsg.value = `已导入 ${result.row_count_inserted} 栋楼`
    phase.value = 'done'
    emit('committed', { session_id: session.session_id, batch_id: result.batch_id })
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '导入失败, 请重试'
    phase.value = 'error'
  } finally {
    progress.value = 0
  }
}

async function uploadFileWithProgress(file: File) {
  const fd = new FormData()
  fd.append('file', file)
  const clientMod = await import('@/api/client')
  const client = clientMod.default
  const resp = await client.post(`/uploads/single?target_type=BUILDING`, fd, {
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
  <section class="buc" :class="{ 'buc--disabled': isDemo }">
    <header class="buc__header">
      <div class="buc__title-wrap">
        <h3 class="buc__title">
          <Building2 :size="16" />
          建筑信息上传
        </h3>
        <p class="buc__subtitle">
          楼栋基础信息 (名称/用途/面积/层数). 上传后 3D 页能按面积估出真实尺寸
        </p>
      </div>
    </header>

    <div v-if="isDemo" class="buc__demo-hint">
      <AlertCircle :size="14" />
      <span>demo 账号只读, 切换到管理员账号可上传建筑信息</span>
    </div>

    <div class="buc__actions">
      <button
        class="buc__btn buc__btn--ghost"
        :disabled="isBusy"
        @click="downloadTemplate"
      >
        <FileDown :size="14" />
        下载 buildings.csv 模板
      </button>

      <button
        class="buc__btn buc__btn--primary"
        :disabled="isBusy || isDemo"
        @click="triggerPick"
      >
        <Loader2 v-if="phase === 'uploading'" :size="14" class="buc__spin" />
        <UploadCloud v-else :size="14" />
        {{ phase === 'uploading' ? '上传中...' : phase === 'committing' ? '导入中...' : '选择文件上传' }}
      </button>

      <input
        ref="fileInputRef"
        type="file"
        class="buc__input"
        accept=".csv,.xlsx"
        @change="onFileChange"
      >
    </div>

    <div v-if="phase === 'uploading' && progress > 0" class="buc__progress">
      <div class="buc__progress-bar" :style="{ width: `${progress}%` }" />
    </div>

    <div v-if="phase === 'committing'" class="buc__committing">
      <Loader2 :size="12" class="buc__spin" />
      <span>正在写入建筑表...</span>
    </div>

    <div v-if="phase === 'done' && successMsg" class="buc__success">
      <CheckCircle2 :size="14" />
      <span>{{ successMsg }}</span>
      <span v-if="lastFilename" class="buc__filename">{{ lastFilename }}</span>
    </div>

    <div v-if="phase === 'error' && errorMsg" class="buc__error">
      <AlertCircle :size="14" />
      <span>{{ errorMsg }}</span>
    </div>
  </section>
</template>

<style scoped lang="scss">
.buc {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-4;
  transition: box-shadow $transition-base;

  &:hover:not(.buc--disabled) {
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
    animation: buc-spin 1s linear infinite;
  }
}

@keyframes buc-spin {
  to { transform: rotate(360deg); }
}
</style>
