<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { PhArrowRight, PhArrowUpRight, PhCaretDown } from '@phosphor-icons/vue'
import type { Lodging, LodgingHotel } from '../api'
import { vExpand } from '../motion'
import RollValue from './RollValue.vue'

const props = defineProps<{ lodging: Lodging; canReplan: boolean; busyId: string | null; error: string; scope?: string }>()
const emit = defineEmits<{ replan: [hotel: LodgingHotel] }>()
const chosen = computed(() => props.lodging.chosen ?? null)
const left = computed(() => props.lodging.replans_left ?? 0)
const busyHotel = computed(() => props.lodging.hotels.find(hotel => hotel.external_id === props.busyId) ?? null)
const area = computed(() => props.lodging.area.endsWith('一带') ? props.lodging.area : `${props.lodging.area}一带`)
const modeText = computed(() => props.lodging.transport === 'transit' ? '公交地铁' : '打车')
const fetched = computed(() => props.lodging.fetched_at ? new Date(props.lodging.fetched_at).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) : '')
const distance = (meters: number) => meters < 1000 ? `${Math.round(meters / 10) * 10} 米` : `${(meters / 1000).toFixed(1)} 公里`

// 票面只放短名：店名去掉括号里的分店，默认起点去掉“附近（默认参考起点）”
const shortName = (name: string) => name.replace(/\s*[（(].*$/, '') || name
const stayName = computed(() => chosen.value ? shortName(chosen.value.name) : area.value)
const chosenHotel = computed(() => props.lodging.hotels.find(hotel => hotel.external_id === chosen.value?.external_id) ?? null)
const defaultName = computed(() => props.lodging.default_name.replace(/\s*[（(]默认参考起点[)）]$/, '').replace(/附近$/, '') || props.lodging.default_name)
const isLong = (day: number) => props.lodging.long_days.includes(day)
// 清单：按高德档次分栏（顺序照后端给的），点一家选中，只有一个重排按钮
const ALL = '全部'
const tier = ref(ALL)
const picked = ref<string | null>(null)
const info = ref(false)
const tierNames = computed(() => [...new Set(props.lodging.hotels.map(hotel => hotel.tier))])
const tierCount = (name: string) => name === ALL ? props.lodging.hotels.length : props.lodging.hotels.filter(hotel => hotel.tier === name).length
const shown = computed(() => tier.value === ALL ? props.lodging.hotels : props.lodging.hotels.filter(hotel => hotel.tier === tier.value))
const pickedHotel = computed(() => props.lodging.hotels.find(hotel => hotel.external_id === picked.value) ?? null)
const branch = (name: string) => name.match(/[（(]([^）)]+)[）)]/)?.[1] ?? ''
const pickable = computed(() => props.canReplan && left.value > 0)
// 条码按推荐片区和选中的那家生成：没选是灰的，选了变黑
const barcode = computed(() => {
  let h = 2166136261
  for (const ch of props.lodging.area + (picked.value ?? '')) h = Math.imul(h ^ ch.charCodeAt(0), 16777619) >>> 0
  return Array.from({ length: 64 }, () => { h = Math.imul(h ^ (h >>> 13), 1274126177) >>> 0; return 1 + (h >>> 11) % 4 })
})
function pick(hotel: LodgingHotel) {
  if (!pickable.value || props.busyId) return
  picked.value = picked.value === hotel.external_id ? null : hotel.external_id
}
function go() { if (pickedHotel.value && !props.busyId) emit('replan', pickedHotel.value) }
// 重排成功（住处变了）或换了一份行程：清掉选中；档次栏没了就回到“全部”
watch(() => chosen.value?.external_id, () => { picked.value = null })
watch(() => props.scope, () => { picked.value = null; tier.value = ALL; info.value = false })
watch(tierNames, names => { if (tier.value !== ALL && !names.includes(tier.value)) tier.value = ALL })
const tear = computed(() => busyHotel.value ? `正在按「${shortName(busyHotel.value.name)}」重排…`
  : pickedHotel.value && pickedHotel.value.external_id !== chosen.value?.external_id ? `已选「${shortName(pickedHotel.value.name)}」`
  : props.lodging.hotels.length ? `附近 ${props.lodging.hotels.length} 家` : '附近住处暂时没查到')
