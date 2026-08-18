<script setup lang="ts">
// ============================================================================
// SinglePhotoUpload - 单图重建上传
// ----------------------------------------------------------------------------
// 客户上传一张建筑正脸照片 + 填尺寸 -> 调 POST /buildings/{id}/reconstruction/single-photo
// 提交后异步 worker 跑 TripoSplat 推理, 生成 .ply + preview.png.
//
// 流程:
//   1. 选楼 (从顶栏 siteId 拉建筑列表)
//   2. 拖拽或点击上传照片 (POST /uploads/photo 拿 photo_id)
//   3. 填 length/width/height/floors_count (用于 splat 尺度校准)
//   4. 提交 (POST /buildings/{id}/reconstruction/single-photo 拿 job_id)
//   5. 父组件把 job_id 喂给 JobStatusList, 实时轮询 job 状态
//
// emit:
//   - jobSubmitted: { job_id, building_id } -- 提交成功, 父组件触发列表刷新
// ============================================================================

import { ref, computed, watch, onMounted, onBeforeUnmount } from 'vue'
import { Button, Select, InputNumber, Form, FormItem, message, Alert } from 'ant-design-vue'
import {
  ImageIcon, UploadCloud, Loader2, Check, RefreshCw,
  X, FileImage, AlertCircle,
} from 'lucide-vue-next'
import { useContextStore } from '@/stores/context'
import { queryApi } from '@/api/query'
import { visualApi, type SinglePhotoRequest } from '@/api/visual'
import { uploadApi, type PhotoUploadResult } from '@/api/upload'
import { ApiError } from '@/api/client'

const emit = defineEmits<{
  jobSubmitted: [payload: { job_id: string; building_id: string }]
}>()

const ctxStore = useContextStore()

// 楼选择 (用 undefined 而非 null: AntD Select v-model:value 的 SelectValue
// 类型不接受 null, 用 undefined 兜底)
const selectedBuildingId = ref<string | undefined>(undefined)
const buildings = ref<Array<{
  building_id: string
  building_code: string
  display_name: string
  sqm: number | null
}>>([])
const buildingsLoading = ref(false)

// 照片上传
const photoFile = ref<File | null>(null)
const photoPreview = ref<string | null>(null)
const photoUploading = ref(false)
const uploadedPhoto = ref<PhotoUploadResult | null>(null)

// 表单
const form = ref({
  length_m: 0,
  width_m: 0,
  height_m: 0,
  floors_count: 1,
  position_x: undefined as number | undefined,
  position_y: undefined as number | undefined,
})

const submitting = ref(false)
const errorMsg = ref<string | null>(null)
const dragOver = ref(false)
const fileInputRef = ref<HTMLInputElement | null>(null)

const hasSite = computed(() => !!ctxStore.siteId)
const hasBuilding = computed(() => !!selectedBuildingId.value)
const hasPhoto = computed(() => !!uploadedPhoto.value)
// 显式求值每个条件, 不用 && 短路 (memory: Vue computed && 短路会破坏 reactive 依赖追踪)
const formValid = computed(() =>
  form.value.length_m > 0 &&
  form.value.width_m > 0 &&
  form.value.height_m > 0 &&
  form.value.floors_count >= 1,
)
const canSubmit = computed(() => hasBuilding.value && hasPhoto.value && formValid.value)

// 按钮下方灰色提示行: 告诉用户为什么不能点
const submitHint = computed<string | null>(() => {
  if (canSubmit.value) return null
  if (!hasSite.value) return '请先在顶栏选择园区'
  if (!hasBuilding.value) return '请选择要建模的建筑'
  if (!hasPhoto.value) return '请上传建筑正脸照片'
  if (!formValid.value) return '请填完尺寸信息 (长 / 宽 / 高 / 楼层数)'
  return null
})

const buildingOptions = computed(() =>
  buildings.value.map((b) => ({
    value: b.building_id,
    label: `${b.display_name} (${b.building_code})`,
  })),
)

watch(
  () => ctxStore.siteId,
  async (id) => {
    if (!id) {
      buildings.value = []
      selectedBuildingId.value = undefined
      return
    }
    await loadBuildings(id)
  },
)

