<script setup lang="ts">
// 「跟 AI 说」输入框上面的选项：一行是 AI 猜的，一行是还能加的；点一项（或点票上那一项）就在这里展开同「自己选」的控件。
// 选完先进草稿，和别的修改一起提交。
import { computed, ref, watch } from 'vue'
import { PhX } from '@phosphor-icons/vue'
import type { Meta, Understanding } from '../api'
import { expandHooks } from '../motion'
import { EXTRAS, FOOD, SOURCE, TRANSPORT, clock, minutesOf, type ExtraKey, type ListKey, type UnderstandingDraft } from '../understandingDraft'
import DetentSlider from './DetentSlider.vue'
import OriginPicker from './OriginPicker.vue'
import SegControl from './SegControl.vue'

const props = defineProps<{ edits: UnderstandingDraft; understanding: Understanding; meta: Meta | null; modelValue: string; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [key: string] }>()
const edits = props.edits
const open = computed(() => props.modelValue)
const isExtra = (key: string): key is ExtraKey => EXTRAS.some(item => item.key === key)
const label = (key: string) => EXTRAS.find(item => item.key === key)?.label ?? edits.row(key)?.label ?? ''
const touched = (key: string) => isExtra(key) ? edits.extraChanged(key) : edits.changed(key)
// 猜的这一行在提交前不增不减，点选时不跳位
const guessed = computed(() => edits.rows.value.filter(row => edits.guessed(row.key) && row.key !== 'city').map(row => row.key))
const addable = ['must_visit', 'avoid', ...EXTRAS.map(item => item.key)]
const filled = (key: string) => isExtra(key) ? edits.extraSet(key) : edits.list(key).length > 0
const toggle = (key: string) => emit('update:modelValue', open.value === key ? '' : key)
// 这一项是怎么来的
const basis = computed(() => {
  if (touched(open.value)) return '已改，还没提交'
  if (isExtra(open.value)) return edits.extraSet(open.value) ? SOURCE.said : '没说'
  const row = edits.row(open.value)
  return row?.source ? [SOURCE[row.source], row.note].filter(Boolean).join('，') : '没说'
})

const pairs = (options: Record<string, string> | undefined) => Object.entries(options ?? {}).map(([value, text]) => ({ value, label: text }))
const choices = computed<Record<string, { value: string; label: string }[]>>(() => ({ companions: pairs(props.meta?.companions), pace: pairs(props.meta?.paces), transport: pairs(TRANSPORT),
  spice: pairs(props.meta?.spice_levels), familiarity: pairs(props.meta?.familiarity_levels), food_preference: pairs(FOOD) }))
const days = [1, 2, 3, 4, 5].map(value => ({ value, label: `${value} 天` }))
const budgets = [200, 500, 800, 1200, 1800, 2500, 4000].map(value => ({ value, label: `¥${value}` }))
function budgetAt(index: number) {
  const left = Math.min(Math.floor(index), budgets.length - 2)
  const raw = budgets[left]!.value + (budgets[left + 1]!.value - budgets[left]!.value) * (index - left)
  const step = raw < 1000 ? 10 : 50
  return Math.round(raw / step) * step
}
const walks = [{ value: 0, label: '按同行人' }, ...[3000, 5000, 8000, 12000].map(value => ({ value, label: `${value / 1000} 公里` }))]
const yesNo = { include_evening: [{ value: 1, label: '顺路安排夜游' }, { value: 0, label: '早点休息' }], allow_taxi_fallback: [{ value: 1, label: '可以改打车' }, { value: 0, label: '也不打车' }] }
const toggleItem = (key: string, item: string) => edits.set(key, edits.list(key).includes(item) ? edits.list(key).filter(other => other !== item) : [...edits.list(key), item])
// 想玩按词表顺序存，跟确认表一致
const toggleInterest = (item: string) => edits.set('interests', (props.meta?.interests ?? []).filter(other => other === item ? !edits.list('interests').includes(other) : edits.list('interests').includes(other)))
const adding = ref('')
watch(open, () => { adding.value = '' })
function add(key: string) {
  const name = adding.value.trim()
  if (name && !edits.list(key).includes(name)) edits.set(key, [...edits.list(key), name])
  adding.value = ''
}
// 日期按中国时间
const chinaDay = (offset: number) => new Date(Date.now() + 8 * 3600e3 + offset * 86400e3).toISOString().slice(0, 10)
const weekend = computed(() => { const weekday = new Date(`${chinaDay(0)}T00:00:00Z`).getUTCDay(); return chinaDay(weekday === 0 ? 0 : 6 - weekday) })
const number = (event: Event) => { const value = Number((event.target as HTMLInputElement).value); return Number.isFinite(value) && value > 0 ? value : null }
</script>

