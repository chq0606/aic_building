<script setup lang="ts">
// ============================================================================
// BlockModelForm - 体块参数表单
// ----------------------------------------------------------------------------
// 给一栋楼提交体块尺寸 (length/width/height/floors_count + position_x/y), 调
// POST /buildings/{id}/visual-models/block 写入 core.building_visual_model 表.
// 提交后 Park.vue 调 scene API 能拿到新尺寸 + color_metric.
//
// 楼选择: 从顶栏 context.siteId 拉建筑列表, 用 a-select 切换. 切完楼后
// 自动拉 GET /buildings/{id}/visual-model 回填已有参数 (没设过则空).
//
// 自动估算提示: 如果用户不填某些字段, 后端会用 sqm + floors_count 估算.
// 但前端要求全填, 给"自动估算"按钮一键填默认值 (从 query API 拿 sqm + floors).
// ============================================================================

import { ref, computed, watch, onMounted } from 'vue'
import { Button, Select, InputNumber, Form, FormItem, message, Tag, Alert } from 'ant-design-vue'
import { Box, Loader2, Check, RefreshCw, Sparkles, Info } from 'lucide-vue-next'
import { useContextStore } from '@/stores/context'
import { queryApi } from '@/api/query'
import { visualApi, type BlockModelRequest, type VisualModel } from '@/api/visual'
import { ApiError } from '@/api/client'

const emit = defineEmits<{
  submitted: [payload: { building_id: string; model_id: string }]
}>()

const ctxStore = useContextStore()

// 当前选中的 building_id (用 undefined 而非 null, AntD Select v-model:value 的
// SelectValue 类型不接受 null)
const selectedBuildingId = ref<string | undefined>(undefined)
// 园区下所有建筑列表
const buildings = ref<Array<{
  building_id: string
  building_code: string
  display_name: string
  primary_use: string
  sub_use: string | null
  sqm: number | null
  eui_kwh_per_m2: number | null
}>>([])

const buildingsLoading = ref(false)
const modelLoading = ref(false)
const submitting = ref(false)
const errorMsg = ref<string | null>(null)

// 表单数据 (内部用 undefined, 提交时把 undefined 转成 null 给 BlockModelRequest)
type BlockFormState = {
  length_m: number
  width_m: number
  height_m: number
  floors_count: number
  position_x: number | undefined
  position_y: number | undefined
}
const form = ref<BlockFormState>({
  length_m: 0,
  width_m: 0,
  height_m: 0,
  floors_count: 1,
  position_x: undefined,
  position_y: undefined,
})

// 建筑用途预设: 决定 3D 页程序化表皮 + 屋顶类型 (跟 buildingAppearance.ts 的
// 匹配规则对齐)。value 是 key, label 是给用户看的中文, primary/sub 落 core.building。
// '' = 不设置 (保持现状 / 默认玻璃幕墙)。
const USE_PRESETS: Array<{
  key: string
  label: string
  primary_use: string
  sub_use: string
  match: RegExp
}> = [
  { key: 'academic', label: '教学楼', primary_use: 'Education', sub_use: 'Academic', match: /academic|teaching/i },
  { key: 'science', label: '科研实验楼', primary_use: 'Science', sub_use: 'Laboratory', match: /science|laboratory|research/i },
  { key: 'sports', label: '体育馆 / 礼堂', primary_use: 'Assembly', sub_use: 'Gymnasium', match: /sports|assembly|gym/i },
  { key: 'student', label: '学生中心 / 宿舍', primary_use: 'Education', sub_use: 'Student Center', match: /student|dormitory/i },
  { key: 'library', label: '图书馆 / 公共服务', primary_use: 'Public Services', sub_use: 'Library', match: /library|public|service/i },
  { key: 'retail', label: '零售商铺', primary_use: 'Commercial', sub_use: 'Retail', match: /retail|shop|store/i },
  { key: 'residential', label: '居民楼', primary_use: 'Residential', sub_use: 'Apartment', match: /residential|apartment|housing/i },
  { key: 'commercial', label: '商业楼宇', primary_use: 'Commercial', sub_use: 'Office', match: /commercial|business|office/i },
]

// 当前选中的用途 key ('' = 不设置)
const useKey = ref<string>('')

