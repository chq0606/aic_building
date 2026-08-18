<script setup lang="ts">
// ============================================================================
// MappingWizard - 字段映射向导 4 步
// ----------------------------------------------------------------------------
// 步骤:
//   1. 列名确认  - 拉 /uploads/{id}/mapping-suggest 拿 columns + 猜测映射, 用户调整
//   2. 时间格式  - timestamp_format + timezone
//   3. 单位时区  - default_energy_type + default_unit
//   4. 预览校验  - 调 /uploads/{id}/validate, 展示结果, can_commit=true 时调 commit
//
// 每步可回退 (上一步), 不强制按顺序走 (用户改了第 1 步要回到第 4 步重 validate).
// 第 4 步校验失败时 can_commit=false, 用户改前几步后重新 validate.
//
// emit:
//   - committed: { batch_id, session_id } -- commit 成功, 父组件触发列表刷新
//   - cancelled: 用户取消向导
//
// 父组件 FileUpload 上传完文件后拿到 session_id, 传给 MappingWizard 走后续流程.
// ============================================================================

import { ref, computed, watch } from 'vue'
import { Steps, Step, Button, Select, Input, Tag, Alert, Table, Tooltip } from 'ant-design-vue'
import {
  ArrowLeft, ArrowRight, Check, AlertCircle, Loader2, ListChecks,
  Clock, Database, FileCheck2, XCircle,
} from 'lucide-vue-next'
import {
  uploadApi,
  type MappingConfig, type MappingSuggestResponse, type ValidateResult,
} from '@/api/upload'
import { ApiError } from '@/api/client'

// 内部表单状态: 把 MappingConfig 里所有 `string | null` 改成 `string | undefined`
// AntD Select v-model:value 的 SelectValue 类型不接受 null, 但接受 undefined.
// 提交时把 undefined 转成 null (MappingConfig 兼容).
type MappingFormState = Omit<MappingConfig, 'building_col' | 'energy_col' | 'value_col' | 'unit_col' | 'timestamp_format' | 'default_energy_type' | 'default_unit'> & {
  building_col: string | undefined
  energy_col: string | undefined
  value_col: string | undefined
  unit_col: string | undefined
  timestamp_format: string | undefined
  default_energy_type: string | undefined
  default_unit: string | undefined
}

const props = defineProps<{
  sessionId: string
  // 上传时选择的 target_type, 决定默认列映射策略
  targetType: 'POINT' | 'WEATHER' | 'BUILDING'
}>()

const emit = defineEmits<{
  committed: [payload: { batch_id: string; session_id: string; row_count: number }]
  cancelled: []
}>()

// 当前步骤 (0-indexed)
const currentStep = ref(0)

// loading 各阶段
const suggestLoading = ref(false)
const validateLoading = ref(false)
const commitLoading = ref(false)
const errorMsg = ref<string | null>(null)

// 后端返的列名 + 样本数据
const columns = ref<string[]>([])
const sampleRows = ref<Array<Record<string, unknown>>>([])

// 用户的映射配置 (双向绑定, 第 1-3 步都改这个)
const mapping = ref<MappingFormState>({
  timestamp_col: '',
  building_col: undefined,
  energy_col: undefined,
  value_col: undefined,
  unit_col: undefined,
  timezone: 'UTC',
  timestamp_format: undefined,
  default_energy_type: undefined,
  default_unit: undefined,
})

// 第 4 步校验结果
const validateResult = ref<ValidateResult | null>(null)
// commit 后拿到的 batch_id
const committedBatchId = ref<string | null>(null)

// 步骤定义
const STEPS = [
  { title: '列名确认', icon: ListChecks, desc: '把文件列映射到标准字段' },
  { title: '时间格式', icon: Clock, desc: '时间戳格式与时区' },
  { title: '单位配置', icon: Database, desc: '默认能源类型与单位' },
  { title: '预览校验', icon: FileCheck2, desc: '校验后提交到 staging' },
] as const

