<script setup lang="ts">
import type { CityInfo } from '../api'
import RevealText from './RevealText.vue'
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { PhArrowLeft, PhArrowClockwise, PhWarningCircle } from '@phosphor-icons/vue'
import DrawCheck from './DrawCheck.vue'
import StreamText from './StreamText.vue'
import { cityArtwork } from '../cityArt'
import { expandHooks } from '../motion'
import { planStages as baseStages, type PlanProgressState } from '../planProgress'

const props = defineProps<{ city: CityInfo; days?: number; progress: PlanProgressState; elapsed: number; error: string; busy: boolean; output: string; outputStatic?: number; think?: string; thinking?: { tag: string; attempt: number; started: number; chars: number; waiting: boolean } | null; queue?: { position: number; limit: number } | null }>()
const planStages = computed(() => baseStages.map(stage => ({ ...stage,
  title: stage.id === 'finalize' ? `整理你的${props.city.brand}` : stage.title,
  description: stage.id === 'retrieve' ? `从${props.city.name}地点库召回符合条件的候选` : stage.description,
})))
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
// 同时生成的份数满了就在排队：标题报第几位，轮到了自动开始
const queued = computed(() => props.busy && !props.error && props.queue ? props.queue : null)
const current = computed(() => queued.value ? `排队中 · 你排在第 ${queued.value.position} 位` : props.progress.active === 'repair' ? '正在调整有冲突的安排' : props.progress.active === 'refine' ? '正在按合理性指标精修行程' :
  planStages.value.find(stage => stage.id === props.progress.active)?.title ?? '正在连接规划服务')
// elapsed 每秒变化，借它让思考秒数随之刷新
const thinkingSeconds = computed(() => (props.elapsed, props.thinking ? Math.max(1, Math.round((Date.now() - props.thinking.started) / 1000)) : 0))
// 还没收到任何片段时如实说在等模型服务，不显示"思考中 · 0 字"；重试时标出第几次
const thinkingLine = computed(() => {
  if (!props.thinking || !props.busy) return ''
  const retry = props.thinking.attempt > 1 ? ` · 第 ${props.thinking.attempt} 次尝试` : ''
  return props.thinking.waiting ? `等待模型服务响应 · ${thinkingSeconds.value} 秒${retry}`
    : `大模型思考中 · ${thinkingSeconds.value} 秒 · 约 ${props.thinking.chars.toLocaleString()} 字${retry}`
})
// 每个正在执行的步骤都给一条带动点的状态行：模型在想就报思考进度，否则报本步已用时
const stageStarted = ref(Date.now())
watch(() => props.progress.active, () => { stageStarted.value = Date.now() })
const stageSeconds = computed(() => (props.elapsed, Math.max(1, Math.round((Date.now() - stageStarted.value) / 1000))))
const isActive = (id: string) => props.progress.active === id || (id === 'validate' && (props.progress.active === 'repair' || props.progress.active === 'refine'))
const stageLine = (id: string) => thinkingLine.value ? thinkingLine.value
  : props.progress.live ? `${props.progress.live} · ${stageSeconds.value} 秒`
  : props.progress.active === 'repair' ? `正在调整候选，随后重新计算时间与路线 · ${stageSeconds.value} 秒`
  : props.progress.active === 'refine' ? `正在检查空档、绕路和节奏，逐处试改并重新校验 · ${stageSeconds.value} 秒`
  : `${planStages.value.find(stage => stage.id === id)?.description ?? '正在处理'} · ${stageSeconds.value} 秒`
const clock = computed(() => `${Math.floor(props.elapsed / 60).toString().padStart(2, '0')}:${(props.elapsed % 60).toString().padStart(2, '0')}`)
// 左边的圆就是那张票撑开的样子：这座城市的插画和行程天数；下面的条码按 7 个真实步骤一段段点亮
const art = computed(() => cityArtwork(props.city.key))
const BARS_PER_STAGE = 8
const bars = computed(() => {
  let hash = 2166136261
  for (const ch of `${props.city.key}|${props.days ?? ''}`) hash = Math.imul(hash ^ ch.charCodeAt(0), 16777619) >>> 0
  return planStages.value.map(stage => ({ stage, widths: Array.from({ length: BARS_PER_STAGE }, () => { hash = Math.imul(hash ^ (hash >>> 13), 1274126177) >>> 0; return 1 + (hash >>> 11) % 4 }) }))
})
// 进场：右边逐条进来；播完就撤掉，之后新出现的内容（出错、实时输出）不跟着延迟
const arriving = ref(true)
let arrivalTimer: ReturnType<typeof setTimeout> | undefined
onMounted(() => { heading.value?.focus({ preventScroll: true }); arrivalTimer = setTimeout(() => { arriving.value = false }, 1700) })
onBeforeUnmount(() => clearTimeout(arrivalTimer))
</script>

