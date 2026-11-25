<script setup lang="ts">
import { computed } from 'vue'
import type { Lodging, LodgingHotel } from '../api'
import { vExpand } from '../motion'

const props = defineProps<{ lodging: Lodging; canReplan: boolean; busyId: string | null; error: string }>()
const emit = defineEmits<{ replan: [hotel: LodgingHotel] }>()
const chosen = computed(() => props.lodging.chosen ?? null)
const left = computed(() => props.lodging.replans_left ?? 0)
const busyHotel = computed(() => props.lodging.hotels.find(hotel => hotel.external_id === props.busyId) ?? null)
const area = computed(() => props.lodging.area.endsWith('一带') ? props.lodging.area : `${props.lodging.area}一带`)
const modeText = computed(() => props.lodging.transport === 'transit' ? '公交地铁' : '打车')
// 高德的档次分组按后端给的顺序展示
const tiers = computed(() => {
  const groups: { tier: string; hotels: Lodging['hotels'] }[] = []
  for (const hotel of props.lodging.hotels) {
    const last = groups.at(-1)
    if (last?.tier === hotel.tier) last.hotels.push(hotel)
    else groups.push({ tier: hotel.tier, hotels: [hotel] })
  }
  return groups
})
const fetched = computed(() => props.lodging.fetched_at ? new Date(props.lodging.fetched_at).toLocaleDateString('zh-CN', { month: 'numeric', day: 'numeric' }) : '')
const distance = (meters: number) => meters < 1000 ? `${Math.round(meters / 10) * 10} 米` : `${(meters / 1000).toFixed(1)} 公里`
</script>

<template>
  <details v-expand class="lodging-card">
    <summary>
      <span v-if="chosen" class="lodging-title"><span>已按<b>{{ chosen.name }}</b>重新规划</span></span>
      <span v-else class="lodging-title"><span>还没定住处？住在<b>{{ area }}</b>更顺路</span></span>
      <span v-if="chosen" class="lodging-line">每天从这里出发、回到这里，去第一站和回去的路程已算进行程</span>
      <span v-else class="lodging-line">
        每天往返约 {{ lodging.average_minutes }} 分钟<template v-if="!lodging.similar"> · 比住{{ lodging.default_name }}少约 {{ lodging.saving_minutes }} 分钟</template><template v-else> · 和住{{ lodging.default_name }}差不多</template>
      </span>
    </summary>
    <ul v-if="!chosen" class="lodging-days">
      <li v-for="entry in lodging.daily" :key="entry.day" :class="{ long: lodging.long_days.includes(entry.day) }">第 {{ entry.day }} 天 {{ entry.minutes }} 分</li>
    </ul>
    <p v-if="!chosen && lodging.long_days.length" class="lodging-warn">
      第 {{ lodging.long_days.join('、') }} 天往返超过 1 小时：这天的景点离其他几天较远，可以考虑这晚换个住处。
    </p>
    <div v-if="tiers.length" class="lodging-hotels">
      <div v-for="group in tiers" :key="group.tier" class="lodging-tier">
        <span class="tier-name">{{ group.tier }}</span>
        <ul>
          <li v-for="hotel in group.hotels" :key="hotel.external_id || hotel.name">
            <a v-if="hotel.link" class="hotel-name" :href="hotel.link" target="_blank" rel="noopener">{{ hotel.name }}</a><span v-else class="hotel-name">{{ hotel.name }}</span>
            <small>{{ hotel.rating != null ? `${hotel.rating.toFixed(1)} 分 · ` : '' }}{{ distance(hotel.distance_m) }}</small>
            <span v-if="chosen?.external_id === hotel.external_id" class="replan-current">当前</span>
            <button v-else-if="canReplan && left > 0" type="button" class="replan-btn" :disabled="!!busyId" @click="emit('replan', hotel)">{{ busyId === hotel.external_id ? '重排中…' : '按这家重排' }}</button>
          </li>
        </ul>
      </div>
    </div>
    <p v-else class="lodging-note">附近住处暂时没查到，可以直接在高德地图里看。</p>
    <p v-if="busyHotel" class="lodging-busy" role="status">正在按「{{ busyHotel.name }}」重排，约 1–2 分钟；景点不变，不调用大模型。</p>
    <p v-else-if="error" class="lodging-warn" role="alert">{{ error }}</p>
    <p v-else-if="canReplan && tiers.length" class="lodging-note">
      <template v-if="left > 0">选一家“按这家重排”：景点不变，以它为每天的起点和终点重新排路线和餐厅，不扣当天的生成次数，可撤销。还可以重排 {{ left }} 次。</template>
      <template v-else>这份行程的重排次数已用完；不满意可以撤销，或重新生成。</template>
    </p>
    <a class="lodging-search" :href="lodging.search_link" target="_blank" rel="noopener">{{ lodging.search_label }} ↗</a>
    <p class="lodging-note">
      往返分钟按{{ modeText }}估算，只算每天去第一站和从最后一站回来，未查实时路况。
      <template v-if="tiers.length">店名、评分与档次来自高德（{{ fetched }} 查询），档次按高德分类；高德不提供房价，房价与预订请在高德或订房平台查看。</template>
    </p>
  </details>
