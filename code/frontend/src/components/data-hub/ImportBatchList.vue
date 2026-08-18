<script setup lang="ts">
// ============================================================================
// ImportBatchList - 灌库批次列表
// ----------------------------------------------------------------------------
// 显示 ingest.import_batch 列表, 每条对应一次 staging -> fact 的 merge.
// 状态机: LOADING -> MERGING -> SUCCEEDED / FAILED.
//   LOADING   session 刚 commit 完, 等用户点 "触发 merge"
//   MERGING   后台线程在跑 upsert + aggregate, 前端轮询
//   SUCCEEDED merge 成功, 数据已进 fact 表
//   FAILED    merge 过程异常, error_message 记录原因
//
// 操作:
//   - LOADING 状态: 显示 "触发 merge" 按钮, 调 POST /imports/{id}/run
//   - MERGING 状态: 显示进度指示器 (旋转图标), 自动轮询 status
//   - SUCCEEDED 状态: 显示 "回滚" 按钮 (二次确认), 调 POST /imports/{id}/rollback
//   - FAILED 状态: 显示错误信息
//
// 轮询策略: 只在有 MERGING 状态的 batch 时才轮询, 5s 一次. 全部终态后停止.
// 这是 [[user 决策的智能轮询策略]]: 避免无意义的全量轮询.
// ============================================================================

import { ref, onMounted, onBeforeUnmount, computed, watch } from 'vue'
import { Button, Tag, Tooltip, Empty, Modal, Spin, message } from 'ant-design-vue'
import {
  RefreshCw, Loader2, CheckCircle2, AlertCircle,
  Play, Undo2, Database, Clock,
} from 'lucide-vue-next'
import { importsApi, type ImportBatch, type BatchStatus } from '@/api/imports'
import { ApiError } from '@/api/client'

const props = defineProps<{
  // 外部触发的刷新信号, 父组件 commit 完后传新值触发 reload
  refreshSignal?: number
}>()

const emit = defineEmits<{
  batchChanged: []  // 任何 batch 状态变化都通知父组件, 用于触发 UploadSessionList 刷新
}>()

const batches = ref<ImportBatch[]>([])
const loading = ref(false)
const errorMsg = ref<string | null>(null)
// 单个 batch 的 run / rollback 操作 loading
const actionLoading = ref<Record<string, boolean>>({})

const STATUS_META: Record<BatchStatus, { color: string; label: string; icon: typeof Play }> = {
  LOADING: { color: 'default', label: '待触发', icon: Play },
  MERGING: { color: 'processing', label: '灌库中', icon: Loader2 },
  SUCCEEDED: { color: 'success', label: '成功', icon: CheckCircle2 },
  FAILED: { color: 'error', label: '失败', icon: AlertCircle },
}

const hasBatches = computed(() => batches.value.length > 0)
const hasMerging = computed(() => batches.value.some((b) => b.status === 'MERGING'))

