// ============================================================================
// useScene composable - 调 scene API + watch context 自动重载
// ----------------------------------------------------------------------------
// 职责:
//   1. 监听 context store (siteId / metric / timePreset / customRange) 变化
//   2. 自动调 visualApi.getScene 拉新 scene 数据
//   3. 把结果灌进 parkStore (setScene / setLoading / setError)
//   4. 提供 manualReload 让用户强制刷新
//
// 用法:
//   const { reload, loading, error } = useScene()
//   onMounted(reload)  // 首次加载
//
// 切 metric / 时间范围时 watch 自动 reload, 调用方不用关心
// ============================================================================

import { watch, onMounted, ref } from 'vue'
import { useContextStore } from '@/stores/context'
import { useParkStore } from '@/stores/park'
import { visualApi, type SceneMetric } from '@/api/visual'
import { ApiError } from '@/api/client'
import { message as antdMessage } from 'ant-design-vue'

export function useScene() {
  const context = useContextStore()
  const park = useParkStore()

  const reloadCount = ref(0)

  async function reload() {
    const siteId = context.siteId
    if (!siteId) {
      park.setError('请先在顶栏选择园区')
      return
    }

    park.setLoading(true)
    park.setError(null)

    // 时间范围转 ISO8601 字符串
    const range = context.currentRange
    const start = range.start.toISOString()
    const end = range.end.toISOString()
    const metric = (context.metric === 'cost' ? 'eui' : context.metric) as SceneMetric

    try {
      const scene = await visualApi.getScene(siteId, { metric, start, end })
      park.setScene(scene)
      reloadCount.value++
    } catch (err) {
      const msg = err instanceof ApiError ? err.message : '加载园区数据失败'
      park.setError(msg)
      antdMessage.error(msg)
    } finally {
      park.setLoading(false)
    }
  }

  // ---- watch context 自动 reload ----
  // siteId 变化: 切园区, 必须重载
  // metric 变化: 切指标, 体块颜色重新映射
  // timePreset 变化: 切时间范围, 数据重新拉
  // customRange 变化: 自定义时间范围, 数据重新拉
  watch(
    () => context.siteId,
    () => { if (context.siteId) reload() },
  )
  watch(
    () => context.metric,
    () => { if (context.siteId) reload() },
  )
  watch(
    () => context.timePreset,
    () => { if (context.siteId) reload() },
  )
  watch(
    () => context.customRange,
    () => { if (context.siteId) reload() },
    { deep: true },
  )

  // ---- 首次加载 + 每次切回 Park 都 reload ----
  // 不加 scene===null 条件: 用户在 DataHub 删/提交 job 后 visual_model 表变了,
  // 切回 Park 必须重拉 scene 才能让 model_kind 反映最新状态 (splat <-> estimated)
  // Park.vue 没用 keep-alive, 每次切路由都会重新挂载触发 onMounted
  onMounted(() => {
    if (context.siteId) reload()
  })

  return {
    reload,
    reloadCount,
    loading: park.loading,
    error: park.error,
    scene: park.scene,
  }
}
