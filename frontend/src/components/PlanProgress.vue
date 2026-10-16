<script setup lang="ts">
import RevealText from './RevealText.vue'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { PhArrowLeft, PhCheck, PhArrowClockwise, PhWarningCircle } from '@phosphor-icons/vue'
import { planStages, type PlanProgressState } from '../planProgress'

const props = defineProps<{ progress: PlanProgressState; elapsed: number; error: string; busy: boolean; output: string; think?: string; thinking?: { tag: string; started: number; chars: number } | null }>()
const emit = defineEmits<{ back: []; retry: [] }>()
const heading = ref<HTMLElement | null>(null)
const expanded = ref(false)
const outputPane = ref<HTMLElement>()
const thinkPane = ref<HTMLElement>()
function follow(pane: HTMLElement | undefined) {
  return async () => {
    const atBottom = pane && pane.scrollHeight - pane.scrollTop - pane.clientHeight < 60
    await nextTick()
    if (pane && atBottom) pane.scrollTop = pane.scrollHeight
  }
}
watch(() => props.output, () => follow(outputPane.value)())
// 思考内容增长很快，始终贴着底部看最新一段
watch([() => props.think, expanded], async () => { await nextTick(); if (thinkPane.value) thinkPane.value.scrollTop = thinkPane.value.scrollHeight })
const current = computed(() => props.progress.active === 'repair' ? '正在调整有冲突的安排' : props.progress.active === 'refine' ? '正在按合理性指标精修行程' :
  planStages.find(stage => stage.id === props.progress.active)?.title ?? '正在连接规划服务')
// elapsed 每秒变化，借它让思考秒数随之刷新
const thinkingSeconds = computed(() => (props.elapsed, props.thinking ? Math.max(1, Math.round((Date.now() - props.thinking.started) / 1000)) : 0))
const thinkingLine = computed(() => props.thinking && props.busy ? `大模型思考中 · ${thinkingSeconds.value} 秒 · 约 ${props.thinking.chars.toLocaleString()} 字` : '')
// 每个正在执行的步骤都给一条带动点的状态行：模型在想就报思考进度，否则报本步已用时
const stageStarted = ref(Date.now())
watch(() => props.progress.active, () => { stageStarted.value = Date.now() })
const stageSeconds = computed(() => (props.elapsed, Math.max(1, Math.round((Date.now() - stageStarted.value) / 1000))))
const isActive = (id: string) => props.progress.active === id || (id === 'validate' && (props.progress.active === 'repair' || props.progress.active === 'refine'))
const stageLine = (id: string) => thinkingLine.value ? thinkingLine.value
  : props.progress.live ? `${props.progress.live} · ${stageSeconds.value} 秒`
  : props.progress.active === 'repair' ? `正在调整候选，随后重新计算时间与路线 · ${stageSeconds.value} 秒`
  : props.progress.active === 'refine' ? `正在检查空档、绕路和节奏，逐处试改并重新校验 · ${stageSeconds.value} 秒`
  : `${planStages.find(stage => stage.id === id)?.description ?? '正在处理'} · ${stageSeconds.value} 秒`
const clock = computed(() => `${Math.floor(props.elapsed / 60).toString().padStart(2, '0')}:${(props.elapsed % 60).toString().padStart(2, '0')}`)
// 插画里的路线：7 个站点对应 7 个真实步骤，已完成的点亮，标记停在当前步骤
const routePath = ref<SVGPathElement | null>(null)
const points = ref<{ x: number; y: number }[]>([])
const marker = ref({ x: 116, y: 400 })
const completedCount = computed(() => planStages.filter(stage => props.progress.completed.includes(stage.id)).length)
const fraction = computed(() => Math.min(1, completedCount.value / (planStages.length - 1)))
function placeRoute() {
  const path = routePath.value
  if (!path) return
  const total = path.getTotalLength()
  points.value = planStages.map((_, index) => { const point = path.getPointAtLength(total * index / (planStages.length - 1)); return { x: point.x, y: point.y } })
  const point = path.getPointAtLength(total * fraction.value)
  marker.value = { x: point.x, y: point.y }
}
watch(fraction, placeRoute)
onMounted(() => { placeRoute(); heading.value?.focus({ preventScroll: true }) })
</script>

