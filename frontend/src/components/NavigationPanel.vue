<script setup lang="ts">
import type { DayPlan, NavigationResponse } from '../api'
defineProps<{ day: DayPlan; home: string; navigation: NavigationResponse | null; loading: boolean; error: string }>()
const emit = defineEmits<{ retry: [] }>()
</script>

<template>
  <div class="navigation-panel">
    <p class="navigation-status" role="status">{{ loading ? '正在查询当天导航路径…' : error || (navigation ? `已取得 ${navigation.legs.filter(leg => ['ready', 'same_place'].includes(leg.status)).length}/${navigation.legs.length} 段导航路径` : '等待导航路径') }}</p>
    <button v-if="error || navigation?.legs.some(leg => ['partial', 'unavailable'].includes(leg.status))" class="navigation-retry" @click="emit('retry')">重新查询路线</button>
    <details v-if="navigation" class="navigation-instructions">
      <summary>逐段导航 <span>步行 / 乘车</span></summary>
      <div class="navigation-scroll">
        <details v-for="(leg, index) in navigation.legs" :key="index" class="navigation-leg">
          <summary>{{ index === 0 ? home : day.items[index - 1]?.name }} → {{ day.items[index]?.name || home }}<small>{{ leg.minutes != null ? `约 ${leg.minutes} 分钟` : '暂无路径' }}</small></summary>
          <p v-if="leg.message">{{ leg.message }}</p>
          <ol><li v-for="(segment, step) in leg.segments" :key="step"><strong>{{ segment.mode }}</strong> {{ segment.instruction }}<small v-if="segment.distance_m">{{ segment.distance_m }} 米</small></li></ol>
        </details>
        <p class="field-help">公交查询携带各段日期与出发时刻；驾车和步行显示查询时的参考路径。未取得的区段留空，导航查询与规划时长可能有差异，公交班次以出发时为准。</p>
      </div>
    </details>
  </div>
</template>
