<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { PhArrowLeft, PhArrowRight, PhEye, PhEyeSlash, PhLockSimple, PhTicket, PhUser } from '@phosphor-icons/vue'
import BrandMark from './BrandMark.vue'
import sunset from '../assets/backdrop/hangzhou-south.svg?raw'
import bridge from '../assets/backdrop/hangzhou-west.svg?raw'
import { authenticate, type AccountUser } from '../api'

// 登录页：全屏动态插画（左断桥北望、右雷峰夕照），左上垂挂竖排布幡，中间一张白色船票表单
// gate：公网未登录时作为整站入口，不提供返回
const props = defineProps<{ reason?: string; inviteRequired?: boolean; gate?: boolean }>()
const invite = ref('')
const emit = defineEmits<{ close: []; signedIn: [user: AccountUser] }>()
const mode = ref<'login' | 'register'>('login')
const username = ref('')
const password = ref('')
const confirm = ref('')
const reveal = ref(false)
const busy = ref(false)
const error = ref('')
const heading = ref<HTMLElement | null>(null)
const today = new Date()
const ticketNo = computed(() => `${today.getFullYear()}${String(today.getMonth() + 1).padStart(2, '0')}${String(today.getDate()).padStart(2, '0')}`)
watch(mode, () => { error.value = ''; password.value = ''; confirm.value = ''; reveal.value = false })
onMounted(() => heading.value?.focus({ preventScroll: true }))

function close() { if (!busy.value && !props.gate) emit('close') }
async function submit() {
  if (busy.value) return
  error.value = ''
  if (mode.value === 'register' && password.value !== confirm.value) { error.value = '两次输入的密码不一致。'; return }
  busy.value = true
  try {
    const response = await authenticate(mode.value, username.value.trim(), password.value, mode.value === 'register' && props.inviteRequired ? invite.value.trim() : undefined)
    emit('signedIn', response.user)
  } catch (failure) { error.value = (failure as Error).message || '暂时无法登录，请重试。' }
  finally { password.value = ''; confirm.value = ''; busy.value = false }
}
</script>

<template>
  <section class="auth-page" aria-labelledby="auth-title" @keydown.esc="close">
    <div class="auth-scene questionnaire-backdrop" aria-hidden="true">
      <div class="auth-art auth-west" v-html="bridge" />
      <div class="auth-art auth-south" v-html="sunset" />
    </div>
    <div class="auth-banner" aria-hidden="true">
      <div class="banner-text"><strong>杭州行记</strong><span>一程山水 · 一份行记</span></div>
      <i class="auth-seal">行记</i>
    </div>
    <button v-if="!gate" type="button" class="auth-back" :disabled="busy" @click="close"><PhArrowLeft :size="16" />返回</button>

    <div class="auth-main">
      <div class="auth-ticket" :data-mode="mode">
        <p class="ticket-head"><BrandMark /><span>西湖游船票</span><time>No.{{ ticketNo }}</time></p>
        <h1 id="auth-title" ref="heading" tabindex="-1">{{ mode === 'login' ? '欢迎回来' : '领一张船票' }}</h1>
        <p v-if="reason" class="auth-reason">{{ reason }}</p>
        <div class="auth-tabs" role="tablist" aria-label="登录方式">
          <button type="button" role="tab" :aria-selected="mode === 'login'" :disabled="busy" @click="mode = 'login'">登录</button>
          <button type="button" role="tab" :aria-selected="mode === 'register'" :disabled="busy" @click="mode = 'register'">创建账号</button>
          <span class="auth-tab-indicator" aria-hidden="true" />
        </div>
        <form @submit.prevent="submit">
          <fieldset :disabled="busy">
            <label class="auth-field"><span>用户名</span>
              <span class="auth-input"><PhUser :size="18" /><input v-model="username" autocomplete="username" required pattern="[A-Za-z0-9_]{3,32}" minlength="3" maxlength="32" placeholder="字母、数字或下划线" /></span>
            </label>
            <label class="auth-field"><span>密码</span>
              <span class="auth-input"><PhLockSimple :size="18" /><input v-model="password" :type="reveal ? 'text' : 'password'" :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" required minlength="10" maxlength="128" placeholder="至少 10 位" />
                <button type="button" class="auth-reveal" :aria-pressed="reveal" :aria-label="reveal ? '隐藏密码' : '显示密码'" @click="reveal = !reveal"><component :is="reveal ? PhEyeSlash : PhEye" :size="18" /></button></span>
            </label>
            <label v-if="mode === 'register'" class="auth-field"><span>确认密码</span>
              <span class="auth-input"><PhLockSimple :size="18" /><input v-model="confirm" :type="reveal ? 'text' : 'password'" autocomplete="new-password" required minlength="10" maxlength="128" placeholder="再输入一次" /></span>
            </label>
            <label v-if="mode === 'register' && inviteRequired" class="auth-field"><span>邀请码</span>
              <span class="auth-input"><PhTicket :size="18" /><input v-model="invite" autocomplete="off" required maxlength="64" placeholder="向网站管理员索取" /></span>
            </label>
          </fieldset>
          <p v-if="error" class="auth-error" role="alert">{{ error }}</p>
          <button class="auth-submit" type="submit" :disabled="busy">{{ busy ? '正在连接…' : mode === 'login' ? '登船' : '领票并登船' }}<PhArrowRight :size="18" /></button>
        </form>
      </div>
    </div>

  </section>
