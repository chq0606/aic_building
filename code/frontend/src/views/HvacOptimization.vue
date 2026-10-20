<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { Wind, Thermometer, ArrowUpRight, ArrowRight, Sun, Sparkles, Info, RefreshCw, Clock3 } from 'lucide-vue-next'
import type { EChartsOption } from 'echarts'
import ChartCard from '@/components/analysis/ChartCard.vue'
import { optimizationApi, type CoolingPlan, type PreviewInput, type EvidenceIndex, type HvacCase } from '@/api/optimization'

const form = reactive<PreviewInput>({ room_c: 25.5, outdoor_c: 32, lower_c: 23, upper_c: 26, internal_kw: .3, solar_kw_m2: .3 })
const presets = [
  { name: '夏日午后', room_c: 25.5, outdoor_c: 32, internal_kw: .3, solar_kw_m2: .3 },
  { name: '温和傍晚', room_c: 25, outdoor_c: 27, internal_kw: .15, solar_kw_m2: .05 },
  { name: '高温晴天', room_c: 28, outdoor_c: 36, internal_kw: .5, solar_kw_m2: .6 },
]
const result = ref<CoolingPlan | null>(null), busy = ref(false), error = ref('')
const evidence = ref<EvidenceIndex | null>(null), caseData = ref<HvacCase | null>(null)
const caseId = ref(''), caseLoading = ref(false), evidenceError = ref('')
let caseRequest = 0
const n = (v: number | null | undefined, digits = 2) => v == null ? '—' : v.toFixed(digits)
const invalid = computed(() => {
  if (Object.values(form).some(v => !Number.isFinite(v))) return '请完整填写有效的温度和环境条件。'
  if (form.upper_c - form.lower_c <= 1) return '舒适温度上下限需相差超过1℃，例如23—26℃。'
  if (form.room_c < 15 || form.room_c > 30 || form.outdoor_c < -20 || form.outdoor_c > 45 || form.lower_c < 15 || form.lower_c > 26 || form.upper_c < 18 || form.upper_c > 30) return '温度超出示例支持范围，请调整后重试。'
  return ''
})
const changed = computed(() => result.value && Object.keys(form).some(k => form[k as keyof PreviewInput] !== result.value!.inputs[k as keyof PreviewInput]))
const recommended = computed(() => result.value?.policies.recommended)
const regular = computed(() => result.value?.policies.regular)
const actionText = computed(() => {
  const a = recommended.value?.first_action
  if (!a) return '等待生成建议'
  if (a.action === 'idle') return '当前可暂缓制冷'
  return a.fan_fraction < .3 ? '当前采用轻柔制冷' : a.fan_fraction < .65 ? '当前采用适中制冷' : '当前先加强制冷'
})
async function calculate() {
  if (invalid.value || busy.value) return
  const inputs = { ...form }
  busy.value = true; error.value = ''
  try { result.value = await optimizationApi.plan(inputs) }
  catch (e) { error.value = e instanceof Error ? e.message : '建议生成失败，请重试。' }
  finally { busy.value = false }
}
function choosePreset(p: typeof presets[number]) {
  const { name: _name, ...conditions } = p
  Object.assign(form, conditions)
}
async function loadCase() {
  const ticket = ++caseRequest
  caseLoading.value = true; evidenceError.value = ''; caseData.value = null
  try { const data = await optimizationApi.case(caseId.value); if (ticket === caseRequest) caseData.value = data }
  catch (e) { if (ticket === caseRequest) evidenceError.value = e instanceof Error ? e.message : '案例加载失败' }
  finally { if (ticket === caseRequest) caseLoading.value = false }
}
async function loadEvidence() {
  caseLoading.value = true; evidenceError.value = ''
  try {
    evidence.value = await optimizationApi.evidence()
    caseId.value = evidence.value.cases[0]?.id ?? ''
    if (caseId.value) await loadCase()
    else { evidenceError.value = '暂无已保存的仿真案例。'; caseLoading.value = false }
  } catch (e) { evidenceError.value = e instanceof Error ? e.message : '案例加载失败'; caseLoading.value = false }
}
onMounted(() => { void calculate(); void loadEvidence() })
const trend = computed<EChartsOption>(() => ({
  color: ['#36765c', '#b9a37a'], tooltip: { trigger: 'axis', valueFormatter: v => `${Number(v).toFixed(2)} ℃` },
  legend: { bottom: 0, data: ['节能方案', '普通运行'] }, grid: { left: 45, right: 32, top: 25, bottom: 55 },
  xAxis: { type: 'value', min: 0, max: 60, name: '分钟', nameLocation: 'end', nameGap: 8 },
  yAxis: { type: 'value', name: '室温 / ℃', scale: true, min: v => Math.floor(Math.min(v.min, result.value?.inputs.lower_c ?? 23)-.4), max: v => Math.ceil(Math.max(v.max, result.value?.inputs.upper_c ?? 26)+.4) },
  series: (['recommended', 'regular'] as const).map((key, i) => ({
    name: i ? '普通运行' : '节能方案', type: 'line', showSymbol: false,
    data: result.value?.policies[key].series.map(p => [p.minute, p.room_c]) ?? [],
    lineStyle: { width: i ? 1.8 : 3, type: i ? 'dashed' : 'solid' },
    ...(i ? {} : { markArea: { silent: true, itemStyle: { color: '#36765c0d' }, data: [[{ yAxis: result.value?.inputs.lower_c ?? 23 }, { yAxis: result.value?.inputs.upper_c ?? 26 }]] },
      markLine: { silent: true, symbol: 'none', lineStyle: { color: '#a5b59d', type: 'dotted' }, label: { position: 'insideEndTop', formatter: '舒适边界' }, data: [{ yAxis: result.value?.inputs.lower_c ?? 23 }, { yAxis: result.value?.inputs.upper_c ?? 26 }] } }),
  })),
}))
const energyTrend = computed<EChartsOption>(() => ({
  color: ['#36765c', '#b9a37a'], tooltip: { trigger: 'axis', valueFormatter: v => `${Number(v).toFixed(3)} 度` },
  legend: { bottom: 0 }, grid: { left: 52, right: 36, top: 25, bottom: 55 },
  xAxis: { type: 'value', max: 60, name: '分钟', nameLocation: 'end', nameGap: 8 },
  yAxis: { type: 'value', name: '预计累计用电 / 度' },
  series: (['recommended', 'regular'] as const).map((key, i) => ({ name: i ? '普通运行' : '节能方案', type: 'line', showSymbol: false,
    data: result.value?.policies[key].series.map(p => [p.minute, p.electricity_kwh]) ?? [], lineStyle: { width: i ? 1.8 : 3, type: i ? 'dashed' : 'solid' } })),
}))
const caseSaving = computed(() => {
  const f = caseData.value?.policies.final?.metrics, s = caseData.value?.policies.simple?.metrics
  return f?.strict_comfort_gate_passed && s?.strict_comfort_gate_passed && s.hvac_terminal_energy_kwh > 0 ? 100*(1-f.hvac_terminal_energy_kwh/s.hvac_terminal_energy_kwh) : null
})
</script>

