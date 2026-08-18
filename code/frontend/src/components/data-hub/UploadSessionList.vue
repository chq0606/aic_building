<script setup lang="ts">
// ============================================================================
// UploadSessionList - 上传会话列表
// ----------------------------------------------------------------------------
// 显示当前租户的所有 ingest.upload_session 记录, 按 created_at 倒序.
// 状态机: UPLOADED -> MAPPED -> VALIDATED -> COMMITTED, 失败置 FAILED.
//
// 每行展示:
//   - 文件名 + target_type 标签
//   - 状态 chip (彩色: 蓝=进行中, 绿=成功, 红=失败, 灰=初始)
//   - 行数统计 (总/有效/错误)
//   - 创建时间
//   - 操作: 进入 MappingWizard (UPLOADED/MAPPED/VALIDATED 状态可继续)
//           已 COMMITTED 的不能改, 跳到 ImportBatchList 看 merge 进度
//
// emit:
//   - resume: { session_id, target_type } -- 用户点 "继续映射" 时触发, 父组件
//             把 MappingWizard 切到这个 session
//   - refresh: 用户点 "刷新" 按钮触发
//
// 父组件 DataHub 在 FileUpload 上传完 + MappingWizard commit 完后, 调 refresh()
// 重新拉列表. ============================================================================

import { ref, onMounted, computed } from 'vue'
import { Button, Tag, Tooltip, Empty, Spin } from 'ant-design-vue'
import {
  RefreshCw, FileText, Loader2, CheckCircle2, AlertCircle,
  ArrowRight, Clock, FileX2,
} from 'lucide-vue-next'
import { uploadApi, type UploadSession, type UploadTargetType, type SessionStatus } from '@/api/upload'
import { ApiError } from '@/api/client'

const emit = defineEmits<{
  resume: [payload: { session_id: string; target_type: UploadTargetType }]
  refresh: []
}>()

const sessions = ref<UploadSession[]>([])
const loading = ref(false)
const errorMsg = ref<string | null>(null)

// 状态对应的展示元数据 (颜色 + 中文 + 图标)
const STATUS_META: Record<SessionStatus, { color: string; label: string; icon: typeof FileText }> = {
  UPLOADED: { color: 'blue', label: '待映射', icon: FileText },
  MAPPED: { color: 'cyan', label: '已映射', icon: FileText },
  VALIDATED: { color: 'gold', label: '已校验', icon: CheckCircle2 },
  COMMITTED: { color: 'green', label: '已提交', icon: CheckCircle2 },
  FAILED: { color: 'red', label: '失败', icon: AlertCircle },
}

const TARGET_LABEL: Record<UploadTargetType, string> = {
  POINT: '能耗读数',
  WEATHER: '气象',
  BUILDING: '建筑信息',
}

const hasSessions = computed(() => sessions.value.length > 0)

async function load() {
  loading.value = true
  errorMsg.value = null
  try {
    sessions.value = await uploadApi.listSessions()
  } catch (e) {
    errorMsg.value = e instanceof ApiError ? e.message : '加载失败'
  } finally {
    loading.value = false
  }
}

function onResume(s: UploadSession) {
  emit('resume', { session_id: s.id, target_type: s.target_type })
}

