<script setup lang="ts">
// 跟 AI 说（2026-10-28 用户决定）：用户说一句，AI 回“我是这么理解的”；想改就接着说，表里改完点“提交修改”也当成用户说的一句话，
// 所有话合在一起重新理解，后说的为准。确认后才开始规划。
// 界面按常见的网页聊天来做（用户：改成 Claude / GPT 网页版那样）：没说话时输入框居中，说了之后是消息流、输入框贴底；
// 每轮回复都留着，旧的只写改了什么，最新一轮带完整的表和“开始规划”。
import { computed, nextTick, ref, watch } from 'vue'
import { PhArrowRight, PhArrowUp, PhNotePencil, PhSparkle, PhWarningCircle } from '@phosphor-icons/vue'
import type { Hotel, Meta, Understanding } from '../api'
import UnderstandingTable from './UnderstandingTable.vue'

const props = defineProps<{ meta: Meta | null; messages: string[]; replies: (Understanding | null)[]; understanding: Understanding | null; busy: boolean; error: string;
  disabled?: boolean; needsLogin?: boolean; quota?: { limit: number; remaining: number } | null }>()
const emit = defineEmits<{ send: [text: string, places: Hotel[]]; retry: []; confirm: []; reset: [] }>()

const SUGGESTIONS = ['周末和女朋友一日游，不想太累', '带爸妈玩三天，人均 1500，别太赶', '第一次来，想去最有名的几个地方', '一个人随便逛逛，坐公交地铁']

const draft = ref('')
const pending = ref<string[]>([])  // 表里改了还没提交的几处，发送时和输入框里的话一起发
const pendingPatch = ref<Record<string, unknown>>({})  // 同一批修改的值；起点要带上坐标
const input = ref<HTMLTextAreaElement>()
const scroller = ref<HTMLElement>()
const blocked = computed(() => !!props.understanding?.issues.some(issue => issue.level === 'blocker'))
const outOfQuota = computed(() => !!props.quota && props.quota.remaining <= 0)
// 最近一次成功的理解在第几句（某句失败时回复为空）
function previous(index: number): number {
  for (let at = index - 1; at >= 0; at -= 1) if (props.replies[at]) return at
  return -1
}
const latest = computed(() => previous(props.replies.length))
const canSend = computed(() => !props.busy && !props.disabled && (!!draft.value.trim() || !!pending.value.length))

// 展示文字里括号是说明（如预算含哪些），聊天里只留值
const short = (text: string) => text.replace(/（[^）]*）/g, '').trim()
const SUMMARY_KEYS = ['start_date', 'days', 'companions', 'budget', 'transport', 'pace', 'interests', 'must_visit', 'avoid']
// 旧的几轮不再带表，用一行写当时的理解
function summary(index: number): string {
  const reply = props.replies[index]
  if (!reply) return ''
  return SUMMARY_KEYS.map(key => reply.fields.find(field => field.key === key))
    .filter(field => field && !(field.key === 'start_date' && field.source === 'default'))
    .map(field => field!.key === 'must_visit' ? `必去${short(field!.display)}` : field!.key === 'avoid' ? `不去${short(field!.display)}` : short(field!.display)).join(' · ')
}

// 跟上一轮比改了什么；表里每项都有展示文字，直接比文字
function changes(index: number): string[] {
  const now = props.replies[index]
  const before = props.replies[previous(index)]
  if (!now || !before) return []
  const old = new Map(before.fields.map(field => [field.key, field]))
  const lines: string[] = []
  for (const field of now.fields) {
    const was = old.get(field.key)
    old.delete(field.key)
    if (!was) lines.push(`${field.label}：${short(field.display)}`)
    else if (was.display !== field.display) lines.push(`${field.label}：${short(was.display)} → ${short(field.display)}`)
  }
  for (const field of old.values()) lines.push(`${field.label}：去掉了${short(field.display)}`)
  return lines
}