<template>
  <main class="hvac-page">
    <header class="hero">
      <div><span class="eyebrow"><Wind :size="16" /> 空调节能助手</span><h1>舒服一点，也省一点。</h1><p>告诉我们你觉得舒适的温度，看看未来一小时可以怎样运行空调。</p>
        <div class="hero-tags"><span><Thermometer :size="14" /> 自定舒适范围</span><span><Clock3 :size="14" /> 预看未来60分钟</span></div></div>
      <div class="hero-device" aria-hidden="true"><Wind :size="38" stroke-width="1.2" /><span>YOUR COMFORT, FIRST</span><strong>{{ result ? `${result.inputs.lower_c}—${result.inputs.upper_c}` : '23—26' }}<small>℃</small></strong><b>由你定义舒适</b></div>
    </header>
    <p class="scope"><Info :size="15" /> 仿真体验 · 未连接真实空调；所有用电与温度结果均为预计值。本页使用独立示例房间，不随顶部园区和日期变化。</p>
    <div class="workspace">
      <section class="settings panel">
        <div class="section-label">01 / 你的舒适偏好</div><h2>从现在的房间开始</h2><p class="muted">选择一个场景，也可以自己修改。</p>
        <div class="presets"><button v-for="p in presets" :key="p.name" @click="choosePreset(p)"><Sun :size="14" />{{ p.name }}</button></div>
        <div class="field-grid"><label>当前室温 <small>℃</small><input v-model.number="form.room_c" aria-label="当前室温" type="number" min="15" max="30" step="0.5" /></label><label>室外温度 <small>℃</small><input v-model.number="form.outdoor_c" aria-label="室外温度" type="number" min="-20" max="45" step="1" /></label></div>
        <fieldset class="comfort"><legend><Thermometer :size="15" /> 我觉得舒适的温度</legend><div class="field-grid"><label>最低 <small>℃</small><input v-model.number="form.lower_c" aria-label="舒适温度下限" type="number" min="15" max="26" step="0.5" /></label><label>最高 <small>℃</small><input v-model.number="form.upper_c" aria-label="舒适温度上限" type="number" min="18" max="30" step="0.5" /></label></div><p>算法会在这个范围内比较运行方案。</p></fieldset>
        <details class="environment"><summary>更多环境条件</summary><div class="field-grid"><label>室内活动<select v-model.number="form.internal_kw"><option :value=".15">较少</option><option :value=".3">一般</option><option :value=".5">较多</option></select></label><label>日照情况<select v-model.number="form.solar_kw_m2"><option :value=".05">较弱</option><option :value=".3">适中</option><option :value=".6">较强</option></select></label></div></details>
        <p v-if="invalid" class="validation" role="alert">{{ invalid }}</p>
        <button class="primary" :disabled="busy || !!invalid" @click="calculate"><RefreshCw v-if="busy" :size="17" class="spin" /><Sparkles v-else :size="17" />{{ busy ? '正在比较运行方案…' : '生成节能建议' }}<ArrowRight v-if="!busy" :size="17" /></button>
        <small class="button-note">温度曲线与用电对比会一起更新。</small>
      </section>
      <section class="results">
        <div v-if="error" class="notice error" role="alert">{{ error }}<button :disabled="busy || !!invalid" @click="calculate">重新生成</button></div>
        <div v-if="changed" class="notice" role="status">{{ busy ? '正在根据新条件计算，下方暂显示上一次结果。' : '条件已修改。下方仍是上一次结果，请点击“生成节能建议”更新。' }}</div>
        <article class="recommendation panel" :aria-busy="busy">
          <div class="result-heading"><span class="section-label">02 / 未来一小时的建议</span><span class="estimate-badge">模型预计</span></div>
          <template v-if="result && recommended"><h2>{{ actionText }}</h2><p>目标舒适范围为 <b>{{ n(result.inputs.lower_c, 1) }}—{{ n(result.inputs.upper_c, 1) }}℃</b>，比较随温度变化的制冷方案。</p>
            <div class="reading-grid"><div><span>方案预计用电</span><strong>{{ n(recommended.electricity_kwh, 3) }}<small>度</small></strong><p>普通运行 {{ n(regular?.electricity_kwh, 3) }} 度</p></div>
              <div><span>{{ result.estimated_saving_percent == null ? '舒适情况' : result.estimated_saving_percent >= 0 ? '同等温度要求下预计少用' : '预计多用' }}</span><strong :class="{ amber: result.estimated_saving_percent != null && result.estimated_saving_percent < 0 }">{{ result.estimated_saving_percent == null ? `${recommended.in_range_minutes}/60` : n(Math.abs(result.estimated_saving_percent), 1) }}<small>{{ result.estimated_saving_percent == null ? '分钟' : '%' }}</small></strong><p>{{ result.estimated_saving_percent == null ? '预测室温落在舒适范围内' : result.estimated_saving_percent >= 0 ? `约少用 ${n(result.electricity_difference_kwh, 3)} 度` : '本次未显示节电优势' }}</p></div></div>
            <div class="action-row"><Wind :size="20" /><div><b>{{ recommended.first_action.action === 'idle' ? '当前可暂缓制冷，随后按温度变化调整' : `当前模拟风量约 ${n(recommended.first_action.fan_fraction * 100, 0)}%` }}</b><small>这是示例系统的风量建议，不对应真实遥控器档位。</small></div></div>
            <p v-if="!result.comparable_comfort" class="caution">本次包含温度未在舒适范围的时段，因此不显示“同等舒适下节电比例”。请结合下方曲线查看降温过程。</p>
            <p class="result-context">本次条件：室温 {{ result.inputs.room_c }}℃ · 室外 {{ result.inputs.outdoor_c }}℃ · 固定示例房间</p>
          </template>
          <div v-else class="empty"><Wind :size="38" /><h2>{{ busy ? '正在为你比较方案' : '设置好温度，就可以开始' }}</h2><p>生成后可查看温度走势、运行建议和预计用电。</p></div>
        </article>
        <ChartCard title="这一小时，房间会有多凉？" subtitle="绿色浅色带为你选择的舒适范围；两条线均为模型预测。" :option="trend" :height="255" :loading="busy" loading-text="正在计算温度变化" :empty="!result" empty-text="生成建议后查看室温走势" />
      </section>
    </div>
    <section class="comparison">
      <div class="comparison-heading"><div class="section-label">03 / 看清用电差别</div><h2>相同的天气，两种运行方式。</h2><p>普通运行接近温度边界时再调整；节能方案提前比较未来温度和用电。</p></div>
      <ChartCard title="预计累计用电" subtitle="包含示例系统的制冷与风机用电，计算覆盖完整60分钟。" :option="energyTrend" :height="250" :loading="busy" :empty="!result" empty-text="生成建议后查看用电对比" />
    </section>
    <details class="explanation panel"><summary>使用说明与验证进度 <Info :size="16" /></summary><div class="details-content">
      <h3>怎样使用</h3><p>选场景 → 调整舒适温度 → 生成建议。修改条件后需要重新生成；图表对应生成时的条件。调宽舒适范围不一定在每次试算中都更省电，请看本次完整结果。</p>
      <h3>这些数字能说明什么</h3><p>交互页沿用现有算法和固定示例房间模型，在相同初始条件下计算60分钟。天气和活动量暂按恒定条件处理，空气与墙体初始温度暂设相同。预计值没有经过真实空调执行验证，也不是一次新的独立物理仿真。普通运行是项目中的简单反馈方案，不代表某品牌空调的出厂控制。</p>
      <h3>目前做到哪一步</h3><p>已完成独立物理仿真对照。已检查国内外多组真实设备记录，但尚未完成“保持舒适且节电”的真实设备联合验收。本页不显示已实现节电、实际减排或全局最优；也未接入原系统虚拟楼层与虚拟设备作为真实证据。</p>
      <details class="saved-cases"><summary>查看已完成的物理仿真案例</summary><p>这是此前保留的独立物理仿真结果，与上面的交互预计值分开。</p><label>选择仿真案例<select v-model="caseId" @change="loadCase"><option v-for="c in evidence?.cases ?? []" :key="c.id" :value="c.id">{{ c.hours }}小时 · 起始日 {{ c.day }} · {{ c.stage === 'robustness01' ? '天气预报简化' : '同条件对照' }}</option></select></label>
        <p v-if="evidenceError" class="validation" role="alert">{{ evidenceError }} <button @click="loadEvidence">重新加载案例</button></p><p v-else-if="caseLoading">案例加载中…</p>
        <template v-else-if="caseData"><p>本例独立物理仿真的完整周期节能：<b>{{ caseSaving == null ? '温度条件不同时不比较比例' : `${n(caseSaving, 1)}%` }}</b>。总能源包含制冷电、风机电与供热燃气，不能全部称为用电。</p><div class="table-wrap"><table><thead><tr><th>运行方式</th><th>总能源 / kWh</th><th>温度检查</th></tr></thead><tbody><tr v-for="key in ['final', 'simple']" :key="key"><td>{{ key === 'final' ? '节能方案' : '普通反馈' }}</td><td>{{ n(caseData.policies[key]?.metrics.hvac_terminal_energy_kwh, 3) }}</td><td>{{ caseData.policies[key]?.metrics.strict_comfort_gate_passed ? '通过' : '未通过' }}</td></tr></tbody></table></div></template>
      </details>
      <div class="source-links"><a v-for="s in evidence?.sources ?? []" :key="s.url" :href="s.url" target="_blank" rel="noopener noreferrer">{{ s.title }}<ArrowUpRight :size="12" /></a></div>
    </div></details>
  </main>
