<script setup lang="ts">
// 跟 AI 说（2026-10-28 用户决定）：用户说一句，AI 回“我是这么理解的”；想改就接着说，点选改完提交也当成用户说的一句话，
// 所有话合在一起重新理解，后说的为准。确认后才开始规划。
// 2026-11-25 用户选定“对话加行程票”：左边是对话，理解结果写在右边的票上，取代聊天里的十二行表；
// 猜的带虚线，点票上一项或输入框上面的一项，就在输入框上面展开选项。每轮回复都留着，写改了什么。
import { computed, nextTick, ref, watch } from 'vue'
import { PhArrowRight, PhArrowUp } from '@phosphor-icons/vue'
import type { CityInfo, Hotel, Meta, Understanding } from '../api'
import { EXTRAS, extraTextFrom, useUnderstandingDraft } from '../understandingDraft'
import IssueList from './IssueList.vue'
import OptionTray from './OptionTray.vue'
import TripTicket, { type TicketCell } from './TripTicket.vue'

const props = defineProps<{ city: CityInfo; meta: Meta | null; messages: string[]; replies: (Understanding | null)[]; understanding: Understanding | null; busy: boolean; error: string;
  active?: boolean; disabled?: boolean; departing?: boolean; needsLogin?: boolean; quota?: { limit: number; remaining: number } | null }>()
const emit = defineEmits<{ send: [text: string, places: Hotel[]]; retry: []; confirm: [] }>()

const SUGGESTIONS = ['周末和女朋友一日游，不想太累', '带爸妈玩三天，人均 1500，别太赶', '第一次来，想去最有名的几个地方', '一个人随便逛逛，坐公交地铁']

const draft = ref('')
const edits = useUnderstandingDraft(() => props.understanding, () => props.meta)
// 点选改了还没提交的几处，发送时和输入框里的话一起发；发出去等回复时票面先保持改后的样子
const pending = computed(() => props.busy ? [] : edits.phrases.value)
const tray = ref('')  // 展开的是哪一项
// 只打了项目名（“出发点”“想去”）：不用问模型，直接打开这一项的选项。只在界面上，不算用户说的话
const asides = ref<{ at: number; text: string; key: string }[]>([])
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
  for (const { key, label } of EXTRAS) {
    const [was, is] = [before, now].map(reply => extraTextFrom(field => reply.profile[field], key))
    if (was !== is) lines.push(`${label}：${was} → ${is}`)
  }
  return lines
}

function grow() {
  const el = input.value
  if (!el) return
  el.style.height = 'auto'
  el.style.height = `${Math.min(el.scrollHeight, 200)}px`
}
const NAMED: [string, RegExp][] = [['hotel', /^(从哪(里)?出发|出发点|出发地|起点|住哪(里)?)$/], ['interests', /^(想玩|想看|兴趣|玩什么|看什么|想玩什么)$/], ['must_visit', /^(想去|必去|一定要去|去哪(里)?|景点)$/],
  ['avoid', /^(不去|不想去)$/], ['start_date', /^(日期|哪天|什么时候|时间)$/], ['days', /^(天数|几天|玩几天)$/], ['budget', /^(预算|多少钱|花多少)$/], ['transport', /^(出行|交通|怎么走|出行方式)$/],
  ['pace', /^(节奏|快慢)$/], ['spice', /^(口味|辣|吃辣)$/], ['companions', /^(同行|和谁|跟谁|同行人)$/], ['familiarity', /^(熟悉|熟悉程度|来过)$/], ['evening', /^(夜间|晚上|夜景|夜游)$/],
  ['food', /^(怎么吃|吃什么|餐饮|吃)$/], ['meal', /^(每餐|每餐预算|餐费)$/], ['window', /^(活动时段|时段|几点|几点出发)$/], ['walk', /^(步行|走路|步行上限)$/], ['fallback', /^(公交不便|改打车)$/]]