<template>
  <section class="plan-progress" :class="{ arriving }" aria-label="行程生成进度">
    <!-- 远景：这座城市的另一幅插画从左边探出来，底下薄雾缓缓飘（2026-11-25 用户选“城市远景加票纸”） -->
    <div class="generation-backdrop" aria-hidden="true"><img v-if="art" :src="art.left" alt="" decoding="async" /><i class="mist far" /><i class="mist near" /></div>
    <div class="generation-art" :class="{ paused: !busy }" aria-hidden="true">
      <div class="generation-scene">
        <svg class="generation-orbit" viewBox="0 0 100 100"><circle cx="50" cy="50" r="49.4" /></svg>
        <div class="generation-ticket">
          <img v-if="art" :src="art.right" alt="" decoding="sync" />
          <p><b>{{ city.name }}</b><span>{{ city.key.toUpperCase() }}<template v-if="days"> · {{ days }} 天</template></span></p>
        </div>
      </div>
      <div class="generation-code"><span v-for="{ stage, widths } in bars" :key="stage.id" :class="{ done: progress.completed.includes(stage.id), active: busy && isActive(stage.id) && !progress.completed.includes(stage.id) }"><i v-for="(width, index) in widths" :key="index" :style="{ width: `${width}px`, '--at': index }" /></span></div>
    </div>
    <div class="generation-content" tabindex="0" role="region" aria-label="生成步骤与模型输出">
    <div class="progress-eyebrow"><span>{{ city.brand }} / 正在准备</span><button type="button" @click="emit('back')"><PhArrowLeft :size="14" />返回修改</button></div>
    <h1 ref="heading" tabindex="-1"><RevealText :text="error ? '这次规划暂未完成' : '正在安排你的行程'" /></h1>
    <p class="progress-intro">{{ error ? '出行条件已为你保留。' : '从想去的地方，到走得通的一天。' }}</p>
    <div class="progress-caption" role="status" aria-live="polite" aria-atomic="true">
      <strong><RevealText :text="error ? '生成已暂停' : current" /></strong>
      <span v-if="queued">同时最多生成 {{ queued.limit }} 份</span>
      <span v-else-if="!error">{{ progress.active === 'repair' ? `第 ${progress.round + (progress.details.repair ? 0 : 1)} 轮调整` : `已完成 ${progress.completed.length} / ${planStages.length} 项` }}</span>
    </div>
    <ol class="progress-steps">
      <li v-for="(stage, index) in planStages" :key="stage.id" :class="{ done: progress.completed.includes(stage.id), active: isActive(stage.id) }"
        :aria-current="progress.active === stage.id ? 'step' : undefined">
        <span class="step-symbol" aria-hidden="true"><DrawCheck v-if="progress.completed.includes(stage.id)" :size="14" :weight="2.4" /><span v-else-if="busy && isActive(stage.id)" class="step-pulse" /><span v-else>{{ String(index + 1).padStart(2, '0') }}</span></span>
        <div><h2>{{ stage.title }}</h2>
          <p v-if="busy && isActive(stage.id) && !progress.completed.includes(stage.id)" class="step-thinking"><span class="thinking-dots" aria-hidden="true"><i /><i /><i /></span>{{ stageLine(stage.id) }}</p>
          <p v-else>{{ progress.details[stage.id] || stage.description }}</p>
        </div>
      </li>
    </ol>
    <p v-if="error" class="progress-error" role="alert"><PhWarningCircle :size="18" />{{ error }}</p>
    <p v-else-if="queued" class="progress-note"><time>已等待 {{ clock }}</time>轮到你会自动开始；排队时离开页面，这次不扣次数。</p>
    <p v-else class="progress-note"><time>已用时 {{ clock }}</time>{{ elapsed >= 60 ? '这一步仍在处理，模型选点或交通查询可能需要更久。' : '进度随实际执行更新，当前步骤完成后继续。' }}<span v-if="progress.round > 0">已进行 {{ progress.round }} 轮自动调整。</span></p>
    <div v-if="error" class="progress-actions"><button type="button" class="progress-retry" @click="emit('retry')"><PhArrowClockwise :size="16" />重新尝试</button></div>
    <button type="button" class="output-toggle" :aria-expanded="expanded" aria-controls="generation-output" @click="expanded = !expanded">{{ expanded ? '收起实时输出 −' : '展开实时输出 +' }}<span>模型回答与执行记录</span></button>
    <Transition :css="false" v-bind="expandHooks"><section v-if="expanded" id="generation-output" class="generation-output"><p>回答随实际生成显示；复用内容会标记为缓存。思考过程只在生成期间展示最近一段，不写入行程。</p>
      <div v-if="think" class="think-block">
        <span class="think-label">思考过程 · 只保留最近一段</span>
        <div ref="thinkPane" class="think-scroll" tabindex="0" aria-label="模型思考过程"><pre>{{ think }}</pre></div>
      </div>
      <div ref="outputPane" class="output-scroll" tabindex="0" aria-label="实时模型回答"><StreamText v-if="output" :text="output" :static-until="outputStatic ?? 0" /><pre v-else>{{ thinkingLine ? '' : '暂时没有模型回答。规则选点时不产生模型输出；当前执行进度见上方。' }}</pre><p v-if="thinkingLine" class="thinking-line" role="status"><span class="thinking-dots" aria-hidden="true"><i /><i /><i /></span>{{ thinkingLine }}，正式回答会随后出现</p></div><ul class="execution-records"><li v-for="stage in planStages.filter(item => progress.details[item.id])" :key="stage.id"><strong>{{ stage.title }}</strong>{{ progress.details[stage.id] }}</li><li v-if="progress.details.repair">{{ progress.details.repair }}</li><li v-if="progress.details.refine">{{ progress.details.refine }}</li></ul></section></Transition>
    </div>
  </section>
