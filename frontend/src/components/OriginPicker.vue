<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, watch } from 'vue'
import { PhMagnifyingGlass, PhMapPin, PhX } from '@phosphor-icons/vue'
import { searchPlaces, type Hotel, type OriginPlace } from '../api'

// 起点：默认用第一个常用起点，以卡片显示；点“更换”后搜索酒店、地铁口或任意地点，空搜索时列出常用起点
const props = defineProps<{ presets: Hotel[]; modelValue: Hotel | null; disabled: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: Hotel | null] }>()
const editing = ref(false)
const query = ref('')
const places = ref<OriginPlace[]>([])
const searching = ref(false)
const searched = ref(false)
const error = ref('')
const input = ref<HTMLInputElement | null>(null)
let controller: AbortController | undefined
let requestId = 0

watch(() => props.presets, presets => {
  if (!props.modelValue && presets.length) emit('update:modelValue', presets[0]!)
}, { immediate: true })

function cancelSearch() {
  requestId++
  controller?.abort()
  searching.value = false
}
watch(query, () => { cancelSearch(); places.value = []; searched.value = false; error.value = '' })

async function startEdit() {
  if (props.disabled) return
  editing.value = true
  await nextTick()
  input.value?.focus()
}
function stopEdit() {
  cancelSearch()
  editing.value = false
  query.value = ''
}
async function search() {
  if (query.value.trim().length < 2 || props.disabled) return
  cancelSearch()
  const currentId = requestId
  controller = new AbortController()
  searching.value = true
  error.value = ''
  try {
    const response = await searchPlaces(query.value.trim(), controller.signal)
    if (currentId !== requestId) return
    places.value = response.places
    searched.value = true
  } catch (failure) {
    if (currentId !== requestId) return
    error.value = failure instanceof Error ? failure.message : '搜索失败，请稍后重试。'
  } finally {
    if (currentId === requestId) searching.value = false
  }
}
function choose(place: Hotel) {
  emit('update:modelValue', place)
  stopEdit()
}
const detail = (place: Hotel) => [place.area, (place as OriginPlace).address].filter(Boolean).join(' · ')
const same = (place: Hotel) => !!props.modelValue && place.name === props.modelValue.name && place.lat === props.modelValue.lat
onBeforeUnmount(cancelSearch)
</script>

<template>
  <div class="origin-picker" @keydown.esc="editing && modelValue && stopEdit()">
    <span id="origin-label" class="field-label">每天的起点与终点</span>
    <div v-if="!editing" class="origin-current" :class="{ empty: !modelValue }">
      <span class="origin-pin" aria-hidden="true"><PhMapPin :size="18" weight="fill" /></span>
      <span class="origin-text">
        <strong>{{ modelValue?.name ?? '还没有选择起点' }}</strong>
        <small>{{ modelValue ? (detail(modelValue) || '每天从这里出发，晚上回到这里') : '搜索你的酒店、地铁口或任意地点' }}</small>
      </span>
      <button type="button" class="origin-change" :disabled="disabled" aria-describedby="origin-label" @click="startEdit">{{ modelValue ? '更换' : '选择' }}</button>
    </div>
    <div v-else class="origin-edit">
      <div class="origin-search" role="search">
        <PhMagnifyingGlass :size="18" aria-hidden="true" />
        <input ref="input" v-model="query" :disabled="disabled" maxlength="80" placeholder="搜索酒店、地铁口或地点" autocomplete="off" aria-labelledby="origin-label" @keydown.enter.prevent="search" />
        <button type="button" class="origin-go" @click="search" :disabled="disabled || searching || query.trim().length < 2">{{ searching ? '搜索中' : '搜索' }}</button>
        <button v-if="modelValue" type="button" class="origin-cancel" aria-label="取消更换" @click="stopEdit"><PhX :size="16" /></button>
      </div>
      <p v-if="error" class="origin-error" role="alert">{{ error }}</p>
      <p v-else-if="searched && !places.length" class="origin-hint" role="status">没有找到匹配地点，试试更完整的名称或附近地铁站。</p>
      <ul v-if="places.length || !searched" class="origin-results" :aria-label="searched ? '搜索结果' : '常用起点'">
        <li v-if="!searched" class="origin-group" aria-hidden="true">常用起点</li>
        <li v-for="place in (searched ? places : presets)" :key="(place as OriginPlace).id || `${place.name}-${place.lon}`">
          <button type="button" :disabled="disabled" :class="{ selected: same(place) }" :aria-pressed="same(place)" @click="choose(place)">
            <PhMapPin :size="16" aria-hidden="true" /><span><strong>{{ place.name }}</strong><small v-if="detail(place)">{{ detail(place) }}</small></span>
          </button>
        </li>
      </ul>
    </div>
  </div>
