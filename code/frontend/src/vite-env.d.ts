/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

interface ImportMetaEnv {
  /** 强制特定建筑走 splat 模式 (dev 测试用, 值为 building_code 末段, 如 "Tammy") */
  readonly VITE_MOCK_SPLAT_BUILDING?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}

