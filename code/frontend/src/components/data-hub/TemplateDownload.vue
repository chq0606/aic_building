<script setup lang="ts">
// ============================================================================
// TemplateDownload - CSV 模板下载卡片
// ----------------------------------------------------------------------------
// 三个模板按钮: buildings / readings / weather. 点击调后端 /uploads/templates/{type}
// 拿 blob, 用 <a download> 触发浏览器下载.
//
// 模板用途:
//   buildings -> 客户录入建筑基础信息 (target_type=BUILDING)
//   readings  -> 客户录入能耗读数长表 (target_type=POINT)
//   weather   -> 客户录入气象读数 (target_type=WEATHER)
//
// 跟 FileUpload 的关系: 客户先下模板填数据, 再用 FileUpload 上传填好的 CSV.
// ============================================================================

import { ref } from 'vue'
import { FileDown, FileSpreadsheet, CloudSun, Building2, Loader2, Check } from 'lucide-vue-next'
import { uploadApi, type UploadTargetType } from '@/api/upload'
import { ApiError } from '@/api/client'

interface TemplateItem {
  type: string
  label: string
  desc: string
  icon: typeof FileSpreadsheet
  targetType: UploadTargetType
}

// 三个模板的展示元信息. label/desc 给前端 UI 用, type 走后端下载.
const TEMPLATES: TemplateItem[] = [
  {
    type: 'buildings',
    label: '建筑基础信息',
    desc: '园区内每栋楼的 ID/名称/用途/面积/楼层',
    icon: Building2,
    targetType: 'BUILDING',
  },
  {
    type: 'readings',
    label: '能耗读数',
    desc: '一行一个时间点的读数, 含 建筑/能源类型/数值/单位',
    icon: FileSpreadsheet,
    targetType: 'POINT',
  },
  {
    type: 'weather',
    label: '气象读数',
    desc: '园区级气温/云量/降水/风速, 用于能耗-气温关联分析',
    icon: CloudSun,
    targetType: 'WEATHER',
  },
]

const downloading = ref<string | null>(null)
const justDownloaded = ref<string | null>(null)
const errorMsg = ref<string | null>(null)

async function download(type: string) {
  downloading.value = type
  errorMsg.value = null
  try {
    const { blob, filename } = await uploadApi.downloadTemplate(type)
    // Blob -> 触发 <a download> 浏览器原生下载. URL.createObjectURL 后 revoke 防内存泄漏.
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)

    // 短暂的成功状态反馈 (2s 后恢复), 用户点过哪个按钮一目了然
    justDownloaded.value = type
    setTimeout(() => {
      if (justDownloaded.value === type) justDownloaded.value = null
    }, 2000)
  } catch (e) {
    const msg = e instanceof ApiError ? e.message : '下载失败, 请重试'
    errorMsg.value = msg
  } finally {
    downloading.value = null
  }
}
</script>

<template>
  <section class="tpl">
    <header class="tpl__header">
      <div class="tpl__title-wrap">
        <h3 class="tpl__title">CSV 模板下载</h3>
        <p class="tpl__subtitle">先下模板填数据, 再用右侧上传组件导入. 模板带注释行说明字段含义</p>
      </div>
      <FileDown :size="20" class="tpl__header-icon" />
    </header>

    <div class="tpl__grid">
      <button
        v-for="tpl in TEMPLATES"
        :key="tpl.type"
        class="tpl__item"
        :class="{ 'tpl__item--done': justDownloaded === tpl.type }"
        :disabled="downloading !== null"
        @click="download(tpl.type)"
      >
        <!-- 顶部文件名条: 全宽 badge, 跟按钮内宽对齐 -->
        <!-- 之前 code 嵌在 title 里, body 只有 ~70px, buildings.csv (~92px) 装不下
             会溢出或被 ellipsis 截断. 挪到 button 顶部全宽 (~109px) 完整显示 -->
        <code class="tpl__item-code">{{ tpl.type }}.csv</code>

        <div class="tpl__item-main">
          <div class="tpl__item-icon-wrap">
            <component
              :is="tpl.icon"
              :size="22"
              class="tpl__item-icon"
            />
          </div>
          <div class="tpl__item-body">
            <div class="tpl__item-title">{{ tpl.label }}</div>
            <p class="tpl__item-desc">{{ tpl.desc }}</p>
            <div class="tpl__item-action">
              <Loader2
                v-if="downloading === tpl.type"
                :size="12"
                class="tpl__spin"
              />
              <Check
                v-else-if="justDownloaded === tpl.type"
                :size="12"
                class="tpl__check"
              />
              <FileDown v-else :size="12" />
              <span>{{ downloading === tpl.type ? '生成中' : justDownloaded === tpl.type ? '已下载' : '点击下载' }}</span>
            </div>
          </div>
        </div>
      </button>
    </div>

    <p v-if="errorMsg" class="tpl__error">{{ errorMsg }}</p>
  </section>
