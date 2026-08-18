// ============================================================================
// AI Store (Pinia) - AI 抽屉全局状态
// ----------------------------------------------------------------------------
// 职责:
//   1. 抽屉开/关状态
//   2. 会话列表 / 当前会话 / 历史消息
//   3. SSE 流式发送消息 (fetch + ReadableStream 手写, 不用 EventSource)
//   4. 流式过程中的临时状态: tool_calls / delta / optimization_plan
//   5. 自动同步 contextStore 上下文 (site/building/time_range/metric)
//
// SSE 客户端为什么不用 EventSource:
//   - EventSource 只支持 GET, 不支持 POST body
//   - EventSource 不支持自定义 header (我们要带 Authorization: Bearer <token>)
//   - 用 fetch + response.body.getReader() 手动解析 text/event-stream
//
// SSE 事件协议 (跟后端 qa_service._sse 对齐):
//   - session:        会话 ID (建会话用, 我们已经提前建好, 忽略)
//   - thinking:       plan 阶段心跳 (5s 一次), 文案带已耗时秒数, 让用户知道在动
//   - tool_calls:     LLM 决定调哪些工具
//   - tool_result:    单个工具执行结果
//   - delta:          LLM 流式回答的增量文本
//   - optimization_plan: 节能优化四段结构化 JSON
//   - done:           回答完成, 含 message_id
//   - error:          错误
// ============================================================================

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { message as antdMessage } from 'ant-design-vue'

import * as assistantApi from '@/api/assistant'
import type {
  ChatContext,
  ChatMessage,
  ChatSession,
  Citation,
  OptimizationPlan,
  ToolCall,
} from '@/api/assistant'
import { getAccessToken } from '@/api/client'
import { useContextStore } from '@/stores/context'

// ---------------------------------------------------------------------------
// 流式过程中的临时 message 结构 (还没拿到 message_id, 用 local_id 跟踪)
// ---------------------------------------------------------------------------

interface StreamingMessage {
  local_id: string
  role: 'assistant'
  content: string
  tool_calls: ToolCall[]
  tool_results: Record<string, unknown>[]  // tool_call_id -> result
  citations: Citation[]
  optimization_plan: OptimizationPlan | null
  is_streaming: boolean
  // 后端 plan 阶段发来的 thinking 心跳文案, 给 MessageBubble 显示用
  // 没收到 thinking 事件前是 null, 收到后是 "正在分析问题... (10s)" 这种文案
  thinking_text: string | null
  error?: string
}

// ---------------------------------------------------------------------------
// Quick Questions 推荐模板 (按场景)
// ---------------------------------------------------------------------------

interface QuickQuestionTemplate {
  match: (ctx: ChatContext | null, scenario: string) => boolean
  questions: string[]
}

const QUICK_TEMPLATES: QuickQuestionTemplate[] = [
  {
    match: (_ctx, scenario) => scenario === 'optimization',
    questions: [
      '这栋楼有什么节能空间?',
      '给我一份节能优化方案',
      '基于异常情况给节能建议',
    ],
  },
  {
    match: (ctx, _s) => !!ctx?.building_id,
    questions: [
      '这栋楼最近 7 天用电情况怎么样?',
      '这栋楼有什么异常?',
      '这栋楼的 EUI 水平如何?',
      '给我节能优化建议',
    ],
  },
  {
    match: (ctx, _s) => !!ctx?.site_id && !ctx?.building_id,
    questions: [
      '园区整体能耗概况?',
      '园区哪栋楼最耗能?',
      '园区异常分布?',
    ],
  },
  {
    match: () => true,
    questions: [
      'GB 55015-2021 对 EUI 限值有什么要求?',
      '什么是 BASELINE_DEVIATION 异常?',
      '帮我节能优化分析',
    ],
  },
]

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