<template>
  <section class="plan-progress" aria-label="行程生成进度">
    <div class="generation-art" :class="{ paused: !busy }" aria-hidden="true">
      <svg class="generation-landscape" viewBox="0 0 480 500">
        <defs>
          <clipPath id="pg-frame"><circle cx="240" cy="246" r="214" /></clipPath>
          <linearGradient id="pg-sky" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#e3ecfd" /><stop offset=".55" stop-color="#f7f1e6" /><stop offset="1" stop-color="#eef3fd" /></linearGradient>
          <linearGradient id="pg-lake" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d9e4fb" /><stop offset="1" stop-color="#bccdf1" /></linearGradient>
          <radialGradient id="pg-glow"><stop offset="0" stop-color="#ffe2a6" /><stop offset="1" stop-color="#ffe2a6" stop-opacity="0" /></radialGradient>
        </defs>
        <g class="pg-orbit"><circle cx="240" cy="246" r="234" fill="none" stroke="#d6dff0" stroke-dasharray="2 9" /></g>
        <circle cx="240" cy="246" r="222" fill="none" stroke="#e1e7f3" />
        <g clip-path="url(#pg-frame)">
          <rect x="0" y="0" width="480" height="500" fill="url(#pg-sky)" />
          <circle class="pg-glow" cx="326" cy="176" r="74" fill="url(#pg-glow)" />
          <circle cx="326" cy="176" r="30" fill="#ffd690" />
          <g class="pg-cloud" fill="#fff"><path d="M86 150c0-12 10-21 22-21 8 0 15 4 18 11 3-2 7-3 11-3 10 0 18 8 18 18H86Z" /><path d="M296 104c0-8 7-14 15-14 5 0 9 2 12 6 2-1 4-2 7-2 7 0 12 5 12 12h-46Z" opacity=".85" /></g>
          <g class="pg-birds" fill="none" stroke="#7d93c4" stroke-width="1.8" stroke-linecap="round"><path d="M368 128q5-5 10 0q5-5 10 0" /><path d="M392 146q4-4 8 0q4-4 8 0" /><path d="M350 150q3.5-3.5 7 0q3.5-3.5 7 0" /></g>
          <path d="M20 312C64 270 110 252 160 266s90-28 140-32 116 24 160 46v40H20Z" fill="#d6e1f7" />
          <path d="M20 318c48-40 104-52 160-34s82 22 110 34H20Z" fill="#b6c8ee" />
          <g fill="#a3b9e8"><circle cx="78" cy="296" r="15" /><circle cx="98" cy="290" r="13" /><circle cx="204" cy="294" r="14" /><circle cx="224" cy="300" r="11" /></g>
          <g id="pg-pagoda">
            <rect x="122" y="280" width="56" height="8" rx="2" fill="#8ea6dc" />
            <rect x="131" y="262" width="38" height="18" fill="#f7f9fe" /><rect x="150" y="262" width="19" height="18" fill="#e1e8f6" />
            <path d="M122 263h56l-7-7h-42z" fill="#2f5bd3" />
            <rect x="135" y="242" width="30" height="14" fill="#f7f9fe" /><rect x="150" y="242" width="15" height="14" fill="#e1e8f6" />
            <path d="M127 243h46l-6-6h-34z" fill="#2f5bd3" />
            <rect x="139" y="225" width="22" height="12" fill="#f7f9fe" /><rect x="150" y="225" width="11" height="12" fill="#e1e8f6" />
            <path d="M132 226h36l-5-6h-26z" fill="#2f5bd3" />
            <rect x="142" y="211" width="16" height="9" fill="#f7f9fe" /><rect x="150" y="211" width="8" height="9" fill="#e1e8f6" />
            <path d="M136 212h28l-5-6h-18z" fill="#2f5bd3" />
            <rect x="149" y="192" width="2.4" height="14" rx="1" fill="#2f5bd3" /><circle cx="150.2" cy="191" r="3" fill="#2f5bd3" />
            <g class="pg-window" fill="#ffc96b"><rect x="146" y="266" width="8" height="10" rx="3" /><rect x="146.5" y="245" width="7" height="8" rx="2.5" /><rect x="147" y="228" width="6" height="7" rx="2" /></g>
          </g>
          <path d="M296 318c44-16 104-20 168-8v26H296Z" fill="#c6d5f4" />
          <path d="M346 306c20-16 74-16 94 0v7h-94Z" fill="#f3f6fd" /><path d="M366 313c9-11 45-11 54 0Z" fill="#9db2e0" />
          <rect x="0" y="318" width="480" height="182" fill="url(#pg-lake)" />
          <use href="#pg-pagoda" transform="translate(0 636) scale(1 -1)" opacity=".16" />
          <g class="pg-shimmer" stroke="#fff" stroke-width="2" stroke-linecap="round"><path d="M110 352h26" /><path d="M250 340h18" /><path d="M300 400h30" style="animation-delay: -1.2s" /><path d="M150 430h22" style="animation-delay: -2s" /><path d="M370 360h20" style="animation-delay: -.6s" /></g>
          <g class="pg-boat"><path d="M268 372h54l-8 9h-38Z" fill="#2f5bd3" /><path d="M280 362h26v10h-26Z" fill="#f7f9fe" /><path d="M278 362h30l-4-5h-22Z" fill="#8ea6dc" /></g>
          <path ref="routePath" d="M116 400C134 382 122 362 156 350S226 336 246 368 318 420 352 390 410 320 404 270" fill="none" stroke="#fff" stroke-width="4" stroke-linecap="round" stroke-dasharray="1 9" opacity=".95" />
          <path d="M116 400C134 382 122 362 156 350S226 336 246 368 318 420 352 390 410 320 404 270" pathLength="100" fill="none" stroke="#2f5bd3" stroke-width="4" stroke-linecap="round" class="pg-progress" :style="{ strokeDashoffset: 100 - fraction * 100 }" />
          <g v-for="(point, index) in points" :key="index" class="pg-stop" :class="{ done: index < completedCount, current: busy && index === completedCount }">
            <circle class="pg-stop-ring" :cx="point.x" :cy="point.y" r="12" />
            <circle class="pg-stop-dot" :cx="point.x" :cy="point.y" r="6" />
          </g>
          <g class="pg-traveler" :style="{ transform: `translate(${marker.x}px, ${marker.y}px)` }"><circle r="11" fill="#2f5bd3" /><circle r="4" fill="#fff" /></g>
        </g>
      </svg>
    </div>
    <div class="generation-content" tabindex="0" role="region" aria-label="生成步骤与模型输出">
    <div class="progress-eyebrow"><span>杭州行记 / 正在准备</span><button type="button" @click="emit('back')"><PhArrowLeft :size="14" />返回修改</button></div>
    <h1 ref="heading" tabindex="-1"><RevealText :text="error ? '这次规划暂未完成' : '正在安排你的行程'" /></h1>
    <p class="progress-intro">{{ error ? '出行条件已为你保留。' : '从想去的地方，到走得通的一天。' }}</p>
    <div class="progress-caption" role="status" aria-live="polite" aria-atomic="true">
      <strong><RevealText :text="error ? '生成已暂停' : current" /></strong>
      <span v-if="!error">{{ progress.active === 'repair' ? `第 ${progress.round + (progress.details.repair ? 0 : 1)} 轮调整` : `已完成 ${progress.completed.length} / ${planStages.length} 项` }}</span>
    </div>
    <div class="progress-segments" aria-hidden="true"><i v-for="stage in planStages" :key="stage.id" :class="{ done: progress.completed.includes(stage.id), active: busy && progress.active === stage.id }" /></div>
    <ol class="progress-steps">
      <li v-for="(stage, index) in planStages" :key="stage.id" :class="{ done: progress.completed.includes(stage.id), active: isActive(stage.id) }"
        :aria-current="progress.active === stage.id ? 'step' : undefined">
        <span class="step-symbol" aria-hidden="true"><PhCheck v-if="progress.completed.includes(stage.id)" :size="14" weight="bold" /><span v-else-if="busy && isActive(stage.id)" class="step-pulse" /><span v-else>{{ String(index + 1).padStart(2, '0') }}</span></span>
        <div><h2>{{ stage.title }}</h2>
          <p v-if="busy && isActive(stage.id) && !progress.completed.includes(stage.id)" class="step-thinking"><span class="thinking-dots" aria-hidden="true"><i /><i /><i /></span>{{ stageLine(stage.id) }}</p>
          <p v-else>{{ progress.details[stage.id] || stage.description }}</p>
        </div>
      </li>
    </ol>
    <p v-if="error" class="progress-error" role="alert"><PhWarningCircle :size="18" />{{ error }}</p>
    <p v-else class="progress-note"><time>已用时 {{ clock }}</time>{{ elapsed >= 60 ? '这一步仍在处理，模型选点或交通查询可能需要更久。' : '进度随实际执行更新，当前步骤完成后继续。' }}<span v-if="progress.round > 0">已进行 {{ progress.round }} 轮自动调整。</span></p>
    <div v-if="error" class="progress-actions"><button type="button" class="progress-retry" @click="emit('retry')"><PhArrowClockwise :size="16" />重新尝试</button></div>
    <button type="button" class="output-toggle" :aria-expanded="expanded" aria-controls="generation-output" @click="expanded = !expanded">{{ expanded ? '收起实时输出 −' : '展开实时输出 +' }}<span>模型回答与执行记录</span></button>
    <section v-if="expanded" id="generation-output" class="generation-output"><p>回答随实际生成显示；复用内容会标记为缓存。思考过程只在生成期间展示最近一段，不写入行程。</p>
      <div v-if="think" class="think-block">
        <span class="think-label">思考过程 · 只保留最近一段</span>
        <div ref="thinkPane" class="think-scroll" tabindex="0" aria-label="模型思考过程"><pre>{{ think }}</pre></div>
      </div>
      <div ref="outputPane" class="output-scroll" tabindex="0" aria-label="实时模型回答"><pre>{{ output || (thinkingLine ? '' : '暂时没有模型回答。规则选点时不产生模型输出；当前执行进度见上方。') }}</pre><p v-if="thinkingLine" class="thinking-line" role="status"><span class="thinking-dots" aria-hidden="true"><i /><i /><i /></span>{{ thinkingLine }}，正式回答会随后出现</p></div><ul class="execution-records"><li v-for="stage in planStages.filter(item => progress.details[item.id])" :key="stage.id"><strong>{{ stage.title }}</strong>{{ progress.details[stage.id] }}</li><li v-if="progress.details.repair">{{ progress.details.repair }}</li><li v-if="progress.details.refine">{{ progress.details.refine }}</li></ul></section>
    </div>
  </section>
