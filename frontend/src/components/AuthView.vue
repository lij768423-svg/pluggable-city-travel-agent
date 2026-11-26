<script setup lang="ts">
// 登录页（2026-11-25 用户选“检票口”，取代布幡和居中白卡）：一张横票，左边插画、中间表单、右边竖条码，条码随填写逐根变黑。
// 从有行程票的页面进来，竖票直接展开成这张横票；返回或登录成功，横票收回竖票（gate.ts）。
// 票上是哪座城跟着进来时的竖票走（face）；没有竖票就按当前城市，中性入口是空票。
// going：选了城市、登录完就进城；gate：公网未登录时作为整站入口，不提供返回
import { computed, nextTick, onMounted, reactive, ref, watch } from 'vue'
import { PhArrowLeft, PhArrowRight, PhEye, PhEyeSlash } from '@phosphor-icons/vue'
import JourneyMark from './JourneyMark.vue'
import { cityArtwork } from '../cityArt'
import { authenticate, type AccountUser, type CityInfo } from '../api'
import type { GateFace } from '../gate'

const props = defineProps<{ city: CityInfo; neutral?: boolean; face?: GateFace | null; going?: string; reason?: string; inviteRequired?: boolean; gate?: boolean }>()
const emit = defineEmits<{ close: []; signedIn: [user: AccountUser] }>()
const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const confirm = ref('')
const invite = ref('')
const reveal = ref(false)
const busy = ref(false)
const error = ref('')
const heading = ref<HTMLElement | null>(null)
const form = ref<HTMLFormElement | null>(null)
const stub = ref<HTMLElement | null>(null)

const face = computed<Omit<GateFace, 'bars'>>(() => props.face ?? (props.neutral ? { name: '行记', code: 'NEXT STOP', art: null, photo: false } : { name: props.city.name, code: props.city.key.toUpperCase(), art: cityArtwork(props.city.key)?.right ?? null, photo: false }))
// 图已经在缓存里就直接是满的（和 TripTicket 一样）
const vReady = { beforeMount: (el: HTMLImageElement) => { if (el.complete) el.classList.add('is-loaded') } }
const verb = computed(() => mode.value === 'login' ? '登录' : '创建账号')
const why = computed(() => props.reason || `${verb.value}后${props.going ? `去${props.going}` : ''}，行程会保存到你的账号。`)
const action = computed(() => busy.value ? '正在检票…' : `${verb.value}${props.going ? `，去${props.going}` : ''}`)
const today = new Date()
const ticketNo = `${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, '0')}${String(today.getDate()).padStart(2, '0')}`

// 条码：从竖票进来就用那张票的条码，填了多少黑多少
const BARS = 46
const bars = computed(() => {
  if (props.face?.bars.length === BARS) return props.face.bars
  let hash = 2166136261
  for (const ch of face.value.code) hash = Math.imul(hash ^ ch.charCodeAt(0), 16777619) >>> 0
  return Array.from({ length: BARS }, () => { hash = Math.imul(hash ^ (hash >>> 13), 1274126177) >>> 0; return `${1 + (hash >>> 11) % 4}px` })
})
// 浏览器或密码管理器自动填上的格子算填满：Chrome 自动填的密码在用户碰页面之前读不到值，但框里已经看得见，条码要和看到的一致。
// 自动填充出现、消失时输入框各起一次空动画（样式里的 gate-autofill-on / off），在 animationstart 里看它现在是不是自动填的
const autofilled = reactive({ username: false, password: false, confirm: false, invite: false })
function sense(field: keyof typeof autofilled, event: AnimationEvent) {
  try { autofilled[field] = (event.target as HTMLInputElement).matches(':-webkit-autofill') } catch { autofilled[field] = false }
}
const filled = computed(() => {
  const part = (field: keyof typeof autofilled, value: string, need: number) => autofilled[field] ? 1 : value.length / need
  const parts = [part('username', username.value, 3), part('password', password.value, 10)]
  if (mode.value === 'register') parts.push(part('confirm', confirm.value, 10), ...(props.inviteRequired ? [part('invite', invite.value, 4)] : []))
  return Math.round(parts.reduce((sum, part) => sum + Math.min(1, part), 0) / parts.length * BARS)
})

