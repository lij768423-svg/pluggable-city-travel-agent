<script setup lang="ts" generic="T extends string | number">
// 刻度滑杆：按住拖动时附近的刻度跟着长高；松手后指针留在松手的位置，不弹回档位（2026-11-25 用户），值取最近的一档。
// 给了 valueAt 就是连续取值（预算）：按位置算出值，不在档位上时数字写在指针下面。点档位名、按方向键会走到那一档。
// 值从别处改了（自己填、重置）指针才跟着走。dense：刻度左疏右密（节奏用）。
import { computed, ref, watch } from 'vue'

const props = defineProps<{ options: readonly { value: T; label: string }[]; modelValue: T; label: string; valueText?: string; valueAt?: (index: number) => T; format?: (value: T) => string; disabled?: boolean; dense?: boolean; mono?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: T] }>()
const TICKS = 58
const ticks = Array.from({ length: TICKS + 1 }, (_, index) => props.dense ? (index / TICKS) ** 0.6 : index / TICKS)
const track = ref<HTMLElement>()
const dragging = ref(false)
const held = ref<number | null>(null)  // 拖动后指针停的位置（0–1）
let sent: T | undefined
const last = computed(() => props.options.length - 1)
const current = computed(() => props.options.findIndex(option => option.value === props.modelValue))
// 当前停在第几档，可以是小数
const index = computed(() => {
  if (current.value >= 0) return current.value
  const value = Number(props.modelValue)
  const stops = props.options.map(option => Number(option.value) || 0)
  if (!Number.isFinite(value) || value <= stops[0]!) return 0
  const next = stops.findIndex(stop => stop > value)
  if (next < 0) return last.value
  return next - 1 + (value - stops[next - 1]!) / (stops[next]! - stops[next - 1]!)
})
const position = computed(() => held.value ?? index.value / last.value)
watch(() => props.modelValue, value => { if (value !== sent) held.value = null })
const tall = (at: number) => 0.34 + 0.66 * Math.exp(-(((at - position.value) / 0.045) ** 2))
function put(value: T) {
  sent = value
  if (value !== props.modelValue) emit('update:modelValue', value)
}
function choose(next: number) {
  held.value = null
  put(props.options[Math.min(last.value, Math.max(0, next))]!.value)
}
function follow(event: PointerEvent) {
  const box = track.value!.getBoundingClientRect()
  held.value = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width))
  const at = held.value * last.value
  put(props.valueAt ? props.valueAt(at) : props.options[Math.round(at)]!.value)
}
function grab(event: PointerEvent) {
  if (props.disabled || event.button !== 0) return
  track.value!.setPointerCapture(event.pointerId)
  dragging.value = true
  follow(event)
}
function keys(event: KeyboardEvent) {
  if (props.disabled) return
  const up = Math.floor(index.value) + 1
  const down = Math.ceil(index.value) - 1
  const to = { ArrowRight: up, ArrowUp: up, ArrowLeft: down, ArrowDown: down, Home: 0, End: last.value }[event.key]
  if (to === undefined) return
  event.preventDefault()
  choose(to)
}
</script>

<template>
  <div class="detent" :class="{ dragging, mono, disabled }">
    <div ref="track" class="detent-track" role="slider" :tabindex="disabled ? -1 : 0" :aria-label="label" aria-valuemin="0" :aria-valuemax="last" :aria-valuenow="index" :aria-valuetext="valueText ?? options[current]?.label" :aria-disabled="disabled || undefined"
      @pointerdown="grab" @pointermove="dragging && follow($event)" @pointerup="dragging = false" @pointercancel="dragging = false" @lostpointercapture="dragging = false" @keydown="keys">
      <span v-for="(at, tick) in ticks" :key="tick" class="detent-tick" :class="{ near: Math.abs(at - position) < .07 }" :style="{ left: `${at * 100}%`, transform: `scaleY(${tall(at).toFixed(3)})` }" />
      <span class="detent-thumb" :style="{ left: `${position * 100}%` }" />
    </div>
    <div class="detent-labels" aria-hidden="true"><button v-for="(option, at) in options" :key="String(option.value)" type="button" tabindex="-1" :class="{ on: at === current, covered: current < 0 && Math.abs(at / last - position) < .085 }" :style="{ left: `${at / last * 100}%` }" :disabled="disabled" @click="choose(at)">{{ option.label }}</button><span v-if="current < 0" class="detent-value" :style="{ left: `${position * 100}%`, transform: `translateX(${position < .04 ? 0 : position > .96 ? -100 : -50}%)` }">{{ format ? format(modelValue) : modelValue }}</span></div>
  </div>
</template>
