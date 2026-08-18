<script setup lang="ts">
// ============================================================================
// DataHub - 数据接入与 3D 建模中心
// ----------------------------------------------------------------------------
// 布局:
//   ┌──────────────────────────────────────────────────────────────┐
//   │ Header: 标题 + 当前园区 + demo 提示                           │
//   ├──────────┬───────────────────────────────────────────────────┤
//   │ 左 tab   │  上半屏 (表单/向导)                                │
//   │ 数据上传 │ ─────────────────────────────────────              │
//   │ 3D 建模  │  下半屏 (列表/任务)                                │
//   └──────────┴───────────────────────────────────────────────────┘
//
// 数据上传 tab:
//   上: TemplateDownload + FileUpload + MappingWizard (有 session_id 时才展开)
//   下: UploadSessionList + ImportBatchList
//
// 3D 建模 tab:
//   上: BlockModelForm + SinglePhotoUpload
//   下: JobStatusList
//
// 知识库 tab:
//   单组件 KnowledgePanel (上传 PDF + 文档列表 + 解析/向量化状态轮询)
//   RAG 检索源: 上传 -> parse -> embed 后, AI 抽屉里的 search_knowledge 工具能用
//
// 事件流:
//   FileUpload -> uploaded (session_id) -> MappingWizard 加载
//   MappingWizard -> committed -> UploadSessionList + ImportBatchList 刷新
//   UploadSessionList -> resume -> MappingWizard 加载该 session
//   SinglePhotoUpload -> jobSubmitted -> JobStatusList 刷新
//   ImportBatchList -> batchChanged -> UploadSessionList 刷新 (commit 后状态变)
//
// demo 账号: 仅能耗数据 (upload/imports/seed) 拦截, 其他写操作 (知识库/3D 建模/
// 异常检测) 都放开。banner 文案如实说明, 不再误导用户切账号
// ============================================================================

import { ref, computed, onMounted, watch } from 'vue'
import { Alert, Tabs, TabPane } from 'ant-design-vue'
import { Database, FileSpreadsheet, Layers, RefreshCw, BookOpen } from 'lucide-vue-next'
import { useContextStore } from '@/stores/context'
import { useAuthStore } from '@/stores/auth'
import { sitesApi } from '@/api/sites'
import TemplateDownload from '@/components/data-hub/TemplateDownload.vue'
import FileUpload from '@/components/data-hub/FileUpload.vue'
import MappingWizard from '@/components/data-hub/MappingWizard.vue'
import UploadSessionList from '@/components/data-hub/UploadSessionList.vue'
import ImportBatchList from '@/components/data-hub/ImportBatchList.vue'
import BlockModelForm from '@/components/data-hub/BlockModelForm.vue'
import SinglePhotoUpload from '@/components/data-hub/SinglePhotoUpload.vue'
import JobStatusList from '@/components/data-hub/JobStatusList.vue'
import KnowledgePanel from '@/components/data-hub/KnowledgePanel.vue'
import FloorUploadCard from '@/components/data-hub/FloorUploadCard.vue'
import BuildingUploadCard from '@/components/data-hub/BuildingUploadCard.vue'
import type { UploadTargetType, UploadedPayload } from '@/api/upload'

const context = useContextStore()
const auth = useAuthStore()

const activeTab = ref<'ingest' | 'modeling' | 'knowledge'>('ingest')

// 当前正在向导中的 session (从 FileUpload 上传成功拿到, 或从 UploadSessionList
// 点 "继续映射" 拿到). null 时向导收起不显示.
const wizardTargetType = ref<UploadTargetType | null>(null)
const wizardSessionId = ref<string | null>(null)

// 刷新信号 (子组件 watch 后 reload)
const sessionListRefreshSignal = ref(0)
const batchListRefreshSignal = ref(0)
const jobListRefreshSignal = ref(0)

// 知识库 tab 刷新信号 (顶部 "全部刷新" 按钮也触发)
const knowledgeRefreshSignal = ref(0)

const isDemo = computed(() => auth.isDemo)

// 当前园区名: 异步拉后端 sites list 找当前 site_id 对应的 site_name, 没拉到就回退
// site_code. 直接显示 siteId 会是一串 UUID (de9304ad-42ce-...), 视觉上是乱码
const siteName = ref<string>('加载中...')