</template>

<style scoped>
.auth-page { --seal: #c8463a; position: fixed; inset: var(--journey-bar-height, 64px) 0 0; z-index: 1400; overflow: hidden; background: #eef2fb; animation: auth-fade .4s ease-out both; }

/* 全屏插画：两幅 4:5 立轴贴底分居左右，内侧渐隐到底色 */
.auth-scene { position: absolute; inset: 0; overflow: hidden; }
.auth-art { position: absolute; bottom: 0; height: 100%; aspect-ratio: 4 / 5; }
.auth-art :deep(svg) { width: 100%; height: 100%; display: block; }
.auth-west { left: clamp(-160px, (100vw - 1500px) / 2, 0px); -webkit-mask-image: linear-gradient(to right, #000 62%, transparent 98%); mask-image: linear-gradient(to right, #000 62%, transparent 98%); }
.auth-south { right: clamp(-160px, (100vw - 1500px) / 2, 0px); -webkit-mask-image: linear-gradient(to left, #000 62%, transparent 98%); mask-image: linear-gradient(to left, #000 62%, transparent 98%); }

/* 左上：垂挂布幡 */
.auth-banner { position: absolute; z-index: 2; left: clamp(28px, 5vw, 72px); top: 0; display: flex; flex-direction: column; align-items: center; gap: 18px; padding: 40px 18px 24px; background: #fffdf7; border-radius: 0 0 14px 14px; box-shadow: 0 0 0 1px #ece5d4, 0 24px 48px -28px #1f3a7a66; transform-origin: 50% 0; animation: banner-drop .9s cubic-bezier(.16, 1, .3, 1) both, banner-sway 7s 1s ease-in-out infinite; }
.auth-banner::before { content: ''; position: absolute; top: 12px; left: -14px; right: -14px; height: 7px; border-radius: 4px; background: #1d2a4a; }
.banner-text { display: flex; flex-direction: row-reverse; align-items: flex-start; gap: 10px; }
.banner-text strong { writing-mode: vertical-rl; font-family: 'Songti SC', 'STSong', 'Noto Serif CJK SC', 'Source Han Serif SC', serif; font-size: clamp(44px, 5.2vw, 78px); font-weight: 900; line-height: 1; letter-spacing: .12em; color: #1d2a4a; }
.banner-text span { writing-mode: vertical-rl; margin-top: 6px; font-family: 'Songti SC', 'STSong', 'Noto Serif CJK SC', serif; font-size: 14px; letter-spacing: .45em; color: #4b5b80; }
.auth-seal { display: grid; place-items: center; width: 46px; height: 46px; border-radius: 6px; background: var(--seal); color: #fff; font-style: normal; font-family: 'Songti SC', 'STSong', 'Noto Serif CJK SC', serif; font-size: 15px; font-weight: 900; writing-mode: vertical-rl; box-shadow: inset 0 0 0 2px #fff6, 0 4px 10px -6px #8a2a20; transform: rotate(-7deg); animation: seal-stamp .55s 1s cubic-bezier(.3, 1.6, .5, 1) both; }

.auth-back { position: absolute; z-index: 3; top: 18px; right: 22px; display: inline-flex; align-items: center; gap: 6px; min-height: 38px; padding: 0 14px; border-radius: 999px; background: #fff; box-shadow: 0 0 0 1px #e1e7f2; color: var(--muted); font-size: 13px; }
.auth-back:hover:not(:disabled) { color: var(--accent); }

/* 中：船票 */
.auth-main { position: absolute; z-index: 2; inset: 0; display: grid; place-items: center; padding: 24px; overflow-y: auto; }
.auth-ticket { width: min(420px, 100%); display: grid; padding: 26px 30px 30px; background: #fff; border-radius: 22px; box-shadow: 0 0 0 1px #e3e8f2, 0 40px 80px -40px #1f3a7a80; animation: ticket-arrive .7s .15s cubic-bezier(.16, 1, .3, 1) both; }
.ticket-head { display: flex; align-items: center; gap: 8px; font-size: 12px; color: var(--muted); letter-spacing: .08em; }
.ticket-head :deep(.brand-mark) { width: 24px; height: 24px; }
.ticket-head time { margin-left: auto; font-family: ui-monospace, 'SF Mono', Menlo, monospace; font-size: 11px; letter-spacing: .04em; color: var(--faint); }
.auth-ticket h1 { margin-top: 22px; font-size: 34px; font-weight: 700; letter-spacing: -.03em; line-height: 1.2; outline: none; }
.auth-reason { margin-top: 8px; color: var(--muted); font-size: 14px; }
.auth-tabs { position: relative; display: grid; grid-template-columns: 1fr 1fr; margin: 22px 0 20px; padding: 4px; border-radius: 12px; background: #eef2f9; }
.auth-tabs button { position: relative; z-index: 1; min-height: 40px; border-radius: 9px; font-size: 14px; color: var(--muted); transition: color .2s; }
.auth-tabs button[aria-selected=true] { color: var(--text); font-weight: 600; }
.auth-tab-indicator { position: absolute; top: 4px; bottom: 4px; left: 4px; width: calc(50% - 4px); border-radius: 9px; background: #fff; box-shadow: 0 1px 3px #1d243314, 0 4px 12px -6px #1d243326; transition: transform .4s cubic-bezier(.16, 1, .3, 1); }
.auth-ticket[data-mode=register] .auth-tab-indicator { transform: translateX(100%); }
fieldset { border: 0; padding: 0; margin: 0; display: grid; gap: 14px; min-width: 0; }
.auth-field { display: grid; gap: 7px; font-size: 13px; font-weight: 500; color: #3e485b; }
.auth-input { display: flex; align-items: center; gap: 10px; min-height: 50px; padding: 0 6px 0 14px; border: 1px solid #dce2ee; border-radius: 12px; background: #fff; color: #8a97ac; transition: border-color .2s, box-shadow .2s; }
.auth-input:focus-within { border-color: var(--accent); box-shadow: 0 0 0 3px #2f5bd31f; color: var(--accent); }
.auth-input input { flex: 1; min-width: 0; height: 46px; padding: 0; border: 0; border-radius: 0; outline: none; background: transparent; font-size: 15px; color: var(--text); }
/* 外框已负责聚焦高亮，屏蔽全站 input:focus 的光晕，避免框里再套一个小框 */
.auth-input input:focus { box-shadow: none; }
.auth-input input:-webkit-autofill, .auth-input input:-webkit-autofill:hover, .auth-input input:-webkit-autofill:focus { -webkit-box-shadow: 0 0 0 40px #fff inset; -webkit-text-fill-color: var(--text); caret-color: var(--text); transition: background-color 9999s; }
.auth-input input::placeholder { color: #a3adbe; font-size: 13px; }
.auth-reveal { width: 38px; height: 38px; display: grid; place-items: center; border-radius: 8px; color: #8a97ac; }
.auth-reveal:hover { background: #f1f4fa; color: var(--accent); }
.auth-error { margin-top: 14px; padding: 10px 12px; border-radius: 10px; background: #fff3f1; color: var(--bad); font-size: 13px; }
.auth-submit { width: 100%; margin-top: 22px; display: inline-flex; align-items: center; justify-content: center; gap: 10px; min-height: 52px; border-radius: 13px; background: var(--accent); color: #fff; font-size: 16px; font-weight: 600; letter-spacing: .08em; transition: background .2s, transform .2s; }
.auth-submit:hover:not(:disabled) { background: #244dbd; transform: translateY(-1px); }
.auth-submit svg { transition: transform .3s cubic-bezier(.16, 1, .3, 1); }
.auth-submit:hover:not(:disabled) svg { transform: translateX(4px); }

@keyframes banner-drop { from { transform: translateY(-105%); } to { transform: translateY(0); } }
@keyframes banner-sway { 0%, 100% { transform: rotate(0); } 50% { transform: rotate(.8deg); } }
@keyframes seal-stamp { from { opacity: 0; transform: rotate(-7deg) scale(1.8); } to { opacity: 1; transform: rotate(-7deg) scale(1); } }
@keyframes ticket-arrive { from { opacity: 0; transform: translateY(18px); } to { opacity: 1; transform: translateY(0); } }
@keyframes auth-fade { from { opacity: 0; } to { opacity: 1; } }

@media (min-width: 861px) and (max-height: 780px) {
  .auth-ticket { padding: 18px 26px 22px; }
  .auth-ticket h1 { margin-top: 12px; font-size: 28px; }
  .auth-tabs { margin: 14px 0 12px; }
  fieldset { gap: 9px; }
  .auth-field { gap: 5px; }
  .auth-input { min-height: 44px; }
  .auth-input input { height: 42px; }
  .auth-submit { margin-top: 14px; min-height: 46px; }
  .banner-text strong { font-size: clamp(40px, 4.4vw, 64px); }
}
@media (max-width: 860px) {
  .auth-west, .banner-text span, .auth-seal { display: none; }
  .auth-south { right: auto; left: 50%; transform: translateX(-50%); -webkit-mask-image: none; mask-image: none; }
  .auth-banner { left: 16px; padding: 28px 10px 14px; }
  .auth-banner::before { top: 9px; left: -8px; right: -8px; height: 5px; }
  .banner-text strong { font-size: 30px; }
  .auth-back { top: 12px; right: 12px; }
  .auth-main { place-items: end center; padding: 190px 16px 20px; }
  .auth-ticket { padding: 22px 20px 24px; }
  .auth-ticket h1 { font-size: 28px; margin-top: 16px; }
}
@media (prefers-reduced-motion: reduce) {
  .auth-banner, .auth-seal, .auth-ticket, .auth-tab-indicator, .auth-submit, .auth-submit svg, .auth-page { animation: none !important; transition: none !important; }
}
</style>
