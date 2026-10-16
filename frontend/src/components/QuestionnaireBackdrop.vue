<script setup lang="ts">
// 问卷背景：两幅杭州插画，左景北望宝石山（保俶塔、断桥、荷），右景雷峰夕照（雷峰塔、画舫、三潭印月）。
// 插画由 scripts/gen_scene.py 生成（内容固定、随仓库管理），内联进页面，动画样式在 questionnaire.css，
// 这样“减少动态效果”的系统设置能统一停掉画中的动画。
import westScene from '../assets/backdrop/hangzhou-west.svg?raw'
import southScene from '../assets/backdrop/hangzhou-south.svg?raw'
</script>

<template>
  <div class="questionnaire-backdrop" aria-hidden="true">
    <!-- eslint-disable-next-line vue/no-v-html -->
    <div class="qb-scene qb-west" v-html="westScene" />
    <!-- eslint-disable-next-line vue/no-v-html -->
    <div class="qb-scene qb-south" v-html="southScene" />
  </div>
</template>