function send() {
  const said = draft.value.trim()
  const named = !pending.value.length && props.understanding && NAMED.find(([, pattern]) => pattern.test(said))?.[0]
  if (named && !props.busy && !props.disabled) {
    asides.value.push({ at: props.messages.length, text: said, key: named })
    draft.value = ''
    tray.value = named
    nextTick(grow)
    return
  }
  const message = [...pending.value, said].filter(Boolean).join('；')
  const place = edits.draft.value.hotel as Hotel | null | undefined
  if (message && !props.busy && !props.disabled) emit('send', message, place ? [place] : [])
}
const labelOf = (key: string) => EXTRAS.find(item => item.key === key)?.label ?? edits.row(key)?.label ?? ''
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
  if (!now) asides.value = []
  tray.value = ''
})
// 没理解成：这句话已经记下，重试时会一起带上，草稿作废
watch(() => props.error, error => { if (error) edits.discard() })
// 新消息、正在理解、回复到了：把最后一句用户的话滚到顶上，下面接着就是回复
function showLast(behavior: ScrollBehavior) {
  const box = scroller.value
  const last = [...(box?.querySelectorAll<HTMLElement>('.chat-user') ?? [])].pop()
  if (box && last) box.scrollTo({ top: Math.max(0, last.offsetTop - 16), behavior })
}
watch(() => [props.messages.length, props.busy, latest.value, props.error, asides.value.length], () => nextTick(() => showLast('smooth')))
// 切走再回来：隐藏时滚动位置会丢，回到最后一句
watch(() => props.active, active => { if (active) nextTick(() => showLast('instant')) })

// —— 行程票 ——
const CELLS = [['days', '天数'], ['companions', '同行'], ['pace', '节奏'], ['transport', '出行'], ['budget', '预算'], ['spice', '口味'], ['familiarity', '熟悉'], ['start_date', '日期'], ['hotel', '起点']] as const
const ROWS = [['interests', '想玩', 3], ['must_visit', '必去', 2], ['avoid', '不去', 1]] as const
function cell(key: string, label: string): TicketCell {
  if (!props.understanding) return { key, label, value: '–', muted: true }
  const row = edits.row(key)
  return { key, label, value: edits.text(key), guess: edits.guessed(key) && !edits.changed(key), changed: edits.changed(key), title: [edits.text(key), row?.note].filter(Boolean).join('\n') }
}
const ticketCells = computed(() => CELLS.map(([key, label]) => cell(key, label)))
const ticketRows = computed<TicketCell[]>(() => {
  const rows: TicketCell[] = ROWS.map(([key, label, span]) => ({ ...cell(key, label), span }))
  const parts = props.understanding ? EXTRAS.filter(item => edits.extraSet(item.key) || edits.extraChanged(item.key))
    .map(item => ({ key: item.key, label: item.label, value: edits.extraText(item.key), changed: edits.extraChanged(item.key) })) : []
  return parts.length ? [...rows, { label: '还有', parts, span: 3 }] : rows
})
const ticketSummary = computed(() => [props.city.name, ...['days', 'companions', 'pace', 'budget'].map(key => edits.text(key))].join(' · '))
const ticketNote = computed(() => {
  if (props.busy) return '正在理解…'
  if (!props.understanding) return props.error ? '还是空的' : '还是空的，说一句就会填上'
  if (pending.value.length) return `改了 ${pending.value.length} 处，还没提交`
  if (blocked.value) return '先处理标红的问题'
  if (outOfQuota.value) return '今天的生成次数已用完'
  const keys = [...CELLS, ...ROWS].map(([key]) => edits.row(key)?.source)
  const said = keys.filter(source => source === 'said' || source === 'form').length + EXTRAS.filter(item => edits.extraSet(item.key)).length
  const guessed = keys.filter(source => source === 'inferred' || source === 'default').length
  return [`${said} 项你说的`, `${guessed} 项我猜的`, props.quota && `今天还可生成 ${props.quota.remaining}/${props.quota.limit} 次`].filter(Boolean).join(' · ')
})
const starting = ref(false)
async function start() {
  starting.value = true
  await nextTick()  // 先让票记下这一屏的样子，再切页面
  emit('confirm')
  await nextTick()
  if (!props.departing) starting.value = false
}
watch(() => props.departing, departing => { if (!departing) starting.value = false })
function edit(key: string) { tray.value = tray.value === key ? '' : key }
</script>

