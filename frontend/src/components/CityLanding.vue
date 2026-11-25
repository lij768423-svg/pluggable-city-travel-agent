<script setup lang="ts">
// 选城页（2026-11-25 用户选“清单加票”，取代 10-21 的双螺旋）：左边城市清单，右边一张票，指到哪座票就换成哪座。
// 票和下一页（问卷 / 跟 AI 说）的票在同一个位置、同一种版式，选定后票原地不动，只换左边（departure.ts 的 swap）。
// 进来时是中性的空票（10-19 用户：入口中性、城市平等）；从某座城市的问卷返回时，票停在那座城市。
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { PhArrowRight } from '@phosphor-icons/vue'
import type { CityInfo } from '../api'
import { cityArtwork } from '../cityArt'
import { cityShowcase } from '../cityShowcase'
import TripTicket, { type TicketCell } from './TripTicket.vue'

const props = defineProps<{ cities: CityInfo[]; pending: string | null; error: string; loading: boolean; initial?: string | null; mode: 'agent' | 'form' }>()
const emit = defineEmits<{ choose: [key: string] }>()

// 12 城：已开放的排在前面；未开放的写“即将开放”，点了不进入
const entries = computed(() => {
  const open = new Map(props.cities.map(city => [city.key, city]))
  const list = cityShowcase.map(item => ({ ...item, name: open.get(item.key)?.name ?? item.name, landmark: item.landmark.split(' · ').join('、'), open: open.has(item.key), art: cityArtwork(item.key)?.right }))
  return [...list.filter(item => item.open), ...list.filter(item => !item.open)]
})
const openCount = computed(() => entries.value.filter(item => item.open).length)
const shownKey = ref(props.initial ?? null)
const shown = computed(() => entries.value.find(item => item.key === (props.pending ?? shownKey.value)))
function choose(item: { key: string; open: boolean }) {
  if (props.pending || props.loading) return
  shownKey.value = item.key
  if (item.open) emit('choose', item.key)
}

// 指到哪座换哪座。往右边的票移动时会扫过另一列，这种快速横移先不换，停下来才换
let hoverTimer: ReturnType<typeof setTimeout> | undefined
let last = { x: 0, y: 0, t: 0 }
function hover(event: PointerEvent) {
  if (event.pointerType === 'touch' || props.pending) return
  const key = (event.target as HTMLElement).closest<HTMLElement>('[data-city]')?.dataset.city
  const [dx, dy, dt] = [event.clientX - last.x, event.clientY - last.y, event.timeStamp - last.t]
  last = { x: event.clientX, y: event.clientY, t: event.timeStamp }
  clearTimeout(hoverTimer)
  if (!key || key === shownKey.value) return
  const passing = dt < 120 && dx > 2 && dx > 1.6 * Math.abs(dy)
  if (passing) hoverTimer = setTimeout(() => { shownKey.value = key }, 160)
  else shownKey.value = key
}

const stopHover = () => clearTimeout(hoverTimer)

// 点下去很快就切走的，票面不变样；超过 0.35 秒才写“正在前往”、条码才动
const slow = ref(false)
let slowTimer: ReturnType<typeof setTimeout> | undefined
watch(() => props.pending, pending => { clearTimeout(slowTimer); slow.value = false; if (pending) slowTimer = setTimeout(() => { slow.value = true }, 350) })

// 票面：和下一页同一种版式，格子留空等用户填
const LABELS = { agent: ['天数', '同行', '节奏', '出行', '预算', '口味', '熟悉', '日期', '起点'], form: ['天数', '同行', '节奏', '出行', '预算', '口味'] }
const cells = computed<TicketCell[]>(() => LABELS[props.mode].map(label => ({ label, value: '–', muted: true })))
const rows = computed<TicketCell[]>(() => props.mode === 'agent'
  ? [{ label: '想玩', value: '–', span: 3, muted: true }, { label: '必去', value: '–', span: 2, muted: true }, { label: '不去', value: '–', span: 1, muted: true }]
  : [{ label: '想看', value: '–', span: 2, muted: true }, { label: '起点', value: '–', muted: true }])