async function loadBuildings(siteId: string) {
  buildingsLoading.value = true
  try {
    const resp = await queryApi.listSiteBuildings(siteId)
    buildings.value = resp.buildings
    if (buildings.value.length > 0 && !selectedBuildingId.value) {
      selectedBuildingId.value = buildings.value[0].building_id
    }
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '拉取建筑列表失败'
  } finally {
    buildingsLoading.value = false
  }
}

// 拖拽 + 点击上传照片
function triggerPick() {
  if (photoUploading.value) return
  fileInputRef.value?.click()
}

function onFileChange(e: Event) {
  const input = e.target as HTMLInputElement
  if (!input.files || input.files.length === 0) return
  handleFile(input.files[0])
  input.value = ''
}

function onDragEnter(e: DragEvent) {
  e.preventDefault()
  if (!photoUploading.value) dragOver.value = true
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
  if (photoUploading.value) return
  const file = e.dataTransfer?.files[0]
  if (file) handleFile(file)
}

async function handleFile(file: File) {
  // 校验后缀 + mime
  const allowedSuffix = ['.jpg', '.jpeg', '.png', '.webp']
  const suffix = '.' + file.name.split('.').pop()?.toLowerCase()
  if (!allowedSuffix.includes(suffix)) {
    message.error(`仅支持 ${allowedSuffix.join(' / ')} 格式`)
    return
  }
  if (file.size > 10 * 1024 * 1024) {
    message.error('照片大小不能超过 10MB')
    return
  }

  photoFile.value = file
  // 生成本地预览 URL (不依赖后端)
  if (photoPreview.value) URL.revokeObjectURL(photoPreview.value)
  photoPreview.value = URL.createObjectURL(file)

  photoUploading.value = true
  errorMsg.value = null
  try {
    const result = await uploadApi.uploadPhoto(file)
    uploadedPhoto.value = result
    message.success('照片上传成功')
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '照片上传失败'
    clearPhoto()
  } finally {
    photoUploading.value = false
  }
}