watch(mode, () => { error.value = ''; password.value = ''; confirm.value = ''; reveal.value = false })
watch([username, password, confirm, invite], () => { if (!busy.value) error.value = '' })
// 有鼠标键盘的设备直接停在第一个没填的格子；触屏不弹键盘
function focusFirst() {
  const field = matchMedia('(hover: hover) and (pointer: fine)').matches ? [...form.value?.querySelectorAll('input') ?? []].find(input => !input.value) : undefined
  ;(field ?? heading.value)?.focus({ preventScroll: true })
}
onMounted(focusFirst)
function pick(next: 'login' | 'register') { if (mode.value === next) return; mode.value = next; void nextTick(focusFirst) }

function close() { if (!busy.value && !props.gate) emit('close') }
async function submit() {
  if (busy.value) return
  error.value = ''
  if (mode.value === 'register' && password.value !== confirm.value) { error.value = '两次输入的密码不一致。'; return }
  busy.value = true
  let failed = ''
  try {
    const response = await authenticate(mode.value, username.value.trim(), password.value, mode.value === 'register' && props.inviteRequired ? invite.value.trim() : undefined)
    emit('signedIn', response.user)
    return
  } catch (failure) { failed = (failure as Error).message || '暂时无法登录，请重试。' }
  password.value = ''; confirm.value = ''
  await nextTick()
  busy.value = false
  error.value = failed
  if (!matchMedia('(prefers-reduced-motion: reduce)').matches) stub.value?.animate({ transform: ['none', 'translateX(-5px)', 'translateX(5px)', 'translateX(-5px)', 'none'] }, { duration: 340 })
  await nextTick()
  form.value?.querySelector<HTMLInputElement>('input[type=password], input[autocomplete$=password]')?.focus({ preventScroll: true })
}
</script>

<template>
  <section class="auth-page" aria-labelledby="auth-title" @keydown.esc="close">
    <button v-if="!gate" type="button" class="auth-back" data-rise :disabled="busy" @click="close"><PhArrowLeft :size="14" />返回</button>
    <div class="auth-main">
      <div class="gate-ticket" :data-mode="mode">
        <div class="gate-sheet" aria-hidden="true" />
        <div class="gate-art" aria-hidden="true">
          <img v-if="face.art" v-ready :class="{ photo: face.photo }" :src="face.art" alt="" decoding="sync" data-fade /><JourneyMark v-else class="gate-mark" />
          <p class="gate-city"><b>{{ face.name }}</b><span>{{ face.code }}</span></p>
        </div>
        <div ref="stub" class="gate-stub" aria-hidden="true">
          <i class="gate-dash" />
          <p class="gate-caption"><span>检票口</span><span>No.{{ ticketNo }}</span></p>
          <div class="gate-code" :class="{ busy }"><i v-for="(width, index) in bars" :key="index" :class="{ lit: index < filled }" :style="{ width }" /></div>
        </div>
        <form ref="form" class="gate-form" @submit.prevent="submit">
          <div class="gate-tabs" data-rise role="tablist" aria-label="登录方式">
            <button type="button" role="tab" :class="{ on: mode === 'login' }" :aria-selected="mode === 'login'" :disabled="busy" @click="pick('login')">登录</button>
            <button type="button" role="tab" :class="{ on: mode === 'register' }" :aria-selected="mode === 'register'" :disabled="busy" @click="pick('register')">创建账号</button>
          </div>
          <div class="gate-head" data-rise><h1 id="auth-title" ref="heading" tabindex="-1">{{ mode === 'login' ? '欢迎回来' : '先领一张票' }}</h1><p>{{ why }}</p></div>
          <fieldset data-rise :disabled="busy">
            <label class="gate-field" :class="{ wide: mode === 'register' && !inviteRequired }"><span>用户名</span><span class="gate-input"><input v-model="username" @animationstart="sense('username', $event)" autocomplete="username" autocapitalize="off" spellcheck="false" required pattern="[A-Za-z0-9_]{3,32}" minlength="3" maxlength="32" placeholder="字母、数字或下划线" /></span></label>
            <label v-if="mode === 'register' && inviteRequired" class="gate-field"><span>邀请码</span><span class="gate-input"><input v-model="invite" @animationstart="sense('invite', $event)" autocomplete="off" required maxlength="64" placeholder="向网站管理员索取" /></span></label>
            <label class="gate-field"><span>密码</span><span class="gate-input"><input v-model="password" @animationstart="sense('password', $event)" :type="reveal ? 'text' : 'password'" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" required minlength="10" maxlength="128" placeholder="至少 10 位" /><button type="button" class="gate-reveal" :aria-pressed="reveal" :aria-label="reveal ? '隐藏密码' : '显示密码'" @click="reveal = !reveal"><component :is="reveal ? PhEyeSlash : PhEye" :size="17" /></button></span></label>
            <label v-if="mode === 'register'" class="gate-field"><span>确认密码</span><span class="gate-input"><input v-model="confirm" @animationstart="sense('confirm', $event)" :type="reveal ? 'text' : 'password'" autocomplete="new-password" required minlength="10" maxlength="128" placeholder="再输入一次" /></span></label>
          </fieldset>
          <p v-if="error" class="gate-note bad" data-rise role="alert">{{ error }}</p>
          <p v-else class="gate-note" data-rise>{{ mode === 'login' ? '还没有账号，先点上面的“创建账号”' : '用户名 3 到 32 位，密码至少 10 位' }}</p>
          <button class="gate-go" type="submit" :disabled="busy"><span class="gate-label">{{ action }}<PhArrowRight v-if="!busy" :size="18" aria-hidden="true" /></span></button>
        </form>
      </div>
    </div>
  </section>
