<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { PhArrowLeft, PhArrowUpRight, PhPlus, PhTrash, PhClockCounterClockwise } from '@phosphor-icons/vue'
import { AlertDialogRoot, AlertDialogPortal, AlertDialogOverlay, AlertDialogContent, AlertDialogTitle, AlertDialogDescription, AlertDialogCancel } from 'reka-ui'
import { getHistory, deleteHistory, ApiError, type HistoryEntry } from '../api'
defineProps<{ username: string }>()
const emit = defineEmits<{ back: []; create: []; open: [id: string]; replan: [id: string]; expired: [] }>()
const entries = ref<HistoryEntry[]>([])
const busy = ref(false)
const error = ref('')
const more = ref(false)
const deleting = ref<HistoryEntry | null>(null)
const deletingBusy = ref(false)
let active = true
onBeforeUnmount(() => { active = false })
const date = (stamp: number) => new Date(stamp * 1000).toLocaleString('zh-CN', { month: 'long', day: 'numeric', hour: '2-digit', minute: '2-digit' })
async function load(append = false) {
  if (busy.value) return
  busy.value = true; error.value = ''
  try { const result = await getHistory(append ? entries.value.length : 0); entries.value = append ? [...entries.value, ...result.items] : result.items; more.value = result.has_more }
  catch (failure) { if (active && failure instanceof ApiError && failure.status === 401) emit('expired'); else error.value = (failure as Error).message }
  finally { busy.value = false }
}
async function remove() {
  if (!deleting.value || deletingBusy.value) return
  deletingBusy.value = true
  try { await deleteHistory(deleting.value.id); deleting.value = null; await load() }
  catch (failure) { deleting.value = null; if (active && failure instanceof ApiError && failure.status === 401) emit('expired'); else error.value = (failure as Error).message }
  finally { deletingBusy.value = false }
}
onMounted(() => load())
</script>
<template>
  <main class="history-page">
    <button class="ghost-btn" @click="emit('back')"><PhArrowLeft :size="17" />返回</button>
    <header class="history-heading"><div><h1>历史行程</h1><p>{{ username }} 的行程记录 · 按最近更新排序</p></div><button class="journey-primary" @click="emit('create')"><PhPlus :size="18" />新建行程</button></header>
    <p v-if="error" class="notice bad" role="alert">{{ error }}<button @click="load()">重试</button></p>
    <div v-if="!entries.length && busy" class="history-empty" role="status">正在打开你的旅行手记…</div>
    <div v-else-if="!entries.length && !error" class="history-empty"><PhClockCounterClockwise :size="42" weight="light" /><h2>还没有出发记录</h2><p>完成第一次规划后，行程会自动出现在这里。</p><button class="journey-secondary" @click="emit('create')">开始规划</button></div>
    <ol class="history-list"><li v-for="(entry, index) in entries" :key="entry.id"><span class="history-number">{{ String(index + 1).padStart(2, '0') }}</span><div><time>{{ date(entry.updated) }} 更新</time><h2><button @click="emit('open', entry.id)">{{ entry.title }}</button></h2><p>保存于 {{ date(entry.created) }}</p></div><div class="history-actions"><button class="journey-secondary" @click="emit('open', entry.id)">查看行程<PhArrowUpRight :size="16" /></button><button class="ghost-btn" @click="emit('replan', entry.id)">重新规划</button><button class="history-delete" :aria-label="`删除 ${entry.title}`" @click="deleting = entry"><PhTrash :size="18" /></button></div></li></ol>
    <button v-if="more" class="journey-secondary history-more" :disabled="busy" @click="load(true)">{{ busy ? '加载中…' : '更多记录' }}</button>
    <p v-if="entries.length" class="history-footnote">记录保留当时的安排和费用。重新规划后会核对当前信息，价格和开放时间请以出行时为准。</p>
    <AlertDialogRoot :open="!!deleting" @update:open="value => !value && !deletingBusy && (deleting = null)"><AlertDialogPortal><AlertDialogOverlay class="confirm-overlay" /><AlertDialogContent class="confirm-dialog"><AlertDialogTitle>删除这份行程？</AlertDialogTitle><AlertDialogDescription class="confirm-body">「{{ deleting?.title }}」将从你的账号中移除，删除后不能恢复。</AlertDialogDescription><div class="confirm-actions"><AlertDialogCancel :disabled="deletingBusy">保留</AlertDialogCancel><button class="journey-primary" :disabled="deletingBusy" @click="remove">{{ deletingBusy ? '删除中…' : '确认删除' }}</button></div></AlertDialogContent></AlertDialogPortal></AlertDialogRoot>
  </main>
</template>