// 时区常用选项 (后端用 pytz 解析, 这里列常见几个)
const TIMEZONE_OPTIONS = [
  { value: 'UTC', label: 'UTC (协调世界时)' },
  { value: 'Asia/Shanghai', label: 'Asia/Shanghai (东八区)' },
  { value: 'Asia/Tokyo', label: 'Asia/Tokyo (东九区)' },
  { value: 'US/Mountain', label: 'US/Mountain (美西山区)' },
  { value: 'US/Pacific', label: 'US/Pacific (美西太平洋)' },
  { value: 'Europe/London', label: 'Europe/London (格林威治)' },
  { value: 'Australia/Sydney', label: 'Australia/Sydney (澳东)' },
]

// 时间格式常用选项, "auto" 表示让 pandas 自动猜
const TIMESTAMP_FORMAT_OPTIONS = [
  { value: '', label: '自动识别 (pandas to_datetime)' },
  { value: '%Y-%m-%d %H:%M:%S', label: '2017-01-01 00:00:00' },
  { value: '%Y-%m-%dT%H:%M:%S', label: '2017-01-01T00:00:00' },
  { value: '%Y-%m-%d %H:%M', label: '2017-01-01 00:00' },
  { value: '%Y-%m-%d', label: '2017-01-01 (日粒度)' },
  { value: '%Y/%m/%d %H:%M', label: '2017/01/01 00:00' },
  { value: '%m/%d/%Y %H:%M', label: '01/01/2017 00:00 (美式)' },
]

// 能源类型常用选项 (跟 BDG2 demo seed 对齐)
const ENERGY_TYPE_OPTIONS = [
  { value: 'electricity', label: 'electricity (电)' },
  { value: 'gas', label: 'gas (天然气)' },
  { value: 'hotwater', label: 'hotwater (热水)' },
  { value: 'chilledwater', label: 'chilledwater (冷冻水)' },
  { value: 'water', label: 'water (自来水)' },
  { value: 'solar', label: 'solar (太阳能)' },
]

// 单位常用选项
const UNIT_OPTIONS = [
  { value: 'kWh', label: 'kWh (千瓦时)' },
  { value: 'm3', label: 'm³ (立方米)' },
  { value: 'L', label: 'L (升)' },
  { value: 'MJ', label: 'MJ (兆焦)' },
  { value: 'GJ', label: 'GJ (吉焦)' },
  { value: 'therm', label: 'therm (色姆)' },
]

// target_type 决定哪些列映射字段必填
const requiredFields = computed<string[]>(() => {
  switch (props.targetType) {
    case 'POINT':
      return ['timestamp_col', 'building_col', 'energy_col', 'value_col']
    case 'WEATHER':
      return ['timestamp_col']
    case 'BUILDING':
      return ['building_id']  // buildings.csv 里 building_id 列
    default:
      return ['timestamp_col']
  }
})

// 第 1 步校验: 必填字段都选了
const step1Valid = computed(() => {
  if (!mapping.value.timestamp_col) return false
  if (requiredFields.value.includes('building_col') && !mapping.value.building_col) return false
  if (requiredFields.value.includes('energy_col') && !mapping.value.energy_col) return false
  if (requiredFields.value.includes('value_col') && !mapping.value.value_col) return false
  return true
})

// 第 2 步校验: 时区必选
const step2Valid = computed(() => !!mapping.value.timezone)

// 第 3 步: 默认能源类型 + 单位 (POINT 类型时必填, 其他可选)
const step3Valid = computed(() => {
  if (props.targetType === 'POINT') {
    return !!mapping.value.default_energy_type && !!mapping.value.default_unit
  }
  return true
})

// 当前步骤是否可下一步
const canNext = computed(() => {
  switch (currentStep.value) {
    case 0: return step1Valid.value
    case 1: return step2Valid.value
    case 2: return step3Valid.value
    default: return false
  }
})