</template>

<!-- 不加 scoped：gate.ts 会往票里补竖票的几样内容 -->
<style>
/* 齿孔大小要能做动画，得登记成长度 */
@property --notch-v { syntax: '<length>'; inherits: false; initial-value: 0px; }
@property --notch-h { syntax: '<length>'; inherits: false; initial-value: 11px; }
/* 底子和选城页、问卷页是同一张网格，盖上去接得住 */
.auth-page { --ticket-w: clamp(340px, 29vw, 388px); --stub-w: 148px; --signal: #b93b28; position: fixed; inset: var(--journey-bar-height, 64px) 0 0; z-index: 1400; overflow: hidden; background: #fff linear-gradient(to right, #1516130a 1px, transparent 1px) 0 calc(-1 * var(--journey-bar-height, 64px)) / 40px 40px; }
.auth-page::before { content: ''; position: absolute; inset: 0; background: linear-gradient(to bottom, #1516130a 1px, transparent 1px) 0 calc(-1 * var(--journey-bar-height, 64px)) / 40px 40px; pointer-events: none; }
.auth-back { position: absolute; z-index: 3; left: max(32px, calc((100vw - 1180px) / 2 + 32px)); top: 14px; display: inline-flex; align-items: center; gap: 6px; min-height: 38px; font-size: 13px; color: var(--muted); }
.auth-back:hover:not(:disabled) { color: var(--text); }
.auth-main { position: absolute; z-index: 2; inset: 0; display: grid; place-items: center; padding: 44px 32px 64px; overflow: hidden auto; }
.auth-page.morphing .auth-main { overflow: hidden; }
/* 收回竖票时底下的页面已经换好：底子当帧撤掉，只剩这张票在演 */
.auth-page.leaving { background: none; pointer-events: none; }
.auth-page.leaving::before, .auth-page.leaving .gate-code::after { display: none; }
.journey-shell[data-gate] :is(.tform, .landing-form) .ticket { visibility: hidden; }

.gate-ticket { position: relative; width: min(1040px, 100%); height: 424px; color: #151613; filter: drop-shadow(0 24px 34px #15161321) drop-shadow(0 2px 4px #15161312); }
/* 白底带四个齿孔：平时开的是横票那两个（上下），竖票那两个（左右）只在展开、收回时用 */
.gate-sheet { --seam: 0px; --at: calc(100% - var(--stub-w)); position: absolute; left: 0; top: 0; width: 100%; height: 100%; border-radius: 16px; background: #fff;
  -webkit-mask: radial-gradient(circle var(--notch-h) at var(--at) 0, #0000 98%, #000), radial-gradient(circle var(--notch-h) at var(--at) 100%, #0000 98%, #000), radial-gradient(circle var(--notch-v) at 0 var(--seam), #0000 98%, #000), radial-gradient(circle var(--notch-v) at 100% var(--seam), #0000 98%, #000); -webkit-mask-composite: source-in;
  mask: radial-gradient(circle var(--notch-h) at var(--at) 0, #0000 98%, #000), radial-gradient(circle var(--notch-h) at var(--at) 100%, #0000 98%, #000), radial-gradient(circle var(--notch-v) at 0 var(--seam), #0000 98%, #000), radial-gradient(circle var(--notch-v) at 100% var(--seam), #0000 98%, #000); mask-composite: intersect; }
.gate-art { position: absolute; left: 0; top: 0; width: calc(100% - var(--stub-w) - var(--ticket-w) - 22px); height: 100%; overflow: hidden; border-radius: 16px 0 0 16px; background: linear-gradient(#f2f3f3, #f9f9f9); }
.gate-art img { position: absolute; right: -40px; bottom: -10%; height: 116%; max-width: none; mix-blend-mode: multiply; -webkit-mask-image: linear-gradient(to right, transparent 6%, #000 38%); mask-image: linear-gradient(to right, transparent 6%, #000 38%); }
.gate-art img.photo { right: -60px; bottom: 0; height: 104%; filter: grayscale(1) brightness(1.22); }
.gate-mark { position: absolute; left: calc(100% - 240px); top: calc(50% - 130px); width: 190px; height: 190px; }
.gate-mark rect { display: none; }
.gate-mark circle:first-of-type { stroke-width: .5; }
.gate-city { position: absolute; left: 30px; bottom: 30px; margin: 0; }
.gate-city b { display: block; font-size: 76px; line-height: 1; font-weight: 650; letter-spacing: -.04em; transform-origin: 0 100%; white-space: nowrap; }
.gate-city span { display: block; margin-top: 14px; font-family: 'Geist Mono Variable', 'SFMono-Regular', Consolas, monospace; font-size: 12px; letter-spacing: .08em; color: #696b64; }

.gate-stub { position: absolute; right: 0; top: 0; width: var(--stub-w); height: 100%; }
.gate-dash { position: absolute; left: -1px; top: 20px; bottom: 20px; border-left: 2px dashed #dedfda; }
.gate-caption { position: absolute; left: 22px; top: 40px; bottom: 40px; margin: 0; display: flex; justify-content: space-between; writing-mode: vertical-rl; font-family: 'Geist Mono Variable', 'SFMono-Regular', Consolas, monospace; font-size: 12px; letter-spacing: .08em; color: #696b64; }
/* 条码和竖票上那条一样长，整根转过来 */
.gate-code { --w: calc(var(--ticket-w) - 44px); position: absolute; left: calc(84px - var(--w) / 2); top: calc(50% - 32px); width: var(--w); height: 64px; display: flex; justify-content: space-between; overflow: hidden; transform: rotate(-90deg); }
.gate-code i { height: 100%; background: #d9dad5; transition: background-color .18s; }
.gate-code i.lit { background: #151613; }
/* 展开、收回时颜色由 gate.ts 接着当前状态演，自己的过渡别插进来 */
.auth-page.morphing .gate-code i { transition: none; }
.gate-code::after { content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 2px; background: var(--signal); opacity: 0; }
.gate-code.busy::after { animation: gate-scan .9s cubic-bezier(.5, 0, .5, 1) infinite alternate; }
@keyframes gate-scan { 0% { opacity: 0; } 10%, 90% { opacity: 1; } 100% { left: calc(100% - 2px); opacity: 0; } }

.gate-form { position: absolute; right: var(--stub-w); top: 0; bottom: 0; width: calc(var(--ticket-w) + 22px); padding: 26px 34px 26px 32px; display: flex; flex-direction: column; }
.gate-tabs { display: flex; gap: 26px; }
.gate-tabs button { position: relative; padding: 4px 0 8px; font-size: 14px; color: #85877f; transition: color .2s; }
.gate-tabs button:disabled { opacity: 1; }
.gate-tabs button::after { content: ''; position: absolute; inset: auto 0 0; height: 2px; background: var(--signal); transform: scaleX(0); transform-origin: left; transition: transform var(--spring-settle-ms) var(--spring-settle); }
.gate-tabs .on { color: #151613; font-weight: 600; }
.gate-tabs .on::after { transform: scaleX(1); }
.gate-head h1 { margin: 8px 0 4px; font-size: 32px; line-height: 1.2; font-weight: 650; letter-spacing: -.03em; outline: none; }
.gate-head p { margin: 0; font-size: 14px; line-height: 20px; color: #696b64; text-wrap: pretty; }
.gate-form fieldset { border: 0; padding: 8px 0 0; margin: 0; min-width: 0; display: grid; grid-template-columns: minmax(0, 1fr); }
[data-mode=register] fieldset { grid-template-columns: repeat(2, minmax(0, 1fr)); column-gap: 18px; }
.gate-field { position: relative; display: block; padding-top: 11px; border-bottom: 1.5px solid #dedfda; }
.gate-field.wide { grid-column: 1 / -1; }
.gate-field > span:first-child { display: block; font-size: 12px; line-height: 18px; color: #696b64; }
.gate-field::after { content: ''; position: absolute; left: 0; right: 0; bottom: -1.5px; height: 2px; background: var(--signal); transform: scaleX(0); transform-origin: left; transition: transform var(--spring-settle-ms) var(--spring-settle); }
.gate-field:focus-within::after { transform: scaleX(1); }
.gate-input { display: flex; align-items: center; }
.gate-input input { flex: 1; width: 100%; min-width: 0; height: 34px; padding: 0; border: 0; border-radius: 0; outline: none; background: transparent; font-size: 17px; font-weight: 550; color: #151613; }
.gate-input input:focus { box-shadow: none; }
/* 只为在自动填充出现、消失时各收到一次 animationstart（见 sense），动画本身什么都不改 */
@keyframes gate-autofill-on { from { opacity: 1; } to { opacity: 1; } }
@keyframes gate-autofill-off { from { opacity: 1; } to { opacity: 1; } }
.gate-input input { animation: gate-autofill-off 1ms; }
.gate-input input:-webkit-autofill { animation-name: gate-autofill-on; }
.gate-input input::placeholder { color: #8f918a; font-size: 14px; font-weight: 400; opacity: 1; }
[data-mode=register] .gate-input input::placeholder { font-size: 13px; }
.gate-input input:-webkit-autofill, .gate-input input:-webkit-autofill:hover, .gate-input input:-webkit-autofill:focus { -webkit-box-shadow: 0 0 0 40px #fff inset; -webkit-text-fill-color: #151613; caret-color: #151613; transition: background-color 9999s; }
.gate-reveal { flex-shrink: 0; width: 34px; height: 34px; margin-right: -8px; display: grid; place-items: center; border-radius: 8px; color: #85877f; }
.gate-reveal:hover { color: #151613; }
.gate-note { margin: 12px 0; font-size: 12px; line-height: 18px; color: #696b64; }
.gate-note.bad { color: #a33535; }
.gate-go { position: relative; width: 100%; min-height: 48px; margin-top: auto; border-radius: 10px; background: #151613; color: #fff; font-size: 15px; font-weight: 500; white-space: nowrap; transition: background-color .2s; }
.gate-go:disabled { opacity: 1; cursor: default; }
.gate-label { display: flex; align-items: center; justify-content: center; gap: 12px; }
.gate-label svg { transition: transform var(--spring-settle-ms) var(--spring-settle); }
@media (hover: hover) { .gate-go:hover:not(:disabled) .gate-label svg { transform: translateX(4px); } }
/* 竖票的内容（gate.ts 临时补上） */
.gate-vertical { --ticket-ink: #151613; --ticket-muted: #696b64; --ticket-line: #dedfda; position: absolute; inset: 0; pointer-events: none; }
.gate-seam { border-top: 2px dashed #dedfda; }
.gate-label.vertical { position: absolute; inset: 0; }

@media (max-width: 900px) {
  .auth-back { left: 18px; top: 8px; }
  .auth-main { place-items: start center; padding: 52px 16px calc(28px + env(safe-area-inset-bottom)); }
  .gate-ticket { height: auto; width: min(460px, 100%); }
  .gate-sheet { --notch-h: 0px; }
  .gate-art { position: relative; width: 100%; height: 132px; border-radius: 16px 16px 0 0; }
  .gate-art img { right: -14px; bottom: -14%; height: 118%; }
  .gate-mark { left: calc(100% - 140px); top: calc(50% - 55px); width: 110px; height: 110px; }
  .gate-city { left: 20px; bottom: 16px; }
  .gate-city b { font-size: 44px; }
  .gate-city span { margin-top: 8px; }
  .gate-form { position: relative; right: auto; width: 100%; padding: 16px 20px 18px; }
  .gate-go { margin-top: 2px; }
  .gate-input input { font-size: 16px; }
  .gate-stub { position: relative; width: 100%; height: 78px; display: block; }
  .gate-ticket { display: flex; flex-direction: column; }
  .gate-stub { order: 3; }
  .gate-dash { left: 18px; right: 18px; top: 0; bottom: auto; border-left: 0; border-top: 2px dashed #dedfda; }
  .gate-caption { display: none; }
  .gate-code { left: 20px; right: 20px; top: 18px; width: auto; height: 42px; transform: none; }
}
@media (max-height: 600px) and (min-width: 901px) { .auth-main { padding-block: 30px 24px; } }
@media (prefers-reduced-motion: reduce) { .auth-page *, .auth-page *::after { animation: none !important; transition: none !important; } }
</style>