async function loadSiteName() {
  if (!context.siteId) {
    siteName.value = '未选择园区'
    return
  }
  try {
    const sites = await sitesApi.listSites()
    const s = sites.find(x => x.site_id === context.siteId)
    siteName.value = s?.site_name ?? s?.site_code ?? context.siteId
  } catch {
    // 拉不到就回退 siteId (总比空白好)
    siteName.value = context.siteId
  }
}

// 切园区时刷新名字
watch(() => context.siteId, loadSiteName)
onMounted(loadSiteName)

function onUploaded(payload: UploadedPayload, targetType: UploadTargetType) {
  // 单文件模式直接拿 session_id; 多文件模式取第一个成功的 session 进入向导
  // 多文件批量上传时, 用户大概率上传同一类型的文件, 第一个就够, 其他可在
  // 下方 UploadSessionList 单独点 "继续映射" 进入向导
  let sessionId: string | null = null
  if (payload.mode === 'single') {
    sessionId = payload.session_id
  } else {
    const first = payload.result.sessions[0]
    if (first) sessionId = first.session_id
  }
  if (!sessionId) return

  wizardSessionId.value = sessionId
  wizardTargetType.value = targetType
}

function onResumeFromList(payload: { session_id: string; target_type: UploadTargetType }) {
  wizardSessionId.value = payload.session_id
  wizardTargetType.value = payload.target_type
}

function onWizardCommitted() {
  // commit 完刷新两边列表 + 通知顶栏重拉园区列表 (POINT/WEATHER commit 时后端
  // 会自动建 'default' 园区, 不重拉的话顶栏一直空, 园区页/建模页卡在空态)
  sessionListRefreshSignal.value += 1
  batchListRefreshSignal.value += 1
  context.bumpSitesVersion()
}

function onDirectCommitted() {
  // FLOOR / BUILDING 直接 commit 完刷新两边列表 + 通知顶栏重拉园区
  // (commit 可能自动建 site, 不重拉的话顶栏/园区页卡空态)
  sessionListRefreshSignal.value += 1
  batchListRefreshSignal.value += 1
  context.bumpSitesVersion()
}

function onWizardCancelled() {
  wizardSessionId.value = null
  wizardTargetType.value = null
}

function onBatchChanged() {
  // merge 完成后 session 状态会从 COMMITTED 走完整链路, 刷新 session 列表
  sessionListRefreshSignal.value += 1
}

function onJobSubmitted() {
  jobListRefreshSignal.value += 1
}

function onJobChanged() {
  // job 状态变化不需要强制刷新列表 (列表本身已轮询), 仅用于联动 scene 缓存失效
}

function refreshAll() {
  sessionListRefreshSignal.value += 1
  batchListRefreshSignal.value += 1
  jobListRefreshSignal.value += 1
  knowledgeRefreshSignal.value += 1
}
</script>