// 已存在的 visual_model (用于回填 + 显示当前状态)
const existingModel = ref<VisualModel | null>(null)

// 楼下拉选项
const buildingOptions = computed(() =>
  buildings.value.map((b) => ({
    value: b.building_id,
    label: `${b.display_name} (${b.building_code})`,
  })),
)

// 用途下拉选项
const useOptions = computed(() =>
  USE_PRESETS.map((p) => ({ value: p.key, label: p.label })),
)

const hasSite = computed(() => !!ctxStore.siteId)
const hasBuilding = computed(() => !!selectedBuildingId.value)
// 表单全填才允许提交
const formValid = computed(() =>
  form.value.length_m > 0 &&
  form.value.width_m > 0 &&
  form.value.height_m > 0 &&
  form.value.floors_count >= 1,
)

// watch siteId 变化重新拉建筑列表
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
  { immediate: false },
)

// watch 选楼变化 -> 拉已有 visual_model 回填 + 同步用途下拉
watch(
  selectedBuildingId,
  async (id) => {
    if (!id) {
      existingModel.value = null
      useKey.value = ''
      return
    }
    syncUseKeyFromBuilding()
    await loadExistingModel(id)
  },
)

// 按当前楼的 sub_use / primary_use 回显用途下拉 (优先 sub_use, 更准)
function syncUseKeyFromBuilding() {
  const b = buildings.value.find((x) => x.building_id === selectedBuildingId.value)
  if (!b) {
    useKey.value = ''
    return
  }
  const sub = b.sub_use ?? ''
  const pri = b.primary_use ?? ''
  const preset = USE_PRESETS.find((p) => p.match.test(sub)) ?? USE_PRESETS.find((p) => p.match.test(pri))
  useKey.value = preset ? preset.key : ''
}

async function loadBuildings(siteId: string) {
  buildingsLoading.value = true
  errorMsg.value = null
  try {
    const resp = await queryApi.listSiteBuildings(siteId)
    buildings.value = resp.buildings
    // 自动选第一栋, 用户切完园区不用再手动选
    if (buildings.value.length > 0 && !selectedBuildingId.value) {
      selectedBuildingId.value = buildings.value[0].building_id
    }
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '拉取建筑列表失败'
  } finally {
    buildingsLoading.value = false
  }
}

async function loadExistingModel(buildingId: string) {
  modelLoading.value = true
  try {
    const model = await visualApi.getBuildingVisualModel(buildingId)
    existingModel.value = model
    // 后端返 null = 该楼没设过 visual_model, 清空表单让用户填
    if (!model) {
      resetForm()
    } else if (model.model_mode === 'BLOCK') {
      // 回填表单 (BLOCK 模式优先, splat 模式不覆盖)
      form.value = {
        length_m: model.dimensions.length_m ?? 0,
        width_m: model.dimensions.width_m ?? 0,
        height_m: model.dimensions.height_m ?? 0,
        floors_count: model.dimensions.floors_count ?? 1,
        // 后端返 number | null, 前端用 undefined 兜底 (AntD InputNumber 不收 null)
        position_x: model.position.x ?? undefined,
        position_y: model.position.y ?? undefined,
      }
    }
  } catch (e) {
    // 其他错误静默, 控制台 warn
    console.warn('load existing model failed:', e)
  } finally {
    modelLoading.value = false
  }
}

function resetForm() {
  form.value = {
    length_m: 0,
    width_m: 0,
    height_m: 0,
    floors_count: 1,
    position_x: undefined,
    position_y: undefined,
  }
}

// 自动估算: 用 building.sqm + floors_count 估算尺寸 (跟后端 _estimate_dimensions 算法一致)
function autoEstimate() {
  const b = buildings.value.find((x) => x.building_id === selectedBuildingId.value)
  if (!b || !b.sqm) {
    message.warning('该楼没有 sqm 数据, 无法自动估算')
    return
  }
  // 跟后端 visual_model_service._estimate_dimensions 算法一致:
  // height = floors * 3.5m, width = sqrt(sqm / 1.5), length = 1.5 * width
  const floors = b.primary_use ? Math.ceil(b.sqm / 1500) : 3  // 估楼层
  const height = floors * 3.5
  const width = Math.sqrt(b.sqm / 1.5)
  const length = 1.5 * width
  form.value.length_m = Math.round(length * 100) / 100
  form.value.width_m = Math.round(width * 100) / 100
  form.value.height_m = Math.round(height * 100) / 100
  form.value.floors_count = floors
  message.success('已用 sqm 估算尺寸, 请校对后提交')
}

