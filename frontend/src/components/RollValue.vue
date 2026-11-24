<script setup lang="ts">
// 值变了只翻变了的那几位（思路参考 Kokonut UI 的 Boarding Pass，这里用 Vue 重写）。
// 首次渲染不动；scope 一起变（换了一天、换了一份行程）也不动，只有同一处的值真的改了才翻。
// 给了 from 时，挂载后从 from 翻到 value（换点预览里“原来 → 现在”）。
// 文字只有一份（翻出去的旧字对读屏隐藏、不能选中），复制和读屏拿到的都是当前值。
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { reducedMotion, springMs } from '../motion'

const props = withDefaults(defineProps<{ value: string | number; from?: string | number | null; scope?: string | number | null; flash?: boolean }>(), { from: null, scope: null, flash: true })

type Cell = { key: number; ch: string; old?: string; dir?: 'up' | 'down'; delay?: number; size?: 'grow' | 'shrink' }
let seq = 0
let settleTimer: ReturnType<typeof setTimeout> | undefined
let startTimer: ReturnType<typeof setTimeout> | undefined
const cells = ref<Cell[]>([])
const flashing = ref(false)
const text = (value: string | number | null | undefined) => (value == null ? '' : String(value))

function show(value: string) {
  clearTimeout(settleTimer)
  cells.value = Array.from(value).map(ch => ({ key: seq++, ch }))
}
// 按数字比大小定方向：变大从下面上来，变小从上面下来；比不了就往上
function direction(before: string, after: string): 'up' | 'down' {
  const [a, b] = [before, after].map(value => Number(value.replace(/[^\d.]/g, '')))
  return Number.isFinite(a) && Number.isFinite(b) && b < a ? 'down' : 'up'
}
function roll(before: string, after: string, flash: boolean) {
  if (before === after || reducedMotion()) { show(after); return }
  clearTimeout(settleTimer)
  // 右对齐比较，个位对个位；位数不同时多出来的位置是“长出来/收回去”
  const [a, b] = [Array.from(before), Array.from(after)]
  const width = Math.max(a.length, b.length)
  const left = (list: string[]) => [...Array(width - list.length).fill(''), ...list]
  const [from, to] = [left(a), left(b)]
  const dir = direction(before, after)
  let changed = 0
  const next: Cell[] = []
  to.forEach((ch, index) => {
    const old = from[index]!
    if (old === ch) { if (ch) next.push({ key: seq++, ch }); return }
    next.push({ key: seq++, ch, old, dir, delay: changed++ * 28, size: !old ? 'grow' : !ch ? 'shrink' : undefined })
  })
  cells.value = next
  if (flash && props.flash) {
    flashing.value = false
    requestAnimationFrame(() => { flashing.value = true })
  }
  settleTimer = setTimeout(() => show(after), springMs('roll') + changed * 28 + 80)
}

onMounted(() => {
  const value = text(props.value)
  const from = text(props.from)
  if (props.from == null || from === value || reducedMotion()) { show(value); return }
  show(from)
  startTimer = setTimeout(() => roll(from, value, false), 90)
})
watch(() => [text(props.value), props.scope] as const, ([value, scope], [before, previousScope]) => {
  clearTimeout(startTimer)
  if (scope !== previousScope) show(value)
  else roll(before, value, true)
})
onBeforeUnmount(() => { clearTimeout(settleTimer); clearTimeout(startTimer) })
</script>

<template>
  <span class="roll" :class="{ 'roll-flash': flashing }" @animationend.self="flashing = false"><span class="roll-face"><template v-for="cell in cells" :key="cell.key"><span v-if="cell.old === undefined">{{ cell.ch }}</span><span v-else class="roll-col" :class="cell.size" :data-dir="cell.dir" :style="{ '--roll-delay': `${cell.delay}ms` }"><span class="roll-out" aria-hidden="true">{{ cell.old || '​' }}</span><span class="roll-in">{{ cell.ch }}</span></span></template></span></span>
</template>