const NEUTRAL: CityInfo = { key: 'journey', name: '行记', brand: '行记', map_center: { lat: 0, lon: 0 }, default_origin: { name: '', lat: 0, lon: 0 } }
const ticketCity = computed<CityInfo>(() => shown.value ? { ...NEUTRAL, key: shown.value.key, name: shown.value.name } : NEUTRAL)
const note = computed(() => !shown.value ? '还是一张空票，从左边挑一座城市' : slow.value ? `正在前往${shown.value.name}…` : shown.value.landmark)
const action = computed(() => !shown.value ? '选一座城市' : shown.value.open ? `去${shown.value.name}` : '即将开放')

// 空闲时把各城的插画先取回来，指过去就有
let idle = 0
const request = window.requestIdleCallback ?? ((run: () => void) => window.setTimeout(run, 600))
onMounted(() => {
  const queue = entries.value.map(item => item.art ?? item.image)
  const next = () => {
    const src = queue.shift()
    if (!src) return
    const image = new Image()
    image.src = src
    // 顺便解好码，指过去当帧就能画
    const ready = () => { idle = request(next) }
    image.decode().then(ready, ready)
  }
  if (matchMedia('(min-width: 901px)').matches) idle = request(next)
})
onBeforeUnmount(() => { stopHover(); clearTimeout(slowTimer); (window.cancelIdleCallback ?? window.clearTimeout)(idle) })
</script>

<template>
  <main class="destination-page landing" :aria-busy="!!pending">
    <section class="landing-form" aria-labelledby="destination-title">
      <div class="landing-main">
        <h1 id="destination-title">下一程，想去哪里？</h1>
        <p class="landing-count">{{ entries.length }} 座城市，{{ openCount }} 座已开放</p>
        <ul class="landing-rows" :class="{ picked: !!shown }" aria-label="选择旅行城市" @pointermove="hover" @pointerleave="stopHover">
          <li v-for="(item, index) in entries" :key="item.key" :style="{ '--at': index }"><a class="landing-row" :class="{ on: shown?.key === item.key, off: !item.open }" :data-city="item.key" :href="item.open ? `#${item.key}` : undefined" :tabindex="item.open ? undefined : 0" :aria-disabled="item.open ? undefined : 'true'"
            :aria-label="item.open ? `选择${item.name}` : `${item.name}即将开放`" @focus="!pending && (shownKey = item.key)" @click.prevent="choose(item)"><b>{{ item.name }}</b><small>{{ item.open ? item.landmark : '即将开放' }}</small><img class="landing-thumb" :class="{ photo: !item.art }" :src="item.art ?? item.image" alt="" loading="lazy" decoding="async" /></a></li>
        </ul>
        <p v-if="error" class="landing-error" role="alert">{{ error }}，请重新选择目的地。</p>
      </div>
      <TripTicket :city="ticketCity" :image="shown && !shown.art ? shown.image : undefined" :code="shown ? undefined : 'NEXT STOP'" :cells="cells" :rows="rows" :summary="ticketCity.name" :note="note" :tearing="false" :departing="false" :editable="mode === 'agent'" blank :busy="slow">
        <button type="button" class="ticket-go" :disabled="!shown?.open || (loading && !pending)" :aria-disabled="!!pending || undefined" @click="shown && choose(shown)">{{ action }}<PhArrowRight v-if="shown?.open" :size="18" aria-hidden="true" /></button>
      </TripTicket>
    </section>
  </main>
</template>

