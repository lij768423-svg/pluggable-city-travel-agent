<script setup lang="ts">
import { ref } from 'vue'
import { getPoiOptions, type PoiOption } from '../api'
import { vExpand } from '../motion'

const props = defineProps<{ planId: string; day: number; itemIndex: number }>()
const emit = defineEmits<{ choose: [option: PoiOption] }>()
const options = ref<PoiOption[] | null>(null)
const loading = ref(false)
const error = ref('')

// 展开时才读，大多数景点不会被换
async function toggled(open: boolean) {
  if (!open || options.value || loading.value) return
  loading.value = true
  error.value = ''
  try {
    options.value = (await getPoiOptions(props.planId, props.day, props.itemIndex)).options
  } catch (failure) {
    error.value = (failure as Error).message || '备选暂时读不出来，请收起后再试'
  } finally { loading.value = false }
}
const price = (option: PoiOption) => option.price > 0 ? `参考 ¥${option.price.toFixed(0)}` : option.price_verified ? '免费' : '票价待核'
const hours = (option: PoiOption) => option.min_hours === option.max_hours ? `${option.min_hours} 小时` : `${option.min_hours}–${option.max_hours} 小时`
</script>

<template>
  <details v-expand class="meal-alternatives poi-options" @click.stop @keydown.enter.stop @keydown.space.stop @toggle="toggled(($event.target as HTMLDetailsElement).open)">
    <summary>换一个景点 <small>· 从这份行程的候选里选</small></summary>
    <div class="expand-body">
    <p v-if="loading" class="field-help" role="status">正在读取备选…</p>
    <p v-else-if="error" class="field-help budget-error" role="alert">{{ error }}</p>
    <p v-else-if="options && !options.length" class="field-help">这一站暂时没有能换的候选。</p>
    <template v-else-if="options">
      <div v-for="option in options" :key="option.poi_id" class="meal-alternative">
        <strong>{{ option.name }}</strong><span>{{ price(option) }}</span>
        <p>{{ option.type }} · {{ option.area }}<template v-if="option.indoor"> · 室内</template> · 建议 {{ hours(option) }}</p>
        <p v-if="option.summary" class="poi-option-summary">{{ option.summary }}</p>
        <div class="stop-actions">
          <span>{{ option.same_area ? '同片区' : '其他片区' }} · 距这一站约 {{ option.distance_km }} 公里</span>
          <button type="button" :aria-label="`把这一站换成${option.name}`" @click.stop="emit('choose', option)">换成这个 <span aria-hidden="true">↗</span></button>
        </div>
      </div>
      <p class="field-help">换之前先重算当天路线与时间，确认后才更新，其他日期不变，可撤销。</p>
    </template>
    </div>
  </details>
</template>
