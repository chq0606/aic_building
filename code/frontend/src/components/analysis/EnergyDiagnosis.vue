<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRouter } from 'vue-router'
import { ArrowUpRight, ShieldCheck, ScanLine, Play, RefreshCw, Info, ChevronRight } from 'lucide-vue-next'
import { message } from 'ant-design-vue'
import type { EChartsOption } from 'echarts'
import { useContextStore } from '@/stores/context'
import { optimizationApi, type Diagnosis, type DiagnosisCase } from '@/api/optimization'
import ChartCard from './ChartCard.vue'

const context = useContextStore(), router = useRouter()
const data = ref<Diagnosis | null>(null), selected = ref(''), loading = ref(false), running = ref(false), error = ref('')
const detail = ref<DiagnosisCase | null>(null)
let request = 0
const shortName = (code: string) => code.split('_').slice(2).join(' ')
async function load() {
  const site = context.siteId, ticket = ++request
  if (!site) { data.value = null; return }
  loading.value = true; error.value = ''
  try {
    const r = context.currentRange
    const result = await optimizationApi.diagnosis({ site_id: site, ...(selected.value ? { building_id: selected.value } : {}), start: r.start.format('YYYY-MM-DDTHH:mm:ss'), end: r.end.add(1, 'second').format('YYYY-MM-DDTHH:mm:ss') })
    if (ticket !== request) return
    data.value = result
    if (!selected.value && result.building) selected.value = result.building.id
  } catch (e) { if (ticket === request) error.value = e instanceof Error ? e.message : '诊断加载失败' }
  finally { if (ticket === request) loading.value = false }
}
async function run() {
  if (!context.siteId) return
  running.value = true
  try { await optimizationApi.run(context.siteId); await load(); message.success('诊断完成，证据与排序已更新') }
  catch (e) { error.value = e instanceof Error ? e.message : '诊断失败' }
  finally { running.value = false }
}
watch(() => context.siteId, () => { selected.value = ''; void load() }, { immediate: true })
watch(() => [context.currentRange.start.valueOf(), context.currentRange.end.valueOf()], () => { void load() })
watch(selected, () => { detail.value = null; void load() })
const tasks = computed(() => [...(data.value?.priorities ?? [])].sort((a, b) => (b.evidence_score_kwh ?? 0) - (a.evidence_score_kwh ?? 0)))
const format = (n: number | null | undefined, digits = 1) => n == null ? '—' : n.toLocaleString('zh-CN', { maximumFractionDigits: digits })
const time = (value: string) => value.replace('T', ' ').slice(0, 16)
const option = computed<EChartsOption>(() => {
  const h = data.value?.hourly ?? []
  return { tooltip: { trigger: 'axis' }, legend: { bottom: 0 }, grid: { left: 58, right: 20, top: 20, bottom: 52 },
    xAxis: { type: 'category', data: h.map(x => time(x.time)), axisLabel: { hideOverlap: true } },
    yAxis: { type: 'value', name: '小时电量 / kWh' },
    series: [
      { name: '较低用电参照', type: 'line', data: h.map(x => x.q20), showSymbol: false, lineStyle: { color: '#b9c9c2', type: 'dashed', width: 1 } },
      { name: '偏高筛查线', type: 'line', data: h.map(x => x.q80), showSymbol: false, lineStyle: { color: '#bd8b38', type: 'dashed', width: 1.5 } },
      { name: '中间用电参照', type: 'line', data: h.map(x => x.q50), showSymbol: false, lineStyle: { color: '#9ca9aa', width: 1 } },
      { name: '实际电量', type: 'line', data: h.map(x => x.y), showSymbol: false, lineStyle: { color: '#32745f', width: 2 }, areaStyle: { color: 'rgba(61,126,106,.07)' } },
    ] }
})
</script>