</template>

<style scoped>
.plan-progress { padding: 40px 20px; max-width: 1180px; margin: auto; display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 500px); align-items: start; gap: 56px; }
.arriving .generation-art { animation: scene-in .5s ease-out .1s both; }
.arriving .generation-backdrop img { animation: backdrop-in 1s cubic-bezier(.16, 1, .3, 1) .3s both; }
.arriving .mist { animation-play-state: paused; }
.arriving .generation-content { animation: paper-in .6s cubic-bezier(.16, 1, .3, 1) .34s both; }
@keyframes backdrop-in { from { opacity: 0; transform: translateX(-46px); } }
@keyframes paper-in { from { opacity: 0; transform: translateY(18px); } }
.arriving .generation-code { animation: arrive .5s cubic-bezier(.16, 1, .3, 1) .8s both; }
.arriving .generation-content > :not(.progress-steps) { animation: arrive .5s cubic-bezier(.16, 1, .3, 1) .4s both; }
.arriving .generation-content > :nth-child(n + 4):not(.progress-steps) { animation-delay: .5s; }
.arriving .generation-content > .progress-steps ~ * { animation-delay: .9s; }
.arriving .progress-steps li { animation: arrive .5s cubic-bezier(.16, 1, .3, 1) both; }
.arriving .progress-steps li:nth-child(1) { animation-delay: .46s; } .arriving .progress-steps li:nth-child(2) { animation-delay: .51s; } .arriving .progress-steps li:nth-child(3) { animation-delay: .56s; } .arriving .progress-steps li:nth-child(4) { animation-delay: .61s; }
.arriving .progress-steps li:nth-child(5) { animation-delay: .66s; } .arriving .progress-steps li:nth-child(6) { animation-delay: .71s; } .arriving .progress-steps li:nth-child(7) { animation-delay: .76s; }
@keyframes scene-in { from { opacity: 0; } }
.generation-art { position: sticky; top: 30px; min-height: 500px; display: grid; align-content: center; justify-items: center; gap: clamp(22px, 4vh, 40px); }
.generation-scene { position: relative; width: min(100%, calc(100dvh - 250px)); aspect-ratio: 1; }
.generation-orbit { position: absolute; inset: -4.5%; width: 109%; height: 109%; animation: orbit 80s linear infinite; }
.generation-orbit circle { fill: none; stroke: #cfd0cb; stroke-width: .36; stroke-dasharray: .4 1.6; }
.generation-ticket { position: relative; width: 100%; height: 100%; border-radius: 50%; overflow: hidden; background: linear-gradient(#f4f5f4, #fff 78%); box-shadow: 0 30px 60px -34px #15161340, 0 1px 3px #1516130f; container-type: size; }
.generation-ticket :is(img, canvas) { position: absolute; right: -9%; bottom: -10%; height: 120%; max-width: none; mix-blend-mode: multiply; -webkit-mask-image: linear-gradient(to right, transparent 6%, #000 38%); mask-image: linear-gradient(to right, transparent 6%, #000 38%); }
.generation-ticket p { position: absolute; left: 11%; top: 39%; margin: 0; }
.generation-ticket b { display: block; font-size: 13.5cqw; line-height: 1; font-weight: 650; letter-spacing: -.04em; color: #151613; }
.generation-ticket span { display: block; margin-top: 2.6cqw; font-family: 'Geist Mono Variable', 'SFMono-Regular', Consolas, monospace; font-size: max(11px, 2.5cqw); letter-spacing: .08em; color: #696b64; }
.generation-code { display: flex; justify-content: space-between; width: min(84%, 420px); height: 30px; }
.generation-code > span { display: flex; justify-content: space-between; flex: 1; padding-right: 3px; }
.generation-code i { height: 100%; background: #d6d7d1; transform-origin: bottom; transition: background-color .5s ease calc(var(--at) * 45ms); }
.generation-code .done i { background: #151613; }
.generation-code .active i { background: #8f918a; animation: code-run 1.1s ease-in-out infinite; animation-delay: calc(var(--at) * 70ms); }
@keyframes code-run { 50% { transform: scaleY(.45); background: #151613; } }
.paused .generation-orbit, .paused i { animation-play-state: paused !important; }
.generation-backdrop { position: fixed; inset: var(--journey-bar-height, 64px) 0 0; z-index: 0; overflow: hidden; pointer-events: none; }
.generation-backdrop img { position: absolute; left: clamp(-60px, calc((100vw - 1440px) / 2), 0px); bottom: 0; height: 100%; max-width: none; mix-blend-mode: multiply; -webkit-mask-image: linear-gradient(to right, #000 34%, transparent 56%); mask-image: linear-gradient(to right, #000 34%, transparent 56%); }
.mist { position: absolute; left: -30%; width: 160%; animation: mist 26s ease-in-out infinite alternate; }
.mist.near { bottom: -6%; height: 46%; opacity: .92; background: radial-gradient(38% 50% at 30% 60%, #fff 20%, #fff0 72%), radial-gradient(34% 44% at 68% 70%, #fff 10%, #fff0 70%); }
.mist.far { bottom: 16%; height: 34%; opacity: .6; background: radial-gradient(38% 50% at 30% 60%, #fff 20%, #fff0 72%); animation-duration: 38s; animation-direction: alternate-reverse; }
@keyframes mist { from { transform: translateX(-7%); } to { transform: translateX(9%); } }
.generation-art, .generation-content { z-index: 1; }
.generation-content { position: relative; min-width: 0; padding: 24px 30px 14px; border-radius: 16px; background: #fff; box-shadow: 0 30px 60px -34px #15161345, 0 1px 3px #15161314; }
.output-toggle { width: 100%; display: flex; justify-content: space-between; gap: 10px; align-items: center; text-align: left; padding: 18px 0; color: var(--accent); font-size: 12px; }
.output-toggle span { color: var(--muted); font-size: 11px; }
.generation-output { border: 1px solid var(--line); border-radius: 6px; background: #fff; padding: 16px; }
.generation-output > p { color: var(--muted); font-size: 11px; line-height: 1.8; margin-bottom: 12px; }
.output-scroll { max-height: 260px; overflow: auto; overscroll-behavior: contain; }
.output-scroll pre { margin: 0; font: 11px/1.9 ui-monospace, monospace; white-space: pre-wrap; overflow-wrap: anywhere; color: #525350; }
.thinking-line { display: flex; align-items: center; gap: 10px; margin-top: 10px; font-size: 12px; color: var(--accent); }
.thinking-dots { display: inline-flex; gap: 4px; }
.thinking-dots i { width: 6px; height: 6px; border-radius: 50%; background: var(--accent); animation: thinking-bounce 1.2s ease-in-out infinite; }
.thinking-dots i:nth-child(2) { animation-delay: .15s; }
.thinking-dots i:nth-child(3) { animation-delay: .3s; }
.step-thinking { display: flex; align-items: center; gap: 8px; color: var(--accent) !important; }
.think-block { margin-bottom: 12px; padding: 10px 12px; border: 1px solid #e9eae7; border-radius: 3px; background: #fafbf8; }
.think-label { display: block; margin-bottom: 6px; font-size: 11px; letter-spacing: .04em; color: var(--faint); }
/* 顶部渐隐，滚动时被切一半的那行不会硬生生截断 */
.think-scroll { max-height: 132px; overflow: auto; overscroll-behavior: contain; -webkit-mask-image: linear-gradient(#0000, #000 16px); mask-image: linear-gradient(#0000, #000 16px); }
.think-scroll pre { margin: 0; font: 11px/1.85 ui-monospace, monospace; white-space: pre-wrap; overflow-wrap: anywhere; color: #868784; }
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
.progress-caption { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; margin: 20px 0 14px; padding-bottom: 10px; border-bottom: 1px solid var(--line); font-size: 13px; }
.progress-caption strong { font-weight: 600; }
.progress-caption > span { color: var(--muted); font-size: 11px; white-space: nowrap; }
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
.progress-note { color: var(--muted); font-size: 11px; line-height: 1.8; padding-top: 10px; border-top: 2px dashed #e4e5e0; }
.progress-note span { display: block; }
.progress-note time { display: block; color: var(--text); font-variant-numeric: tabular-nums; margin-bottom: 3px; }
.progress-error { display: flex; align-items: flex-start; gap: 8px; padding: 12px; background: #fff3f1; border-radius: 3px; color: var(--bad); font-size: 12px; }
.progress-error svg { flex-shrink: 0; }
.progress-actions { display: flex; justify-content: space-between; gap: 12px; margin-top: 18px; }
.progress-actions button { display: inline-flex; align-items: center; gap: 7px; min-height: 40px; padding: 8px 10px; border-radius: 3px; font-size: 12px; color: var(--muted); }
.progress-actions button:hover { background: var(--bg); }
.progress-actions .progress-retry { color: var(--accent); background: var(--accent-soft); }
@keyframes breathe { 50% { opacity: .35; transform: scale(.7); } }
@keyframes arrive { from { opacity: 0; transform: translateY(8px); } to { opacity: 1; transform: translateY(0); } }
@media (max-width: 900px) { .plan-progress { gap: 24px; grid-template-columns: minmax(0, .7fr) minmax(0, 1fr); } .generation-art { min-height: 360px; } }
@media (min-width: 641px) {
  .plan-progress { height: 100%; min-height: 0; width: 100%; padding: 12px 20px; grid-template-rows: minmax(0, 1fr); align-items: stretch; }
  .generation-art { position: relative; top: auto; min-height: 0; width: 100%; align-self: center; justify-self: center; }
  .generation-content { min-height: 0; max-height: 100%; align-self: center; overflow-y: auto; overflow-x: hidden; overscroll-behavior-y: contain; scrollbar-width: thin; scrollbar-color: #d4d5d2 transparent; }
}
/* 矮屏：清单压紧，票纸里尽量不滚 */
@media (min-width: 641px) and (max-height: 820px) {
  .plan-progress { padding-block: 0; }
  .generation-content { padding: 18px 28px 8px; }
  h1 { margin: 8px 0 4px; font-size: 25px; }
  .progress-caption { margin: 14px 0 10px; padding-bottom: 8px; }
  .progress-steps li { min-height: 48px; padding-bottom: 4px; }
  .output-toggle { padding: 12px 0; }
}
@media (max-width: 900px) { .generation-backdrop img { opacity: .5; } }
@media (max-width: 640px) { .generation-backdrop img { display: none; } .generation-content { padding: 18px 20px 8px; } }
@media (max-width: 640px) { .plan-progress { display: flex; flex-direction: column; padding: 4px 0 30px; gap: 10px; } .generation-art { position: relative; top: auto; min-height: 0; width: 100%; gap: 22px; padding-top: 14px; } .generation-scene { width: 210px; } .generation-code { width: 250px; height: 24px; } .generation-content { width: 100%; } h1 { margin-top: 12px; } }
@media (prefers-reduced-motion: reduce) { *, *::before, *::after, .generation-landscape * { animation: none !important; transition: none !important; } }
</style>
