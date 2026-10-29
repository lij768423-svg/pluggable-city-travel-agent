<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref } from 'vue'
import { PhArrowUpRight } from '@phosphor-icons/vue'
import '@fontsource-variable/geist-mono'
import '@fontsource-variable/manrope'
import '../ljj-helix.css'
import type { CityInfo } from '../api'
import { cityShowcase } from '../cityShowcase'
const props = defineProps<{ cities: CityInfo[]; pending: string | null; error: string; loading: boolean }>()
const emit = defineEmits<{ choose: [key: string] }>()
const stage = ref<HTMLElement>()
const canvas = ref<HTMLCanvasElement>()
const seed = ref<HTMLElement>()
const cardRefs = new Map<string, HTMLElement>()
const animationReady = ref(false)
const axisSeedExpired = ref(false)
// 12 城：已开放的排在前面（螺旋最左侧）；未开放的说明行写“即将开放”，点了不进入。
const entries = computed(() => {
  const open = new Set(props.cities.map(city => city.key))
  const names = new Map(props.cities.map(city => [city.key, city.name]))
  const list = cityShowcase.map(item => ({ ...item, name: names.get(item.key) || item.name, open: open.has(item.key) }))
  return [...list.filter(item => item.open), ...list.filter(item => !item.open)]
})
const openCount = computed(() => entries.value.filter(item => item.open).length)
const setCard = (key: string) => (node: unknown) => { if (node instanceof HTMLElement) cardRefs.set(key, node); else cardRefs.delete(key) }
function choose(item: { key: string; open: boolean }) { if (item.open && !props.pending && !props.loading) emit('choose', item.key) }

// 逐行对照 ~/personal-portfolio/src/components/ProjectHelix.tsx（2026-10-21 用户：完全按照原版排版）。
// 原版用 three.js 正交相机，投影只用 x、y，z 只决定层级；这里用 Canvas 2D 画同样的线与点，节奏、参数、卡片位置公式不变。
const HELIX_PHASE_SPEED = 0.34
const HELIX_VERTICAL_LIMIT = 4.2
const HELIX_AXIS_MS = 460
const HELIX_BLOOM_MS = 1040
let dispose: (() => void) | null = null

async function waitForProjectImages(root: HTMLElement) {
  const images = Array.from(root.querySelectorAll<HTMLImageElement>('.project-card-media img'))
  await Promise.all(images.map(async image => {
    if (!image.complete) await new Promise<void>(resolve => { image.addEventListener('load', () => resolve(), { once: true }); image.addEventListener('error', () => resolve(), { once: true }) })
    if (!image.decode || image.naturalWidth === 0) return
    try { await image.decode() } catch { /* 单张图失败不拖住整段动画 */ }
  }))
}

