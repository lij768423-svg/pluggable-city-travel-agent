<script setup lang="ts">
// 历史行程「票夹」：每份行程一张横票，左边是城市的石膏模型照片，中间是去了哪些地方，右边撕线外是票根（查看、重新规划、删除）。
// 和行程票、住宿凭条、登录页检票口是同一套票面；条码由行程 id 算出来，只是装饰。手机上竖过来，票根落到底部一行。
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { PhArrowLeft, PhArrowUpRight, PhArrowClockwise, PhPlus, PhTrash, PhClockCounterClockwise } from '@phosphor-icons/vue'
import { AlertDialogRoot, AlertDialogPortal, AlertDialogOverlay, AlertDialogContent, AlertDialogTitle, AlertDialogDescription, AlertDialogCancel } from 'reka-ui'
import { getHistory, deleteHistory, ApiError, type HistoryEntry, type HistoryTotals } from '../api'
import { cityShowcase } from '../cityShowcase'
defineProps<{ username: string }>()
const emit = defineEmits<{ back: []; create: []; open: [id: string]; replan: [id: string]; expired: [] }>()
const entries = ref<HistoryEntry[]>([])
const totals = ref<HistoryTotals | null>(null)
const busy = ref(false)
const error = ref('')
const more = ref(false)
const deleting = ref<HistoryEntry | null>(null)
const deletingBusy = ref(false)
let active = true
onBeforeUnmount(() => { active = false })
const PACE: Record<string, string> = { relaxed: '舒缓', normal: '均衡', intense: '充实' }
const WHO: Record<string, string> = { solo: '独行', couple: '情侣', friends: '朋友', family_kids: '亲子', elderly: '带老人' }
const date = (stamp: number) => new Date(stamp * 1000).toLocaleString('zh-CN', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' })
const photo = (city: string) => cityShowcase.find(entry => entry.key === city)?.image
const short = (name: string) => name.replace(/[（(].*?[)）]/g, '').trim() || name
function departs(entry: HistoryEntry) {
  if (!entry.start_date) return ''
  const [year, month, day] = entry.start_date.split('-').map(Number)
  return `${month}月${day}日 周${'日一二三四五六'[new Date(year, month - 1, day).getDay()]}`
}
const mood = (entry: HistoryEntry) => [entry.pace && PACE[entry.pace], entry.companions && WHO[entry.companions]].filter(Boolean).join(' · ') || '—'
const stay = (entry: HistoryEntry) => entry.stay || (entry.days === 1 ? '当天往返' : '')
const money = (value: number | null) => value == null ? '—' : `¥${Math.round(value)}`
function bars(seed: string) {
  let hash = 2166136261
  for (const ch of seed) hash = Math.imul(hash ^ ch.charCodeAt(0), 16777619) >>> 0
  return Array.from({ length: 42 }, () => { hash = Math.imul(hash ^ (hash >>> 13), 1274126177) >>> 0; return 1 + (hash >>> 11) % 3 })
}
async function load(append = false) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try {
    const result = await getHistory(append ? entries.value.length : 0)
    entries.value = append ? [...entries.value, ...result.items] : result.items; more.value = result.has_more
    if (result.totals) totals.value = result.totals
  }
  catch (failure) { if (active && failure instanceof ApiError && failure.status === 401) emit('expired'); else error.value = (failure as Error).message }
  finally { busy.value = false }
}
async function remove() {
  if (!deleting.value || deletingBusy.value) return
  deletingBusy.value = true
  try { await deleteHistory(deleting.value.id); deleting.value = null; await load() }
  catch (failure) { deleting.value = null; if (active && failure instanceof ApiError && failure.status === 401) emit('expired'); else error.value = (failure as Error).message }
  finally { deletingBusy.value = false }
}
onMounted(() => load())
</script>
<template>
  <main class="history-page">
    <button class="ghost-btn" @click="emit('back')"><PhArrowLeft :size="17" />返回</button>
    <header class="wallet-head">
      <div><small>TRAVEL LOG · {{ username.toUpperCase() }}</small><h1>历史行程</h1>
        <p v-if="totals && totals.trips" class="wallet-tally"><span><b>{{ totals.trips }}</b>份行程</span><span><b>{{ totals.cities }}</b>座城市</span><span><b>{{ totals.days }}</b>天</span><span><b>{{ totals.stops }}</b>个去处</span></p>
        <p v-else class="wallet-tally">{{ username }} 的行程记录 · 按最近更新排序</p>
      </div>
      <button class="journey-primary" @click="emit('create')"><PhPlus :size="18" />新建行程</button>
    </header>
    <p v-if="error" class="notice bad" role="alert">{{ error }}<button @click="load()">重试</button></p>
    <div v-if="!entries.length && busy" class="history-empty" role="status">正在打开你的旅行手记…</div>
    <div v-else-if="!entries.length && !error" class="history-empty"><PhClockCounterClockwise :size="42" weight="light" /><h2>还没有出发记录</h2><p>完成第一次规划后，行程会自动出现在这里。</p><button class="journey-secondary" @click="emit('create')">开始规划</button></div>
    <TransitionGroup tag="ol" name="stub" class="stubs">
      <li v-for="(entry, index) in entries" :key="entry.id" class="stub" :style="{ '--n': Math.min(index, 8) }">
        <article class="stub-body">
          <div class="stub-art" aria-hidden="true"><img v-if="photo(entry.city)" :src="photo(entry.city)" alt="" loading="lazy" decoding="async" /><span>{{ entry.city.toUpperCase() }}</span></div>
          <div class="stub-main">
            <p class="stub-row"><span>NO.{{ String(index + 1).padStart(2, '0') }}</span><span v-if="entry.draft" class="stub-draft">测试草稿 · 待核实</span><time>{{ date(entry.updated) }} 更新</time></p>
            <h2><button @click="emit('open', entry.id)">{{ entry.city_name }} {{ entry.days }} 日</button><small>{{ entry.theme }}</small></h2>
            <p class="stub-route" :title="entry.route.map(short).join(' → ')">
              <template v-if="entry.route.length"><template v-for="(name, at) in entry.route" :key="at"><i v-if="at">→</i>{{ short(name) }}</template><i v-if="entry.stops > entry.route.length">→ …</i></template>
              <template v-else>这份行程没有安排景点</template>
            </p>
            <dl class="stub-grid">
              <div><dt>出发</dt><dd :class="{ none: !entry.start_date }">{{ departs(entry) || '日期未定' }}</dd></div>
              <div><dt>节奏 · 同行</dt><dd>{{ mood(entry) }}</dd></div>
              <div><dt>住在</dt><dd :class="{ none: !entry.stay }" :title="stay(entry)">{{ short(stay(entry)) || '—' }}</dd></div>
              <div><dt>{{ entry.stops }} 站 · 预计</dt><dd class="num">{{ money(entry.total) }}</dd></div>
            </dl>
          </div>
          <div class="stub-side">
            <div class="stub-code" aria-hidden="true"><i v-for="(width, at) in bars(entry.id + entry.city)" :key="at" :style="{ width: `${width}px` }" /></div>
            <p class="stub-no" aria-hidden="true"><span>{{ entry.id.slice(0, 8).toUpperCase() }}</span><span>{{ entry.days }}D</span></p>
            <button class="journey-primary" @click="emit('open', entry.id)">查看行程<PhArrowUpRight :size="16" /></button>
            <nav><button class="ghost-btn" @click="emit('replan', entry.id)"><PhArrowClockwise :size="15" />重新规划</button><button class="stub-delete" :aria-label="`删除 ${entry.title}`" @click="deleting = entry"><PhTrash :size="17" /></button></nav>
          </div>
        </article>
      </li>
    </TransitionGroup>
    <button v-if="more" class="journey-secondary history-more" :disabled="busy" @click="load(true)">{{ busy ? '加载中…' : '更多记录' }}</button>
    <p v-if="entries.length" class="history-footnote">记录保留当时的安排和费用。重新规划后会核对当前信息，价格和开放时间请以出行时为准。</p>
    <AlertDialogRoot :open="!!deleting" @update:open="value => !value && !deletingBusy && (deleting = null)"><AlertDialogPortal><AlertDialogOverlay class="confirm-overlay" /><AlertDialogContent class="confirm-dialog"><AlertDialogTitle>删除这份行程？</AlertDialogTitle><AlertDialogDescription class="confirm-body">「{{ deleting?.title }}」将从你的账号中移除，删除后不能恢复。</AlertDialogDescription><div class="confirm-actions"><AlertDialogCancel :disabled="deletingBusy">保留</AlertDialogCancel><button class="journey-primary" :disabled="deletingBusy" @click="remove">{{ deletingBusy ? '删除中…' : '确认删除' }}</button></div></AlertDialogContent></AlertDialogPortal></AlertDialogRoot>
  </main>