// 样本数据表格列定义
const sampleColumns = computed(() => {
  return columns.value.slice(0, 8).map((col) => ({
    title: col,
    dataIndex: col,
    key: col,
    ellipsis: true,
    width: 120,
  }))
})

// 进入向导后立即拉 mapping-suggest
watch(
  () => props.sessionId,
  async (id) => {
    if (!id) return
    await loadMappingSuggest(id)
  },
  { immediate: true },
)

async function loadMappingSuggest(id: string) {
  suggestLoading.value = true
  errorMsg.value = null
  try {
    const resp: MappingSuggestResponse = await uploadApi.mappingSuggest(id)
    columns.value = resp.columns
    sampleRows.value = resp.sample_rows
    // 用后端猜测的 mapping 初始化 (用户在此基础上调整)
    // 后端返 null 的字段, 前端转 undefined 给 AntD Select 兜底, 提交时再转回 null
    mapping.value = {
      timestamp_col: resp.suggested.timestamp_col || '',
      building_col: resp.suggested.building_col ?? undefined,
      energy_col: resp.suggested.energy_col ?? undefined,
      value_col: resp.suggested.value_col ?? undefined,
      unit_col: resp.suggested.unit_col ?? undefined,
      quality_col: resp.suggested.quality_col ?? undefined,
      wide_melt: resp.suggested.wide_melt ?? null,
      timezone: resp.suggested.timezone || 'UTC',
      timestamp_format: resp.suggested.timestamp_format ?? undefined,
      default_energy_type: resp.suggested.default_energy_type ?? undefined,
      default_unit: resp.suggested.default_unit ?? undefined,
    }
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '读文件失败, 请重试'
  } finally {
    suggestLoading.value = false
  }
}

function nextStep() {
  if (currentStep.value < 3) currentStep.value++
}

function prevStep() {
  if (currentStep.value > 0) currentStep.value--
}

// 第 3 步 -> 第 4 步时自动保存 mapping + 触发 validate
async function goToValidate() {
  if (!canNext.value) return
  currentStep.value = 3
  // 先保存 mapping (UPLOADED/MAPPED 状态都可以), 再 validate
  validateResult.value = null
  await saveMappingAndValidate()
}

async function saveMappingAndValidate() {
  validateLoading.value = true
  errorMsg.value = null
  try {
    // 提交给后端: 把 undefined 转成 null (MappingConfig 用 null 表示字段未选)
    const payload: { mapping: MappingConfig } = {
      mapping: {
        timestamp_col: mapping.value.timestamp_col,
        building_col: mapping.value.building_col ?? null,
        energy_col: mapping.value.energy_col ?? null,
        value_col: mapping.value.value_col ?? null,
        unit_col: mapping.value.unit_col ?? null,
        quality_col: mapping.value.quality_col ?? null,
        wide_melt: mapping.value.wide_melt ?? null,
        timezone: mapping.value.timezone,
        timestamp_format: mapping.value.timestamp_format ?? null,
        default_energy_type: mapping.value.default_energy_type ?? null,
        default_unit: mapping.value.default_unit ?? null,
      },
    }
    await uploadApi.saveMapping(props.sessionId, payload)
    const result = await uploadApi.validate(props.sessionId)
    validateResult.value = result
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '校验失败, 请重试'
  } finally {
    validateLoading.value = false
  }
}

async function commitToStaging() {
  if (!validateResult.value?.can_commit) return
  commitLoading.value = true
  errorMsg.value = null
  try {
    const result = await uploadApi.commit(props.sessionId)
    committedBatchId.value = result.batch_id
    emit('committed', {
      batch_id: result.batch_id,
      session_id: props.sessionId,
      row_count: result.row_count_inserted,
    })
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '提交失败, 请重试'
  } finally {
    commitLoading.value = false
  }
}

function cancel() {
  emit('cancelled')
}
</script>