<template>
  <section class="diagnosis" aria-label="楼栋节能诊断">
    <header class="diagnosis__header">
      <div><span class="eyebrow"><ScanLine :size="14" /> 楼栋用电诊断</span><h2>哪些时段用电偏高？先从这里查起。</h2><p>结合天气和历史用电，找出值得检查的时段，按优先级安排核查。</p></div>
      <div class="diagnosis__controls">
        <select v-model="selected" aria-label="诊断建筑" :disabled="loading || running"><option value="">选择已验证建筑</option><option v-for="b in data?.supported_buildings ?? []" :key="b.id" :value="b.id">{{ shortName(b.code) }} · {{ b.code.includes('education') ? '教学楼' : b.code.includes('office') ? '办公楼' : '宿舍楼' }}</option></select>
        <button class="primary" :disabled="running || !data?.supported_buildings.length" @click="run"><RefreshCw v-if="running" :size="14" class="spin" /><Play v-else :size="14" />{{ running ? '正在计算证据…' : '运行诊断' }}</button>
      </div>
    </header>
    <div v-if="error" class="notice error">{{ error }} <button @click="load">重试加载</button></div>
    <div v-else-if="data?.status !== 'ready'" class="diagnosis__empty"><ShieldCheck :size="25" /><div><strong>{{ data?.message ?? '正在读取园区诊断…' }}</strong><p>当前冻结版本验证了Coleman、Alma、Melaine三栋BDG2建筑。其他建筑仍保留在原分析流程中。</p></div></div>
    <template v-else>
      <div class="diagnosis__metrics"><div><span>完成时段</span><strong>{{ data.case_count }}</strong><small>六小时一段</small></div><div><span>进入核查候选</span><strong>{{ data.candidate_count }}</strong><small>多层证据均满足</small></div><div><span>每周优先任务</span><strong>{{ tasks.length }}</strong><small>全园区每周最多3个名额</small></div><div class="diagnosis__proof"><ShieldCheck :size="18" /><span>{{ data.source_match ? '来源数据与已验证版本一致' : '来源已变化，验证结论不沿用' }}<small>仅用当时已完成记录形成证据</small></span></div></div>
      <div class="diagnosis__body">
        <ChartCard title="实际用电与历史参照" subtitle="选定范围内最近7天 · 可诊断2017年7—12月 · 超过筛查线不等于故障" :option="option" :height="245" :loading="loading" :empty="!data.hourly?.length" />
        <aside class="diagnosis__tasks">
          <div class="list-title"><h3>优先核查清单</h3><span>{{ tasks.length }} 项</span></div>
          <div v-if="!tasks.length" class="no-task">选定范围内没有发布的优先任务。证据不足时，算法会保留判断。</div>
          <div v-else :key="data.building?.id" class="task-list" role="region" aria-label="优先核查任务列表" tabindex="0">
            <button v-for="task in tasks" :key="task.case_id" class="task" @click="detail = task"><span class="task__rank">{{ task.weekly_rank }}</span><span><strong>{{ time(task.start) }}</strong><small>偏高证据量 {{ format(task.evidence_score_kwh) }} kWh · 查看来源</small></span><ChevronRight :size="16" /></button>
          </div>
          <p v-if="tasks.length > 4" class="scroll-hint">上下滚动查看全部 {{ tasks.length }} 项，点击可查看详情。</p>
          <p class="fine"><Info :size="13" /> 偏高证据量用于核查排序，不是已经节省的电量。</p>
        </aside>
      </div>
    </template>
    <footer class="diagnosis__footer"><span>清单用于安排人工检查；空调助手可体验不同温度条件下的预计运行效果。</span><button @click="router.push('/hvac-optimization')">体验空调节能助手 <ArrowUpRight :size="14" /></button></footer>
    <a-modal :open="!!detail" title="核查任务的证据" :footer="null" @cancel="detail = null">
      <template v-if="detail"><p>{{ time(detail.start) }} — {{ time(detail.end) }}</p><p><strong>{{ detail.status_plain }}</strong> · 六小时实际电量 {{ format(detail.energy) }} kWh</p><div v-for="(ref, key) in detail.comparisons" :key="key" class="evidence-row"><strong>{{ key === 'historic_2016' ? '去年相似天气与时段' : '本时段之前的近期参照' }}</strong><p>匹配 {{ ref.n }} 段；中位电量 {{ format(ref.median_kwh) }} kWh；{{ format((ref.fraction_strictly_lower ?? 0) * 100, 0) }}%参照低于本次。</p></div><p class="fine">该任务需要人工核查原因；没有设备动作、舒适状态和实测节能量的证据，不会自动生成已实现节能结论。</p></template>
    </a-modal>
  </section>
</template>