</template>

<style scoped>
.lodging-card { margin-bottom: 20px; padding: 0 14px; border: 1px solid var(--line); border-radius: 3px; background: var(--panel); }
.lodging-card > summary { list-style: none; cursor: pointer; padding: 12px 0; display: grid; gap: 2px; }
.lodging-card > summary::-webkit-details-marker { display: none; }
.lodging-title { font-size: 13px; font-weight: 600; display: flex; justify-content: space-between; gap: 8px; }
.lodging-title b { font-weight: 700; margin: 0 2px; }
.lodging-title::after { content: '展开'; font-weight: 400; color: var(--accent); white-space: nowrap; }
.lodging-card[open]:not([data-expanding=close]) .lodging-title::after { content: '收起'; }
.lodging-line { font-size: 12px; color: var(--muted); }
.lodging-days { display: flex; flex-wrap: wrap; gap: 6px; margin: 0 0 10px; padding: 0; list-style: none; }
.lodging-days li { font-size: 11px; padding: 2px 8px; border-radius: 3px; background: var(--accent-soft); color: var(--muted); font-family: var(--mono); }
.lodging-days li.long { background: var(--warn-bg); color: var(--warn); }
.lodging-warn { font-size: 12px; color: var(--warn); margin: 0 0 10px; line-height: 1.6; }
.lodging-hotels { display: grid; gap: 8px; margin-bottom: 10px; }
.lodging-tier { display: grid; grid-template-columns: 64px 1fr; gap: 8px; font-size: 12px; }
.tier-name { color: var(--faint); padding-top: 1px; }
.lodging-tier ul { margin: 0; padding: 0; list-style: none; display: grid; gap: 4px; }
.lodging-tier li { display: flex; align-items: baseline; gap: 8px; min-width: 0; }
.hotel-name { flex: 0 1 auto; min-width: 0; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
a.hotel-name { text-decoration: underline; text-decoration-color: var(--line); text-underline-offset: 3px; }
.lodging-tier small { color: var(--faint); white-space: nowrap; font-size: 11px; margin-left: auto; }
.lodging-search { display: inline-block; font-size: 12px; color: var(--accent); font-weight: 500; margin-bottom: 8px; }
.lodging-note { font-size: 11px; color: var(--faint); line-height: 1.6; margin: 0 0 12px; }
.lodging-busy { font-size: 12px; color: var(--muted); margin: 0 0 10px; line-height: 1.6; }
.replan-btn { flex: none; font-size: 11px; padding: 1px 8px; border: 1px solid var(--line); border-radius: 3px; background: var(--panel); color: var(--accent); cursor: pointer; white-space: nowrap; }
.replan-btn:disabled { color: var(--faint); cursor: default; }
.replan-current { flex: none; font-size: 11px; padding: 1px 8px; border-radius: 3px; background: var(--accent-soft); color: var(--muted); }
</style>