<template>
  <section class="mw">
    <header class="mw__header">
      <div class="mw__title-wrap">
        <h3 class="mw__title">字段映射向导</h3>
        <p class="mw__subtitle">把上传的文件列映射到标准字段, 校验通过后提交到 staging</p>
      </div>
      <button class="mw__close" @click="cancel">
        <XCircle :size="16" />
      </button>
    </header>

    <Steps :current="currentStep" size="small" class="mw__steps">
      <Step v-for="(s, i) in STEPS" :key="i" :title="s.title" />
    </Steps>

    <!-- 错误条 (任何阶段都可能出错) -->
    <Alert
      v-if="errorMsg"
      type="error"
      show-icon
      :message="errorMsg"
      class="mw__alert"
    />

    <!-- loading 遮罩 -->
    <div v-if="suggestLoading" class="mw__loading">
      <Loader2 :size="24" class="mw__spin" />
      <span>读取文件列名...</span>
    </div>

    <!-- 各步骤内容 -->
    <div v-else class="mw__body">
      <!-- 步骤 1: 列名确认 -->
      <div v-show="currentStep === 0" class="mw__step">
        <div class="mw__step-header">
          <ListChecks :size="18" class="mw__step-icon" />
          <div>
            <h4 class="mw__step-title">列名确认</h4>
            <p class="mw__step-desc">从文件列下拉选择对应的标准字段</p>
          </div>
        </div>

        <div class="mw__form-grid">
          <div class="mw__field">
            <label class="mw__label">
              时间戳列 <span class="mw__required">*</span>
            </label>
            <Select
              v-model:value="mapping.timestamp_col"
              :options="columns.map((c) => ({ value: c, label: c }))"
              placeholder="选择时间戳列"
              show-search
              class="mw__select"
            />
          </div>

          <div v-if="targetType === 'POINT'" class="mw__field">
            <label class="mw__label">
              建筑编码列 <span class="mw__required">*</span>
            </label>
            <Select
              v-model:value="mapping.building_col"
              :options="columns.map((c) => ({ value: c, label: c }))"
              placeholder="选择 building_id 列"
              show-search
              allow-clear
              class="mw__select"
            />
          </div>

          <div v-if="targetType === 'POINT'" class="mw__field">
            <label class="mw__label">
              能源类型列 <span class="mw__required">*</span>
            </label>
            <Select
              v-model:value="mapping.energy_col"
              :options="columns.map((c) => ({ value: c, label: c }))"
              placeholder="选择 energy_type 列"
              show-search
              allow-clear
              class="mw__select"
            />
          </div>

          <div v-if="targetType !== 'BUILDING'" class="mw__field">
            <label class="mw__label">
              数值列 <span v-if="targetType === 'POINT'" class="mw__required">*</span>
            </label>
            <Select
              v-model:value="mapping.value_col"
              :options="columns.map((c) => ({ value: c, label: c }))"
              placeholder="选择 value 数值列"
              show-search
              allow-clear
              class="mw__select"
            />
          </div>

          <div v-if="targetType !== 'BUILDING'" class="mw__field">
            <label class="mw__label">单位列 (可选)</label>
            <Select
              v-model:value="mapping.unit_col"
              :options="columns.map((c) => ({ value: c, label: c }))"
              placeholder="选 unit 列 (无则用默认单位)"
              show-search
              allow-clear
              class="mw__select"
            />
          </div>
        </div>

        <!-- 样本数据表格 -->
        <div class="mw__sample">
          <div class="mw__sample-title">
            样本数据 (前 {{ sampleRows.length }} 行)
            <Tag color="default">{{ columns.length }} 列</Tag>
          </div>
          <Table
            :columns="sampleColumns"
            :data-source="sampleRows"
            :pagination="false"
            size="small"
            :scroll="{ x: 'max-content' }"
            bordered
            row-key="timestamp"
          />
        </div>
      </div>

      <!-- 步骤 2: 时间格式 -->
      <div v-show="currentStep === 1" class="mw__step">
        <div class="mw__step-header">
          <Clock :size="18" class="mw__step-icon" />
          <div>
            <h4 class="mw__step-title">时间格式</h4>
            <p class="mw__step-desc">选择时间戳格式 (自动识别通常够用) 与所在时区</p>
          </div>
        </div>

        <div class="mw__form-grid mw__form-grid--2col">
          <div class="mw__field">
            <label class="mw__label">时间戳格式</label>
            <Select
              v-model:value="mapping.timestamp_format"
              :options="TIMESTAMP_FORMAT_OPTIONS"
              placeholder="自动识别 (推荐)"
              allow-clear
              class="mw__select"
            />
            <p class="mw__hint">选 "自动识别" 让 pandas to_datetime 自己猜, 多数 CSV 都能识别</p>
          </div>

          <div class="mw__field">
            <label class="mw__label">
              时区 <span class="mw__required">*</span>
            </label>
            <Select
              v-model:value="mapping.timezone"
              :options="TIMEZONE_OPTIONS"
              placeholder="选择数据来源时区"
              show-search
              class="mw__select"
            />
            <p class="mw__hint">BDG2 数据集原始时区是 US/Mountain, 中国建筑数据通常用 Asia/Shanghai</p>
          </div>
        </div>
      </div>

      <!-- 步骤 3: 单位配置 -->
      <div v-show="currentStep === 2" class="mw__step">
        <div class="mw__step-header">
          <Database :size="18" class="mw__step-icon" />
          <div>
            <h4 class="mw__step-title">单位配置</h4>
            <p class="mw__step-desc">设置能源类型 + 单位的默认值 (列缺失时用)</p>
          </div>
        </div>

        <div class="mw__form-grid mw__form-grid--2col">
          <div v-if="targetType === 'POINT'" class="mw__field">
            <label class="mw__label">
              默认能源类型 <span class="mw__required">*</span>
            </label>
            <Select
              v-model:value="mapping.default_energy_type"
              :options="ENERGY_TYPE_OPTIONS"
              placeholder="选默认能源类型"
              show-search
              allow-clear
              class="mw__select"
            />
            <p class="mw__hint">energy_col 缺失或行为空时用这个默认值</p>
          </div>

          <div v-if="targetType === 'POINT'" class="mw__field">
            <label class="mw__label">
              默认单位 <span class="mw__required">*</span>
            </label>
            <Select
              v-model:value="mapping.default_unit"
              :options="UNIT_OPTIONS"
              placeholder="选默认单位"
              show-search
              allow-clear
              class="mw__select"
            />
            <p class="mw__hint">unit_col 缺失或行为空时用这个默认值</p>
          </div>

          <div v-if="targetType !== 'POINT'" class="mw__hint-box">
            <AlertCircle :size="16" />
            <span>{{ targetType === 'WEATHER' ? '气象数据无能源类型/单位, 直接下一步' : '建筑基础信息无能源类型/单位, 直接下一步' }}</span>
          </div>
        </div>
      </div>

      <!-- 步骤 4: 预览校验 -->
      <div v-show="currentStep === 3" class="mw__step">
        <div class="mw__step-header">
          <FileCheck2 :size="18" class="mw__step-icon" />
          <div>
            <h4 class="mw__step-title">预览校验</h4>
            <p class="mw__step-desc">点击重新校验以应用最新的映射配置</p>
          </div>
        </div>

        <!-- 校验结果展示 -->
        <div v-if="validateLoading" class="mw__loading">
          <Loader2 :size="24" class="mw__spin" />
          <span>校验中...</span>
        </div>

        <div v-else-if="validateResult" class="mw__result">
          <div class="mw__result-stats">
            <div class="mw__stat">
              <div class="mw__stat-value">{{ validateResult.row_count_total }}</div>
              <div class="mw__stat-label">总行数</div>
            </div>
            <div class="mw__stat mw__stat--ok">
              <div class="mw__stat-value">{{ validateResult.row_count_valid }}</div>
              <div class="mw__stat-label">有效行</div>
            </div>
            <div class="mw__stat" :class="{ 'mw__stat--err': validateResult.row_count_error > 0 }">
              <div class="mw__stat-value">{{ validateResult.row_count_error }}</div>
              <div class="mw__stat-label">错误行</div>
            </div>
          </div>

          <!-- 错误明细 (前 20 条) -->
          <div v-if="validateResult.errors.length > 0" class="mw__errors">
            <div class="mw__errors-title">
              <AlertCircle :size="14" />
              <span>错误明细 (前 {{ validateResult.errors.length }} 条)</span>
            </div>
            <ul class="mw__error-list">
              <li v-for="(err, i) in validateResult.errors" :key="i" class="mw__error-item">
                <span class="mw__error-idx">#{{ i + 1 }}</span>
                <code class="mw__error-detail">{{ JSON.stringify(err) }}</code>
              </li>
            </ul>
          </div>

          <!-- 校验通过 -> 提交按钮 -->
          <div v-if="validateResult.can_commit" class="mw__commit-zone">
            <Alert type="success" show-icon message="校验通过, 可以提交到 staging" />
            <Button
              v-if="!committedBatchId"
              type="primary"
              :loading="commitLoading"
              size="large"
              @click="commitToStaging"
            >
              <template #icon><Check :size="14" /></template>
              提交到 staging
            </Button>
            <div v-else class="mw__committed">
              <Check :size="16" />
              <span>已提交, batch_id: <code>{{ committedBatchId.slice(0, 8) }}</code></span>
            </div>
          </div>

          <!-- 校验未通过 -> 提示改前几步 -->
          <Alert
            v-else
            type="warning"
            show-icon
            message="校验未通过, 回到前几步调整列映射或时间格式后重新校验"
          />
        </div>

        <div v-else class="mw__empty">
          <FileCheck2 :size="32" />
          <p>点击下方按钮开始校验</p>
        </div>
      </div>
    </div>

    <!-- 底部操作按钮 -->
    <footer class="mw__footer">
      <Button
        v-if="currentStep > 0 && currentStep < 3"
        @click="prevStep"
      >
        <template #icon><ArrowLeft :size="14" /></template>
        上一步
      </Button>

      <div class="mw__footer-right">
        <Button v-if="currentStep === 0" @click="cancel">取消</Button>

        <Button
          v-if="currentStep < 2"
          type="primary"
          :disabled="!canNext"
          @click="nextStep"
        >
          下一步
          <template #icon><ArrowRight :size="14" /></template>
        </Button>

        <Button
          v-if="currentStep === 2"
          type="primary"
          :disabled="!canNext"
          :loading="validateLoading"
          @click="goToValidate"
        >
          下一步: 预览校验
          <template #icon><ArrowRight :size="14" /></template>
        </Button>

        <Button
          v-if="currentStep === 3 && validateResult && !validateResult.can_commit"
          type="primary"
          :loading="validateLoading"
          @click="saveMappingAndValidate"
        >
          <template #icon><Loader2 v-if="validateLoading" :size="14" /></template>
          重新校验
        </Button>
      </div>
    </footer>
  </section>
