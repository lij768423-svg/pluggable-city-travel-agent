<script setup lang="ts" generic="T extends string | number">
// 单选分段：选中的底块用共用的伸缩高亮（motion.ts useInk）。方向键换选项，Tab 只停一次。
import { computed, ref, type Component } from 'vue'
import { useInk } from '../motion'

const props = defineProps<{ options: readonly { value: T; label: string; icon?: Component }[]; modelValue: T; label: string; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: T] }>()
const root = ref<HTMLElement>()
const current = computed(() => props.options.findIndex(option => option.value === props.modelValue))
useInk(root, () => props.modelValue)
function move(event: KeyboardEvent) {
  const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key]
  if (!step || props.disabled) return
  event.preventDefault()
  const next = (Math.max(0, current.value) + step + props.options.length) % props.options.length
  emit('update:modelValue', props.options[next]!.value)
  root.value?.querySelectorAll<HTMLElement>('[role=radio]')[next]?.focus()
}
</script>

<template>
  <div ref="root" class="seg-control" role="radiogroup" :aria-label="label" @keydown="move">
    <span class="ink" aria-hidden="true" />
    <button v-for="(option, index) in options" :key="String(option.value)" type="button" role="radio" :class="{ on: index === current }" :aria-checked="index === current" :tabindex="index === Math.max(0, current) ? 0 : -1" :disabled="disabled" @click="emit('update:modelValue', option.value)"><component :is="option.icon" v-if="option.icon" :size="17" aria-hidden="true" />{{ option.label }}</button>
  </div>
</template>
