<script setup lang="ts">
// 出现时自己画一笔的勾（替代原来直接出现的 PhCheck），动效样式在 motion.css
withDefaults(defineProps<{ size?: number; weight?: number }>(), { size: 14, weight: 2.2 })
</script>

<template>
  <svg class="draw-check" :width="size" :height="size" viewBox="0 0 16 16" aria-hidden="true" focusable="false"><path d="M3.2 8.6 6.6 11.9 12.9 4.7" pathLength="1" :stroke-width="weight" /></svg>
</template>
