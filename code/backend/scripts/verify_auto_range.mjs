// ============================================================================
// 用 CDP 驱动 headless Chrome: 预设 localStorage token -> 跳转 /park ->
// 等 DefaultLayout onMounted 自动检测 data-range -> 读顶栏时间选择器显示
// ----------------------------------------------------------------------------
// 不走真实登录表单, 因为登录后路由跳转 + Pinia store 初始化时序复杂.
// 直接预设 aic_access_token / aic_user 到 localStorage, 让 Pinia auth store
// 从 storage 读到登录态, 路由守卫放行 /park.
// ============================================================================

// WebSocket 在 Node 22+ 是全局对象, 不用从 undici 导
import { spawn } from 'node:child_process'

const CHROME = 'C:/Program Files/Google/Chrome/Application/chrome.exe'
const PROFILE_DIR = 'C:/Users/31258/AppData/Local/Temp/aic-chrome-profile'
const DEBUG_PORT = 9222

// 拿 fresh token + user 信息
async function fetchToken() {
  const resp = await fetch('http://localhost:8000/api/v1/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: 'demo', password: 'demo123' }),
  })
  const data = await resp.json()
  return data.data
}

// 等 Chrome 启动并暴露 CDP endpoint
async function waitForCdp() {
  for (let i = 0; i < 30; i++) {
    try {
      const resp = await fetch(`http://localhost:${DEBUG_PORT}/json/version`)
      if (resp.ok) return await resp.json()
    } catch (e) {
      // 还没起来
    }
    await new Promise((r) => setTimeout(r, 500))
  }
  throw new Error('Chrome CDP 没起来')
}

// 拿到一个可控制的 page target
async function getPageTarget() {
  const resp = await fetch(`http://localhost:${DEBUG_PORT}/json/list`)
  const targets = await resp.json()
  // 找 type=page 的 target
  return targets.find((t) => t.type === 'page')
}

function sendWs(ws, method, params = {}) {
  return new Promise((resolve, reject) => {
    const id = Math.floor(Math.random() * 1e9)
    const handler = (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.id === id) {
        ws.removeEventListener('message', handler)
        if (msg.error) reject(new Error(JSON.stringify(msg.error)))
        else resolve(msg.result)
      }
    }
    ws.addEventListener('message', handler)
    ws.send(JSON.stringify({ id, method, params }))
  })
}

async function evaluate(ws, expression) {
  const result = await sendWs(ws, 'Runtime.evaluate', {
    expression,
    awaitPromise: true,
    returnByValue: true,
  })
  return result.result.value
}