</template>

<style scoped lang="scss">
.mw {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-4;
  display: flex;
  flex-direction: column;
  gap: $space-3;

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

  &__close {
    background: transparent;
    border: none;
    color: $color-text-secondary;
    cursor: pointer;
    padding: 4px;
    border-radius: $radius-sm;
    transition: all $transition-fast;

    &:hover {
      background: $gray-100;
      color: $color-concrete;
    }
  }

  &__alert {
    margin: 0;
  }

  &__loading {
    display: flex;
    align-items: center;
    justify-content: center;
    gap: $space-2;
    padding: $space-5;
    color: $color-text-secondary;
    font-size: $fs-sm;
  }

  &__spin {
    color: $color-amber;
    animation: mw-spin 1s linear infinite;
  }

  &__body {
    min-height: 280px;
  }

  &__step-header {
    display: flex;
    align-items: center;
    gap: $space-2;
    margin-bottom: $space-3;
  }

  &__step-icon {
    color: $color-amber;
  }

  &__step-title {
    font-size: $fs-base;
    font-weight: $fw-semibold;
    color: $color-concrete;
    margin: 0;
  }

  &__step-desc {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin: 2px 0 0;
  }

  &__form-grid {
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: $space-3;

    &--2col {
      grid-template-columns: repeat(2, 1fr);
    }

    @media (max-width: 720px) {
      grid-template-columns: 1fr;
    }
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

  &__required {
    color: $color-red;
    margin-left: 2px;
  }

  &__select {
    width: 100%;
  }

  &__hint {
    font-size: 11px;
    color: $color-text-secondary;
    margin: 2px 0 0;
    line-height: $lh-snug;
  }

  &__hint-box {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: $space-2;
    background: $gray-50;
    border-left: 3px solid $color-amber;
    border-radius: $radius-sm;
    font-size: $fs-xs;
    color: $color-text-secondary;
  }

  &__sample {
    margin-top: $space-4;
  }

  &__sample-title {
    display: flex;
    align-items: center;
    gap: $space-2;
    font-size: $fs-sm;
    font-weight: $fw-medium;
    color: $color-concrete;
    margin-bottom: $space-2;
  }

  &__result {
    display: flex;
    flex-direction: column;
    gap: $space-3;
  }

  &__result-stats {
    display: grid;
    grid-template-columns: repeat(3, 1fr);
    gap: $space-2;
  }

  &__stat {
    text-align: center;
    padding: $space-3;
    background: $gray-50;
    border: 1px solid $gray-200;
    border-radius: $radius-sm;

    &--ok {
      background: $color-green-soft;
      border-color: $color-green;
    }

    &--err {
      background: $color-red-soft;
      border-color: $color-red;
    }
  }

  &__stat-value {
    font-size: $fs-2xl;
    font-weight: $fw-bold;
    color: $color-concrete;
    font-family: $font-mono;
    line-height: 1;
  }

  &__stat-label {
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin-top: 4px;
  }

  &__errors {
    background: $color-red-soft;
    border: 1px solid $color-red;
    border-radius: $radius-sm;
    padding: $space-3;
  }

  &__errors-title {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: $fs-xs;
    font-weight: $fw-semibold;
    color: $color-red;
    margin-bottom: $space-2;
  }

  &__error-list {
    margin: 0;
    padding: 0;
    list-style: none;
    max-height: 200px;
    overflow-y: auto;
  }

  &__error-item {
    display: flex;
    align-items: flex-start;
    gap: $space-2;
    padding: 4px 0;
    font-size: $fs-xs;
  }

  &__error-idx {
    color: $color-red;
    font-weight: $fw-semibold;
    flex-shrink: 0;
    min-width: 28px;
  }

  &__error-detail {
    font-family: $font-mono;
    font-size: 11px;
    color: $color-concrete;
    word-break: break-all;
  }

  &__commit-zone {
    display: flex;
    flex-direction: column;
    gap: $space-3;
    align-items: flex-start;
  }

  &__committed {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: $space-2 $space-3;
    background: $color-green-soft;
    border: 1px solid $color-green;
    border-radius: $radius-sm;
    color: $color-green;
    font-size: $fs-sm;
    font-weight: $fw-medium;

    code {
      font-family: $font-mono;
      font-size: $fs-xs;
      background: rgba(255, 255, 255, 0.5);
      padding: 1px 4px;
      border-radius: $radius-xs;
    }
  }

  &__empty {
    display: flex;
    flex-direction: column;
    align-items: center;
    gap: $space-2;
    padding: $space-5;
    color: $color-text-secondary;
  }

  &__footer {
    display: flex;
    justify-content: space-between;
    align-items: center;
    gap: $space-2;
    padding-top: $space-3;
    border-top: 1px solid $gray-100;
  }

  &__footer-right {
    display: flex;
    gap: $space-2;
    margin-left: auto;
  }
}

@keyframes mw-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