<template>
  <div class="dh">
    <!-- 顶部标题 + 园区 + demo 提示 -->
    <header class="dh__header">
      <div class="dh__title-wrap">
        <h2 class="dh__title">
          <Database :size="20" />
          数据接入与 3D 建模
        </h2>
        <p class="dh__subtitle">
          <Layers :size="12" />
          当前园区: {{ siteName }}
        </p>
      </div>
      <button class="dh__refresh" @click="refreshAll">
        <RefreshCw :size="14" />
        全部刷新
      </button>
    </header>

    <Alert
      v-if="isDemo"
      type="info"
      show-icon
      class="dh__demo-banner"
      message="demo 账号预置 BDG2 能耗数据用于展示。能耗数据上传 / 灌库 / 回滚等改源数据的操作会被拦截, 知识库 / 3D 建模 / 异常检测等其他功能可正常体验。"
    />

    <!-- 主体: 左 tab + 右上下分屏 -->
    <div class="dh__body">
      <Tabs v-model:activeKey="activeTab" tab-position="left" class="dh__tabs">
        <TabPane key="ingest" tab="数据上传">
          <div class="dh__pane">
            <!-- 上半屏: 模板下载 + 文件上传 + 向导 -->
            <section class="dh__top dh__top--ingest">
              <div class="dh__top-grid">
                <TemplateDownload />
                <FileUpload
                  :target-type="'POINT'"
                  :multiple="false"
                  :accept="'.csv,.xlsx'"
                  title="上传能耗数据"
                  subtitle="拖拽 CSV / XLSX 到此处, 上传后进入列名映射向导"
                  @uploaded="onUploaded($event, 'POINT')"
                />
              </div>

              <MappingWizard
                v-if="wizardSessionId && wizardTargetType"
                :session-id="wizardSessionId"
                :target-type="wizardTargetType"
                @committed="onWizardCommitted"
                @cancelled="onWizardCancelled"
              />
              <div v-else class="dh__wizard-placeholder">
                <FileSpreadsheet :size="32" class="dh__wizard-placeholder-icon" />
                <p class="dh__wizard-placeholder-text">
                  上传文件后, 这里会展开 4 步映射向导 (列名 -> 时间格式 -> 单位 -> 校验提交)
                </p>
              </div>

              <!-- 楼层/建筑信息上传 (独立卡片, 不走 MappingWizard, 上传后直接 commit) -->
              <FloorUploadCard @committed="onDirectCommitted" />
              <BuildingUploadCard @committed="onDirectCommitted" />
            </section>

            <!-- 下半屏: 两个列表并排 -->
            <section class="dh__bottom dh__bottom--ingest">
              <UploadSessionList
                :refresh-signal="sessionListRefreshSignal"
                @resume="onResumeFromList"
              />
              <ImportBatchList
                :refresh-signal="batchListRefreshSignal"
                @batch-changed="onBatchChanged"
              />
            </section>
          </div>
        </TabPane>

        <TabPane key="modeling" tab="3D 建模">
          <div class="dh__pane">
            <!-- 上半屏: 体块表单 + 单图上传 (左右两栏) -->
            <section class="dh__top dh__top--modeling">
              <div class="dh__top-grid dh__top-grid--modeling">
                <BlockModelForm />
                <SinglePhotoUpload @job-submitted="onJobSubmitted" />
              </div>
            </section>

            <!-- 下半屏: 重建任务列表 -->
            <section class="dh__bottom dh__bottom--modeling">
              <JobStatusList
                :refresh-signal="jobListRefreshSignal"
                @job-changed="onJobChanged"
              />
            </section>
          </div>
        </TabPane>

        <TabPane key="knowledge" tab="知识库">
          <div class="dh__pane dh__pane--knowledge">
            <KnowledgePanel :refresh-signal="knowledgeRefreshSignal" />
          </div>
        </TabPane>
      </Tabs>
    </div>
  </div>
</template>