async function main() {
  console.log('[1/7] 拿 token...')
  const authData = await fetchToken()
  console.log(`  user: ${authData.user.display_name}`)

  console.log('[2/7] 启动 Chrome with --remote-debugging-port...')
  const chrome = spawn(
    CHROME,
    [
      `--remote-debugging-port=${DEBUG_PORT}`,
      `--user-data-dir=${PROFILE_DIR}`,
      '--headless=new',
      '--disable-gpu',
      '--no-sandbox',
      '--no-first-run',
      '--no-default-browser-check',
      'http://localhost:5173/',  // 先开登录页 (没 token, 路由会跳 /login)
    ],
    { stdio: 'ignore', detached: false },
  )

  try {
    console.log('[3/7] 等 CDP 就绪...')
    await waitForCdp()

    console.log('[4/7] 等 page target 加载完...')
    // 等页面加载 (默认 5 秒够 Vue 启动)
    await new Promise((r) => setTimeout(r, 5000))

    const target = await getPageTarget()
    if (!target) throw new Error('没找到 page target')
    console.log(`  target url: ${target.url}`)

    const wsUrl = target.webSocketDebuggerUrl
    const ws = new WebSocket(wsUrl)
    await new Promise((resolve, reject) => {
      ws.addEventListener('open', resolve)
      ws.addEventListener('error', reject)
    })

    console.log('[5/7] 预设 localStorage (token + user + 清空 context)...')
    const setLs = `
      localStorage.setItem('aic_access_token', ${JSON.stringify(authData.access_token)});
      localStorage.setItem('aic_refresh_token', ${JSON.stringify(authData.refresh_token)});
      localStorage.setItem('aic_user', ${JSON.stringify(JSON.stringify(authData.user))});
      // 清空 aic_context 让 DefaultLayout 走"全新用户"路径触发自动检测
      localStorage.removeItem('aic_context');
      'ok'
    `
    const setResult = await evaluate(ws, setLs)
    console.log(`  set: ${setResult}`)

    console.log('[6/7] 跳转 /park, 触发 DefaultLayout onMounted...')
    // 用 window.location 整页跳转, 让 Pinia store 重新从 localStorage init
    await evaluate(ws, `window.location.href = 'http://localhost:5173/park'; 'navigating'`)

    // 等跳转 + onMounted + loadSites + maybeAutoSetRange 全部跑完
    // listSites + getSiteDataRange 各一次 RTT, 加 Vue 启动, 8 秒够
    await new Promise((r) => setTimeout(r, 9000))

    console.log('[7/7] 读顶栏时间选择器显示 + localStorage.aic_context...')
    const finalUrl = await evaluate(ws, `window.location.href`)
    console.log(`  final url: ${finalUrl}`)

    const ctx = await evaluate(ws, `localStorage.getItem('aic_context')`)
    console.log(`  aic_context: ${ctx}`)

    const timePickerText = await evaluate(ws, `
      // 顶栏时间选择器: a-button 文本 + 自定义范围 span
      const btn = document.querySelector('.app-header__center .ant-btn span:first-child');
      const custom = document.querySelector('.app-header__center .ant-btn .custom-range');
      JSON.stringify({
        preset: btn ? btn.textContent.trim() : null,
        custom: custom ? custom.textContent.trim() : null,
        // range-picker 输入框的值
        rangeInput: document.querySelector('.ant-picker-input input')?.value || null,
        // 范围 picker 的两个输入框
        rangeStart: document.querySelectorAll('.ant-picker-input input')[0]?.value || null,
        rangeEnd: document.querySelectorAll('.ant-picker-input input')[1]?.value || null,
      })
    `)
    console.log(`  顶栏时间选择器: ${timePickerText}`)

    // 额外: 看下 /park 园区探索页是否还显示 DeveloperError 或空数据
    const parkContent = await evaluate(ws, `
      const cesium = document.querySelector('.cesium-viewer');
      const devErr = document.body.innerText.includes('DeveloperError');
      const stats = document.querySelector('.park-overview__value, .stat-card__value');
      JSON.stringify({
        hasCesiumViewer: !!cesium,
        hasDeveloperError: devErr,
        firstStatValue: stats ? stats.textContent.trim() : null,
      })
    `)
    console.log(`  园区探索页状态: ${parkContent}`)

    console.log('[8/8] 跳转 /analysis, 验证分析中心图表有数据...')
    await evaluate(ws, `window.location.href = 'http://localhost:5173/analysis'; 'navigating'`)
    // 等图表渲染 (BuildingCompare 拉数据 + ECharts init, 6 秒够)
    await new Promise((r) => setTimeout(r, 7000))

    const analysisContent = await evaluate(ws, `
      // 看分析中心的关键指标
      const statValues = Array.from(document.querySelectorAll('.stat-card__value, .kpi-card__value')).map(e => e.textContent.trim());
      // 看建筑列表 EUI 是否非 0
      const euiValues = Array.from(document.querySelectorAll('.compare-chip__value')).map(e => e.textContent.trim());
      // 看是否有 ECharts canvas/svg 渲染
      const echartsCount = document.querySelectorAll('.echarts-canvas, svg.echarts-svg, [_echarts_instance_]').length;
      const chartDivs = document.querySelectorAll('[class*="chart"], [class*="Chart"]').length;
      // 看是否有空态提示 (比如"暂无数据")
      const emptyHints = Array.from(document.querySelectorAll('.ant-empty-description, .chart-card__empty')).map(e => e.textContent.trim());
      JSON.stringify({
        statValues: statValues.slice(0, 5),
        euiValues,
        echartsCount,
        chartDivs,
        emptyHints: emptyHints.slice(0, 5),
      })
    `)
    console.log(`  分析中心状态: ${analysisContent}`)

    ws.close()
  } finally {
    console.log('  关闭 Chrome...')
    chrome.kill()
  }
}

main().catch((e) => {
  console.error('FAIL:', e)
  process.exit(1)
})