let pollTimer: ReturnType<typeof setInterval> | null = null

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    batches.value = await importsApi.listBatches()
    // 根据 hasMerging 决定是否启动轮询
    schedulePolling()
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function schedulePolling() {
  // 已有 MERGING 状态的 batch -> 启动轮询; 没有 -> 停止
  if (hasMerging.value) {
    if (!pollTimer) {
      pollTimer = setInterval(async () => {
        // 拉所有 MERGING 状态的 batch, 更新状态
        const merging = batches.value.filter((b) => b.status === 'MERGING')
        if (merging.length === 0) {
          stopPolling()
          return
        }
        for (const b of merging) {
          try {
            const updated = await importsApi.getStatus(b.batch_id)
            const idx = batches.value.findIndex((x) => x.batch_id === b.batch_id)
            if (idx >= 0) batches.value[idx] = updated
          } catch (e) {
            // 单个 batch 拉取失败不中断其他 batch 轮询
            console.warn('轮询 batch 失败:', b.batch_id, e)
          }
        }
        // 如果所有 MERGING 都到终态, 停止轮询
        if (!hasMerging.value) {
          stopPolling()
          emit('batchChanged')
        }
      }, 5000)
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

async function runMerge(b: ImportBatch) {
  actionLoading.value[b.batch_id] = true
  try {
    await importsApi.runMerge(b.batch_id)
    // 立即更新本地状态为 MERGING, 然后启动轮询
    const idx = batches.value.findIndex((x) => x.batch_id === b.batch_id)
    if (idx >= 0) batches.value[idx].status = 'MERGING'
    schedulePolling()
    message.success('merge 已启动, 后台正在灌库')
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : '触发失败'
    message.error(msg)
  } finally {
    actionLoading.value[b.batch_id] = false
  }
}

function confirmRollback(b: ImportBatch) {
  Modal.confirm({
    title: '确认回滚该批次?',
    content: `回滚将删除该批次写入 fact.point_reading 的 ${b.row_count_success} 行数据, 并重算受影响建筑的日聚合. 此操作不可撤销.`,
    okText: '确认回滚',
    cancelText: '取消',
    okType: 'danger',
    onOk: () => doRollback(b),
  })
}

async function doRollback(b: ImportBatch) {
  actionLoading.value[b.batch_id] = true
  try {
    await importsApi.rollback(b.batch_id)
    message.success('回滚完成, fact 数据已删, 日聚合已重算')
    await load()
    emit('batchChanged')
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : '回滚失败'
    message.error(msg)
  } finally {
    actionLoading.value[b.batch_id] = false
  }
}

function formatTime(iso: string | null): string {
  if (!iso) return '-'
  const d = new Date(iso)
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function shortId(id: string): string {
  return id.slice(0, 8)
}

watch(
  () => props.refreshSignal,
  () => {
    if (props.refreshSignal !== undefined && props.refreshSignal > 0) load()
  },
)

defineExpose({ load })

onMounted(load)
onBeforeUnmount(stopPolling)
</script>

<template>
  <section class="ibl">
    <header class="ibl__header">
      <div class="ibl__title-wrap">
        <h3 class="ibl__title">灌库批次</h3>
        <p class="ibl__subtitle">临时表灌入正式表的合并状态. 共 {{ batches.length }} 条</p>
      </div>
      <Button size="small" :loading="loading" @click="load">
        <template #icon><RefreshCw :size="12" /></template>
        刷新
      </Button>
    </header>

    <div v-if="loading && !hasBatches" class="ibl__loading">
      <Loader2 :size="20" class="ibl__spin" />
    </div>

    <div v-else-if="errorMsg" class="ibl__error">
      <AlertCircle :size="14" />
      {{ errorMsg }}
    </div>

    <Empty
      v-else-if="!hasBatches"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
      description="暂无灌库批次. 在上传会话完成 commit 后, 这里会出现待 merge 的批次"
      class="ibl__empty"
    />

    <div v-else class="ibl__list">
      <div
        v-for="b in batches"
        :key="b.batch_id"
        class="ibl__row"
        :class="`ibl__row--${b.status.toLowerCase()}`"
      >
        <div class="ibl__row-main">
          <div class="ibl__row-name">
            <Database :size="14" class="ibl__row-icon" />
            <code class="ibl__row-id">#{{ shortId(b.batch_id) }}</code>
            <Tag :color="STATUS_META[b.status].color" class="ibl__row-status">
              <component
                :is="STATUS_META[b.status].icon"
                :size="10"
                :class="{ 'ibl__spin-icon': b.status === 'MERGING' }"
              />
              {{ STATUS_META[b.status].label }}
            </Tag>
          </div>

          <div class="ibl__row-stats">
            <Tooltip title="总行数">
              <span class="ibl__stat">{{ b.row_count_total }}</span>
            </Tooltip>
            <Tooltip title="成功行数">
              <span class="ibl__stat ibl__stat--ok">+{{ b.row_count_success }}</span>
            </Tooltip>
            <Tooltip v-if="b.row_count_error > 0" title="错误行数">
              <span class="ibl__stat ibl__stat--err">!{{ b.row_count_error }}</span>
            </Tooltip>
            <span class="ibl__stat ibl__stat--time">
              <Clock :size="11" />
              {{ formatTime(b.created_at) }}
            </span>
          </div>

          <p v-if="b.error_summary && b.status === 'FAILED'" class="ibl__row-error">
            <AlertCircle :size="11" />
            {{ b.error_summary }}
          </p>
        </div>

        <div class="ibl__row-right">
          <Button
            v-if="b.status === 'LOADING'"
            size="small"
            type="primary"
            :loading="actionLoading[b.batch_id]"
            @click="runMerge(b)"
          >
            <template #icon><Play :size="12" /></template>
            触发 merge
          </Button>

          <span v-else-if="b.status === 'MERGING'" class="ibl__row-merging">
            <Loader2 :size="12" class="ibl__spin-icon" />
            <span>后台灌库中...</span>
          </span>

          <span v-else-if="b.status === 'SUCCEEDED'" class="ibl__row-success">
            <CheckCircle2 :size="12" />
            <span>{{ b.row_count_success }} 行已入库</span>
          </span>

          <Button
            v-if="b.status === 'SUCCEEDED'"
            size="small"
            danger
            :loading="actionLoading[b.batch_id]"
            @click="confirmRollback(b)"
          >
            <template #icon><Undo2 :size="12" /></template>
            回滚
          </Button>

          <Tooltip v-if="b.started_at" :title="`开始: ${formatTime(b.started_at)}\n结束: ${formatTime(b.finished_at)}`">
            <Clock :size="11" class="ibl__row-clock" />
          </Tooltip>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped lang="scss">
.ibl {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-4;
  display: flex;
  flex-direction: column;
  gap: $space-2;

  &__header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: $space-2;
    margin-bottom: $space-2;
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

  &__loading {
    display: flex;
    justify-content: center;
    padding: $space-4;
  }

  &__spin {
    color: $color-amber;
    animation: ibl-spin 1s linear infinite;
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
  }

  &__empty {
    padding: $space-5 0;
  }

  &__list {
    display: flex;
    flex-direction: column;
    gap: $space-1;
    // 弹性高度: 空间富余时最高撑到 320px, 上半屏内容多把下半屏压矮时跟着
    // 收缩 (滚动条消化), 不再因固定 max-height 溢出到容器外
    flex: 1 1 auto;
    min-height: 160px;
    max-height: 500px;
    overflow-y: auto;
  }

  &__row {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: $space-3;
    padding: $space-3;
    background: $gray-50;
    border: 1px solid $gray-200;
    border-radius: $radius-sm;
    transition: all $transition-fast;
    border-left: 3px solid transparent;

    &:hover {
      background: $color-card;
      border-color: $color-line;
    }

    &--loading {
      border-left-color: $color-stone;
    }

    &--merging {
      border-left-color: $color-amber;
      background: $color-amber-soft;
    }

    &--succeeded {
      border-left-color: $color-green;
    }

    &--failed {
      border-left-color: $color-red;
      background: $color-red-soft;
    }

    &-main {
      flex: 1;
      min-width: 0;
    }

    &-name {
      display: flex;
      align-items: center;
      gap: 6px;
      font-size: $fs-sm;
      color: $color-concrete;
    }

    &-icon {
      color: $color-stone;
      flex-shrink: 0;
    }

    &-id {
      font-family: $font-mono;
      font-size: $fs-xs;
      color: $color-text-secondary;
      background: $gray-100;
      padding: 1px 4px;
      border-radius: $radius-xs;
    }

    &-status {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 11px !important;
      padding: 2px 8px;
      line-height: 16px;
    }

    &-stats {
      display: flex;
      align-items: center;
      gap: $space-2;
      margin-top: 2px;
      font-size: $fs-xs;
    }

    &-error {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      color: $color-red;
      margin: 2px 0 0;
    }

    &-right {
      display: flex;
      align-items: center;
      gap: $space-2;
      flex-shrink: 0;
    }

    &-merging {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: $fs-xs;
      color: $color-amber;
    }

    &-success {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: $fs-xs;
      color: $color-green;
    }

    &-clock {
      color: $color-text-secondary;
    }
  }

  &__stat {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    color: $color-text-secondary;
    font-family: $font-mono;

    &--ok {
      color: $color-green;
    }

    &--up {
      color: $color-amber;
    }

    &--err {
      color: $color-red;
      font-weight: $fw-medium;
    }

    &--time {
      margin-left: auto;
      font-family: $font-sans;
    }
  }

  &__spin-icon {
    animation: ibl-spin 1s linear infinite;
  }
}

@keyframes ibl-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