<style scoped lang="scss">
.dh {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: $space-4;
  gap: $space-3;

  &__header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: $space-3;
    flex-shrink: 0;
  }

  &__title-wrap {
    display: flex;
    flex-direction: column;
    gap: 2px;
  }

  &__title {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: $fs-lg;
    font-weight: $fw-semibold;
    color: $color-concrete;
    margin: 0;
  }

  &__subtitle {
    display: flex;
    align-items: center;
    gap: 4px;
    font-size: $fs-xs;
    color: $color-text-secondary;
    margin: 0;
  }

  &__refresh {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 6px 12px;
    border: 1px solid $color-line;
    background: $color-card;
    border-radius: $radius-sm;
    font-size: $fs-xs;
    color: $color-concrete;
    cursor: pointer;
    transition: all $transition-fast;

    &:hover {
      border-color: $color-amber;
      color: $color-amber;
    }

    &:active {
      transform: scale(0.97);
    }
  }

  &__demo-banner {
    margin: 0;
    flex-shrink: 0;
  }

  &__body {
    flex: 1;
    min-height: 0;
    background: $color-card;
    border: 1px solid $gray-200;
    border-radius: $radius-md;
    overflow: hidden;
  }

  &__tabs {
    height: 100%;

    :deep(.ant-tabs-nav) {
      background: $gray-50;
      border-right: 1px solid $color-line;
      margin: 0;
      min-width: 140px;
    }

    :deep(.ant-tabs-tab) {
      padding: $space-3 $space-4;
      margin: 0 !important;
      font-size: $fs-sm;
      color: $color-text-secondary;
      transition: all $transition-fast;
    }

    :deep(.ant-tabs-tab-active) {
      background: $color-card;
      color: $color-amber;
      font-weight: $fw-medium;
    }

    :deep(.ant-tabs-ink-bar) {
      background: $color-amber;
      width: 3px !important;
      left: 0;
    }

    :deep(.ant-tabs-content-holder) {
      overflow-y: auto;
      padding: $space-3;
    }

    :deep(.ant-tabs-content) {
      height: 100%;
    }

    :deep(.ant-tabs-tabpane) {
      height: 100%;
    }
  }

  &__pane {
    display: flex;
    flex-direction: column;
    gap: $space-3;
    height: 100%;
    min-height: 0;

    // 知识库 pane 不走上下分屏, KnowledgePanel 内部自己分
    &--knowledge {
      gap: 0;
    }
  }

  &__top {
    display: flex;
    flex-direction: column;
    gap: $space-3;
    flex: 1 1 60%;
    min-height: 0;

    // 数据上传 tab: 上半屏内容多 (模板/上传 + 向导占位 + 楼层卡片), 按内容
    // 自适应高度。之前 flex: 1 1 auto + min-height: 0 会让上半屏被压扁, 底部
    // 的白色楼层卡片溢出盖住下半屏列表第一行。参照 3D 建模 tab 的修法改 0 0 auto。
    &--ingest {
      flex: 0 0 auto;
    }

    // 3D 建模 tab: 上半部分按内容自适应高度, 不撑开。
    // 之前 flex: 1 1 auto 会让上半部分被拉高, 内容 (spu 提交按钮) 超出 cell
    // 后溢出到下方 JobStatusList。改成 0 0 auto 让上半部分贴顶, 下半部分
    // 占满剩余空间, 两个框就不会再被压扁。
    &--modeling {
      flex: 0 0 auto;
    }
  }

  &__top-grid {
    display: grid;
    grid-template-columns: minmax(280px, 1fr) minmax(360px, 1.4fr);
    gap: $space-3;

    @media (max-width: 1080px) {
      grid-template-columns: 1fr;
    }

    // 3D 建模 tab: 体块表单 + 单图上传, 1:1 等宽 (两个组件内容差不多多)
    &--modeling {
      grid-template-columns: minmax(320px, 1fr) minmax(320px, 1fr);
    }
  }

  &__wizard-placeholder {
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: $space-2;
    padding: $space-6 $space-4;
    background: $gray-50;
    border: 1px dashed $color-line;
    border-radius: $radius-md;
    text-align: center;
  }

  &__wizard-placeholder-icon {
    color: $color-stone;
    opacity: 0.6;
  }

  &__wizard-placeholder-text {
    font-size: $fs-sm;
    color: $color-text-secondary;
    margin: 0;
    // 不限宽 + nowrap: 这句提示是一整句话, 限宽会在 "-> 时间格式" 处断行,
    // 让 "提交)" 单独跑到下一行, 视觉很突兀。占位区有 padding 兜底, 文字超过
    // 父容器宽也会居中显示不会溢出 (overflow: hidden 在 .dh__wizard-placeholder 上)
    white-space: nowrap;
    line-height: $lh-snug;
  }

  &__bottom {
    display: flex;
    gap: $space-3;
    flex: 1 1 40%;
    min-height: 0;

    &--ingest {
      flex: 1 1 auto;
      // 保底高度: 上半屏 (模板/上传/向导/楼层卡片) 内容多时, 下半屏两个外框
      // 也不会被压到太矮。外框本身没写 height, 高度全靠这里撑
      min-height: 420px;

      > * {
        flex: 1;
        min-width: 0;
      }
    }

    // 3D 建模 tab: 下半部分占满剩余空间 (上半部分 flex: 0 0 auto 后)
    // 加 min-height 保底: 上半屏 (体块表单 + 单图上传) 内容多时, 重建任务列表
    // 也不会被压到太矮
    &--modeling {
      flex: 1 1 auto;
      min-height: 420px;
      > * {
        flex: 1;
        min-width: 0;
      }
    }
  }
}
</style>