function grow() {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 200)}px`
}
function send() {
  const message = [...pending.value, draft.value.trim()].filter(Boolean).join('；')
  const place = pendingPatch.value.hotel as Hotel | null | undefined
  if (message && !props.busy && !props.disabled) emit('send', message, place ? [place] : [])
}
function onEnter(event: KeyboardEvent) {
  if (event.isComposing) return  // 中文输入法选字时的回车不是发送
  event.preventDefault()
  send()
}
function suggest(text: string) {
  draft.value = text
  nextTick(() => { grow(); input.value?.focus() })
}

// 外层收下这句话（加进对话）才清空输入；没登录等情况下保留，登录后还能接着发
watch(() => props.messages.length, (now, before) => {
  if (now > before) { draft.value = ''; nextTick(grow) }
  pending.value = []
  pendingPatch.value = {}
})
// 新消息、正在理解、回复到了：把最后一句用户的话滚到顶上，下面接着就是回复
watch(() => [props.messages.length, props.busy, latest.value, props.error], () => nextTick(() => {
  const box = scroller.value
  const last = [...(box?.querySelectorAll<HTMLElement>('.chat-user') ?? [])].pop()
  if (box && last) box.scrollTo({ top: Math.max(0, last.offsetTop - 16), behavior: 'smooth' })
}))
</script>

<template>
  <section class="qform-card agent-chat" :class="{ empty: !messages.length }" aria-labelledby="agent-title" :aria-busy="busy">
    <header class="chat-head">
      <span class="chat-title">跟 AI 说</span>
      <button v-if="messages.length" type="button" class="chat-new" :disabled="busy" @click="emit('reset')"><PhNotePencil :size="16" />新对话</button>
    </header>

    <div ref="scroller" class="chat-scroll">
      <div v-if="!messages.length" class="chat-welcome">
        <h2 id="agent-title" tabindex="-1">想怎么玩，直接说</h2>
        <p>说得随意一点也行。没说的我先按常见情况猜，猜了什么都会标出来，不对再说一句就行。</p>
      </div>
      <ol v-else class="chat-thread" role="log" aria-label="对话">
        <template v-for="(text, index) in messages" :key="index">
          <li class="chat-user">{{ text }}</li>
          <li v-if="replies[index]" class="chat-ai">
            <span class="chat-avatar" aria-hidden="true"><PhSparkle :size="15" weight="fill" /></span>
            <div class="chat-ai-body">
              <template v-if="index === latest && !changes(index).length && previous(index) >= 0">
                <p>这句我没看出要改哪一项，下面还是原来的理解。可以换个说法，或者直接在表里改。</p>
              </template>
              <template v-else-if="changes(index).length">
                <p>好的，改了这几处：</p>
                <ul class="chat-changes"><li v-for="line in changes(index)" :key="line">{{ line }}</li></ul>
              </template>
              <p v-else-if="index === latest">我是这么理解的。标黄的是我猜的，不对就直接告诉我，或者在表里改完点“提交修改”。</p>
              <p v-else>我是这么理解的：{{ summary(index) }}</p>
              <template v-if="index === latest && understanding">
                <p v-if="changes(index).length" class="chat-now">现在是这样：</p>
                <UnderstandingTable :understanding="understanding" :meta="meta" :busy="busy" mode="agent" @apply="send" @pending="(phrases, patch) => { pending = phrases; pendingPatch = patch }" />
                <div class="chat-actions">
                  <button type="button" class="journey-primary" :disabled="busy || disabled || blocked || outOfQuota || !!pending.length" @click="emit('confirm')">就这样，开始规划<PhArrowRight :size="18" /></button>
                  <span v-if="pending.length" class="chat-action-note">先提交表里的修改</span>
                  <span v-else-if="blocked" class="chat-action-note">先处理上面标红的问题</span>
                  <span v-else-if="quota" class="chat-action-note" :class="{ bad: outOfQuota }">{{ outOfQuota ? '今天的生成次数已用完' : `今天还可生成 ${quota.remaining}/${quota.limit} 次` }}</span>
                </div>
              </template>
            </div>
          </li>
        </template>
        <li v-if="busy" class="chat-ai" role="status">
          <span class="chat-avatar" aria-hidden="true"><PhSparkle :size="15" weight="fill" /></span>
          <div class="chat-ai-body chat-typing"><span class="chat-dots" aria-hidden="true"><i /><i /><i /></span>正在理解…</div>
        </li>
        <li v-else-if="error" class="chat-ai" role="alert">
          <span class="chat-avatar bad" aria-hidden="true"><PhWarningCircle :size="15" /></span>
          <div class="chat-ai-body"><p class="chat-error">{{ error }}</p><button type="button" class="chat-retry" @click="emit('retry')">重试</button></div>
        </li>
      </ol>
    </div>

    <div class="chat-dock">
      <form class="chat-composer" @submit.prevent="send">
        <textarea ref="input" v-model="draft" rows="1" aria-label="说说你想怎么玩" :disabled="busy || disabled"
          :placeholder="messages.length ? '还想改什么，直接说' : '说说你想怎么玩'"
          @input="grow" @keydown.enter.exact="onEnter" />
        <button type="submit" class="chat-send" :disabled="!canSend" :aria-label="needsLogin ? '登录后发送' : '发送'" :title="needsLogin ? '登录后发送' : '发送'"><PhArrowUp :size="18" weight="bold" /></button>
      </form>
      <div v-if="!messages.length" class="chat-suggestions">
        <button v-for="text in SUGGESTIONS" :key="text" type="button" :disabled="disabled" @click="suggest(text)">{{ text }}</button>
      </div>
      <p class="chat-hint">{{ pending.length ? `表里改了 ${pending.length} 处，会和这句话一起发` : needsLogin ? '登录后就能发送，写好的话会保留' : 'Enter 发送，Shift + Enter 换行' }}</p>
    </div>
  </section>
</template>

<style scoped>
.agent-chat { flex: 1 1 auto; height: 100%; }
.chat-head { flex-shrink: 0; display: flex; align-items: center; justify-content: space-between; gap: 12px; min-height: 48px; padding: 8px 14px 8px 20px; border-bottom: 1px solid var(--line); }
.chat-title { font-size: 14px; font-weight: 600; }
.chat-new { display: inline-flex; align-items: center; gap: 6px; font: inherit; font-size: 13px; padding: 6px 10px; border: 0; border-radius: 6px; background: transparent; color: var(--muted); cursor: pointer; }
.chat-new:hover:not(:disabled) { background: var(--accent-soft); color: var(--text); }

.chat-scroll { position: relative; flex: 1 1 auto; min-height: 0; overflow-y: auto; overscroll-behavior: contain; scrollbar-width: thin; scrollbar-color: var(--line-strong) transparent; }
.agent-chat.empty .chat-scroll { flex: 0 0 auto; margin-top: auto; overflow: visible; }
.agent-chat.empty .chat-dock { margin-bottom: auto; padding-bottom: 28px; }
.chat-welcome { max-width: 720px; margin: 0 auto; padding: 24px 20px 20px; text-align: center; }
.chat-welcome h2 { margin: 0 0 10px; font-size: clamp(26px, 2.6vw, 32px); line-height: 1.3; font-weight: 650; letter-spacing: -.02em; outline: none; }
.chat-welcome p { margin: 0; font-size: 14px; line-height: 1.7; color: var(--muted); }

.chat-thread { list-style: none; max-width: 760px; margin: 0 auto; padding: 24px 20px 12px; display: flex; flex-direction: column; gap: 22px; }
.chat-user { align-self: flex-end; max-width: 75%; padding: 10px 16px; border-radius: 18px; background: var(--accent-soft); font-size: 15px; line-height: 1.6; white-space: pre-wrap; overflow-wrap: anywhere; }
.chat-ai { display: flex; align-items: flex-start; gap: 12px; }
.chat-avatar { flex: 0 0 28px; height: 28px; display: grid; place-items: center; border: 1px solid var(--line); border-radius: 50%; background: #ffffff; color: var(--text); }
.chat-avatar.bad { color: var(--signal); }
.chat-ai-body { flex: 1; min-width: 0; padding-top: 3px; font-size: 15px; line-height: 1.7; }
.chat-ai-body > p { margin: 0 0 10px; }
.chat-changes { margin: 0 0 12px; padding-left: 1.2em; }
.chat-changes li { margin: 2px 0; }
.chat-now { color: var(--muted); font-size: 14px; }
.chat-actions { display: flex; flex-wrap: wrap; align-items: center; gap: 10px 16px; margin-top: 16px; }
.chat-action-note { font-size: 13px; color: var(--muted); }
.chat-action-note.bad { color: var(--signal); }
.chat-typing { display: flex; align-items: center; gap: 10px; color: var(--muted); font-size: 14px; }
.chat-dots { display: inline-flex; gap: 4px; }
.chat-dots i { width: 6px; height: 6px; border-radius: 50%; background: var(--muted); animation: chat-dot 1.2s infinite ease-in-out; }
.chat-dots i:nth-child(2) { animation-delay: .15s; }
.chat-dots i:nth-child(3) { animation-delay: .3s; }
@keyframes chat-dot { 0%, 80%, 100% { opacity: .25; transform: translateY(0); } 40% { opacity: 1; transform: translateY(-3px); } }
@media (prefers-reduced-motion: reduce) { .chat-dots i { animation: none; opacity: .6; } }
.chat-error { color: var(--signal); }
.chat-retry { font: inherit; font-size: 13px; padding: 4px 12px; border: 1px solid var(--line-strong); border-radius: 6px; background: #ffffff; cursor: pointer; }

.chat-dock { flex-shrink: 0; padding: 8px 20px 12px; }
.chat-composer { max-width: 760px; margin: 0 auto; display: flex; align-items: flex-end; gap: 8px; padding: 8px 8px 8px 18px; border: 1px solid var(--line-strong); border-radius: 24px; background: #ffffff; box-shadow: 0 8px 28px -20px #15161380; transition: border-color .15s; }
.chat-composer:focus-within { border-color: var(--text); }
.chat-composer textarea { flex: 1; min-width: 0; min-height: 0; height: 37px; max-height: 200px; padding: 7px 0; border: 0; outline: none; resize: none; background: transparent; font: inherit; font-size: 15px; line-height: 1.5; color: var(--text); }
.chat-composer textarea::placeholder { color: var(--faint); }
.chat-send { flex: 0 0 36px; height: 36px; display: grid; place-items: center; border: 0; border-radius: 50%; background: var(--text); color: #ffffff; cursor: pointer; }
.chat-send:disabled { background: var(--line); color: #ffffff; cursor: default; }
.chat-suggestions { max-width: 760px; margin: 14px auto 0; display: flex; flex-wrap: wrap; justify-content: center; gap: 8px; }
.chat-suggestions button { font: inherit; font-size: 13px; padding: 7px 14px; border: 1px solid var(--line); border-radius: 18px; background: #ffffff; color: var(--muted); cursor: pointer; }
.chat-suggestions button:hover:not(:disabled) { border-color: var(--line-strong); color: var(--text); }
.chat-hint { max-width: 760px; margin: 6px auto 0; text-align: center; font-size: 12px; color: var(--faint); }

@media (max-width: 560px) {
  .chat-head { padding: 6px 8px 6px 16px; }
  .chat-thread { padding: 18px 16px 8px; gap: 18px; }
  .chat-user { max-width: 85%; }
  .chat-ai { gap: 8px; }
  .chat-avatar { flex-basis: 24px; height: 24px; }
  .chat-dock { padding: 6px 12px 10px; }
  .chat-welcome { padding-inline: 16px; }
}
</style>