async function submit() {
  if (!selectedBuildingId.value || !formValid.value) return
  submitting.value = true
  errorMsg.value = null
  try {
    // 把 undefined 转成 null 给后端 (BlockModelRequest.position_x?: number | null)
    const payload: BlockModelRequest = {
      length_m: form.value.length_m,
      width_m: form.value.width_m,
      height_m: form.value.height_m,
      floors_count: form.value.floors_count,
      position_x: form.value.position_x ?? null,
      position_y: form.value.position_y ?? null,
    }
    const result = await visualApi.submitBlockModel(selectedBuildingId.value, payload)
    // 选了用途就一并 PATCH 到 core.building (决定 3D 页表皮/屋顶)
    if (useKey.value) {
      const preset = USE_PRESETS.find((p) => p.key === useKey.value)
      if (preset) {
        await visualApi.updateBuildingUse(selectedBuildingId.value, {
          primary_use: preset.primary_use,
          sub_use: preset.sub_use,
        })
      }
    }
    message.success('体块参数已保存')
    emit('submitted', { building_id: selectedBuildingId.value, model_id: result.model_id })
    // 重新拉 model 显示最新状态
    await loadExistingModel(selectedBuildingId.value)
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '提交失败, 请重试'
  } finally {
    submitting.value = false
  }
}

onMounted(async () => {
  if (ctxStore.siteId) await loadBuildings(ctxStore.siteId)
})
</script>

<template>
  <section class="bmf">
    <header class="bmf__header">
      <div class="bmf__title-wrap">
        <h3 class="bmf__title">体块参数</h3>
        <p class="bmf__subtitle">为建筑提交体块尺寸, 提交后在园区 3D 页可见</p>
      </div>
      <Box :size="20" class="bmf__header-icon" />
    </header>

    <Alert
      v-if="!hasSite"
      type="info"
      show-icon
      message="请先在顶栏选择园区"
      class="bmf__alert"
    />

    <Alert
      v-else-if="buildings.length === 0 && !buildingsLoading"
      type="info"
      show-icon
      message="该园区暂无建筑, 请先上传建筑数据"
      class="bmf__alert"
    />

    <div v-else class="bmf__content">
      <!-- 楼选择 -->
      <div class="bmf__field">
        <label class="bmf__label">选择建筑</label>
        <Select
          v-model:value="selectedBuildingId"
          :options="buildingOptions"
          :loading="buildingsLoading"
          placeholder="选择要建模的建筑"
          show-search
          class="bmf__select"
        />
      </div>

      <!-- 建筑用途: 决定 3D 页外观 -->
      <div class="bmf__field">
        <label class="bmf__label">建筑用途</label>
        <Select
          v-model:value="useKey"
          :options="useOptions"
          placeholder="选择用途 (决定 3D 外观)"
          allow-clear
          show-search
          :disabled="!hasBuilding"
          class="bmf__select"
        />
        <p class="bmf__use-hint">
          决定园区 3D 页的建筑外观 (表皮 + 屋顶). 留空 = 默认玻璃幕墙
        </p>
      </div>

      <!-- 已有 model 状态提示 -->
      <div v-if="existingModel" class="bmf__existing">
        <Tag :color="existingModel.model_mode === 'BLOCK' ? 'green' : 'gold'">
          {{ existingModel.model_mode === 'BLOCK' ? '已有体块模型' : '已有 splat 模型' }}
        </Tag>
        <span class="bmf__existing-id">model: {{ existingModel.id.slice(0, 8) }}</span>
      </div>

      <!-- 表单字段 -->
      <Form layout="vertical" class="bmf__form">
        <div class="bmf__form-row">
          <FormItem label="长度 (米)" required>
            <InputNumber
              v-model:value="form.length_m"
              :min="0.1"
              :step="0.1"
              :precision="2"
              class="bmf__input-number"
              placeholder="如 120.5"
            />
          </FormItem>

          <FormItem label="宽度 (米)" required>
            <InputNumber
              v-model:value="form.width_m"
              :min="0.1"
              :step="0.1"
              :precision="2"
              class="bmf__input-number"
              placeholder="如 80.0"
            />
          </FormItem>

          <FormItem label="高度 (米)" required>
            <InputNumber
              v-model:value="form.height_m"
              :min="0.1"
              :step="0.1"
              :precision="2"
              class="bmf__input-number"
              placeholder="如 7.0"
            />
          </FormItem>

          <FormItem label="楼层数" required>
            <InputNumber
              v-model:value="form.floors_count"
              :min="1"
              :step="1"
              :precision="0"
              class="bmf__input-number"
              placeholder="如 2"
            />
          </FormItem>
        </div>

        <div class="bmf__form-row">
          <FormItem label="园区 X 坐标 (米, 可选)">
            <InputNumber
              v-model:value="form.position_x"
              :step="1"
              :precision="2"
              class="bmf__input-number"
              placeholder="留空由前端布局兜底"
            />
          </FormItem>

          <FormItem label="园区 Y 坐标 (米, 可选)">
            <InputNumber
              v-model:value="form.position_y"
              :step="1"
              :precision="2"
              class="bmf__input-number"
              placeholder="留空由前端布局兜底"
            />
          </FormItem>

          <div class="bmf__hint-wrap">
            <Info :size="12" class="bmf__hint-icon" />
            <p class="bmf__hint">
              坐标用于园区 3D 页定位建筑. 留空时前端用网格自动布局.
              坐标系是米单位本地 ENU, 原点在园区中心.
            </p>
          </div>
        </div>
      </Form>

      <!-- 自动估算 + 提交按钮 -->
      <div class="bmf__actions">
        <Button :disabled="!hasBuilding" @click="autoEstimate">
          <template #icon><Sparkles :size="12" /></template>
          用 sqm 自动估算
        </Button>

        <Button
          type="primary"
          size="large"
          :loading="submitting"
          :disabled="!formValid"
          @click="submit"
        >
          <template #icon>
            <Loader2 v-if="submitting" :size="14" class="bmf__spin" />
            <Check v-else :size="14" />
          </template>
          {{ existingModel?.model_mode === 'BLOCK' ? '更新体块参数' : '保存体块参数' }}
        </Button>
      </div>

      <p v-if="errorMsg" class="bmf__error">
        <RefreshCw :size="12" />
        {{ errorMsg }}
      </p>
    </div>
  </section>