function formatTime(iso: string): string {
  if (!iso) return '-'
  const d = new Date(iso)
  // 用 YYYY-MM-DD HH:mm 格式, 不带秒 (秒对会话列表没用)
  const pad = (n: number) => n.toString().padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function shortId(id: string): string {
  return id.slice(0, 8)
}

// 是否能继续走 MappingWizard (UPLOADED/MAPPED/VALIDATED/FAILED 都可以重新走, COMMITTED 不行)
function canResume(s: UploadSession): boolean {
  return s.status !== 'COMMITTED'
}

defineExpose({ load })

onMounted(load)
</script>

<template>
  <section class="usl">
    <header class="usl__header">
      <div class="usl__title-wrap">
        <h3 class="usl__title">上传会话</h3>
        <p class="usl__subtitle">已上传文件的映射 / 校验 / 提交状态. 共 {{ sessions.length }} 条</p>
      </div>
      <Button size="small" :loading="loading" @click="load">
        <template #icon><RefreshCw :size="12" /></template>
        刷新
      </Button>
    </header>

    <div v-if="loading && !hasSessions" class="usl__loading">
      <Loader2 :size="20" class="usl__spin" />
    </div>

    <div v-else-if="errorMsg" class="usl__error">
      <AlertCircle :size="14" />
      {{ errorMsg }}
    </div>

    <Empty
      v-else-if="!hasSessions"
      :image="Empty.PRESENTED_IMAGE_SIMPLE"
      description="暂无上传会话"
      class="usl__empty"
    />

    <div v-else class="usl__list">
      <div
        v-for="s in sessions"
        :key="s.id"
        class="usl__row"
        :class="`usl__row--${s.status.toLowerCase()}`"
      >
        <div class="usl__row-main">
          <div class="usl__row-name">
            <FileText :size="14" class="usl__row-icon" />
            <span class="usl__row-filename">{{ s.original_filename }}</span>
            <Tag class="usl__row-target">{{ TARGET_LABEL[s.target_type] }}</Tag>
          </div>

          <div class="usl__row-stats">
            <Tooltip title="总行数">
              <span class="usl__stat">
                <FileText :size="11" />
                {{ s.row_count_total }}
              </span>
            </Tooltip>
            <Tooltip title="有效行">
              <span class="usl__stat usl__stat--ok">{{ s.row_count_valid }}</span>
            </Tooltip>
            <Tooltip v-if="s.row_count_error > 0" title="错误行">
              <span class="usl__stat usl__stat--err">{{ s.row_count_error }}</span>
            </Tooltip>
            <span class="usl__stat usl__stat--time">
              <Clock :size="11" />
              {{ formatTime(s.created_at) }}
            </span>
          </div>

          <p v-if="s.error_summary" class="usl__row-error">{{ s.error_summary }}</p>
        </div>

        <div class="usl__row-right">
          <Tag :color="STATUS_META[s.status].color" class="usl__row-status">
            <component :is="STATUS_META[s.status].icon" :size="10" />
            {{ STATUS_META[s.status].label }}
          </Tag>

          <Tooltip v-if="s.committed_batch_id" :title="`batch_id: ${s.committed_batch_id}`">
            <code class="usl__row-batch">#{{ shortId(s.committed_batch_id) }}</code>
          </Tooltip>

          <Button
            v-if="canResume(s)"
            size="small"
            type="link"
            @click="onResume(s)"
          >
            {{ s.status === 'UPLOADED' ? '开始映射' : '继续映射' }}
            <template #icon><ArrowRight :size="12" /></template>
          </Button>
          <span v-else class="usl__row-done">
            <CheckCircle2 :size="12" />
            已完成
          </span>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped lang="scss">
.usl {
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
    animation: usl-spin 1s linear infinite;
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

    &--committed {
      border-left-color: $color-green;
    }

    &--failed {
      border-left-color: $color-red;
      background: $color-red-soft;
    }

    &--validated {
      border-left-color: $color-amber;
    }

    &--uploaded,
    &--mapped {
      border-left-color: $color-stone;
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

    &-filename {
      font-weight: $fw-medium;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    &-target {
      font-size: 11px !important;
      padding: 0 6px;
      line-height: 18px;
      flex-shrink: 0;
    }

    &-stats {
      display: flex;
      align-items: center;
      gap: $space-2;
      margin-top: 2px;
      font-size: $fs-xs;
    }

    &-error {
      font-size: 11px;
      color: $color-red;
      margin: 2px 0 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }

    &-right {
      display: flex;
      align-items: center;
      gap: $space-2;
      flex-shrink: 0;
    }

    &-status {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 11px !important;
      padding: 2px 8px;
      line-height: 16px;
    }

    &-batch {
      font-family: $font-mono;
      font-size: 11px;
      color: $color-text-secondary;
      background: $gray-100;
      padding: 1px 4px;
      border-radius: $radius-xs;
    }

    &-done {
      display: flex;
      align-items: center;
      gap: 4px;
      font-size: 11px;
      color: $color-green;
    }
  }

  &__stat {
    display: inline-flex;
    align-items: center;
    gap: 3px;
    color: $color-text-secondary;

    &--ok {
      color: $color-green;
    }

    &--err {
      color: $color-red;
      font-weight: $fw-medium;
    }

    &--time {
      margin-left: auto;
    }
  }
}

@keyframes usl-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