function startHelix() {
  const root = stage.value, surface = canvas.value
  if (!root || !surface || window.innerWidth <= 1080 || dispose) return
  const context = surface.getContext('2d')
  if (!context) return
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches
  let disposed = false
  animationReady.value = reduceMotion
  axisSeedExpired.value = reduceMotion
  const readThemeColor = (name: string, fallback: string) => getComputedStyle(root).getPropertyValue(name).trim() || fallback
  let width = 1, height = 1, left = -4, right = 4
  let frame: number | null = null
  let visible = true
  let lastTimestamp = performance.now()
  let expansionStartedAt: number | null = reduceMotion ? performance.now() - HELIX_AXIS_MS - HELIX_BLOOM_MS : null
  let phase = 0.44
  const clamp = (value: number) => Math.min(1, Math.max(0, value))
  const easeInOutCubic = (value: number) => value < 0.5 ? 4 * value * value * value : 1 - Math.pow(-2 * value + 2, 3) / 2
  const easeOutBack = (value: number) => { const overshoot = 1.42; return 1 + (overshoot + 1) * Math.pow(value - 1, 3) + overshoot * Math.pow(value - 1, 2) }
  const count = entries.value.length
  const columnCount = Math.max(1, Math.ceil(count / 2))
  const helixHalfSpan = Math.min(6.55, Math.max(2.4, (columnCount - 1) * 1.31))
  const nodeX = (column: number) => columnCount === 1 ? 0 : -helixHalfSpan + (column / (columnCount - 1)) * helixHalfSpan * 2
  const helixAt = (x: number, strand: number, currentPhase: number) => {
    const direction = strand === 0 ? 1 : -1
    const angle = x * 1.08 + currentPhase
    return { y: Math.sin(angle) * 0.92 * direction, z: Math.cos(angle) * 1.16 * direction }
  }
  const project = (x: number, y: number) => [((x - left) / (right - left)) * width, (0.5 - y / (HELIX_VERTICAL_LIMIT * 2)) * height] as const
  const resize = () => {
    const bounds = root.getBoundingClientRect()
    width = Math.max(1, bounds.width); height = Math.max(1, bounds.height)
    const horizontalExtent = Math.max(HELIX_VERTICAL_LIMIT * (width / height) + 1.08, helixHalfSpan + 1.42)
    left = -horizontalExtent; right = horizontalExtent
    const ratio = Math.min(window.devicePixelRatio || 1, 1.5)
    surface.width = Math.round(width * ratio); surface.height = Math.round(height * ratio)
    context.setTransform(ratio, 0, 0, ratio, 0, 0)
  }
  const polyline = (points: [number, number][], color: string, alpha: number) => {
    context.beginPath()
    points.forEach(([x, y], index) => { const [sx, sy] = project(x, y); if (index) context.lineTo(sx, sy); else context.moveTo(sx, sy) })
    context.globalAlpha = alpha; context.strokeStyle = color; context.stroke()
  }
  const segments = (pairs: [number, number, number, number][], color: string, alpha: number) => {
    context.beginPath()
    for (const [ax, ay, bx, by] of pairs) { const [sx, sy] = project(ax, ay); const [ex, ey] = project(bx, by); context.moveTo(sx, sy); context.lineTo(ex, ey) }
    context.globalAlpha = alpha; context.strokeStyle = color; context.stroke()
  }
  const renderFrame = (timestamp: number) => {
    frame = null
    if (!visible) return
    const delta = Math.min((timestamp - lastTimestamp) / 1000, 0.08)
    lastTimestamp = timestamp
    const elapsed = expansionStartedAt === null ? 0 : timestamp - expansionStartedAt
    const axisLinear = reduceMotion ? 1 : clamp(elapsed / HELIX_AXIS_MS)
    const bloomLinear = reduceMotion ? 1 : clamp((elapsed - HELIX_AXIS_MS) / HELIX_BLOOM_MS)
    const axisExpansion = 1 - Math.pow(1 - axisLinear, 4)
    const bloomExpansion = easeInOutCubic(bloomLinear)
    if (!reduceMotion && bloomLinear > 0) phase = (phase + delta * HELIX_PHASE_SPEED) % (Math.PI * 2)
    root.dataset.helixStage = axisLinear < 1 ? 'axis' : bloomLinear < 1 ? 'bloom' : 'live'
    root.dataset.helixExpanded = bloomLinear >= 1 ? 'true' : 'false'
    const text = readThemeColor('--text', '#151613'), accent = readThemeColor('--accent', '#b93b28')
    context.clearRect(0, 0, width, height)
    context.lineWidth = 1
    const strandOne: [number, number][] = [], strandTwo: [number, number][] = []
    for (let index = 0; index < 180; index += 1) {
      const targetX = -helixHalfSpan - 0.46 + (index / 179) * (helixHalfSpan * 2 + 0.92)
      const x = targetX * axisExpansion
      strandOne.push([x, helixAt(targetX, 0, phase).y * bloomExpansion])
      strandTwo.push([x, helixAt(targetX, 1, phase).y * bloomExpansion])
    }
    const rungs: [number, number, number, number][] = []
    for (let index = 0; index < 24; index += 1) {
      const targetX = -helixHalfSpan + (index / 23) * helixHalfSpan * 2
      const x = targetX * axisExpansion
      rungs.push([x, helixAt(targetX, 0, phase).y * bloomExpansion, x, helixAt(targetX, 1, phase).y * bloomExpansion])
    }
    const tethers: [number, number, number, number][] = [], nodes: [number, number][] = []
    entries.value.forEach((item, index) => {
      const column = Math.floor(index / 2), strand = index % 2, direction = strand === 0 ? 1 : -1
      const targetX = nodeX(column)
      const x = targetX * axisExpansion
      const targetBackbone = helixAt(targetX, strand, phase)
      const backbone = { y: targetBackbone.y * bloomExpansion, z: targetBackbone.z * bloomExpansion }
      const y = (direction * 2.2 + targetBackbone.y * 0.5) * bloomExpansion
      tethers.push([x, backbone.y, x, y]); nodes.push([x, backbone.y])
      const card = cardRefs.get(item.key)
      if (!card) return
      const [screenX, screenY] = project(x, y)
      const depth = (targetBackbone.z + 1.16) / 2.32
      const distanceFromCenter = helixHalfSpan === 0 ? 0 : Math.abs(targetX) / helixHalfSpan
      const revealAt = 0.54 + distanceFromCenter * 0.2
      const entryLinear = clamp((bloomExpansion - revealAt) / 0.13)
      const entry = easeOutBack(entryLinear)
      card.style.setProperty('--helix-x', `${screenX}px`)
      card.style.setProperty('--helix-y', `${screenY}px`)
      card.style.setProperty('--helix-scale', (0.58 + entry * 0.42).toFixed(4))
      card.style.setProperty('--helix-rotate', `${(Math.cos(x * 1.08 + phase) * direction * 7).toFixed(3)}deg`)
      card.style.setProperty('--helix-opacity', clamp(entry).toFixed(3))
      card.style.zIndex = String(12 + Math.round(depth * 16))
    })
    // 与原版 scene.add 顺序一致：两条链、横档、系绳、节点
    polyline(strandOne, accent, 0.54)
    polyline(strandTwo, text, 0.28)
    segments(rungs, text, 0.2 * bloomExpansion)
    segments(tethers, accent, 0.36 * clamp((bloomExpansion - 0.34) / 0.66))
    context.globalAlpha = 0.88 * clamp((bloomExpansion - 0.42) / 0.58); context.fillStyle = accent
    for (const [nx, ny] of nodes) { const [sx, sy] = project(nx, ny); context.fillRect(sx - 2.5, sy - 2.5, 5, 5) }
    context.globalAlpha = 1
    if (!reduceMotion && expansionStartedAt !== null) frame = requestAnimationFrame(renderFrame)
  }
  const requestFrame = () => { if (frame === null && visible) frame = requestAnimationFrame(renderFrame) }
  const resizeObserver = new ResizeObserver(() => { resize(); requestFrame() })
  const intersectionObserver = new IntersectionObserver(([entry]) => {
    visible = entry.isIntersecting
    if (visible) { lastTimestamp = performance.now(); requestFrame() } else if (frame !== null) { cancelAnimationFrame(frame); frame = null }
  })
  resizeObserver.observe(root); intersectionObserver.observe(root)
  resize(); requestFrame()
  const prepareAnimation = async () => {
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
    if (disposed) return
    await waitForProjectImages(root)
    if (disposed) return
    await new Promise<void>(resolve => requestAnimationFrame(() => resolve()))
    if (disposed) return
    expansionStartedAt = performance.now()
    lastTimestamp = expansionStartedAt
    animationReady.value = true
    // 原版的轴线种子：0.9 秒内从 3.5% 宽拉满，再淡出
    const seedAnimation = seed.value?.animate([{ opacity: 1, transform: 'scaleX(0.035)' }, { opacity: 1, transform: 'scaleX(1)', offset: HELIX_AXIS_MS / 900 }, { opacity: 0, transform: 'scaleX(1)' }],
      { duration: 900, easing: 'cubic-bezier(0.16, 1, 0.3, 1)', fill: 'forwards' })
    if (seedAnimation) seedAnimation.onfinish = () => { axisSeedExpired.value = true }
    else axisSeedExpired.value = true
    requestFrame()
  }
  if (!reduceMotion) void prepareAnimation()
  dispose = () => {
    disposed = true
    if (frame !== null) cancelAnimationFrame(frame)
    resizeObserver.disconnect(); intersectionObserver.disconnect()
  }
}
function onWindowResize() { startHelix() }

