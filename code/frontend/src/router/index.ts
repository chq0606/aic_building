// ============================================================================
// Vue Router 配置
// ----------------------------------------------------------------------------
// 布局分层:
//   /login, /register      -> AuthLayout     (左 hero + 右表单)
//   /park, /analysis, ... -> DefaultLayout   (顶栏 + 侧栏 + 内容 + AI 抽屉)
//
// 守卫:
//   - 未登录访问受保护页 -> 跳 /login?redirect=...
//   - 已登录访问 /login 或 /register -> 跳 /park
// ============================================================================

import { createRouter, createWebHistory, type RouteRecordRaw } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import AuthLayout from '@/layouts/AuthLayout.vue'
import DefaultLayout from '@/layouts/DefaultLayout.vue'

const routes: RouteRecordRaw[] = [
  // ---- 公开路由: 走 AuthLayout ----
  // 注意: 两个 path: '/' 的路由 (AuthLayout / DefaultLayout) 共存时, Vue Router 4
  // 只匹配第一个 (AuthLayout), 但 AuthLayout children 原本只有 login/register,
  // 没有 '', 导致已登录用户访问 / 时停在 AuthLayout 但 RouterView 渲染空, 表现为
  // "右侧表单空白". 这里加 '' redirect 到 /park, 由 router.beforeEach 决定后续跳转
  // (已登录 -> DefaultLayout/Park, 未登录 -> /login).
  {
    path: '/',
    component: AuthLayout,
    children: [
      { path: '', redirect: '/park' },
      {
        path: 'login',
        name: 'login',
        component: () => import('@/views/Login.vue'),
        meta: { public: true, title: '登录' },
      },
      {
        path: 'register',
        name: 'register',
        component: () => import('@/views/Register.vue'),
        meta: { public: true, title: '注册' },
      },
    ],
  },

  // ---- 受保护路由: 走 DefaultLayout ----
  {
    path: '/',
    component: DefaultLayout,
    children: [
      {
        path: '',
        redirect: '/park',
      },
      {
        path: 'park',
        name: 'park',
        component: () => import('@/views/Park.vue'),
        meta: { title: '园区探索' },
      },
      {
        // buildingId 可选: 从 Park 抽屉跳转时带, 直接访问 /floor-view 用 context.buildingId
        path: 'floor-view/:buildingId?',
        name: 'floor-view',
        component: () => import('@/views/FloorView.vue'),
        meta: { title: '楼层分析' },
      },
      {
        path: 'analysis',
        name: 'analysis',
        component: () => import('@/views/Analysis.vue'),
        meta: { title: '分析中心' },
      },
      {
        path: 'data-hub',
        name: 'data-hub',
        component: () => import('@/views/DataHub.vue'),
        meta: { title: '数据接入' },
      },
      {
        path: 'settings',
        name: 'settings',
        component: () => import('@/views/Settings.vue'),
        meta: { title: '系统设置' },
      },
    ],
  },

  // ---- 404 (独立, 无 layout) ----
  {
    path: '/:pathMatch(.*)*',
    name: 'not-found',
    component: () => import('@/views/NotFound.vue'),
    meta: { public: true, title: '404' },
  },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(_to, _from, savedPosition) {
    return savedPosition ?? { top: 0 }
  },
})

// 全局前置守卫: 鉴权 + 标题
router.beforeEach((to, _from) => {
  const auth = useAuthStore()

  // 设置页面标题
  document.title = to.meta.title
    ? `${to.meta.title} - 建筑能耗平台`
    : '建筑能耗分析与节能优化平台'

  // 公开路由: /login /register /404
  if (to.meta.public) {
    // 已登录的用户访问登录页 -> 跳主页
    if (auth.isAuthenticated && (to.name === 'login' || to.name === 'register')) {
      return { name: 'park' }
    }
    return true
  }

  // 受保护路由
  if (!auth.isAuthenticated) {
    return {
      name: 'login',
      query: { redirect: to.fullPath },
    }
  }

  return true
})

export default router