<template>
  <section class="tform tchat" :class="{ empty: !messages.length }" aria-labelledby="agent-title" :aria-busy="busy">
    <div class="tchat-main">
      <div v-if="!messages.length" class="tchat-hello">
        <h2 id="agent-title" tabindex="-1">想怎么玩，直接说</h2>
        <p>说得随意一点也行。没说的我先按常见情况猜，猜了什么都会在票上标出来。</p>
      </div>
      <div v-else ref="scroller" class="tchat-scroll">
        <h2 id="agent-title" class="sr-only">跟 AI 说</h2>
        <ol class="tchat-thread" role="log" aria-label="对话">
          <template v-for="(text, index) in messages" :key="index">
            <li class="chat-user">{{ text }}</li>
            <li v-if="replies[index]" class="chat-ai">
              <p v-if="index === latest && !changes(index).length && previous(index) >= 0">这句我没看出要改哪一项，还是原来的理解。可以换个说法，或者点票上那一项改。</p>
              <template v-else-if="changes(index).length">
                <p>好的，改了这几处：</p>
                <ul><li v-for="line in changes(index)" :key="line">{{ line }}</li></ul>
              </template>
              <p v-else-if="index === latest">我是这么理解的，都写在票上。<span class="chat-guess">带虚线的</span>是我猜的，不对就直接告诉我，或者点那一项改。</p>
              <p v-else>我是这么理解的：{{ summary(index) }}</p>
              <IssueList v-if="index === latest && understanding" :issues="understanding.issues" :edits="edits" :busy="busy" />
            </li>
            <template v-for="aside in asides.filter(item => item.at === index + 1)" :key="`${aside.at}-${aside.key}`"><li class="chat-user">{{ aside.text }}</li><li class="chat-ai"><p>好，{{ labelOf(aside.key) }}在下面选。</p></li></template>
          </template>
          <li v-if="busy" class="chat-ai chat-typing" role="status"><i aria-hidden="true" />正在理解…</li>
          <li v-else-if="error" class="chat-ai" role="alert"><p class="chat-error">{{ error }}</p><button type="button" class="pill" @click="emit('retry')">重试</button></li>
        </ol>
      </div>
      <div class="tchat-dock">
        <OptionTray v-if="understanding && !busy && messages.length" v-model="tray" :edits="edits" :understanding="understanding" :meta="meta" :disabled="disabled" />
        <form class="composer" @submit.prevent="send">
          <textarea ref="input" v-model="draft" rows="1" aria-label="说说你想怎么玩" :disabled="busy || disabled" :placeholder="messages.length ? '还想改什么，直接说' : '说说你想怎么玩'" @input="grow" @keydown.enter.exact="onEnter" />
          <div class="composer-row"><span>{{ pending.length ? `改了 ${pending.length} 处，会和这句话一起发` : needsLogin ? '登录后就能发送，写好的话会保留' : 'Enter 发送，Shift + Enter 换行' }}</span><button type="submit" class="composer-send" :disabled="!canSend" :title="needsLogin ? '登录后发送' : undefined">发送<PhArrowUp :size="17" aria-hidden="true" /></button></div>
        </form>
        <div v-if="!messages.length" class="pill-row tchat-suggestions"><button v-for="text in SUGGESTIONS" :key="text" type="button" class="pill" :disabled="disabled" @click="suggest(text)">{{ text }}</button></div>
      </div>
    </div>
    <TripTicket :city="city" :cells="ticketCells" :rows="ticketRows" :summary="ticketSummary" :note="ticketNote" :tearing="starting" :departing="!!departing" editable :active="tray" :blank="!understanding" :busy="busy" @edit="edit">
      <button v-if="pending.length" type="button" class="ticket-back" @click="edits.discard()">撤销</button>
      <button v-if="pending.length" type="button" class="ticket-go" :disabled="disabled" @click="send">提交修改</button>
      <button v-else type="button" class="ticket-go" :disabled="!understanding || busy || disabled || blocked || outOfQuota" @click="start">就这样，开始规划<PhArrowRight :size="18" aria-hidden="true" /></button>
    </TripTicket>
  </section>
</template>
