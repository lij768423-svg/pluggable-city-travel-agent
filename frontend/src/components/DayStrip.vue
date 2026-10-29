<script setup lang="ts">
import { computed, nextTick, ref, watch } from 'vue'
import { PhArrowUpRight, PhBus, PhCar, PhHouseLine, PhPersonSimpleWalk } from '@phosphor-icons/vue'
import type { DayPlan } from '../api'

// 地图下方的当天概览：站点卡片横排，点击与左侧时间线一样定位地图
const props = defineProps<{ day: DayPlan; hotelName: string; selectedIndex: number | null; travelMinutes: number }>()
const emit = defineEmits<{ focus: [index: number]; details: [index: number] }>()
const track = ref<HTMLElement | null>(null)
const clock = (m: number) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
const labels = computed(() => {
  let n = 0
  return props.day.items.map(item => (item.kind === 'meal' ? '餐' : String(++n)))
})
const stops = computed(() => props.day.items.filter(item => item.kind === 'poi').length)
const departAt = computed(() => (props.day.items[0] ? props.day.items[0].start - props.day.items[0].travel_min : 0))
const returnAt = computed(() => Math.max(props.day.items.at(-1)?.end || 0, ...(props.day.reserved_meals || []).map(slot => slot.end)) + props.day.return_travel_min)
const modeIcon = (mode: string) => (mode === '步行' ? PhPersonSimpleWalk : mode === '打车' ? PhCar : PhBus)
watch(() => [props.selectedIndex, props.day.day], async () => {
  await nextTick()
  if (props.selectedIndex == null) { track.value?.scrollTo({ left: 0 }); return }
  track.value?.querySelector<HTMLElement>(`[data-index="${props.selectedIndex}"]`)?.scrollIntoView({ block: 'nearest', inline: 'center', behavior: 'smooth' })
})
</script>

<template>
  <section class="day-strip" aria-label="当天概览">
    <header>
      <h2>第 {{ day.day }} 天<span>{{ stops }} 个地点 · 路上约 {{ travelMinutes }} 分钟 · {{ clock(departAt) }}–{{ clock(returnAt) }}</span></h2>
      <slot />
    </header>
    <ol ref="track" class="day-strip-track">
      <li class="strip-card strip-home"><span class="strip-badge"><PhHouseLine :size="14" weight="bold" /></span><span class="strip-text"><time>{{ clock(departAt) }}</time><strong>出发</strong></span></li>
      <template v-for="(item, index) in day.items" :key="`${day.day}-${index}`">
        <li v-if="item.travel_mode !== '未计入'" class="strip-leg" aria-hidden="true"><span><component :is="modeIcon(item.travel_mode)" :size="13" />{{ item.travel_min }} 分</span></li>
        <li class="strip-card" :class="[item.kind, { on: selectedIndex === index }]" :data-index="index">
          <button type="button" class="strip-main" :aria-label="`在地图上定位${item.name}`" @click="emit('focus', index)">
            <span class="strip-badge">{{ labels[index] }}</span>
            <span class="strip-text"><time>{{ item.start_hm }}–{{ item.end_hm }}</time><strong :title="item.name">{{ item.name }}</strong></span>
          </button>
          <button type="button" class="strip-more" :aria-label="`查看${item.name}的详情`" title="查看详情" @click="emit('details', index)"><PhArrowUpRight :size="14" /></button>
        </li>
      </template>
      <li v-if="day.return_leg?.mode !== '未计入'" class="strip-leg" aria-hidden="true"><span><component :is="modeIcon(day.return_leg?.mode || '')" :size="13" />{{ day.return_travel_min }} 分</span></li>
      <li class="strip-card strip-home"><span class="strip-badge"><PhHouseLine :size="14" weight="bold" /></span><span class="strip-text"><time>{{ clock(returnAt) }}</time><strong>回到起点</strong></span></li>
    </ol>
  </section>
</template>