</template>

<style scoped lang="scss">
.bmf {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-4;
  display: flex;
  flex-direction: column;
  gap: $space-3;
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
    gap: $space-3;
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

  &__use-hint {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin: 4px 0 0;
    line-height: $lh-snug;
  }

  &__existing {
    display: flex;
    align-items: center;
    gap: $space-2;
    padding: $space-2;
    background: $color-green-soft;
    border: 1px solid $color-green;
    border-radius: $radius-sm;

    span {
      font-size: $fs-xs;
      color: $color-green;
      font-weight: $fw-medium;
    }
  }

  &__existing-id {
    font-family: $font-mono;
    font-size: 11px;
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

  &__hint-wrap {
    grid-column: span 2;
    display: flex;
    align-items: flex-start;
    gap: $space-2;
    // padding 之前是 space-2 (8px), 配 11px 字号显得块大字小空洞.
    // 提到 space-3 (12px) + 字号 fs-sm, 块跟字比例协调, 也跟 form item 的
    // label/input 视觉权重一致, 不会显得 hint 是 "被挤进去的小字"
    padding: $space-3;
    background: $gray-50;
    border-radius: $radius-sm;
    border-left: 3px solid $color-amber;
  }

  &__hint-icon {
    color: $color-amber;
    flex-shrink: 0;
    margin-top: 2px;
  }

  &__hint {
    font-size: $fs-sm;
    color: $color-text-secondary;
    line-height: $lh-snug;
    margin: 0;
  }

  &__actions {
    display: flex;
    gap: $space-2;
    align-items: center;
    padding-top: $space-2;
    border-top: 1px solid $gray-100;
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

  &__spin {
    animation: bmf-spin 1s linear infinite;
  }
}

@keyframes bmf-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
