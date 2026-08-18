// ============================================================================
// 应用入口 main.ts
// ----------------------------------------------------------------------------
// 注册: Pinia + Vue Router + Ant Design Vue (按需) + 全局样式
//
// Cesium 静态资源路径: 在 import Cesium 之前就要设好 window.CESIUM_BASE_URL,
// 否则 Cesium 内部资源 (Workers / Assets / Widgets) 加载不到。
// viteStaticCopy 把这些资源 copy 到 /cesium/ 下 (vite.config.ts)。
// ============================================================================

// Cesium 静态资源 base url, 必须在 import 'cesium' 之前设置
;(window as any).CESIUM_BASE_URL = '/cesium/'
// Cesium 默认会请求 ion token, 我们用本地坐标系不需要 ion 服务,
// 显式设 undefined 避免控制台报 "Cesium ion access token is required" 警告
;(window as any).CesiumIon = { defaultAccessToken: undefined }

import { createApp } from 'vue'
import { createPinia } from 'pinia'
import Antd from 'ant-design-vue'
import 'ant-design-vue/dist/reset.css'

import App from './App.vue'
import router from './router'

// 全局样式 (顺序: reset.css 先, 然后 tokens (scss 全局注入), 然后 global)
import './styles/global.scss'

const app = createApp(App)

app.use(createPinia())
app.use(router)
app.use(Antd)

app.mount('#app')