<style scoped>
.day-strip { position: relative; z-index: 3; flex: 0 0 auto; display: grid; gap: 12px; padding: 14px 18px 16px; background: #fff; border-top: 1px solid var(--line); }
header { display: flex; align-items: center; justify-content: space-between; gap: 16px; min-width: 0; }
h2 { display: flex; align-items: baseline; flex-wrap: wrap; gap: 4px 12px; font-size: 16px; font-weight: 650; }
h2 span { font-size: 12px; font-weight: 400; color: var(--muted); font-variant-numeric: tabular-nums; }
header :deep(.navigation-panel) { position: relative; flex-shrink: 0; display: flex; align-items: center; gap: 10px; text-align: right; }
header :deep(.navigation-status) { margin: 0; font-size: 11px; }
header :deep(.navigation-instructions) { margin: 0; padding: 0; border: 0; }
header :deep(.navigation-instructions > summary) { list-style: none; display: inline-flex; align-items: center; min-height: 30px; padding: 0 12px; border: 1px solid #e3e4e1; border-radius: 999px; color: var(--accent); font-size: 12px; }
header :deep(.navigation-instructions > summary::-webkit-details-marker) { display: none; }
header :deep(.navigation-instructions > summary span) { display: none; }
header :deep(.navigation-instructions[open] > .navigation-scroll) { position: absolute; right: 0; bottom: calc(100% + 10px); width: min(380px, 70vw); max-height: 52dvh; margin: 0; padding: 12px 14px; text-align: left; background: #fff; border: 1px solid var(--line); border-radius: 6px; box-shadow: 0 16px 48px #24252226; }
.day-strip-track { display: flex; align-items: center; overflow-x: auto; overscroll-behavior-x: contain; scroll-snap-type: x proximity; scrollbar-width: thin; scrollbar-color: #d5d6d3 transparent; padding: 2px 2px 8px; }
.strip-card { position: relative; flex: 0 0 auto; display: flex; align-items: center; height: 58px; border: 1px solid var(--line); border-radius: 6px; background: #fff; scroll-snap-align: start; transition: border-color .2s, box-shadow .2s; }
.strip-card:not(.strip-home) { width: 200px; }
.strip-home { gap: 10px; padding: 0 14px 0 10px; background: #f8f9f6; }
.strip-main { flex: 1; min-width: 0; height: 100%; display: flex; align-items: center; gap: 10px; padding: 0 30px 0 10px; text-align: left; border-radius: 6px; }
.strip-badge { flex-shrink: 0; width: 28px; height: 28px; display: grid; place-items: center; border-radius: 50%; background: var(--accent); color: #fff; font-size: 12px; font-weight: 700; }
.strip-home .strip-badge { background: var(--text); }
.meal .strip-badge { background: var(--meal); font-size: 11px; }
.strip-text { min-width: 0; display: grid; gap: 1px; }
.strip-text time { font-size: 11px; color: var(--muted); font-variant-numeric: tabular-nums; }
.strip-text strong { font-size: 13px; font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.strip-more { position: absolute; top: 6px; right: 6px; width: 24px; height: 24px; display: grid; place-items: center; border-radius: 6px; color: #aaaba8; }
.strip-more:hover { background: var(--accent-soft); color: var(--accent); }
.strip-leg { flex: 0 0 auto; display: flex; align-items: center; min-width: 64px; padding: 0 2px; }
.strip-leg::before, .strip-leg::after { content: ''; flex: 1; min-width: 6px; border-top: 1.5px solid #dddedb; }
.strip-leg span { display: inline-flex; align-items: center; gap: 3px; padding: 2px 6px; color: var(--faint); font-size: 11px; white-space: nowrap; font-variant-numeric: tabular-nums; }
.strip-card:not(.strip-home):hover { border-color: #c7c8c5; }
.strip-card.on { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent), 0 8px 20px -14px #151613; }
.strip-card.meal.on { border-color: var(--meal); box-shadow: 0 0 0 1px var(--meal); }
.day-strip-track > li { animation: strip-in .45s cubic-bezier(.16, 1, .3, 1) both; }
.day-strip-track > li:nth-child(2) { animation-delay: 30ms; } .day-strip-track > li:nth-child(3) { animation-delay: 60ms; } .day-strip-track > li:nth-child(4) { animation-delay: 90ms; } .day-strip-track > li:nth-child(5) { animation-delay: 120ms; } .day-strip-track > li:nth-child(6) { animation-delay: 150ms; } .day-strip-track > li:nth-child(7) { animation-delay: 180ms; } .day-strip-track > li:nth-child(8) { animation-delay: 210ms; } .day-strip-track > li:nth-child(9) { animation-delay: 240ms; } .day-strip-track > li:nth-child(10) { animation-delay: 270ms; } .day-strip-track > li:nth-child(11) { animation-delay: 300ms; } .day-strip-track > li:nth-child(12) { animation-delay: 330ms; } .day-strip-track > li:nth-child(13) { animation-delay: 360ms; } .day-strip-track > li:nth-child(14) { animation-delay: 390ms; } .day-strip-track > li:nth-child(15) { animation-delay: 420ms; }
@keyframes strip-in { from { opacity: 0; transform: translateX(14px); } to { opacity: 1; transform: none; } }
@media (max-width: 860px) {
  .day-strip { padding: 12px 12px 76px; }
  header :deep(.navigation-status) { display: none; }
  .strip-card:not(.strip-home) { width: 168px; }
}
@media (prefers-reduced-motion: reduce) { .strip-card { transition: none; } .day-strip-track > li { animation: none; } }
</style>