</template>

<style scoped>
.plan-progress { padding: 40px 20px; max-width: 1180px; margin: auto; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 480px); align-items: start; gap: 60px; animation: arrive .35s ease-out; }
.generation-art { position: sticky; top: 30px; min-height: 500px; display: grid; align-content: center; }
.generation-landscape { width: 100%; position: relative; z-index: 1; }
.paused * { animation-play-state: paused !important; }
.pg-orbit { transform-box: view-box; transform-origin: 240px 246px; animation: orbit 80s linear infinite; }
.pg-glow { transform-box: fill-box; transform-origin: center; animation: pg-glow 5s ease-in-out infinite; }
.pg-cloud { animation: pg-drift 18s ease-in-out infinite alternate; }
.pg-birds { animation: pg-birds 14s ease-in-out infinite alternate; }
.pg-window { animation: pg-window 2.8s ease-in-out infinite; }
.pg-shimmer path { animation: pg-shimmer 3.2s ease-in-out infinite; }
.pg-boat { animation: pg-boat 3.6s ease-in-out infinite; }
.pg-progress { stroke-dasharray: 100; transition: stroke-dashoffset .9s cubic-bezier(.16, 1, .3, 1); }
.pg-stop-dot { fill: #fff; stroke: #9db2e0; stroke-width: 2.5; transition: fill .4s, stroke .4s; }
.pg-stop-ring { fill: none; stroke: #2f5bd3; stroke-width: 2; opacity: 0; }
.pg-stop.done .pg-stop-dot { fill: #2f5bd3; stroke: #fff; }
.pg-stop.current .pg-stop-dot { stroke: #2f5bd3; }
.pg-stop.current .pg-stop-ring { transform-box: fill-box; transform-origin: center; animation: pg-ring 1.8s ease-out infinite; }
.pg-traveler { transition: transform .9s cubic-bezier(.16, 1, .3, 1); filter: drop-shadow(0 3px 6px #2346b655); }
@keyframes pg-glow { 50% { opacity: .55; transform: scale(1.1); } }
@keyframes pg-drift { from { transform: translateX(-10px); } to { transform: translateX(24px); } }
@keyframes pg-birds { from { transform: translate(0, 0); } to { transform: translate(-40px, 10px); } }
@keyframes pg-window { 50% { opacity: .55; } }
@keyframes pg-shimmer { 0%, 100% { opacity: .15; transform: translateX(0); } 50% { opacity: .85; transform: translateX(8px); } }
@keyframes pg-boat { 50% { transform: translateY(3px) rotate(-1.5deg); } }
@keyframes pg-ring { from { opacity: .8; transform: scale(.6); } to { opacity: 0; transform: scale(1.5); } }
.generation-content { min-width: 0; }
.output-toggle { width: 100%; display: flex; justify-content: space-between; gap: 10px; align-items: center; text-align: left; padding: 18px 0; color: var(--accent); font-size: 12px; }
.output-toggle span { color: var(--muted); font-size: 11px; }
.generation-output { border: 1px solid var(--line); border-radius: 12px; background: #fff; padding: 16px; }
.generation-output > p { color: var(--muted); font-size: 11px; line-height: 1.8; margin-bottom: 12px; }
.output-scroll { max-height: 260px; overflow: auto; overscroll-behavior: contain; }
.output-scroll pre { margin: 0; font: 11px/1.9 ui-monospace, monospace; white-space: pre-wrap; overflow-wrap: anywhere; color: #455371; }
.thinking-line { display: flex; align-items: center; gap: 10px; margin-top: 10px; font-size: 12px; color: var(--accent); }
.thinking-dots { display: inline-flex; gap: 4px; }
.thinking-dots i { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); animation: thinking-bounce 1.2s ease-in-out infinite; }
.thinking-dots i:nth-child(2) { animation-delay: .15s; }
.thinking-dots i:nth-child(3) { animation-delay: .3s; }
.step-thinking { display: flex; align-items: center; gap: 8px; color: var(--accent) !important; }
.think-block { margin-bottom: 12px; padding: 10px 12px; border: 1px solid #e3e9f6; border-radius: 10px; background: #f8faff; }
.think-label { display: block; margin-bottom: 6px; font-size: 11px; letter-spacing: .04em; color: var(--faint); }
/* 顶部渐隐，滚动时被切一半的那行不会硬生生截断 */
.think-scroll { max-height: 132px; overflow: auto; overscroll-behavior: contain; -webkit-mask-image: linear-gradient(#0000, #000 16px); mask-image: linear-gradient(#0000, #000 16px); }
.think-scroll pre { margin: 0; font: 11px/1.85 ui-monospace, monospace; white-space: pre-wrap; overflow-wrap: anywhere; color: #7b86a0; }
@keyframes thinking-bounce { 0%, 80%, 100% { opacity: .25; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-3px); } }
.execution-records { margin-top: 16px; border-top: 1px solid var(--line); padding-top: 12px; display: grid; gap: 7px; font-size: 11px; color: var(--muted); }
.execution-records strong { margin-right: 8px; color: var(--text); font-weight: 500; }
@keyframes orbit { to { transform: rotate(360deg); } }
.progress-eyebrow { display: flex; justify-content: space-between; gap: 16px; color: var(--accent); font-size: 11px; letter-spacing: .1em; }
.progress-eyebrow { align-items: center; }
.progress-eyebrow button { display: inline-flex; align-items: center; gap: 5px; min-height: 36px; padding: 6px; color: var(--muted); letter-spacing: 0; border-radius: 6px; }
.progress-eyebrow button:hover { background: var(--bg); }
h1 { font-size: clamp(23px, 2.2vw, 29px); letter-spacing: -.04em; font-weight: 650; margin: 18px 0 8px; outline: none; }
.progress-intro { color: var(--muted); font-size: 13px; }
.progress-caption { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin-top: 26px; font-size: 13px; }
.progress-caption strong { font-weight: 600; }
.progress-caption > span { color: var(--muted); font-size: 11px; white-space: nowrap; }
.progress-segments { display: flex; gap: 5px; margin: 12px 0 20px; }
.progress-segments i { flex: 1; height: 3px; border-radius: 3px; background: var(--line); transition: background .3s; }
.progress-segments i.done { background: var(--accent); }
.progress-segments i.active { background: #93adea; }
.progress-steps { display: grid; gap: 0; }
.progress-steps li { position: relative; display: flex; gap: 13px; min-height: 54px; padding-bottom: 9px; color: var(--faint); }
.progress-steps li:not(:last-child)::before { content: ''; position: absolute; left: 12px; top: 30px; bottom: 4px; width: 1px; background: var(--line); }
.step-symbol { display: grid; place-items: center; width: 25px; height: 25px; flex: 0 0 25px; border: 1px solid var(--line); border-radius: 50%; font-size: 10px; font-variant-numeric: tabular-nums; }
.progress-steps h2 { font-size: 13px; font-weight: 500; line-height: 25px; }
.progress-steps p { font-size: 11px; line-height: 1.7; margin-top: 1px; color: var(--muted); }
.progress-steps .done { color: var(--muted); }
.done .step-symbol { color: var(--accent); background: var(--accent-soft); border-color: transparent; }
.progress-steps .active { color: var(--text); }
.active h2 { font-weight: 650; }
.active .step-symbol { border-color: var(--accent); color: var(--accent); }
.step-pulse { width: 7px; height: 7px; background: var(--accent); border-radius: 50%; animation: breathe 1.8s ease-in-out infinite; }
.progress-note { color: var(--muted); font-size: 11px; line-height: 1.8; padding-top: 8px; border-top: 1px solid var(--line-soft); }
.progress-note span { display: block; }
.progress-note time { display: block; color: var(--text); font-variant-numeric: tabular-nums; margin-bottom: 3px; }
.progress-error { display: flex; align-items: flex-start; gap: 8px; padding: 12px; background: #fff3f1; border-radius: 8px; color: var(--bad); font-size: 12px; }
.progress-error svg { flex-shrink: 0; }
.progress-actions { display: flex; justify-content: space-between; gap: 12px; margin-top: 18px; }
.progress-actions button { display: inline-flex; align-items: center; gap: 7px; min-height: 40px; padding: 8px 10px; border-radius: 7px; font-size: 12px; color: var(--muted); }
.progress-actions button:hover { background: var(--bg); }
.progress-actions .progress-retry { color: var(--accent); background: var(--accent-soft); }
@keyframes breathe { 50% { opacity: .35; transform: scale(.7); } }
@keyframes arrive { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
@media (max-width: 900px) { .plan-progress { gap: 24px; grid-template-columns: minmax(0, .7fr) minmax(0, 1fr); } .generation-art { min-height: 360px; } }
@media (min-width: 641px) {
  .plan-progress { height: 100%; min-height: 0; width: 100%; padding: 12px 20px; grid-template-rows: minmax(0, 1fr); align-items: stretch; overflow: hidden; }
  .generation-art { position: relative; top: auto; min-height: 0; max-width: min(100%, calc(100dvh - 150px)); width: 100%; aspect-ratio: 480 / 500; align-self: center; justify-self: center; }
  .generation-content { min-height: 0; overflow-y: auto; overflow-x: hidden; overscroll-behavior-y: contain; scrollbar-gutter: stable; scrollbar-width: thin; scrollbar-color: #cfd4dc transparent; padding: 0 12px 24px 4px; }
}
@media (max-width: 640px) { .plan-progress { display: flex; flex-direction: column; padding: 4px 0 30px; gap: 10px; } .generation-art { position: relative; top: auto; min-height: 0; width: 220px; margin: auto; } .generation-content { width: 100%; } h1 { margin-top: 12px; } }
@media (prefers-reduced-motion: reduce) { *, *::before, .generation-landscape * { animation: none !important; transition: none !important; } }
</style>