</template>

<style scoped lang="scss">
.hvac-page{max-width:1440px;margin:auto;padding:26px 30px 40px;color:#30463b;background:#f5f4ee;min-height:100%}button,input,select{font:inherit}button{cursor:pointer}button:disabled{cursor:wait;opacity:.6}button:focus-visible,summary:focus-visible{outline:2px solid #b68435;outline-offset:4px}.hero{display:flex;align-items:center;justify-content:space-between;gap:30px;background:linear-gradient(115deg,#e6ede2,#faf8f0);border:1px solid #dce2d5;border-radius:18px;padding:36px 42px}.eyebrow,.hero-tags,.hero-tags span{display:flex;align-items:center;gap:8px}.eyebrow{font-size:13px;color:#53765e}.hero h1{font-size:38px;font-weight:550;letter-spacing:1px;margin:16px 0 12px}.hero p{font-size:14px;color:#6d7f70;margin:0;line-height:1.8}.hero-tags{gap:22px;margin-top:24px;font-size:12px;color:#59765f}.hero-device{display:flex;flex-direction:column;align-items:center;min-width:230px;background:#ffffff85;border:1px solid #d7dfd0;border-radius:16px;padding:22px 32px;color:#56816b;box-shadow:0 10px 35px #4c644806}.hero-device>span{font-size:9px;letter-spacing:2px;margin:8px 0}.hero-device strong{font-size:38px;font-weight:450}.hero-device small{font-size:15px;margin-left:4px}.hero-device b{font-size:11px;font-weight:400;color:#879180;margin-top:3px}.scope{display:flex;align-items:flex-start;gap:8px;font-size:12px;line-height:1.8;color:#7c8377;margin:16px 2px 23px}.scope svg{flex-shrink:0;margin-top:3px}.workspace{display:grid;grid-template-columns:350px minmax(0,1fr);gap:22px}.panel{background:#fffefa;border:1px solid #e0e2d7;border-radius:14px}.settings{padding:26px;height:fit-content}.section-label{font-size:10px;letter-spacing:1.4px;color:#718374}h2{font-size:23px;font-weight:500;margin:12px 0;color:#344d3d}.muted{font-size:12px;color:#8a9083;line-height:1.8}.presets{display:flex;gap:6px;margin:20px 0}.presets button{display:flex;align-items:center;gap:4px;padding:8px 6px;font-size:11px;border:1px solid #e1e5d8;border-radius:7px;background:#f7f8f1;color:#6d7e65}.presets button:hover{border-color:#789c80;background:#eef4e9}.field-grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}label{display:block;color:#74816d;font-size:12px;line-height:1.8}label small{float:right;font-size:11px;color:#98a08f}input,select{display:block;box-sizing:border-box;width:100%;min-width:0;margin-top:7px;padding:11px 10px;border:1px solid #dce2d3;border-radius:7px;background:white;color:#3a5544;outline:none}input:focus,select:focus{border-color:#68927a;box-shadow:0 0 0 3px #65907710}input{font-size:20px}.comfort{margin:22px 0 15px;padding:17px 13px 10px;border:1px solid #d9e1ce;border-radius:10px;background:#f6f8f0}.comfort legend{display:flex;align-items:center;gap:6px;padding:0 5px;font-size:12px;color:#587253}.comfort p{font-size:10px;color:#89927d;margin:12px 0 2px}.environment{font-size:12px;color:#77846f;padding:8px 0 20px}.environment .field-grid{margin-top:15px}summary{cursor:pointer;line-height:1.8}.primary{display:flex;align-items:center;justify-content:center;gap:10px;width:100%;padding:14px 10px;border:0;border-radius:8px;background:#397358;color:white;font-size:13px}.primary>svg:last-child{margin-left:auto}.primary:hover{background:#2e6149}.button-note{display:block;margin-top:11px;text-align:center;color:#9b9e90;font-size:10px}.results{display:flex;flex-direction:column;gap:18px;min-width:0}.recommendation{padding:25px 29px;background:linear-gradient(135deg,#f2f6ec,#fffef9)}.result-heading{display:flex;align-items:center;justify-content:space-between;gap:10px}.estimate-badge{font-size:10px;padding:4px 9px;border:1px solid #d6dfcc;border-radius:20px;color:#78906e}.recommendation h2{font-size:26px;margin:15px 0 9px}.recommendation>p{font-size:12px;line-height:1.9;color:#7e8b75}.recommendation b{font-weight:500;color:#4c6c4d}.reading-grid{display:grid;grid-template-columns:1fr 1fr;margin:22px 0}.reading-grid>div+div{border-left:1px solid #dce4d4;padding-left:25px}.reading-grid span{font-size:11px;color:#7b8973}.reading-grid strong{display:block;font-size:34px;font-weight:450;color:#36654c;line-height:1.4;margin-top:5px}.reading-grid strong.amber{color:#a87931}.reading-grid small{font-size:12px;margin-left:6px}.reading-grid p{font-size:11px;color:#929887;margin:3px 0 0}.action-row{display:flex;align-items:center;gap:12px;padding-top:17px;border-top:1px solid #e0e6d6;color:#719264}.action-row b{display:block;font-size:12px}.action-row small{display:block;font-size:10px;color:#939b85;margin-top:5px}.recommendation .result-context{font-size:10px;margin:14px 0 0;color:#979b8b}.caution{background:#f5ecdc;padding:10px;border-radius:7px;color:#987239!important}.notice{display:flex;align-items:center;justify-content:space-between;gap:10px;background:#f6eddc;border-radius:8px;padding:12px 16px;font-size:12px;color:#947746;line-height:1.8}.notice.error{background:#f8e9e2;color:#b26445}.notice button,.validation button{border:0;background:transparent;color:inherit;text-decoration:underline;flex-shrink:0}.validation{font-size:12px;line-height:1.8;color:#b86942}.empty{text-align:center;padding:32px 10px;color:#8c9b80}.empty h2{font-size:22px}.empty p{font-size:12px}.comparison{display:grid;grid-template-columns:280px minmax(0,1fr);gap:30px;margin:25px 0;align-items:center}.comparison-heading{padding:10px 4px}.comparison h2{font-size:24px;line-height:1.6}.comparison p{font-size:12px;line-height:1.9;color:#8c9181}.explanation{padding:18px 24px;font-size:12px;color:#7a8571}.explanation>summary{display:flex;align-items:center;justify-content:space-between;color:#687e60;list-style:none}.explanation>summary::before{content:'＋';margin-right:10px}.explanation[open]>summary::before{content:'−'}.explanation>summary svg{margin-left:auto}.details-content{padding-top:12px}.details-content h3{font-size:13px;font-weight:500;color:#5e7755;margin:19px 0 6px}.details-content p{line-height:1.9;margin:8px 0}.saved-cases{margin-top:20px;padding-top:15px;border-top:1px solid #e2e6d9}.saved-cases select{max-width:440px}.source-links{display:flex;flex-wrap:wrap;gap:10px 20px;margin-top:20px}.source-links a{display:flex;align-items:center;gap:4px;color:#63835c;font-size:11px}.table-wrap{overflow-x:auto}table{width:100%;border-collapse:collapse;margin:15px 0;font-size:12px}th,td{text-align:left;padding:10px;border-bottom:1px solid #e4e7dd}th{font-weight:500}.spin{animation:spin 1.5s linear infinite}@keyframes spin{to{transform:rotate(360deg)}}@media(max-width:1100px){.hvac-page{padding:20px}.workspace{grid-template-columns:310px minmax(0,1fr)}.hero{padding:28px}.hero h1{font-size:31px}.settings{padding:20px}.presets{flex-wrap:wrap}.comparison{grid-template-columns:230px minmax(0,1fr)}}@media(max-width:850px){.workspace{grid-template-columns:1fr}.hero-device{display:none}.comparison{grid-template-columns:1fr;gap:0}.comparison-heading{padding:12px 0}.hero h1{font-size:29px}.recommendation{padding:22px}.hvac-page{padding:16px}.hero{padding:24px}.scope{font-size:11px}.reading-grid strong{font-size:30px}}@media(prefers-reduced-motion:reduce){.spin{animation:none}}
</style>
