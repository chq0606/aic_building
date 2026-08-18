// ============================================================================
// Ant Design Vue 4.x 主题定制 (ConfigProvider)
// ----------------------------------------------------------------------------
// 把 tokens.scss 的色板映射到 AntD 的 design token 系统
// 用法: <a-config-provider :theme="themeConfig">
// ============================================================================

import { theme as antdTheme } from 'ant-design-vue'

// 主色映射: 琥珀色作为 AntD 的 primaryColor
// AntD 4.x 用 design token + algorithm 系统, 不再支持简单的 less 变量覆盖
export const themeConfig = {
  algorithm: antdTheme.defaultAlgorithm,
  token: {
    // ---- 颜色 ----
    colorPrimary: '#D49B3B',         // 琥珀主色
    colorPrimaryHover: '#B8842C',
    colorPrimaryActive: '#8B5A1F',
    colorSuccess: '#3D7E6A',          // 翠绿
    colorWarning: '#D49B3B',
    colorError: '#B84A3C',
    colorInfo: '#5A6B7C',             // 岩蓝灰

    colorTextBase: '#4A4A4A',        // 主文本
    colorTextSecondary: '#8A8275',    // 次级
    colorTextTertiary: '#A8A294',
    colorTextQuaternary: '#D1CEC5',

    colorBgLayout: '#F5F2EB',         // 页面背景 (paper)
    colorBgContainer: '#FFFFFF',      // 卡片背景
    colorBgElevated: '#FFFFFF',
    colorBorder: '#D1CEC5',
    colorBorderSecondary: '#E5E1D8',

    // ---- 字体 ----
    fontFamily: "'Noto Sans SC', 'PingFang SC', 'Microsoft YaHei', -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif",
    fontSize: 14,

    // ---- 圆角 ----
    borderRadius: 4,
    borderRadiusLG: 6,
    borderRadiusSM: 2,

    // ---- 阴影 ----
    boxShadow: '0 2px 8px rgba(74, 74, 74, 0.06)',
    boxShadowSecondary: '0 4px 12px rgba(74, 74, 74, 0.08)',

    // ---- 间距 (8px 网格) ----
    paddingXS: 8,
    paddingSM: 12,
    padding: 16,
    paddingMD: 20,
    paddingLG: 24,
    paddingXL: 32,

    marginXS: 8,
    marginSM: 12,
    margin: 16,
    marginMD: 20,
    marginLG: 24,
    marginXL: 32,

    // ---- 控件高度 ----
    controlHeight: 32,
    controlHeightSM: 24,
    controlHeightLG: 40,

    // ---- 行高 ----
    lineHeight: 1.5,
  },
  components: {
    Button: {
      // 琥珀色按钮悬停态轻微抬升, 增强反馈
      primaryShadow: '0 2px 4px rgba(212, 155, 59, 0.15)',
      fontWeight: 500,
    },
    Layout: {
      // Layout 的 ComponentToken 只支持这三个属性 (其他要走 AliasToken)
      colorBgHeader: '#FFFFFF',
      colorBgBody: '#F5F2EB',
      colorBgTrigger: '#4A4A4A',
    },
    Menu: {
      itemBg: 'transparent',
      itemColor: '#5A6B7C',
      itemSelectedBg: '#F5E6C8',
      itemSelectedColor: '#8B5A1F',
      itemActiveBg: '#FAF5EB',
      itemHeight: 40,
      iconSize: 16,
    },
    Card: {
      headerBg: 'transparent',
      paddingLG: 24,
    },
    Drawer: {
      // 抽屉用纸张暖白 + 微弱阴影
      colorBgElevated: '#FFFFFF',
    },
    Input: {
      activeBorderColor: '#D49B3B',
      hoverBorderColor: '#D49B3B',
    },
    Select: {
      colorPrimary: '#D49B3B',
    },
    Table: {
      headerBg: '#FAFAF8',
      headerColor: '#4A4A4A',
      rowHoverBg: '#FAF5EB',
      borderColor: '#E5E1D8',
    },
    Tag: {
      defaultBg: '#F5F2EB',
      defaultColor: '#5A6B7C',
    },
    Modal: {
      // 模态用更厚的阴影, 营造悬浮感
      contentBg: '#FFFFFF',
      headerBg: '#FFFFFF',
    },
    Tabs: {
      itemColor: '#8A8275',
      itemSelectedColor: '#4A4A4A',
      inkBarColor: '#D49B3B',
    },
    Tooltip: {
      colorBgSpotlight: '#4A4A4A',
      colorTextLightSolid: '#F5F2EB',
    },
  },
}

export type ThemeConfig = typeof themeConfig
