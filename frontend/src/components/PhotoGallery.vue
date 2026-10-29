<script setup lang="ts">
// 地点图片（最多 5 张）：横向滑动 + 点击放大。来源与许可见后端 app/media.py、docs/图片资源规范.md。
import { computed, onBeforeUnmount, ref, watch } from 'vue'
import { DialogRoot, DialogPortal, DialogContent, DialogTitle } from 'reka-ui'
import { PhCaretLeft, PhCaretRight, PhImageBroken, PhX } from '@phosphor-icons/vue'
import type { PlaceMedia, PlacePhoto } from '../api'

const props = defineProps<{ media: PlaceMedia | null; loading: boolean; name: string }>()
const broken = ref(new Set<string>())
const strip = ref<HTMLElement>()
const current = ref(0)
const viewer = ref<number | null>(null)

const photos = computed<PlacePhoto[]>(() => (props.media?.photos || []).filter(photo => !broken.value.has(photo.fingerprint)))
const hasAmap = computed(() => photos.value.some(photo => photo.source !== 'manual'))
watch(() => props.media, () => { broken.value = new Set(); current.value = 0; viewer.value = null; strip.value?.scrollTo({ left: 0 }) })
watch(photos, available => {
  current.value = Math.max(0, Math.min(current.value, available.length - 1))
  if (viewer.value != null) viewer.value = available.length ? Math.min(viewer.value, available.length - 1) : null
})

function onScroll() {
  const el = strip.value
  if (!el || !el.children.length) return
  const slide = (el.children[0] as HTMLElement).offsetWidth + 8
  current.value = Math.min(photos.value.length - 1, Math.round(el.scrollLeft / slide))
}
function go(step: number) {
  const el = strip.value
  if (!el || !el.children.length) return
  const target = Math.max(0, Math.min(photos.value.length - 1, current.value + step))
  ;(el.children[target] as HTMLElement).scrollIntoView({ behavior: 'smooth', block: 'nearest', inline: 'start' })
}
function open(index: number) {
  viewer.value = index
}
function step(delta: number) {
  if (viewer.value == null) return
  viewer.value = (viewer.value + delta + photos.value.length) % photos.value.length
}
function onKey(event: KeyboardEvent) {
  if (viewer.value == null) return
  if (event.key === 'ArrowRight') { step(1); event.preventDefault() }
  else if (event.key === 'ArrowLeft') { step(-1); event.preventDefault() }
}
onBeforeUnmount(() => { viewer.value = null })
</script>

<template>
  <section class="gallery" aria-label="地点图片">
    <div v-if="loading" class="gallery-strip" aria-hidden="true"><div v-for="n in 3" :key="n" class="gallery-slide skeleton" /></div>
    <template v-else-if="photos.length">
      <div ref="strip" class="gallery-strip" @scroll.passive="onScroll">
        <button v-for="(photo, index) in photos" :key="photo.fingerprint" type="button" class="gallery-slide"
          :aria-label="`放大查看第 ${index + 1} 张图片${photo.caption ? '：' + photo.caption : ''}`" @click="open(index)">
          <img :src="photo.thumb_url || photo.url" :alt="photo.caption || `${name} 图片 ${index + 1}`" :loading="index === 0 ? 'eager' : 'lazy'"
            :fetchpriority="index === 0 ? 'high' : 'auto'" decoding="async" referrerpolicy="no-referrer" data-fade
            @error="broken = new Set([...broken, photo.fingerprint])">
          <span v-if="photo.caption || photo.source === 'amap_related'" class="gallery-caption">{{ photo.caption || photo.source_label.replace('高德地图 · ', '') }}</span>
        </button>
      </div>
      <div class="gallery-bar">
        <span>{{ current + 1 }} / {{ photos.length }}<template v-if="photos.length < 3"> · 公开图片较少</template></span>
        <span class="gallery-nav">
          <button type="button" aria-label="上一张" :disabled="current === 0" @click="go(-1)"><PhCaretLeft :size="14" weight="bold" /></button>
          <button type="button" aria-label="下一张" :disabled="current >= photos.length - 1" @click="go(1)"><PhCaretRight :size="14" weight="bold" /></button>
        </span>
      </div>
      <p v-if="hasAmap" class="gallery-credit">图片来自高德地图（商户或用户上传），仅供参考，以现场为准</p>
    </template>
    <p v-else class="gallery-empty"><PhImageBroken :size="16" />{{ media?.status === 'unavailable' ? '图片暂时加载失败' : '暂无可用的公开图片' }}</p>

    <DialogRoot :open="viewer != null" @update:open="value => { if (!value) viewer = null }">
      <DialogPortal>
        <DialogContent v-if="viewer != null && photos[viewer]" class="photo-viewer" :aria-describedby="undefined"
          @keydown="onKey" @click.self="viewer = null">
      <DialogTitle class="sr-only">{{ name }} 图片</DialogTitle>
      <button type="button" class="viewer-close" aria-label="关闭大图" @click="viewer = null"><PhX :size="20" /></button>
      <button v-if="photos.length > 1" type="button" class="viewer-arrow left" aria-label="上一张" @click="step(-1)"><PhCaretLeft :size="22" weight="bold" /></button>
      <figure>
        <img :src="photos[viewer]!.url" :alt="photos[viewer]!.caption || name" referrerpolicy="no-referrer"
          @error="broken = new Set([...broken, photos[viewer]!.fingerprint])">
        <figcaption>
          <span>{{ viewer + 1 }} / {{ photos.length }}{{ photos[viewer]!.caption ? ' · ' + photos[viewer]!.caption : '' }}</span>
          <small>{{ photos[viewer]!.source_label }}<template v-if="photos[viewer]!.attribution"> · {{ photos[viewer]!.attribution }}</template>
            <a v-if="photos[viewer]!.source_url" :href="photos[viewer]!.source_url" target="_blank" rel="noopener noreferrer">来源</a></small>
        </figcaption>
      </figure>
      <button v-if="photos.length > 1" type="button" class="viewer-arrow right" aria-label="下一张" @click="step(1)"><PhCaretRight :size="22" weight="bold" /></button>
        </DialogContent>
      </DialogPortal>
    </DialogRoot>
  </section>
</template>