</template>

<style scoped lang="scss">
.tpl {
  background: $color-card;
  border: 1px solid $gray-200;
  border-radius: $radius-md;
  padding: $space-4;
  transition: box-shadow $transition-base;

  &:hover {
    box-shadow: $shadow-sm;
  }

  &__header {
    display: flex;
    align-items: flex-start;
    justify-content: space-between;
    gap: $space-2;
    margin-bottom: $space-3;
  }

  &__title-wrap {
    flex: 1;
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
    line-height: $lh-snug;
  }

  &__header-icon {
    color: $color-amber;
    flex-shrink: 0;
  }

  &__grid {
    display: grid;
    // minmax(0, 1fr) 而不是 1fr: 1fr 等价于 minmax(auto, 1fr), auto 的最小值
    // 是 min-content。当 .tpl__item-title 里的 "气象读数" + <code>weather.csv</code>
    // 不换行组合超过 1fr 计算宽度时, grid item 会溢出到旁边的 FileUpload 卡片下面。
    // minmax(0, ...) 允许 item 收缩到 0, 强制内容自己换行 (code 标签会 wrap 到下一行)
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: $space-2;

    // 窄屏堆叠
    @media (max-width: 720px) {
      grid-template-columns: 1fr;
    }
  }

  &__item {
    // 改成 column 布局: 顶部 code 条 + 下方 main (icon + body)
    // padding 移到 .tpl__item-main 上, 让 code 条能突破到 button 边缘形成全宽 badge
    display: flex;
    flex-direction: column;
    padding: 0;
    overflow: hidden;  // 让 code 顶部圆角跟 button 圆角对齐
    background: $gray-50;
    border: 1px solid $gray-200;
    border-radius: $radius-sm;
    cursor: pointer;
    text-align: left;
    transition: all $transition-fast;

    &:hover:not(:disabled) {
      background: $color-amber-soft;
      border-color: $color-amber;
      transform: translateY(-1px);
    }

    &:disabled {
      cursor: wait;
      opacity: 0.7;
    }

    &--done {
      background: $color-green-soft;
      border-color: $color-green;

      // 已下载时 code 顶部条也变绿, 跟 button 整体配色一致
      .tpl__item-code {
        background: rgba(61, 126, 106, 0.12);
        color: $color-green;
        border-bottom-color: rgba(61, 126, 106, 0.2);
      }
    }

    &-main {
      // 原 .tpl__item 的 padding 移到这里 (icon + body 区域保留内边距)
      display: flex;
      align-items: flex-start;
      gap: $space-2;
      padding: $space-3;
      flex: 1;
    }

    &-icon-wrap {
      flex-shrink: 0;
      width: 32px;
      height: 32px;
      border-radius: $radius-sm;
      background: $color-card;
      border: 1px solid $color-line;
      display: flex;
      align-items: center;
      justify-content: center;
      color: $color-stone;
    }

    &-icon {
      color: $color-stone;
    }

    &-body {
      flex: 1;
      min-width: 0;
    }

    &-title {
      font-size: $fs-sm;
      font-weight: $fw-semibold;
      color: $color-concrete;
    }

    &-code {
      // 顶部全宽 badge: 突破 button padding, 跟 button 内宽对齐
      // 给文件名 ~109px 可用宽度 (button 内宽), buildings.csv (~92px) 完整显示
      display: block;
      padding: $space-2 $space-3;
      background: $gray-100;
      border-bottom: 1px solid $color-line;
      font-family: $font-mono;
      font-size: $fs-xs;
      color: $color-text-secondary;
      text-align: left;
      // 文件名通常 ~80-92px, button 内宽 ~109px 够放, 但窄屏 (< 1080px) grid
      // 改 1 列时按钮变宽, 不会溢出; 极窄屏按钮变窄, ellipsis 兜底防溢出
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }

    &-desc {
      font-size: $fs-xs;
      color: $color-text-secondary;
      margin: 4px 0 0;
      line-height: $lh-snug;
    }

    &-action {
      display: flex;
      align-items: center;
      gap: 4px;
      margin-top: $space-2;
      font-size: $fs-xs;
      color: $color-amber;
      font-weight: $fw-medium;
    }
  }

  &__spin {
    animation: tpl-spin 1s linear infinite;
  }

  &__check {
    color: $color-green;
  }

  &__error {
    margin-top: $space-2;
    padding: $space-2;
    background: $color-red-soft;
    border-left: 3px solid $color-red;
    color: $color-red;
    font-size: $fs-xs;
    border-radius: $radius-sm;
  }
}

@keyframes tpl-spin {
  from { transform: rotate(0deg); }
  to { transform: rotate(360deg); }
}
</style>
