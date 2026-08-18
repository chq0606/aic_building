<script setup lang="ts">
// 根组件: AntD ConfigProvider 主题包裹 + 路由出口 + 全局通知容器
// + 全局 AI 抽屉 (挂在根上所有路由都能用)
import { onMounted } from 'vue'
import { ConfigProvider } from 'ant-design-vue'
import { useRouter } from 'vue-router'
import zhCN from 'ant-design-vue/es/locale/zh_CN'
import dayjs from 'dayjs'
import 'dayjs/locale/zh-cn'
import { themeConfig } from '@/styles/theme'
import type { ThemeConfig } from 'ant-design-vue/es/config-provider/context'
import GlobalAiFab from '@/components/GlobalAiFab.vue'
import AiDrawer from '@/components/ai-drawer/AiDrawer.vue'
import { useAuthStore } from '@/stores/auth'

dayjs.locale('zh-cn')

// theme.ts 里 components 部分的某些 token 在 AntD 4.x 类型里没暴露 (Tabs/Menu 等
// 私有 token), 这里整体 cast 成 ThemeConfig 跳过严格检查。运行时这些值照样生效。
const theme = themeConfig as unknown as ThemeConfig

const router = useRouter()
const auth = useAuthStore()

// 应用启动时验证 token: 若 localStorage 里有 token 就调 /auth/me 验证。
// 失败 (token 过期 / 后端没起 / 网络挂) 则 clearTokens + 跳登录页,
// 避免用户带着无效 token 停在受保护页看到一堆 "服务器内部错误"。
// 放在 onMounted 而不是 main.ts mount 前, 是为了让用户先看到页面而不是白屏等后端响应。
onMounted(async () => {
  if (!auth.isAuthenticated) return
  try {
    await auth.fetchMe()
  } catch {
    // fetchMe 内部已 clearTokens, 这里只负责跳转。
    // 已在 /login 时不重复跳 (redirectToLogin 可能已经刷过页面)。
    if (!window.location.pathname.startsWith('/login')) {
      router.replace({
        name: 'login',
        query: { redirect: window.location.pathname + window.location.search },
      })
    }
  }
})
</script>

<template>
  <ConfigProvider :theme="theme" :locale="zhCN">
    <RouterView />
    <GlobalAiFab />
    <AiDrawer />
  </ConfigProvider>
</template>