export const useAiStore = defineStore('ai', () => {
  // ---- state ----
  const drawerOpen = ref(false)
  const sessions = ref<ChatSession[]>([])
  const currentSessionId = ref<string | null>(null)
  const messages = ref<ChatMessage[]>([])
  const streamingMessage = ref<StreamingMessage | null>(null)
  const isStreaming = ref(false)
  const loadingSessions = ref(false)
  const loadingMessages = ref(false)
  const error = ref<string | null>(null)

  // ---- getters ----
  const allMessages = computed(() => {
    // 已持久化的 + 当前流式中的
    if (streamingMessage.value) {
      return [...messages.value, streamingMessage.value as unknown as ChatMessage]
    }
    return messages.value
  })

  const contextStore = useContextStore()
  const currentContext = computed<ChatContext | null>(() => {
    if (!contextStore.siteId) return null
    const ctx: ChatContext = {
      site_id: contextStore.siteId,
      // site_name 一起传, 后端 build_system_prompt 要用 (LLM 知道当前园区叫什么,
      // 调 query_park_overview 时能带上, 不会瞎编 site-001 这种占位符)
      site_name: contextStore.siteName ?? undefined,
      building_id: contextStore.buildingId ?? undefined,
      building_name: contextStore.buildingName ?? undefined,
      time_range: {
        start: contextStore.currentRange.start.toISOString(),
        end: contextStore.currentRange.end.toISOString(),
      },
      metric: contextStore.metric,
    }
    return ctx
  })

  const quickQuestions = computed(() => {
    const ctx = currentContext.value
    // 简单识别场景 (跟后端 decide_scenario 对齐, 但前端只用于选问题模板)
    const text = ''
    const scenario = ctx?.building_id ? 'data' : 'park'
    for (const t of QUICK_TEMPLATES) {
      if (t.match(ctx, scenario)) return t.questions
    }
    return QUICK_TEMPLATES[QUICK_TEMPLATES.length - 1].questions
  })

  // ---- actions: 抽屉 ----
  async function openDrawer() {
    drawerOpen.value = true
    if (sessions.value.length === 0) {
      await loadSessions()
    }
    if (!currentSessionId.value && sessions.value.length > 0) {
      selectSession(sessions.value[0].id).catch(() => {})
    }
  }

  function closeDrawer() {
    drawerOpen.value = false
  }

  function toggleDrawer() {
    if (drawerOpen.value) closeDrawer()
    else openDrawer()
  }

  // ---- actions: 会话 ----
  async function loadSessions() {
    loadingSessions.value = true
    error.value = null
    try {
      const resp = await assistantApi.listSessions(50)
      sessions.value = resp.items
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '加载会话失败'
      error.value = msg
      antdMessage.error(msg)
    } finally {
      loadingSessions.value = false
    }
  }

  async function createSession(context?: ChatContext, title?: string) {
    const ctx = context ?? currentContext.value ?? undefined
    const session = await assistantApi.createSession({ title, context: ctx })
    sessions.value.unshift(session)
    currentSessionId.value = session.id
    messages.value = []
    return session
  }

  async function selectSession(id: string) {
    if (currentSessionId.value === id && messages.value.length > 0) return
    currentSessionId.value = id
    await loadMessages(id)
  }

  async function loadMessages(sessionId: string) {
    loadingMessages.value = true
    try {
      const resp = await assistantApi.listMessages(sessionId, 100)
      messages.value = resp.items
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '加载消息失败'
      antdMessage.error(msg)
    } finally {
      loadingMessages.value = false
    }
  }

  // ---- actions: 发消息 (SSE 流式) ----

  async function sendMessage(content: string) {
    if (!content.trim() || isStreaming.value) return

    // 没有当前会话就先建一个
    let sessionId = currentSessionId.value
    if (!sessionId) {
      const session = await createSession()
      sessionId = session.id
    }

    // 立即把 user 消息推进列表 (前端先显示, 不等后端持久化)
    const userMsg: ChatMessage = {
      id: `local_user_${Date.now()}`,
      session_id: sessionId,
      role: 'user',
      content,
      created_at: new Date().toISOString(),
    }
    messages.value.push(userMsg)

    // 初始化流式 assistant 消息
    streamingMessage.value = {
      local_id: `local_assistant_${Date.now()}`,
      role: 'assistant',
      content: '',
      tool_calls: [],
      tool_results: [],
      citations: [],
      optimization_plan: null,
      is_streaming: true,
      thinking_text: null,
    }
    isStreaming.value = true
    error.value = null

    try {
      await _streamSSE(sessionId, content)
    } catch (e: unknown) {
      const msg = e instanceof Error ? e.message : '发送失败'
      if (streamingMessage.value) {
        streamingMessage.value.error = msg
        streamingMessage.value.is_streaming = false
      }
      antdMessage.error(msg)
    } finally {
      isStreaming.value = false
    }
  }

  // ---- SSE 客户端 ----

  async function _streamSSE(sessionId: string, content: string): Promise<void> {
    const token = getAccessToken()
    if (!token) throw new Error('未登录或登录已过期')

    const ctx = currentContext.value ?? undefined
    const resp = await fetch(`/api/v1/assistant/sessions/${sessionId}/messages`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ content, context: ctx }),
    })

    if (!resp.ok) {
      const text = await resp.text().catch(() => '')
      throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`)
    }
    if (!resp.body) throw new Error('响应无 body')

    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''

    // eslint-disable-next-line no-constant-condition
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      // SSE 事件以 \n\n 分隔, 一次 read 可能拿到多个事件
      const events = buffer.split('\n\n')
      buffer = events.pop() ?? ''  // 最后一段可能不完整, 留给下次

      for (const evt of events) {
        if (!evt.trim()) continue
        _handleSSEEvent(evt)
      }
    }
    // 处理最后一段 (如果有)
    if (buffer.trim()) {
      _handleSSEEvent(buffer)
    }

    // 流结束后, streamingMessage 转成持久化消息
    if (streamingMessage.value) {
      streamingMessage.value.is_streaming = false
      // 如果后端给了 message_id (done 事件), 用后端的; 否则用 local_id
      // _handleSSEEvent 里 done 事件会做这件事
    }
  }

  function _handleSSEEvent(raw: string): void {
    // 提取 data: 行
    const lines = raw.split('\n')
    let dataLine = ''
    for (const line of lines) {
      const trimmed = line.trim()
      if (trimmed.startsWith('data:')) {
        dataLine += trimmed.slice(5).trim()
      }
    }
    if (!dataLine) return

    let payload: Record<string, unknown>
    try {
      payload = JSON.parse(dataLine)
    } catch {
      console.warn('[AI] SSE payload 解析失败, 跳过:', dataLine.slice(0, 100))
      return
    }

    const type = payload.type as string
    const sm = streamingMessage.value
    if (!sm) return

    switch (type) {
      case 'thinking': {
        // plan 阶段 + 第二阶段 LLM 输出停顿心跳都走这里
        // 总是更新 thinking_text, 让 MessageBubble 在 content 下方显示"正在继续..."
        // 指示器。下次 delta 到来时清掉 thinking_text (delta case 里有 = null)
        const text = (payload.text as string) || '正在思考...'
        sm.thinking_text = text
        break
      }
      case 'tool_calls': {
        const items = (payload.items as ToolCall[]) || []
        sm.tool_calls = items
        sm.thinking_text = null  // 进入工具调用阶段, 清掉 thinking 文案
        break
      }
      case 'tool_result': {
        const tcId = payload.tool_call_id as string
        const ok = payload.ok as boolean
        const data = payload.data
        sm.tool_results.push({ tool_call_id: tcId, ok, data })
        // 更新对应 tool_call 的 ok 状态
        const tc = sm.tool_calls.find(t => t.id === tcId)
        if (tc) tc.ok = ok
        // 收集 citations (从 data 里抽, 后端 _extract_references 同款逻辑)
        _collectCitations(sm, payload.name as string, data)
        break
      }
      case 'delta': {
        const text = (payload.text as string) || ''
        sm.content += text
        sm.thinking_text = null  // 开始流式回答, 清掉 thinking 文案
        break
      }
      case 'optimization_plan': {
        sm.optimization_plan = (payload.data as OptimizationPlan) || null
        break
      }
      case 'done': {
        const messageId = payload.message_id as string
        const sessionId = payload.session_id as string
        // 转成持久化消息
        const finalized: ChatMessage = {
          id: messageId || sm.local_id,
          session_id: sessionId,
          role: 'assistant',
          content: sm.content,
          tool_calls: sm.tool_calls,
          citations: sm.citations,
          optimization_plan: sm.optimization_plan,
          created_at: new Date().toISOString(),
        }
        messages.value.push(finalized)
        streamingMessage.value = null
        // 刷新会话列表 (更新 updated_at / message_count)
        loadSessions().catch(() => {})
        break
      }
      case 'error': {
        const errMsg = (payload.message as string) || 'AI 助手错误'
        const code = (payload.code as string) || 'unknown'
        sm.content += `\n\n[错误: ${errMsg}]`
        sm.error = errMsg
        if (code === 'key_invalid') {
          antdMessage.warning('请先到系统设置页配置 GLM API Key')
        }
        break
      }
      default:
        console.warn('[AI] 未知 SSE 事件类型:', type)
    }
  }

  function _collectCitations(sm: StreamingMessage, toolName: string, data: unknown): void {
    if (!data || typeof data !== 'object') return
    const d = data as Record<string, unknown>
    if (!d.ok) return

    if (toolName === 'search_knowledge') {
      const items = (d.items as Array<Record<string, unknown>>) || []
      for (const item of items) {
        sm.citations.push({
          type: 'document',
          standard_no: item.standard_no as string,
          section_title: item.section_title as string,
          content_snippet: (item.content_snippet as string)?.slice(0, 200),
          score: item.score_final,
        })
      }
    } else if (toolName === 'query_anomalies') {
      const items = (d.items as Array<Record<string, unknown>>) || []
      for (const item of items) {
        sm.citations.push({
          type: 'anomaly',
          event_id: item.id,
          event_type: item.event_type,
          severity: item.severity,
          observed_value: item.observed_value,
          baseline_value: item.baseline_value,
          evidence: (item.evidence as string)?.slice(0, 200),
        })
      }
    } else if (toolName === 'query_building_energy') {
      const stats = d.stats as Record<string, unknown>
      if (stats) {
        sm.citations.push({
          type: 'data',
          building_name: d.building_name,
          metric: d.metric,
          granularity: d.granularity,
          stats,
        })
      }
    } else if (toolName === 'query_park_overview') {
      sm.citations.push({
        type: 'data',
        scope: 'park',
        building_count: d.building_count,
        total_kwh: d.total_kwh,
        avg_eui: d.avg_eui,
        anomaly_count: d.anomaly_count,
      })
    }
  }

  // ---- actions: 内嵌式节能建议 (详情抽屉用, 不污染 AI 抽屉当前会话) ----

  // 为某栋楼生成节能优化方案, 直接返回四段结构化结果 (plan + messageId)。
  // 跟 sendMessage 的区别: 这里自建一个独立会话 (scenario=optimization),
  // 只关心 optimization_plan / done / error 三个 SSE 事件, 不写进 AI 抽屉的
  // messages / streamingMessage, 避免"在详情面板点生成"污染抽屉里正在进行的对话。
  async function generateOptimization(
    buildingId: string,
    buildingName: string,
  ): Promise<{ plan: OptimizationPlan; messageId: string } | null> {
    const token = getAccessToken()
    if (!token) throw new Error('未登录或登录已过期')

    const ctx: ChatContext = {
      site_id: contextStore.siteId ?? undefined,
      site_name: contextStore.siteName ?? undefined,
      building_id: buildingId,
      building_name: buildingName,
      time_range: {
        start: contextStore.currentRange.start.toISOString(),
        end: contextStore.currentRange.end.toISOString(),
      },
      metric: contextStore.metric,
      scenario: 'optimization',
    }

    const session = await assistantApi.createSession({
      title: `${buildingName} 节能建议`,
      context: ctx,
    })

    const resp = await fetch(`/api/v1/assistant/sessions/${session.id}/messages`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({
        content: `请为 ${buildingName} 生成一份节能优化方案，包含主要问题、节能措施、预期收益和优先级排序。`,
        context: ctx,
      }),
    })

    if (!resp.ok) {
      const text = await resp.text().catch(() => '')
      throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`)
    }
    if (!resp.body) throw new Error('响应无 body')

    const reader = resp.body.getReader()
    const decoder = new TextDecoder('utf-8')
    let buffer = ''
    let plan: OptimizationPlan | null = null
    let messageId: string | null = null
    // 流里的 delta 文本。拿到 plan 时它是 summary 一句话; 没拿到 plan 时它是后端
    // 降级说明 (如 "节能优化场景需要先调用工具拿真实数据..."), 是给用户看的真实原因
    let deltaText = ''

    // 解析单个 SSE 事件, 抽 optimization_plan / done / error / delta
    const handleEvent = (raw: string) => {
      const dataLine = raw
        .split('\n')
        .map(l => l.trim())
        .filter(l => l.startsWith('data:'))
        .map(l => l.slice(5).trim())
        .join('')
      if (!dataLine) return
      let payload: Record<string, unknown>
      try {
        payload = JSON.parse(dataLine)
      } catch {
        return
      }
      if (payload.type === 'optimization_plan') {
        plan = (payload.data as OptimizationPlan) ?? null
      } else if (payload.type === 'delta') {
        deltaText += (payload.text as string) ?? ''
      } else if (payload.type === 'done') {
        messageId = (payload.message_id as string) ?? null
      } else if (payload.type === 'error') {
        throw new Error((payload.message as string) || 'AI 助手错误')
      }
    }

    // eslint-disable-next-line no-constant-condition
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      const events = buffer.split('\n\n')
      buffer = events.pop() ?? ''
      for (const evt of events) {
        if (evt.trim()) handleEvent(evt)
      }
    }
    if (buffer.trim()) handleEvent(buffer)

    if (plan && messageId) return { plan, messageId }
    // 没拿到 plan: 优先把后端降级说明抛给调用方 (真实原因, 比如 AI 没调到工具),
    // 别让调用方显示"请检查 API Key"这种猜测性文案误导用户
    if (deltaText.trim()) throw new Error(deltaText.trim())
    return null
  }

  // ---- actions: PDF 导出 ----

  async function exportPdf(messageId: string): Promise<Blob> {
    return assistantApi.exportPdf(messageId)
  }

  return {
    // state
    drawerOpen,
    sessions,
    currentSessionId,
    messages,
    streamingMessage,
    isStreaming,
    loadingSessions,
    loadingMessages,
    error,
    // getters
    allMessages,
    currentContext,
    quickQuestions,
    // actions
    openDrawer,
    closeDrawer,
    toggleDrawer,
    loadSessions,
    createSession,
    selectSession,
    loadMessages,
    sendMessage,
    generateOptimization,
    exportPdf,
  }
})
