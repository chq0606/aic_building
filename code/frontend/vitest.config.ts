// ============================================================================
// vitest.config.ts - 前端单元测试配置
// ----------------------------------------------------------------------------
// 环境: jsdom (DOM API 可用)
// 别名: @ -> src (跟 vite.config.ts 对齐)
// ============================================================================
import { defineConfig } from 'vitest/config'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  test: {
    environment: 'jsdom',
    globals: true,
    include: ['src/**/__tests__/**/*.spec.ts'],
    coverage: {
      provider: 'v8',
      reporter: ['text', 'json', 'html'],
      exclude: ['node_modules/', 'dist/', '**/*.d.ts'],
    },
  },
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  // 静态资源 (cesium Workers/Assets) 不真处理, 测试不依赖
  assetsInclude: ['**/*.wasm', '**/*.wkv'],
})