<style scoped lang="scss">
.diagnosis{background:#fffdf8;border:1px solid #d8dfd5;border-radius:12px;overflow:hidden;box-shadow:0 3px 16px #263c2e06}.diagnosis__header{display:flex;justify-content:space-between;gap:24px;align-items:center;padding:25px 28px;background:linear-gradient(105deg,#eff3eb,#fcfaf4)}.eyebrow{display:flex;gap:8px;align-items:center;color:#43715d;font-size:12px;letter-spacing:1px}h2{font-size:23px;line-height:1.4;margin:10px 0 8px;color:#283b31;font-weight:600}p{margin:0;color:#7c8178;font-size:13px}.diagnosis__controls{display:flex;gap:10px;align-items:center}select{padding:10px;border:1px solid #d9dcd3;border-radius:6px;background:#fff;color:#37483f;font:inherit;max-width:230px}button{font:inherit;cursor:pointer}.primary{display:flex;gap:7px;align-items:center;background:#376f59;color:#fff;border:0;border-radius:6px;padding:11px 16px;white-space:nowrap}button:disabled{opacity:.55;cursor:wait}.diagnosis__metrics{display:grid;grid-template-columns:repeat(3,1fr) 1.65fr;gap:20px;padding:20px 28px;border-bottom:1px solid #ecece2}.diagnosis__metrics>div{display:flex;flex-direction:column;gap:6px}.diagnosis__metrics span{font-size:12px;color:#7a8077}.diagnosis__metrics strong{font-size:26px;font-weight:600;color:#2c5340}.diagnosis__metrics small{font-size:11px;color:#95998f}.diagnosis__metrics .diagnosis__proof{flex-direction:row;align-items:center;color:#3d7e6a;gap:10px}.diagnosis__proof small{display:block;margin-top:7px}.diagnosis__body{display:grid;grid-template-columns:1.65fr 1fr;gap:20px;padding:20px 24px}.diagnosis__tasks{padding:0 4px}.list-title{display:flex;justify-content:space-between;align-items:center}h3{font-size:14px;font-weight:600;color:#38483e;margin:3px 0 12px}.list-title>span{font-size:12px;color:#879081}.task{display:flex;width:100%;gap:12px;align-items:center;text-align:left;padding:12px 0;border:0;border-bottom:1px solid #ebece5;background:none;color:#435245}.task>span:nth-child(2){flex:1}.task strong{display:block;font-size:12px;font-weight:500}.task small{display:block;margin-top:5px;font-size:11px;color:#8a8f81}.task__rank{display:grid;place-items:center;width:25px;height:29px;background:#edf1e7;border-radius:4px;color:#568064}.task:hover{background:#f4f5ed}.fine{font-size:11px;color:#96988a;line-height:1.7;display:flex;gap:6px;margin-top:13px}.diagnosis__footer{display:flex;justify-content:space-between;align-items:center;background:#f7f6ef;padding:12px 28px;font-size:12px;color:#898d7e}.diagnosis__footer button{display:flex;gap:7px;align-items:center;background:none;border:0;color:#45755c}.diagnosis__empty{display:flex;gap:16px;padding:30px;color:#6f826d}.diagnosis__empty strong{font-size:14px}.diagnosis__empty p{margin-top:8px}.no-task{padding:30px 0;font-size:13px;color:#939589;line-height:1.9}.notice{padding:12px 24px}.error{color:#a04c36}.evidence-row{margin:18px 0;padding:12px;background:#f5f5ed}.spin{animation:turn 1s linear infinite}@keyframes turn{to{transform:rotate(360deg)}}@media(max-width:1050px){.diagnosis__header{align-items:flex-start;flex-direction:column}.diagnosis__body{grid-template-columns:1fr}.diagnosis__metrics{grid-template-columns:repeat(3,1fr)}.diagnosis__proof{grid-column:1/-1}.diagnosis__footer{gap:12px;flex-wrap:wrap}}@media(max-width:600px){.diagnosis__header,.diagnosis__metrics{padding:18px}.diagnosis__controls{flex-wrap:wrap}h2{font-size:20px}}
.task-list{max-height:240px;overflow-y:auto;overscroll-behavior:contain;scrollbar-gutter:stable;scrollbar-width:thin;scrollbar-color:#9eb49e #f0f2e9;padding-right:6px}
.task-list:focus-visible{outline:2px solid #73937a;outline-offset:3px;border-radius:4px}
.task-list::-webkit-scrollbar{width:7px}
.task-list::-webkit-scrollbar-track{background:#f0f2e9;border-radius:6px}
.task-list::-webkit-scrollbar-thumb{background:#9eb49e;border-radius:6px}
.task-list .task:focus-visible{outline:2px solid #73937a;outline-offset:-2px}
.scroll-hint{font-size:11px;color:#7e8978;line-height:1.7;margin-top:7px}
</style>
