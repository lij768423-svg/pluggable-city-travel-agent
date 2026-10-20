<script setup lang="ts">
import { nextTick, onMounted, ref } from 'vue'
import { PhArrowUpRight, PhCompass, PhSparkle } from '@phosphor-icons/vue'
import type { CityInfo } from '../api'
import { cityArtwork, type CityArtwork } from '../cityArt'
const props = defineProps<{ cities: CityInfo[]; pending: string | null; error: string; loading: boolean }>()
const emit = defineEmits<{ choose: [key: string] }>()
const heading = ref<HTMLElement>()
const previewCache = new Map<string, CityArtwork | undefined>()
function previewArtwork(key: string) {
  if (!previewCache.has(key)) {
    const art = cityArtwork(key)
    const prefix = (svg: string) => svg.replace(/id="([^"]+)"/g, `id="preview-${key}-$1"`).replace(/url\(#([^)]+)\)/g, `url(#preview-${key}-$1)`)
    previewCache.set(key, art ? { ...art, left: prefix(art.left), right: prefix(art.right) } : undefined)
  }
  return previewCache.get(key)
}
const descriptions: Record<string, string> = { hangzhou: '湖山之间，慢一点。', nanjing: '山河旧事，巷陌烟火。' }
onMounted(() => nextTick(() => heading.value?.focus({ preventScroll: true })))
</script>

<template>
  <main class="destination-page" aria-labelledby="destination-title" :aria-busy="!!pending">
    <div class="destination-contours" aria-hidden="true"><i /><i /><i /></div>
    <div class="destination-content">
      <header class="destination-heading">
        <p class="destination-kicker"><span /> 每一程，都从心动开始</p>
        <h1 id="destination-title" ref="heading" tabindex="-1">下一程，<br class="destination-mobile-break" />想去哪里？</h1>
        <p class="destination-intro">先选一座城市，<br class="destination-mobile-break" />再把喜欢的日子慢慢安排。</p>
        <span class="destination-compass" aria-hidden="true"><PhCompass :size="84" weight="thin" /><i /></span>
      </header>
      <div class="destination-cities" :class="{ departing: pending }" aria-label="选择旅行城市">
        <button v-for="(city, index) in cities" :key="city.key" type="button" class="destination-card" :class="{ selected: pending === city.key, muted: pending && pending !== city.key }"
          :style="{ '--card-index': index }" :disabled="!!pending || loading" :aria-label="`选择${city.name}`" @click="emit('choose', city.key)">
          <div class="destination-card-heading"><span class="destination-number">{{ String(index + 1).padStart(2, '0') }}</span><span class="destination-card-caption">一城一份行记</span><PhArrowUpRight class="destination-card-arrow" :size="22" /></div>
          <div class="destination-card-copy"><h2>{{ city.name }}</h2><p>{{ descriptions[city.key] || '去走走，发现这座城。' }}</p></div>
          <div v-if="previewArtwork(city.key)" class="destination-art" aria-hidden="true"><div class="destination-art-left" v-html="previewArtwork(city.key)!.left" /><div class="destination-art-right" v-html="previewArtwork(city.key)!.right" /></div>
          <div v-else class="destination-art destination-art-empty" aria-hidden="true"><PhCompass :size="100" weight="thin" /></div>
          <span class="destination-card-bottom"><span>{{ pending === city.key ? `正在前往${city.name}…` : '从这里出发' }}</span><i aria-hidden="true">↗</i></span>
        </button>
      </div>
      <p v-if="error" class="destination-error" role="alert">{{ error }}，请重新选择目的地。</p>
      <footer class="destination-foot"><span class="destination-foot-line" /><PhSparkle :size="16" weight="light" /><span>把一座城，走成自己的故事。</span><span class="destination-foot-line" /></footer>
    </div>
  </main>
</template>