<template>
  <div class="tray">
    <Transition :css="false" v-bind="expandHooks">
      <div v-if="open" class="tray-open"><div class="tray-sheet">
        <p class="tray-head"><b>{{ label(open) }}</b><small>{{ basis }}</small><button type="button" aria-label="收起选项" @click="emit('update:modelValue', '')"><PhX :size="16" /></button></p>
        <SegControl v-if="open === 'days'" :model-value="Number(edits.value('days'))" :options="days" label="天数" :disabled="disabled" @update:model-value="edits.set('days', $event)" />
        <SegControl v-else-if="choices[open]" :model-value="String(edits.value(open))" :options="choices[open]!" :label="label(open)" :disabled="disabled" @update:model-value="edits.set(open, $event)" />
        <template v-else-if="open === 'budget'">
          <DetentSlider :model-value="edits.budgetShown.value" :options="budgets" label="人均总预算" :value-text="`${edits.budgetShown.value} 元`" :value-at="budgetAt" :format="value => `¥${value}`" :disabled="disabled" mono @update:model-value="edits.set('budget', $event)" />
          <p class="tray-note">{{ edits.value('days') }} 天，平均每天 {{ edits.perDay.value }} 元<input class="tinput tinput-mini" type="number" min="1" max="100000" :value="edits.budgetShown.value" aria-label="人均总预算" :disabled="disabled" @change="edits.change('budget', ($event.target as HTMLInputElement).value)" /></p>
        </template>
        <div v-else-if="open === 'start_date'" class="pill-row small"><button type="button" class="pill" :class="{ on: !edits.value('start_date') }" :disabled="disabled" @click="edits.set('start_date', null)">没定</button><button type="button" class="pill" :class="{ on: edits.value('start_date') === chinaDay(1) }" :disabled="disabled" @click="edits.set('start_date', chinaDay(1))">明天</button><button type="button" class="pill" :class="{ on: edits.value('start_date') === weekend }" :disabled="disabled" @click="edits.set('start_date', weekend)">这个周末</button><input class="tinput" type="date" :value="edits.value('start_date') || ''" aria-label="出游日期" :disabled="disabled" @change="edits.change('start_date', ($event.target as HTMLInputElement).value)" /></div>
        <OriginPicker v-else-if="open === 'hotel'" compact :city="String(understanding.profile.city)" :presets="edits.hotelPresets.value" :model-value="edits.hotelPicked.value" :disabled="!!disabled" @update:model-value="edits.pickOrigin" />
        <div v-else-if="open === 'interests'" class="pill-row small"><button v-for="item in meta?.interests ?? []" :key="item" type="button" class="pill" :class="{ on: edits.list('interests').includes(item) }" :aria-pressed="edits.list('interests').includes(item)" :disabled="disabled" @click="toggleInterest(item)">{{ item }}</button></div>
        <form v-else-if="open === 'must_visit' || open === 'avoid'" class="pill-row small" @submit.prevent="add(open)"><button v-for="name in edits.list(open)" :key="name" type="button" class="pill on" :aria-label="`去掉${name}`" :disabled="disabled" @click="toggleItem(open, name)">{{ name }}<PhX :size="13" aria-hidden="true" /></button><input v-model="adding" class="tinput" type="text" maxlength="40" :aria-label="`添加${label(open as ListKey)}的地方`" :placeholder="open === 'must_visit' ? '地点名，例如灵隐寺' : '地点名，例如宋城'" :disabled="disabled" /><button type="submit" class="pill" :disabled="disabled || !adding.trim()">加上</button></form>
        <SegControl v-else-if="open === 'evening'" :model-value="edits.current('include_evening') === false ? 0 : 1" :options="yesNo.include_evening" label="夜间" :disabled="disabled" @update:model-value="edits.set('include_evening', !!$event)" />
        <SegControl v-else-if="open === 'fallback'" :model-value="edits.current('allow_taxi_fallback') === false ? 0 : 1" :options="yesNo.allow_taxi_fallback" label="公交不方便时" :disabled="disabled" @update:model-value="edits.set('allow_taxi_fallback', !!$event)" />
        <SegControl v-else-if="open === 'food'" :model-value="String(edits.value('food_preference') ?? 'balanced')" :options="choices.food_preference!" label="怎么吃" :disabled="disabled" @update:model-value="edits.set('food_preference', $event)" />
        <SegControl v-else-if="open === 'walk'" :model-value="Number(edits.value('max_walking_m') ?? 0)" :options="walks" label="每天步行上限" :disabled="disabled" @update:model-value="edits.set('max_walking_m', $event || null)" />
        <div v-else-if="open === 'meal'" class="tray-pair"><label>最少<input class="tinput" type="number" min="0" max="2000" placeholder="¥" :value="edits.value('meal_budget_min') ?? ''" :disabled="disabled" @change="edits.set('meal_budget_min', number($event))" /></label><label>最多<input class="tinput" type="number" min="1" max="2000" placeholder="¥" :value="edits.value('meal_budget_max') ?? ''" :disabled="disabled" @change="edits.set('meal_budget_max', number($event))" /></label></div>
        <div v-else-if="open === 'window'" class="tray-pair"><label>每天出发<input class="tinput" type="time" :value="clock(edits.value('day_start'))" :disabled="disabled" @change="edits.set('day_start', minutesOf(($event.target as HTMLInputElement).value))" /></label><label>最晚返回<input class="tinput" type="time" :value="clock(edits.value('day_end'))" :disabled="disabled" @change="edits.set('day_end', minutesOf(($event.target as HTMLInputElement).value))" /></label></div>
      </div></div>
    </Transition>
    <div v-if="guessed.length" class="tray-chips"><span>我猜的</span><button v-for="key in guessed" :key="key" type="button" :class="{ guess: !touched(key), set: touched(key), on: open === key }" :aria-expanded="open === key" :disabled="disabled" @click="toggle(key)">{{ label(key) }}</button></div>
    <div class="tray-chips"><span>还能加</span><button v-for="key in addable" :key="key" type="button" :class="{ set: touched(key) || filled(key), on: open === key }" :aria-expanded="open === key" :disabled="disabled" @click="toggle(key)">{{ label(key) }}</button></div>
  </div>
</template>
