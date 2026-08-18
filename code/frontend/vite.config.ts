import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import Components from 'unplugin-vue-components/vite'
import { AntDesignVueResolver } from 'unplugin-vue-components/resolvers'
import { viteStaticCopy } from 'vite-plugin-static-copy'
import { fileURLToPath, URL } from 'node:url'

// Vite 6 配置。
// dev server 跑在 5173, /api/v1 代理到后端 8000, 避免 CORS 麻烦。
// @ 别名指向 src/, 引用组件不用相对路径 ../../../。
// Ant Design Vue 用 unplugin-vue-components 自动按需引入, 不用手动 import。
//
// Cesium 静态资源 (Workers / Assets / Widgets / ThirdParty) 用 viteStaticCopy
// 复制到 dev 时的 public/cesium/ 和 build 时的 dist/cesium/。
// Cesium 内部代码引用这些资源时用相对路径 window.CESIUM_BASE_URL, 在 main.ts 设为 /cesium/。
// 不用 vite-plugin-cesium (年久失修, 不支持 vite 6), 手动 copy 更可控。
export default defineConfig({
  plugins: [
    vue(),
    Components({
      resolvers: [
        AntDesignVueResolver({
          importStyle: false,
        }),
      ],
      dts: 'src/components.d.ts',
    }),
    viteStaticCopy({
      targets: [
        { src: 'node_modules/cesium/Build/Cesium/Workers', dest: 'cesium' },
        { src: 'node_modules/cesium/Build/Cesium/Assets', dest: 'cesium' },
        { src: 'node_modules/cesium/Build/Cesium/Widgets', dest: 'cesium' },
        { src: 'node_modules/cesium/Build/Cesium/ThirdParty', dest: 'cesium' },
      ],
    }),
  ],
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  css: {
    preprocessorOptions: {
      scss: {
        // 全局注入 tokens.scss, 所有组件直接用 $color-amber 等变量
        additionalData: `@use "@/styles/tokens.scss" as *;`,
      },
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api/v1': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
  // Cesium 包大, chunkSize 调大避免 vite 把 cesium 拆成几百个小 chunk
  build: {
    chunkSizeWarningLimit: 4000,
  },
})