const toggleText = computed(() => !props.lodging.hotels.length ? '查看说明' : !props.canReplan || left.value <= 0 ? '看附近住处' : chosen.value ? '换一家' : '挑一家住处')
</script>

<template>
  <!-- 住宿凭条：上半张是推荐住处和每天往返，撕线下面那行展开附近住处清单 -->
  <div class="lodging-ticket">
    <details v-expand class="lodging-card">
      <summary>
        <span class="stay-top">
          <span class="stay-k"><span>STAY · {{ chosen ? '住处' : '推荐住在' }}</span><PhCaretDown class="stay-caret" :size="16" /></span>
          <span class="stay-name"><b><RollValue :value="stayName" :scope="scope" :flash="false" /></b><span v-if="chosen" class="stay-tag">起终点</span></span>
          <span class="stay-line">
            <template v-if="chosen">每天从这里出发、回到这里，往返已算进行程</template>
            <template v-else>每天往返约 {{ lodging.average_minutes }} 分钟 · <template v-if="!lodging.similar">比住{{ defaultName }}少约 {{ lodging.saving_minutes }} 分钟</template><template v-else>和住{{ defaultName }}差不多</template></template>
          </span>
          <span class="stay-grid">
            <template v-if="chosen">
              <span class="stay-cell"><small>档次</small><b class="stay-text">{{ chosen.tier }}</b></span>
              <span v-if="chosenHotel?.rating != null" class="stay-cell"><small>高德评分</small><b><span class="num">{{ chosenHotel.rating.toFixed(1) }}</span><small>分</small></b></span>
            </template>
            <template v-else>
              <span v-for="entry in lodging.daily" :key="entry.day" class="stay-cell" :class="{ long: isLong(entry.day) }"><small>第 {{ entry.day }} 天往返<em v-if="isLong(entry.day)">超 1 小时</em></small><b><span class="num">{{ entry.minutes }}</span><small>分</small></b></span>
            </template>
            <span v-if="canReplan && lodging.hotels.length" class="stay-cell"><small>可重排</small><b><span class="num"><RollValue :value="left" :scope="scope" :flash="false" /></span><small>次</small></b></span>
          </span>
        </span>
        <span class="stay-tear">
          <span class="stay-count">{{ tear }}</span>
          <span class="stay-toggle"><span class="when-closed">{{ toggleText }}</span><span class="when-open">收起</span><PhCaretDown :size="14" /></span>
        </span>
      </summary>
      <div class="expand-body lodging-body">
        <template v-if="lodging.hotels.length">
          <div v-if="tierNames.length > 1" class="stay-seg">
            <button v-for="name in [ALL, ...tierNames]" :key="name" type="button" :class="{ on: tier === name }" :aria-pressed="tier === name" @click="tier = name">{{ name.split('/')[0] }}<small>{{ tierCount(name) }}</small></button>
          </div>
          <ul class="stay-list">
            <li v-for="hotel in shown" :key="hotel.external_id || hotel.name" :class="{ on: picked === hotel.external_id }">
              <button type="button" class="stay-pick" :disabled="!pickable || !!busyId" :aria-pressed="picked === hotel.external_id" @click="pick(hotel)">
                <span class="stay-hn">
                  <b><span class="stay-short">{{ shortName(hotel.name) }}</span><span v-if="tier === ALL" class="stay-tier">{{ hotel.tier }}</span></b>
                  <small><template v-if="hotel.rating != null"><span class="num">{{ hotel.rating.toFixed(1) }}</span> 分 · </template><span class="num">{{ distance(hotel.distance_m) }}</span><template v-if="branch(hotel.name)"> · {{ branch(hotel.name) }}</template></small>
                </span>
                <span v-if="chosen?.external_id === hotel.external_id" class="stay-tag">当前</span>
              </button>
              <a v-if="hotel.link" class="stay-ext" :href="hotel.link" target="_blank" rel="noopener" :title="`在高德地图打开 ${hotel.name}`" :aria-label="`在高德地图打开 ${hotel.name}`"><PhArrowUpRight :size="15" /></a>
            </li>
          </ul>
          <template v-if="canReplan">
            <span class="stay-code" :class="{ lit: pickedHotel && !busyId }" aria-hidden="true"><i v-for="(width, k) in barcode" :key="k" :style="{ width: `${width}px`, '--k': k }" /></span>
            <button v-if="busyHotel" type="button" class="stay-go busy" disabled><span class="stay-spin" />正在按「{{ shortName(busyHotel.name) }}」重排…</button>
            <button v-else-if="left <= 0" type="button" class="stay-go" disabled>重排次数已用完</button>
            <button v-else-if="!pickedHotel" type="button" class="stay-go" disabled>先选一家住处</button>
            <button v-else-if="pickedHotel.external_id === chosen?.external_id" type="button" class="stay-go" disabled>已经从这家出发</button>
            <button v-else type="button" class="stay-go" @click="go"><span class="stay-go-name">{{ chosen ? '改按' : '按' }}「{{ shortName(pickedHotel.name) }}」重排</span><PhArrowRight :size="16" /></button>
            <p v-if="error && !busyHotel" class="stay-error" role="alert">{{ error }}</p>
            <p class="stay-note" role="status">{{ busyHotel ? '约 1–2 分钟 · 景点不变，不调用大模型' : left > 0 ? '景点不变，每天从这家出发、回到这家 · 不扣生成次数 · 可撤销' : '不满意可以撤销，或重新生成' }}</p>
          </template>
        </template>
        <p v-else class="stay-note left">附近住处暂时没查到，可以直接在高德地图里看。</p>
        <div class="stay-foot">
          <a :href="lodging.search_link" target="_blank" rel="noopener">{{ lodging.search_label }}<PhArrowUpRight :size="13" /></a>
          <button type="button" :aria-expanded="info" @click="info = !info">{{ info ? '收起说明' : '怎么算的' }}</button>
        </div>
        <p v-if="info" class="stay-fine">
          往返分钟按{{ modeText }}估算，只算每天去第一站和从最后一站回来，未查实时路况。
          <template v-if="lodging.hotels.length">店名、评分与档次来自高德（{{ fetched }} 查询），档次按高德分类；高德不提供房价，房价与预订请在高德或订房平台查看。</template>
        </p>
      </div>
    </details>
  </div>
