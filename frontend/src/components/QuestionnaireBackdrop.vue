<script setup lang="ts">
import { computed } from 'vue'
import { cityArtwork } from '../cityArt'
// 各城市两幅静态主题插画；资产由 scripts/gen_city_art.py 生成。
const props = defineProps<{ city: string }>()
const scene = computed(() => cityArtwork(props.city))
</script>

<template>
  <div class="questionnaire-backdrop" :class="{ 'neutral-backdrop': !scene }" :data-city-art="city" aria-hidden="true">
    <div v-if="scene" class="qb-scene qb-west" :data-scene="scene.leftLabel"><img :src="scene.left" alt="" decoding="async" data-fade /></div>
    <div v-if="scene" class="qb-scene qb-south" :data-scene="scene.rightLabel"><img :src="scene.right" alt="" decoding="async" data-fade /></div>
  </div>
</template>