onMounted(() => nextTick(() => {
  startHelix()
  window.addEventListener('resize', onWindowResize)
}))
onBeforeUnmount(() => { dispose?.(); window.removeEventListener('resize', onWindowResize) })
</script>

<template>
  <main class="destination-page ljj-scope" :aria-busy="!!pending">
    <div class="route-main">
      <section class="project-index has-stage-title project-dna-page" aria-labelledby="destination-title">
        <div class="project-dna-shell section-shell">
          <header class="project-dna-heading">
            <h1 id="destination-title">下一程，想去哪里？</h1>
            <p>{{ entries.length }} 座城市 · {{ openCount }} 座已开放</p>
          </header>
          <div ref="stage" :class="['index-items', 'project-cloud', 'project-helix', `project-cloud-count-${entries.length}`]"
            :data-helix-ready="animationReady ? 'true' : 'false'" :data-axis-seed-expired="axisSeedExpired ? 'true' : 'false'">
            <div ref="seed" class="project-helix-axis-seed" aria-hidden="true" />
            <canvas ref="canvas" class="project-helix-canvas" aria-hidden="true" />
            <div class="project-helix-nodes" role="list" aria-label="选择旅行城市">
              <article v-for="item in entries" :key="item.key" :ref="setCard(item.key)" class="index-item helix-node" :data-project-id="item.key" role="listitem">
                <a class="project-card-link" :href="item.open ? `#${item.key}` : undefined" :aria-disabled="item.open ? undefined : 'true'"
                  :aria-label="item.open ? `选择${item.name}` : `${item.name}即将开放`" @click.prevent="choose(item)">
                  <div class="project-card-kinetic">
                    <div class="project-card-float">
                      <div class="project-card-surface helix-poster-card project-card-product">
                        <div class="project-card-media"><img :src="item.image" sizes="190px" alt="" width="1200" height="750" loading="eager" /></div>
                        <div class="helix-poster-caption">
                          <span>{{ pending === item.key ? `正在前往${item.name}…` : item.open ? item.landmark : `即将开放 · ${item.landmark}` }}</span>
                          <div><h2>{{ item.name }}</h2><PhArrowUpRight :size="16" weight="bold" aria-hidden="true" /></div>
                        </div>
                      </div>
                    </div>
                  </div>
                </a>
              </article>
            </div>
          </div>
        </div>
      </section>
    </div>
    <p v-if="error" class="destination-error" role="alert">{{ error }}，请重新选择目的地。</p>
  </main>
</template>