</template>

<style scoped>
/* 票形：外层投影，上下两半各用遮罩挖出撕线两端的半圆缺口；圆角由 clip-path 统一裁，展开收起时跟着高度走 */
.lodging-ticket { margin-bottom: 20px; filter: drop-shadow(0 8px 18px #15161314) drop-shadow(0 1px 2px #15161317); }
.lodging-card { --notch: 9px; clip-path: inset(0 round 14px); }
.lodging-card > summary { list-style: none; cursor: pointer; display: block; }
.lodging-card > summary::-webkit-details-marker { display: none; }
.stay-top, .stay-tear, .lodging-body { display: block; background: var(--panel); }
.stay-top {
  padding: 16px 18px 14px; container-type: inline-size;
  -webkit-mask: radial-gradient(circle var(--notch) at 0 100%, #0000 98%, #000) left / 51% 100% no-repeat, radial-gradient(circle var(--notch) at 100% 100%, #0000 98%, #000) right / 51% 100% no-repeat;
  mask: radial-gradient(circle var(--notch) at 0 100%, #0000 98%, #000) left / 51% 100% no-repeat, radial-gradient(circle var(--notch) at 100% 100%, #0000 98%, #000) right / 51% 100% no-repeat;
}
.stay-tear {
  position: relative; display: flex; justify-content: space-between; align-items: center; gap: 12px; padding: 12px 18px; font-size: 12px; color: var(--muted);
  -webkit-mask: radial-gradient(circle var(--notch) at 0 0, #0000 98%, #000) left / 51% 100% no-repeat, radial-gradient(circle var(--notch) at 100% 0, #0000 98%, #000) right / 51% 100% no-repeat;
  mask: radial-gradient(circle var(--notch) at 0 0, #0000 98%, #000) left / 51% 100% no-repeat, radial-gradient(circle var(--notch) at 100% 0, #0000 98%, #000) right / 51% 100% no-repeat;
}
.stay-tear::before { content: ''; position: absolute; left: 14px; right: 14px; top: 0; border-top: 1.5px dashed var(--line); }
.stay-k { display: flex; justify-content: space-between; align-items: center; font: 11px/1.4 var(--mono); letter-spacing: .08em; color: var(--faint); }
.stay-caret, .stay-toggle svg { flex: none; color: var(--faint); transition: transform .45s cubic-bezier(.16, 1, .3, 1); }
.lodging-card[open]:not([data-expanding=close]) .stay-caret, .lodging-card[open]:not([data-expanding=close]) .stay-toggle svg { transform: rotate(180deg); }
.stay-name { display: flex; align-items: baseline; gap: 8px; margin-top: 6px; min-width: 0; }
.stay-name b { min-width: 0; font-size: 24px; font-weight: 650; letter-spacing: -.02em; line-height: 1.25; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stay-tag { flex: none; font-size: 11px; padding: 1px 7px; border-radius: 4px; background: var(--text); color: #fff; }
.stay-line { display: block; margin-top: 4px; font-size: 12px; color: var(--muted); }
.stay-grid { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 10px 12px; margin-top: 14px; }
.stay-cell { display: flex; flex-direction: column; min-width: 0; }
.stay-cell > small { display: block; font-size: 11px; line-height: 1.5; color: var(--muted); }
.stay-cell em { font-style: normal; color: var(--warn); white-space: nowrap; }
.stay-cell em::before { content: ' · '; }
/* 手机上一格不到 110px，“超 1 小时”整个换到下一行，三格的数字都贴底对齐 */
@container (max-width: 360px) { .stay-cell em { display: block; } .stay-cell em::before { content: none; } }
.stay-cell b { display: block; margin-top: auto; font-size: 16px; font-weight: 600; line-height: 1.5; }
.stay-cell b .num { font-family: var(--mono); font-variant-numeric: tabular-nums; }
.stay-cell b small { margin-left: 2px; font-size: 11px; font-weight: 400; color: var(--muted); }
.stay-cell.long b { color: var(--warn); }
.stay-cell b.stay-text { font-size: 14px; line-height: 24px; }
.stay-toggle { display: inline-flex; align-items: center; gap: 4px; flex: none; color: var(--text); font-weight: 500; }
.stay-toggle .when-open { display: none; }
.lodging-card[open]:not([data-expanding=close]) .stay-toggle .when-open { display: inline; }
.lodging-card[open]:not([data-expanding=close]) .stay-toggle .when-closed { display: none; }
.lodging-card > summary:hover .stay-toggle { text-decoration: underline; text-underline-offset: 3px; }
.lodging-body { padding: 2px 18px 4px; }

/* 撕线下面：档次分栏、住处小卡、条码和一个重排按钮 */
.stay-seg { display: flex; gap: 2px; padding: 3px; margin-top: 2px; border-radius: 9px; background: var(--accent-soft); font-size: 12px; overflow-x: auto; scrollbar-width: none; }
.stay-seg button { flex: 1; padding: 5px 6px; border-radius: 7px; color: var(--muted); white-space: nowrap; text-align: center; transition: background .2s, color .2s, box-shadow .2s; }
.stay-seg button.on { background: var(--panel); color: var(--text); font-weight: 550; box-shadow: 0 1px 2px #15161314; }
.stay-seg small { margin-left: 3px; font-family: var(--mono); font-size: 10px; color: var(--faint); }
.stay-list { display: grid; gap: 6px; margin: 12px 0 0; padding: 0; list-style: none; }
.stay-list li { display: flex; align-items: center; gap: 4px; border: 1px solid var(--line); border-radius: 10px; transition: border-color .2s, box-shadow .2s; }
.stay-list li:hover { border-color: #c9cbc4; }
.stay-list li.on { border-color: var(--text); box-shadow: 0 0 0 1px var(--text); }
.stay-pick { flex: 1; min-width: 0; display: flex; align-items: center; gap: 8px; padding: 9px 4px 9px 12px; text-align: left; }
.stay-pick:disabled { opacity: 1; cursor: default; }
.stay-hn { flex: 1; min-width: 0; }
.stay-hn b { display: flex; align-items: center; gap: 6px; min-width: 0; font-size: 13px; font-weight: 550; }
.stay-short { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.stay-tier { flex: none; padding: 1px 6px; border-radius: 4px; background: var(--accent-soft); color: var(--muted); font-size: 10px; font-weight: 400; }
.stay-hn small { display: block; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; color: var(--muted); }
.stay-hn .num { font-family: var(--mono); }
.stay-ext { flex: none; display: grid; place-items: center; width: 30px; height: 30px; margin-right: 6px; border-radius: 7px; color: var(--faint); }
.stay-ext:hover { background: var(--accent-soft); color: var(--text); }
.stay-code { display: flex; justify-content: space-between; height: 24px; margin-top: 14px; }
.stay-code i { background: var(--line); transition: background .35s; }
.stay-code.lit i { background: var(--text); transition-delay: calc(var(--k) * 5ms); }
.stay-go { display: flex; align-items: center; justify-content: center; gap: 8px; width: 100%; height: 42px; margin-top: 12px; padding: 0 18px; border-radius: 999px; background: var(--text); color: #fff; font-size: 13px; font-weight: 550; white-space: nowrap; transition: background .2s, color .2s; }
.stay-go:disabled { opacity: 1; background: var(--accent-soft); color: var(--faint); cursor: default; }
.stay-go.busy:disabled { background: var(--text); color: #fff; }
.stay-go-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
.stay-spin { flex: none; width: 13px; height: 13px; border: 2px solid #ffffff55; border-top-color: #fff; border-radius: 50%; animation: stay-spin .8s linear infinite; }
@keyframes stay-spin { to { transform: rotate(360deg); } }
.stay-error { margin: 10px 0 0; font-size: 12px; line-height: 1.6; color: var(--warn); text-align: center; }
.stay-note { margin: 8px 0 0; font-size: 11px; line-height: 1.6; color: var(--faint); text-align: center; }
.stay-note.left { margin: 4px 0 0; text-align: left; font-size: 12px; color: var(--muted); }
.stay-foot { display: flex; justify-content: space-between; align-items: center; gap: 10px; margin: 14px 0 12px; font-size: 12px; }
.stay-foot a { display: inline-flex; align-items: center; gap: 3px; color: var(--text); font-weight: 500; }
.stay-foot a:hover { text-decoration: underline; text-underline-offset: 3px; }
.stay-foot button { flex: none; color: var(--faint); font-size: 12px; }
.stay-foot button:hover { color: var(--text); }
.stay-fine { margin: -4px 0 12px; font-size: 11px; line-height: 1.7; color: var(--faint); }
@media (prefers-reduced-motion: reduce) { .stay-code i, .stay-seg button, .stay-list li { transition: none; } .stay-spin { animation-duration: 2.4s; } }
</style>
