<script setup lang="ts">
// 实时回答逐词浮现（2026-11-24 第二批）：只动这一刻从模型收到的新文字，到多少显示多少，最多错开 160ms，不拖慢也不假装在生成。
// staticUntil 之前的部分（缓存的回答、分隔行）直接显示；展开面板时已有的文字也直接显示。
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { reducedMotion, springMs } from '../motion'

const props = defineProps<{ text: string; staticUntil: number }>()

type Piece = { key: number; text: string; delay: number }
const STEP = 18
const SPREAD = 160
let seq = 0
const settled = ref(props.text.length)  // 这之前的文字按普通文本显示
const fresh = ref<Piece[]>([])
let timer: ReturnType<typeof setTimeout> | undefined

const plain = computed(() => props.text.slice(0, settled.value))
type Segments = Iterable<{ segment: string }>
const segmenter = typeof Intl !== 'undefined' && 'Segmenter' in Intl
  ? new (Intl as unknown as { Segmenter: new (locale: string, options: { granularity: 'word' }) => { segment: (text: string) => Segments } }).Segmenter('zh', { granularity: 'word' }) : null
const words = (text: string) => segmenter ? Array.from(segmenter.segment(text), part => part.segment) : text.split(/(\s+)/).filter(Boolean)

function settle() {
  clearTimeout(timer)
  settled.value = props.text.length
  fresh.value = []
}
// 不变量：settled + fresh 里的字数 = 已收到的字数
watch(() => props.text, (now, before) => {
  const from = Math.max(before.length, props.staticUntil)
  // 换了一次生成、超长从头截断、减少动态效果、一次到太多（例如页面在后台攒了一批）都直接显示
  const parts = now.startsWith(before) && !reducedMotion() ? words(now.slice(from)) : []
  if (!parts.length || parts.length > 400) { settle(); return }
  if (from > before.length) { settled.value = from; fresh.value = [] }
  const step = Math.min(STEP, SPREAD / parts.length)
  let pieces = [...fresh.value, ...parts.map((text, index) => ({ key: seq++, text, delay: Math.round(index * step) }))]
  // 连续到达时把早已放完的词并回普通文本，节点数不会一直涨
  if (pieces.length > 480) {
    settled.value += pieces.slice(0, -400).reduce((sum, piece) => sum + piece.text.length, 0)
    pieces = pieces.slice(-400)
  }
  fresh.value = pieces
  clearTimeout(timer)
  timer = setTimeout(settle, SPREAD + springMs('fade') + 60)
})
onBeforeUnmount(() => clearTimeout(timer))
</script>

<template><pre>{{ plain }}<span v-for="piece in fresh" :key="piece.key" class="stream-word" :style="{ animationDelay: `${piece.delay}ms` }">{{ piece.text }}</span></pre></template>
