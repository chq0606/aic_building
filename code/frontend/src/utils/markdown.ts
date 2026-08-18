// ============================================================================
// markdown 渲染辅助 - 给 AI 抽屉的 assistant 消息用
// ----------------------------------------------------------------------------
// LLM 输出常含 markdown (列表/加粗/代码块/表格), 用 markdown-it 渲染成 HTML。
// 安全配置: html=false (不渲染原始 HTML 标签), linkify=true (URL 自动转链接)。
// ============================================================================

import MarkdownIt from 'markdown-it'
import { computed } from 'vue'

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
  typographer: false,
})

// 给所有链接加 target="_blank" rel="noopener", 防止钓鱼
const defaultLinkOpen = md.renderer.rules.link_open
md.renderer.rules.link_open = function (tokens, idx, options, _env, self) {
  const token = tokens[idx]
  token.attrSet('target', '_blank')
  token.attrSet('rel', 'noopener noreferrer')
  if (defaultLinkOpen) {
    return defaultLinkOpen(tokens, idx, options, _env, self)
  }
  return self.renderToken(tokens, idx, options)
}

export function renderMarkdown(text: string): string {
  if (!text) return ''
  return md.render(text)
}

// Vue composable 形式, 给组件用 computed 包一下避免重复渲染
export function useMarkdown(source: () => string) {
  return computed(() => renderMarkdown(source()))
}