</template>

<style scoped>
.origin-current { display: flex; align-items: center; gap: 12px; min-height: 64px; padding: 10px 10px 10px 12px; border: 1px solid var(--line); border-radius: 12px; background: #fff; }
.origin-pin { flex-shrink: 0; width: 38px; height: 38px; display: grid; place-items: center; border-radius: 50%; background: var(--accent-soft); color: var(--accent); }
.origin-current.empty .origin-pin { background: #f1f3f7; color: var(--faint); }
.origin-text { flex: 1; min-width: 0; display: grid; gap: 2px; }
.origin-text strong { font-size: 15px; font-weight: 600; color: var(--text); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.origin-text small { font-size: 12px; color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.origin-change { flex-shrink: 0; min-height: 36px; padding: 0 16px; border: 1px solid #d5def0; border-radius: 999px; color: var(--accent); font-size: 13px; transition: background .2s, border-color .2s; }
.origin-change:hover:not(:disabled) { background: var(--accent-soft); border-color: #b9c9ee; }
.origin-edit { position: relative; display: grid; gap: 8px; animation: origin-in .3s cubic-bezier(.16, 1, .3, 1) both; }
.origin-search { display: flex; align-items: center; gap: 8px; min-height: 52px; padding: 0 6px 0 14px; border: 1px solid var(--accent); border-radius: 12px; background: #fff; box-shadow: 0 0 0 3px #2f5bd31a; color: var(--accent); }
.origin-search input { flex: 1; min-width: 0; height: 46px; border: 0 !important; outline: none; background: transparent !important; box-shadow: none !important; padding: 0 !important; min-height: 0 !important; font-size: 14px; color: var(--text); }
.origin-go { flex-shrink: 0; min-height: 36px; padding: 0 16px; border-radius: 9px; background: var(--accent); color: #fff; font-size: 13px; }
.origin-go:disabled { background: #c7d2ea; opacity: 1; }
.origin-cancel { flex-shrink: 0; width: 36px; height: 36px; display: grid; place-items: center; border-radius: 9px; color: var(--muted); }
.origin-cancel:hover { background: #f1f4fa; color: var(--text); }
.origin-results { position: absolute; z-index: 20; top: calc(100% + 6px); left: 0; right: 0; list-style: none; margin: 0; padding: 6px; max-height: 236px; box-shadow: 0 18px 40px -18px #1f3a7a59; overflow-y: auto; overscroll-behavior: contain; border: 1px solid var(--line); border-radius: 12px; background: #fff; }
.origin-group { padding: 6px 10px 4px; font-size: 11px; letter-spacing: .08em; color: var(--faint); }
.origin-results button { width: 100%; display: flex; align-items: flex-start; gap: 10px; padding: 9px 10px; border-radius: 8px; text-align: left; color: var(--muted); }
.origin-results button:hover, .origin-results button.selected { background: var(--accent-soft); color: var(--accent); }
.origin-results button svg { flex-shrink: 0; margin-top: 2px; }
.origin-results span { min-width: 0; display: grid; gap: 1px; }
.origin-results strong { font-size: 13px; font-weight: 600; color: var(--text); }
.origin-results small { font-size: 12px; color: var(--muted); }
.origin-hint, .origin-error { margin: 0; font-size: 12px; line-height: 1.6; color: var(--muted); }
.origin-error { color: #a33535; }
@keyframes origin-in { from { opacity: 0; transform: translateY(-4px); } to { opacity: 1; transform: none; } }
@media (prefers-reduced-motion: reduce) { .origin-edit { animation: none; } }
</style>
