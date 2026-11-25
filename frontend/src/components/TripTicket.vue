<script setup lang="ts">
// 行程票：左边选什么、说什么，票面跟着填什么；条码由当前选择算出来，选择变了条码就变（装饰，不可扫）。
// 「自己选」只看不点；「跟 AI 说」里 AI 猜的带虚线，点一项就在左边打开那一项的选项（editable）。
// 票面随指针轻微倾斜（思路参考 DesignBookmark Motion 的 Parallax ticket 与 Kokonut UI 的 Boarding Pass，这里用 Vue 重写）。
// 提交后票根掀开一点表示正在核对；真的开始规划、页面切走时（departing），票根撕落、票面飞去生成页。手机上收成底部一条，点开看整张票。
import { computed, ref, watch } from 'vue'
import { PhCaretUp } from '@phosphor-icons/vue'
import type { CityInfo } from '../api'
import { cityArtwork } from '../cityArt'
import { captureDeparture, playDeparture, type Departure } from '../departure'
import { reducedMotion } from '../motion'
import JourneyMark from './JourneyMark.vue'
import RollValue from './RollValue.vue'

export interface TicketPart { key: string; label: string; value: string; changed?: boolean }
export interface TicketCell { key?: string; label: string; value?: string; words?: string[]; parts?: TicketPart[]; span?: 1 | 2 | 3; guess?: boolean; changed?: boolean; title?: string; muted?: boolean }
const props = defineProps<{ city: CityInfo; cells: TicketCell[]; rows: TicketCell[]; date?: string; summary: string; note: string; error?: string; tearing: boolean; departing: boolean;
  editable?: boolean; active?: string; blank?: boolean; busy?: boolean;
  image?: string; code?: string }>()  // 选城页用：没有插画的城市给照片，中性空票给小字
const emit = defineEmits<{ edit: [key: string] }>()
// 图已经在缓存里就直接是满的，不再从透明淡进来；没取到的才等下载完淡入（data-fade）
const vReady = { beforeMount: (el: HTMLImageElement) => { if (el.complete) el.classList.add('is-loaded') } }
const art = computed(() => cityArtwork(props.city.key)?.right ?? props.image)
const wrap = ref<HTMLElement>()
const opened = ref(false)
// 手机上票是展开盖住的，点了一项就收起来，好看到下面打开的选项
function edit(key: string) { opened.value = false; emit('edit', key) }
const BARS = 46
const bars = computed(() => {
  const seed = [props.city.key, props.date, ...[...props.cells, ...props.rows].map(cell => cell.value ?? cell.words?.join() ?? cell.parts?.map(part => part.value).join())].join('|')
  let hash = 2166136261
  for (const ch of seed) hash = Math.imul(hash ^ ch.charCodeAt(0), 16777619) >>> 0
  return Array.from({ length: BARS }, () => {
    hash = Math.imul(hash ^ (hash >>> 13), 1274126177) >>> 0
    return 1 + (hash >>> 11) % 4
  })
})
// 只在指针停在票面上时倾斜，移到票根的按钮上就放平，文字不会糊
function tilt(event: PointerEvent) {
  if (event.pointerType === 'touch' || props.tearing || reducedMotion()) return
  const box = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const x = (event.clientX - box.left) / box.width - 0.5
  const y = (event.clientY - box.top) / box.height - 0.5
  const style = wrap.value!.style
  style.setProperty('--ticket-ry', `${(x * 9).toFixed(2)}deg`); style.setProperty('--ticket-rx', `${(-y * 6).toFixed(2)}deg`)
  style.setProperty('--ticket-px', `${(-x * 14).toFixed(1)}px`); style.setProperty('--ticket-py', `${(-y * 8).toFixed(1)}px`)
}
function level() {
  for (const name of ['--ticket-ry', '--ticket-rx', '--ticket-px', '--ticket-py']) wrap.value?.style.removeProperty(name)
}
// 掀开之前先记下这一屏的样子，页面真的切到生成页时在原位接着演（departure.ts）
let torn: Departure | null = null
watch(() => props.tearing, tearing => {
  level()
  if (tearing) torn = wrap.value ? captureDeparture(wrap.value) : null
  else if (!props.departing) torn = null
})
watch(() => props.departing, departing => {
  const departure = torn
  torn = null
  if (departing && departure) void playDeparture(departure)
})
</script>

<template>
  <aside ref="wrap" class="ticket-wrap" aria-label="行程票">
    <div class="ticket" :class="{ opened, tearing, blank, editable }">
      <div class="ticket-main" @pointermove="tilt" @pointerleave="level">
        <button type="button" class="ticket-peek" :aria-expanded="opened" @click="opened = !opened"><span>{{ summary }}</span><PhCaretUp :size="16" aria-hidden="true" /></button>
        <div class="ticket-fold"><div>
          <div class="ticket-art">
            <!-- 换城当帧换图，不做交叉淡化和滑动：两幅叠着会重影，滑动时 Chrome 会露出一道裁切接缝 -->
            <img v-if="art" :key="art" v-ready :class="{ photo: art === image }" :src="art" alt="" decoding="sync" data-fade /><JourneyMark v-else class="ticket-mark" />
            <p class="ticket-city"><b>{{ city.name }}</b><span>{{ code ?? city.key.toUpperCase() }}<template v-if="date"> · {{ date }}</template></span></p>
          </div>
          <dl class="ticket-grid">
            <div v-for="cell in [...cells, ...rows]" :key="cell.label" :class="cell.span && `span-${cell.span}`"><dt>{{ cell.label }}</dt>
              <dd :class="{ long: rows.includes(cell) }" :title="cell.title ?? cell.value">
                <span v-if="cell.parts" class="ticket-parts"><button v-for="part in cell.parts" :key="part.key" type="button" class="ticket-value" :class="{ changed: part.changed, active: active === part.key }" :disabled="busy" @click="edit(part.key)"><small>{{ part.label }}</small>{{ part.value }}</button></span>
                <component :is="editable && cell.key && !blank ? 'button' : 'span'" v-else :type="editable && cell.key && !blank ? 'button' : undefined" class="ticket-value" :class="{ guess: cell.guess, changed: cell.changed, active: !!cell.key && active === cell.key, muted: cell.muted }" :disabled="editable && busy || undefined" @click="editable && cell.key && !blank && edit(cell.key)">
                  <template v-if="cell.words"><span v-for="(word, index) in cell.words" :key="word" class="ticket-word">{{ word }}<template v-if="index < cell.words.length - 1">、</template></span></template>
                  <RollValue v-else :value="cell.value ?? ''" :scope="city.key" />
                </component>
              </dd>
            </div>
          </dl>
        </div></div>
      </div>
      <div class="ticket-stub">
        <div class="ticket-code" :class="{ busy }" aria-hidden="true"><i v-for="(width, index) in bars" :key="index" :style="{ width: `${width}px`, '--at': index }" /></div>
        <p v-if="error" class="ticket-note bad" role="alert">{{ error }}</p>
        <p v-else class="ticket-note">{{ note }}</p>
        <div class="ticket-actions"><slot /></div>
      </div>
    </div>
  </aside>
</template>