function clearPhoto() {
  if (photoPreview.value) URL.revokeObjectURL(photoPreview.value)
  photoFile.value = null
  photoPreview.value = null
  uploadedPhoto.value = null
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`
}

async function submit() {
  if (!canSubmit.value || !uploadedPhoto.value || !selectedBuildingId.value) return
  submitting.value = true
  errorMsg.value = null
  try {
    const payload: SinglePhotoRequest = {
      photo_upload_id: uploadedPhoto.value.photo_id,
      length_m: form.value.length_m,
      width_m: form.value.width_m,
      height_m: form.value.height_m,
      floors_count: form.value.floors_count,
      position_x: form.value.position_x,
      position_y: form.value.position_y,
    }
    const result = await visualApi.submitSinglePhotoJob(selectedBuildingId.value, payload)
    message.success('重建任务已提交, 等待 worker 消费')
    emit('jobSubmitted', { job_id: result.job_id, building_id: selectedBuildingId.value })
    // 重置照片 + 表单, 让用户能继续提交下一个 job
    clearPhoto()
    form.value = {
      length_m: 0,
      width_m: 0,
      height_m: 0,
      floors_count: 1,
      position_x: undefined,
      position_y: undefined,
    }
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '提交失败, 请重试'
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  if (ctxStore.siteId) await loadBuildings(ctxStore.siteId)
})

onBeforeUnmount(() => {
  if (photoPreview.value) URL.revokeObjectURL(photoPreview.value)
})
</script>

<template>
  <section class="spu">
    <header class="spu__header">
      <div class="spu__title-wrap">
        <h3 class="spu__title">单图 3D 重建</h3>
        <p class="spu__subtitle">上传建筑正脸照片 + 填尺寸, 后台异步生成 .ply 模型</p>
      </div>
      <ImageIcon :size="20" class="spu__header-icon" />
    </header>

    <Alert
      v-if="!hasSite"
      type="info"
      show-icon
      message="请先在顶栏选择园区"
      class="spu__alert"
    />

    <Alert
      v-else-if="buildings.length === 0 && !buildingsLoading"
      type="info"
      show-icon
      message="该园区暂无建筑, 请先上传建筑数据"
      class="spu__alert"
    />

    <div v-else class="spu__content">
      <!-- 楼选择 -->
      <div class="spu__field">
        <label class="spu__label">选择建筑</label>
        <Select
          v-model:value="selectedBuildingId"
          :options="buildingOptions"
          :loading="buildingsLoading"
          placeholder="选择要建模的建筑"
          show-search
          class="spu__select"
        />
      </div>

      <!-- 照片上传区 -->
      <div class="spu__field">
        <label class="spu__label">建筑正脸照片</label>

        <div
          v-if="!hasPhoto"
          class="spu__dropzone"
          :class="{ 'spu__dropzone--over': dragOver, 'spu__dropzone--uploading': photoUploading }"
          @dragenter="onDragEnter"
          @dragover="onDragOver"
          @dragleave="onDragLeave"
          @drop="onDrop"
          @click="triggerPick"
        >
          <input
            ref="fileInputRef"
            type="file"
            class="spu__input"
            accept=".jpg,.jpeg,.png,.webp"
            @change="onFileChange"
          >
          <Loader2 v-if="photoUploading" :size="28" class="spu__spin" />
          <UploadCloud v-else :size="28" class="spu__dropzone-icon" />
          <p class="spu__dropzone-hint">
            {{ photoUploading ? '上传中...' : '拖拽照片到此处, 或点击选择' }}
          </p>
          <p class="spu__dropzone-sub">支持 .jpg / .png / .webp, 最大 10MB</p>
        </div>

        <!-- 已上传照片预览 -->
        <div v-else class="spu__preview">
          <img
            v-if="photoPreview"
            :src="photoPreview"
            alt="上传的照片"
            class="spu__preview-img"
          >
          <div class="spu__preview-info">
            <FileImage :size="14" />
            <div class="spu__preview-meta">
              <div class="spu__preview-name">{{ photoFile?.name }}</div>
              <div class="spu__preview-size">
                {{ photoFile ? formatSize(photoFile.size) : '' }}
                <span class="spu__preview-tag">已上传</span>
              </div>
            </div>
          </div>
          <Button size="small" danger @click="clearPhoto">
            <template #icon><X :size="12" /></template>
            删除重选
          </Button>
        </div>
      </div>

      <!-- 尺寸表单 -->
      <Form layout="vertical" class="spu__form">
        <div class="spu__form-row">
          <FormItem label="长度 (米)" required>
            <InputNumber
              v-model:value="form.length_m"
              :min="0.1"
              :step="0.1"
              :precision="2"
              class="spu__input-number"
              placeholder="如 30.0"
            />
          </FormItem>

          <FormItem label="宽度 (米)" required>
            <InputNumber
              v-model:value="form.width_m"
              :min="0.1"
              :step="0.1"
              :precision="2"
              class="spu__input-number"
              placeholder="如 20.0"
            />
          </FormItem>

          <FormItem label="高度 (米)" required>
            <InputNumber
              v-model:value="form.height_m"
              :min="0.1"
              :step="0.1"
              :precision="2"
              class="spu__input-number"
              placeholder="如 12.0"
            />
          </FormItem>

          <FormItem label="楼层数" required>
            <InputNumber
              v-model:value="form.floors_count"
              :min="1"
              :step="1"
              :precision="0"
              class="spu__input-number"
              placeholder="如 3"
            />
          </FormItem>
        </div>
      </Form>

      <!-- 提交按钮 -->
      <div class="spu__actions">
        <!-- 提示行放按钮上方: spu 容器底部可能被下方 JobStatusList 盖住,
             放按钮下方会看不见, 放上方保证始终可见 -->
        <p v-if="submitHint" class="spu__hint">
          <AlertCircle :size="12" />
          {{ submitHint }}
        </p>
        <p v-else class="spu__tip">
          提交后 worker 异步推理约 4 分钟, 完成后下方列表显示状态
        </p>
        <Button
          type="primary"
          size="large"
          :loading="submitting"
          :disabled="!canSubmit"
          @click="submit"
        >
          <template #icon>
            <Loader2 v-if="submitting" :size="14" class="spu__spin" />
            <Check v-else :size="14" />
          </template>
          提交重建任务
        </Button>
      </div>

      <p v-if="errorMsg" class="spu__error">
        <RefreshCw :size="12" />
        {{ errorMsg }}
      </p>
    </div>
  </section>
</template>

<style scoped lang="scss">
.spu {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-3;
  display: flex;
  flex-direction: column;
  gap: $space-2;
  transition: box-shadow $transition-base;

  &:hover {
    box-shadow: $shadow-sm;
  }

  &__header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: $space-2;
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

  &__header-icon {
    color: $color-amber;
    flex-shrink: 0;
  }

  &__alert {
    margin: 0;
  }

  &__content {
    display: flex;
    flex-direction: column;
    gap: $space-2;
  }

  &__field {
    display: flex;
    flex-direction: column;
    gap: 4px;
  }

  &__label {
    font-size: $fs-xs;
    font-weight: $fw-medium;
    color: $color-concrete;
  }

  &__select {
    width: 100%;
  }

  &__dropzone {
    position: relative;
    border: 2px dashed $color-line;
    border-radius: $radius-md;
    padding: $space-3 $space-4;
    text-align: center;
    cursor: pointer;
    background: $gray-50;
    transition: all $transition-base;

    &:hover:not(.spu__dropzone--uploading) {
      border-color: $color-amber;
      background: $color-amber-soft;
    }

    &--over {
      border-color: $color-amber;
      background: $color-amber-soft;
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
    pointer-events: none;
  }

  &__dropzone-icon {
    color: $color-amber;
  }

  &__dropzone-hint {
    font-size: $fs-sm;
    color: $color-concrete;
    font-weight: $fw-medium;
    margin: $space-2 0 0;
  }

  &__dropzone-sub {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin: 2px 0 0;
  }

  &__spin {
    color: $color-amber;
    animation: spu-spin 1s linear infinite;
  }

  &__preview {
    display: flex;
    align-items: center;
    gap: $space-3;
    padding: $space-2;
    background: $color-green-soft;
    border: 1px solid $color-green;
    border-radius: $radius-sm;
  }

  &__preview-img {
    width: 80px;
    height: 80px;
    object-fit: cover;
    border-radius: $radius-sm;
    flex-shrink: 0;
    border: 1px solid $color-line;
  }

  &__preview-info {
    display: flex;
    align-items: center;
    gap: 6px;
    flex: 1;
    min-width: 0;
    color: $color-stone;
  }

  &__preview-meta {
    flex: 1;
    min-width: 0;
  }

  &__preview-name {
    font-size: $fs-sm;
    color: $color-concrete;
    font-weight: $fw-medium;
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }

  &__preview-size {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-top: 2px;
  }

  &__preview-tag {
    padding: 1px 6px;
    background: $color-green;
    color: white;
    border-radius: $radius-xs;
    font-size: 11px;
    font-weight: $fw-medium;
  }

  &__form {
    display: flex;
    flex-direction: column;
    gap: $space-2;
  }

  &__form-row {
    display: grid;
    grid-template-columns: repeat(4, 1fr);
    gap: $space-2;

    @media (max-width: 720px) {
      grid-template-columns: repeat(2, 1fr);
    }
  }

  &__input-number {
    width: 100%;
  }

  &__actions {
    display: flex;
    flex-direction: column;
    gap: $space-2;
    padding-top: $space-2;
    border-top: 1px solid $gray-100;
  }

  &__tip {
    font-size: 11px;
    color: $color-text-secondary;
    margin: 0;
    line-height: $lh-snug;
  }

  &__hint {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 11px;
    color: $color-amber-deep;
    background: $color-amber-soft;
    border-radius: $radius-xs;
    padding: 4px $space-2;
    margin: 0;
    line-height: $lh-snug;
  }

  &__error {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: $space-2;
    background: $color-red-soft;
    border-left: 3px solid $color-red;
    color: $color-red;
    font-size: $fs-xs;
    border-radius: $radius-sm;
    margin: 0;
  }
}

@keyframes spu-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