<style scoped>
.landing { display: flex; justify-content: center; padding: 52px 28px 0; color: var(--text); background: none; }
/* 骨架和问卷页的 .plan-entry、.tform 一致：票才会落在同一个位置 */
.landing-form { width: 100%; max-width: 1124px; padding: 0 4px; min-height: 0; display: grid; grid-template-columns: minmax(0, 1fr) clamp(340px, 29vw, 388px); column-gap: clamp(36px, 5.5vw, 88px); }
.landing-main { min-width: 0; padding: clamp(6px, calc((100dvh - 720px) * .3 + 12px), 118px) 0 24px; }
h1 { font-size: clamp(32px, 3.6vw, 50px); line-height: 1.15; font-weight: 650; letter-spacing: -.03em; text-wrap: balance; }
.landing-count { margin: 10px 0 clamp(14px, 3vh, 26px); font-size: 14px; color: var(--muted); }
.landing-rows { list-style: none; margin: 0; padding: 0; display: grid; grid-template: repeat(6, auto) / 1fr 1fr; grid-auto-flow: column; column-gap: 32px; }
.landing-rows li { border-top: 1px solid #15161314; }
.landing-rows li:nth-child(6n) { border-bottom: 1px solid #15161314; }
.landing-row { --row: clamp(50px, 8.4vh, 74px); position: relative; display: flex; align-items: baseline; gap: 14px; color: var(--text); text-decoration: none; cursor: pointer; }
.landing-row b { font-size: clamp(24px, 2.4vw, 32px); line-height: var(--row); font-weight: 620; letter-spacing: -.03em; transition: transform var(--spring-settle-ms) var(--spring-settle), color .2s; }
.landing-row small { margin-left: auto; font-size: 13px; color: var(--muted); white-space: nowrap; }
.landing-thumb { display: none; }
.landing-row::before { content: ''; position: absolute; left: 0; top: 50%; width: 14px; height: 2px; background: var(--signal); transform: scaleX(0); transform-origin: left; transition: transform var(--spring-settle-ms) var(--spring-settle); }
.picked .landing-row:not(.on) b, .landing-row.off b { color: #a3a59e; }
.landing-row.on b { transform: translateX(24px); }
.landing-row.on::before { transform: none; }
.landing-row.off { cursor: default; }
.landing-row:focus-visible { outline: 2px solid var(--accent); outline-offset: -2px; border-radius: 6px; }
.landing-error { margin-top: 16px; font-size: 13px; color: var(--bad); }

/* 第一次进来：标题、清单逐行、票依次到位 */
h1, .landing-count, .landing-rows li { animation: landing-rise .6s cubic-bezier(.16, 1, .3, 1) both; }
.landing-count { animation-delay: .06s; }
.landing-rows li { animation-delay: calc(.1s + var(--at) * 28ms); }
.landing :deep(.ticket-wrap) { animation: landing-ticket .8s cubic-bezier(.16, 1, .3, 1) .12s both; }
@keyframes landing-rise { from { opacity: 0; transform: translateY(14px); } }
@keyframes landing-ticket { from { opacity: 0; transform: translateY(34px) rotate(1.4deg); } }
:global([data-swap] .landing h1), :global([data-swap] .landing .landing-count), :global([data-swap] .landing .landing-rows li), :global([data-swap] .landing .ticket-wrap) { animation: none; }

@media (max-width: 900px) {
  .landing { display: block; padding: 0; }
  .landing-form { display: block; padding: 0; }
  .landing :deep(.ticket-wrap) { display: none; }
  .landing-main { padding: 26px 18px calc(28px + env(safe-area-inset-bottom)); }
  h1 { font-size: 30px; }
  .landing-rows { grid-template: none / 1fr; grid-auto-flow: row; }
  .landing-rows li:nth-child(6n) { border-bottom: 0; }
  .landing-rows li:last-child { border-bottom: 1px solid #15161314; }
  .landing-row { --row: 76px; overflow: hidden; }
  .landing-row b { font-size: 26px; }
  .landing-row small { margin: 0; align-self: center; }
  .landing-thumb { display: block; position: absolute; right: 0; bottom: -10px; height: 88px; mix-blend-mode: multiply; -webkit-mask: linear-gradient(to right, transparent 6%, #000 40%, #000 84%, transparent), linear-gradient(to top, transparent 11%, #000 40%, #000 var(--thumb-top, 74%), transparent 97%); -webkit-mask-composite: source-in; mask: linear-gradient(to right, transparent 6%, #000 40%, #000 84%, transparent), linear-gradient(to top, transparent 11%, #000 40%, #000 var(--thumb-top, 74%), transparent 97%); mask-composite: intersect; }
  .landing-thumb.photo { --thumb-top: 46%; bottom: 0; height: 76px; opacity: .6; }
  .landing-row.off .landing-thumb { filter: saturate(0); opacity: .45; }
  .picked .landing-row:not(.on):not(.off) b { color: inherit; }
}
@media (min-width: 561px) and (max-width: 900px) { .landing-rows { grid-template: repeat(6, auto) / 1fr 1fr; grid-auto-flow: column; } }
</style>