</template>
<style scoped>
.wallet-head { display: flex; justify-content: space-between; align-items: flex-end; gap: 24px; padding: 32px 0 28px; }
.wallet-head small { display: block; font: 12px var(--mono); letter-spacing: .12em; color: var(--faint); }
.wallet-head h1 { font-size: 32px; font-weight: 600; letter-spacing: -.04em; margin: 6px 0 10px; }
.wallet-tally { display: flex; flex-wrap: wrap; gap: 6px 22px; font-size: 13px; color: var(--muted); }
.wallet-tally b { font: 600 15px var(--mono); color: var(--text); margin-right: 4px; }
/* 列表用块排布而不是 grid：删除时离场的那张绝对定位，才会留在原处淡出，后面的票再补上来 */
.stubs { position: relative; }
.stub { --side: 196px; position: relative; margin-bottom: 18px; filter: drop-shadow(0 0 .6px #b9bbb4) drop-shadow(0 6px 16px #1516130d);
  transition: transform .35s cubic-bezier(.16, 1, .3, 1), filter .35s; }
/* 入场动画放在票面里层：li 上同时有动画和过渡时，Vue 会把它当成动画，删除后剩下的票就不会滑上来 */
.stub-body { animation: stub-in .45s calc(var(--n) * 35ms) cubic-bezier(.16, 1, .3, 1) backwards; }
@keyframes stub-in { from { opacity: 0; transform: translateY(10px); } }
.stub:hover { transform: translateY(-2px); filter: drop-shadow(0 0 .6px #a9aba4) drop-shadow(0 12px 26px #15161318); }
.stub-move { transition: transform .45s cubic-bezier(.16, 1, .3, 1); }
.stub-leave-active { position: absolute; left: 0; right: 0; transition: opacity .3s, transform .35s cubic-bezier(.16, 1, .3, 1); }
.stub-leave-to { opacity: 0; transform: translateX(-14px); }
/* 撕线两端的半圆缺口：上下各一层遮罩，在票根分界处挖掉一个圆 */
.stub-body { display: grid; grid-template-columns: 176px minmax(0, 1fr) var(--side); min-height: 168px; border-radius: 14px; background: #fff; overflow: hidden;
  -webkit-mask: radial-gradient(circle 10px at calc(100% - var(--side)) 0, #0000 97%, #000) top / 100% 51% no-repeat, radial-gradient(circle 10px at calc(100% - var(--side)) 100%, #0000 97%, #000) bottom / 100% 51% no-repeat;
  mask: radial-gradient(circle 10px at calc(100% - var(--side)) 0, #0000 97%, #000) top / 100% 51% no-repeat, radial-gradient(circle 10px at calc(100% - var(--side)) 100%, #0000 97%, #000) bottom / 100% 51% no-repeat; }
.stub-art { position: relative; background: #e9e9e6; overflow: hidden; }
.stub-art img { position: absolute; inset: 0; width: 100%; height: 100%; object-fit: cover; object-position: 66% 62%; transform: scale(1.04); transition: transform .8s cubic-bezier(.16, 1, .3, 1); }
.stub:hover .stub-art img { transform: scale(1.1); }
.stub-art span { position: absolute; left: 12px; bottom: 10px; font: 600 11px var(--mono); letter-spacing: .14em; color: #15161399; }
.stub-main { display: flex; flex-direction: column; min-width: 0; padding: 18px 22px 16px; }
.stub-row { display: flex; align-items: center; gap: 10px; font: 11px var(--mono); letter-spacing: .08em; color: var(--faint); }
.stub-row time { margin-left: auto; letter-spacing: 0; white-space: nowrap; }
.stub-draft { padding: 1px 7px; border-radius: 4px; background: var(--warn-bg); color: var(--warn); font-family: inherit; letter-spacing: 0; }
.stub-main h2 { display: flex; align-items: baseline; gap: 10px; margin: 6px 0 2px; font-size: 22px; font-weight: 650; letter-spacing: -.02em; }
.stub-main h2 button { text-align: left; }
.stub-main h2 button:hover { text-decoration: underline; text-underline-offset: 4px; text-decoration-thickness: 1px; }
.stub-main h2 small { font-size: 13px; font-weight: 400; color: var(--muted); letter-spacing: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stub-route { font-size: 13px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stub-route i { font-style: normal; color: #c3c5be; margin: 0 6px; }
.stub-grid { display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 12px; margin-top: auto; padding-top: 14px; border-top: 1px dashed var(--line); }
.stub-grid dt { font: 10px var(--mono); letter-spacing: .1em; color: var(--faint); white-space: nowrap; }
.stub-grid dd { margin-top: 2px; font-size: 14px; font-weight: 550; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.stub-grid dd.num { font-family: var(--mono); font-weight: 500; }
.stub-grid dd.none { color: var(--faint); font-weight: 400; }
.stub-side { position: relative; display: flex; flex-direction: column; gap: 8px; padding: 18px 18px 16px; background: #fbfbfa; }
.stub-side::before { content: ''; position: absolute; left: 0; top: 16px; bottom: 16px; border-left: 1.5px dashed #d3d4ce; }
.stub-code { display: flex; gap: 2px; height: 30px; margin-bottom: 4px; overflow: hidden; }
.stub-code i { flex: none; background: var(--text); border-radius: .5px; opacity: .85; }
.stub-no { display: flex; justify-content: space-between; font: 10px var(--mono); letter-spacing: .14em; color: var(--faint); }
.stub-side .journey-primary { margin-top: auto; width: 100%; }
.stub-side nav { display: flex; justify-content: space-between; align-items: center; }
.stub-delete { width: 34px; height: 34px; display: grid; place-items: center; border-radius: 6px; color: var(--faint); }
.stub-delete:hover { color: var(--bad); background: #fff1ef; }
/* 窄一些的屏（平板、小窗口）：照片和票根收窄，中间四格排成两行，日期和花费不被截断 */
@media (max-width: 1080px) {
  .stub { --side: 176px; }
  .stub-body { grid-template-columns: 148px minmax(0, 1fr) var(--side); }
  .stub-main { padding: 16px 18px 14px; }
  .stub-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px 14px; padding-top: 10px; }
}
/* 手机：票竖过来，照片在上，票根落到底部一行；缺口挪到左右两侧的撕线上 */
@media (max-width: 760px) {
  .wallet-head { flex-direction: column; align-items: stretch; padding: 22px 0 22px; }
  .wallet-head h1 { font-size: 27px; }
  .wallet-head > .journey-primary { align-self: flex-start; }
  .wallet-tally { gap: 6px 14px; }
  .stub { --side: 70px; margin-bottom: 14px; }
  .stub:hover { transform: none; }
  .stub-body { grid-template-columns: minmax(0, 1fr); min-height: 0;
    -webkit-mask: radial-gradient(circle 10px at 0 calc(100% - var(--side)), #0000 97%, #000) left / 51% 100% no-repeat, radial-gradient(circle 10px at 100% calc(100% - var(--side)), #0000 97%, #000) right / 51% 100% no-repeat;
    mask: radial-gradient(circle 10px at 0 calc(100% - var(--side)), #0000 97%, #000) left / 51% 100% no-repeat, radial-gradient(circle 10px at 100% calc(100% - var(--side)), #0000 97%, #000) right / 51% 100% no-repeat; }
  .stub-art { height: 124px; }
  .stub-art img { object-position: 50% 42%; }
  .stub-main { padding: 14px 16px; }
  .stub-main h2 { font-size: 19px; }
  .stub-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px 12px; }
  .stub-side { height: var(--side); flex-direction: row; align-items: center; padding: 0 16px; }
  .stub-side::before { left: 16px; right: 16px; top: 0; bottom: auto; border-left: 0; border-top: 1.5px dashed #d3d4ce; }
  .stub-code, .stub-no { display: none; }
  .stub-side .journey-primary { margin-top: 0; width: auto; flex: 1; min-height: 42px; }
  .stub-side nav { gap: 2px; }
}
</style>
