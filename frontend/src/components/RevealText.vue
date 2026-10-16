<script setup lang="ts">
defineProps<{ text: string }>()
</script>

<template>
  <span class="reveal-text"><span class="sr-only">{{ text }}</span><span v-for="(character, index) in Array.from(text)" :key="`${text}-${index}`" aria-hidden="true" class="reveal-character" :style="{ '--character-index': index }">{{ character === ' ' ? '\u00a0' : character }}</span></span>
</template>

<style scoped>
.reveal-text { display: inline-block; }
.reveal-character { display: inline-block; }
@media (prefers-reduced-motion: no-preference) {
  .reveal-character { animation: character-enter .48s cubic-bezier(.16, 1, .3, 1) both; animation-delay: calc(var(--character-index) * 32ms); }
}
@keyframes character-enter {
  from { opacity: 0; transform: translateY(.38em) rotateX(-20deg); }
  to { opacity: 1; transform: translateY(0) rotateX(0); }
}
</style>
