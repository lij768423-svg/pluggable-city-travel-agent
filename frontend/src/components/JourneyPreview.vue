<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { cityArtwork } from '../cityArt'
const props = defineProps<{ step: number; city: { key: string; name: string } }>()
const scene = computed(() => cityArtwork(props.city.key))
const photoIndex = ref(props.step)
watch(() => props.step, value => { photoIndex.value = value })
const photos = [
  { file: 'west-lake.jpg', place: '西湖 · 暮色', line: ['不赶路，', '也能遇见好风景。'], author: 'Peter Dowley', license: 'CC BY 2.0', source: 'https://commons.wikimedia.org/wiki/File:Sunset_at_West_Lake_(Xi_Hu),_Hangzhou_(2790877585).jpg' },
  { file: 'longjing.jpg', place: '龙井 · 茶田', line: ['山间走走，', '给一杯茶留点时间。'], author: 'Wishva de Silva', license: 'CC BY-SA 3.0', source: 'https://commons.wikimedia.org/wiki/File:Tea_plantations_in_Longjing,_Hangzhou.jpg' },
  { file: 'tangqi.jpg', place: '塘栖 · 水乡', line: ['沿着小巷，', '找一找人间烟火。'], author: 'Huandy618', license: 'CC BY-SA 3.0', source: 'https://commons.wikimedia.org/wiki/File:Ancient_Town_of_Tangqi_21.jpg' },
  { file: 'lake-evening.jpg', place: '西湖 · 日落', line: ['把喜欢的，', '慢慢走成一段旅程。'], author: 'Morio', license: 'CC BY-SA 4.0', source: 'https://commons.wikimedia.org/wiki/File:West_Lake_(Hangzhou)_sunset_2016_January.jpg' },
]
const activePhoto = computed(() => photos[photoIndex.value] || photos[0]!)
const failedPhotos = ref<string[]>([])
function photoFailed(file: string) { if (!failedPhotos.value.includes(file)) failedPhotos.value.push(file) }
</script>

<template>
  <aside v-if="city.key !== 'hangzhou'" class="journey-preview city-art-preview" :class="{ 'neutral-backdrop': !scene }" :aria-label="`${city.name}主题插画`"><div v-if="scene" class="preview-illustration"><img :src="scene.right" alt="" decoding="async" /></div><div class="journey-photo-top"><span>{{ scene?.rightLabel || city.name }}</span><span>{{ city.name }}印象 · 插画</span></div><div class="journey-photo-caption"><p>把喜欢的，<br />慢慢走成一段旅程。</p><span>先选喜欢的，再安排去哪里。</span></div></aside>
  <aside v-else class="journey-preview" aria-label="杭州实景">
    <Transition name="journey-photo"><img v-if="!failedPhotos.includes(activePhoto.file)" :key="activePhoto.file" class="journey-photo" :src="`/questionnaire/${activePhoto.file}`" :alt="activePhoto.place" @error="photoFailed(activePhoto.file)" /></Transition>
    <div class="journey-photo-shade" />
    <div class="journey-photo-top"><span>{{ activePhoto.place }}</span><span>杭州印象</span></div>
    <div class="journey-photo-caption"><Transition name="photo-copy" mode="out-in"><p :key="activePhoto.file">{{ activePhoto.line[0] }}<br />{{ activePhoto.line[1] }}</p></Transition><span>先选喜欢的，再安排去哪里。</span></div>
    <div class="journey-photo-controls"><div class="journey-photo-dots" aria-label="切换杭州实景"><button v-for="(photo, index) in photos" :key="photo.file" type="button" :aria-label="`查看${photo.place}照片`" :aria-pressed="photoIndex === index" @click="photoIndex = index"><span /></button></div><a :href="activePhoto.source" target="_blank" rel="noopener noreferrer" :aria-label="`${activePhoto.author} 摄影，${activePhoto.license}，查看来源`">{{ activePhoto.author }} · {{ activePhoto.license }} ↗</a></div>
  </aside>
</template>

<style scoped>
.preview-illustration { position: absolute; inset: 0; }
.preview-illustration img { display: block; width: 100%; height: 100%; object-fit: cover; }
</style>
