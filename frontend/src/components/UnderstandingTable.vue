<script setup lang="ts">
// “我是这么理解的”：上面是规划前就能看出的问题和一键修改，下面逐项列出理解与依据；除城市外每项都能就地改（2026-10-28 用户：能改的太少）。
// 改动先攒着，用户改完点“提交修改”才一次交出去（2026-10-28 用户：不要改一个就调整）。
// 怎么处理由外层决定：问卷模式直接改字段，跟 AI 说模式把这几处修改合成一句话（列表类整份说“改成”，免得被理解成追加）。
import { computed, ref, watch } from 'vue'
import { PhInfo, PhWarningCircle, PhX } from '@phosphor-icons/vue'
import type { Hotel, Meta, Understanding, UnderstandingField, UnderstandingIssue } from '../api'
import OriginPicker from './OriginPicker.vue'

const props = withDefaults(defineProps<{ understanding: Understanding; meta: Meta | null; busy: boolean; mode?: 'agent' | 'form' }>(), { mode: 'form' })
const emit = defineEmits<{ apply: [patch: Record<string, unknown>, phrases: string[]]; pending: [phrases: string[], patch: Record<string, unknown>] }>()

const SOURCE = { said: '你说的', form: '你填的', inferred: '推断', default: '默认' } as const
const TRANSPORT: Record<string, string> = { taxi: '打车', transit: '公交地铁' }
const LISTS = { must_visit: '必去', avoid: '不去' } as const
const LABELS: Record<string, string> = { transport: '出行', companions: '同行', familiarity: '对这座城市', pace: '节奏', spice: '口味' }
type ListKey = keyof typeof LISTS

// 没说必去、不去时服务端不列这两行；这里补成“没有”，好让用户能加（顺序固定：必去在前）
const rows = computed<(UnderstandingField | { key: ListKey; label: string; display: string; source: null; note: string })[]>(() => [
  ...props.understanding.fields.filter(field => !(field.key in LISTS)),
  ...(Object.keys(LISTS) as ListKey[]).map(key => props.understanding.fields.find(field => field.key === key)
    ?? { key, label: LISTS[key], display: '没有', source: null, note: '' }),
])

// 还没提交的修改；表里显示的都是“改过的值，没改就用理解出来的值”
const draft = ref<Record<string, unknown>>({})
const changed = (key: string) => key in draft.value
const current = (key: string) => (changed(key) ? draft.value[key] : props.understanding.profile[key])
const value = (key: string) => current(key) as string | number | null | undefined
const list = (key: string) => (current(key) as string[] | undefined) ?? []
const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)

function set(key: string, next: unknown) {
  const rest = { ...draft.value }
  delete rest[key]
  // 改回原样就不算改
  draft.value = same(next, props.understanding.profile[key]) ? rest : { ...rest, [key]: next }
}

function phrase(key: string, next: unknown): string {
  if (key === 'days') return `玩 ${next} 天`
  if (key === 'budget') return `预算改成人均共 ${next} 元`
  if (key === 'start_date') return next ? `日期改成 ${next}` : '日期还没定'
  if (key === 'hotel') return next ? `从${(next as Hotel).name}出发` : '起点不定，不用回起点'
  const names = next as string[]
  if (key === 'interests') return names.length ? `想玩的改成：${names.join('、')}` : '想玩的不限，综合推荐'
  if (key === 'must_visit') return names.length ? `一定要去的改成：${names.join('、')}` : '没有一定要去的地方了'
  if (key === 'avoid') return names.length ? `不想去的改成：${names.join('、')}` : '没有不想去的地方了'
  const labels: Record<string, Record<string, string> | undefined> = { transport: TRANSPORT, companions: props.meta?.companions,
    familiarity: props.meta?.familiarity_levels, pace: props.meta?.paces, spice: props.meta?.spice_levels }
  return `${LABELS[key] ?? key}改成${labels[key]?.[next as string] ?? next}`
}
// 按表格顺序出句子，同一份修改每次说法一样
const phrases = computed(() => rows.value.filter(row => changed(row.key)).map(row => phrase(row.key, draft.value[row.key])))
watch(phrases, value => emit('pending', value, { ...draft.value }))

const editing = ref<string | null>(null)
const picked = ref<string[]>([])
const adding = ref('')
// 重新理解后旧的草稿作废
watch(() => props.understanding, () => { draft.value = {}; editing.value = null })

function submit() {
  if (!phrases.value.length || props.busy) return
  emit('apply', { ...draft.value }, phrases.value)
}
function discard() {
  draft.value = {}
  editing.value = null
}

// 预算是整趟的人均总额。没说预算时它随天数估（每天同样多），所以只改了天数时先按新天数算给用户看
const budgetShown = computed(() => {
  const profile = props.understanding.profile as { budget: number; days: number }
  const guessed = props.understanding.fields.find(field => field.key === 'budget')?.source === 'default'
  if (changed('budget') || !guessed || !changed('days')) return Number(value('budget'))
  return Math.round(profile.budget / profile.days * Number(value('days')))
})
const perDay = computed(() => Math.round(budgetShown.value / Number(value('days') || 1)))

const originKnown = computed(() => props.understanding.profile.origin_known !== false)
// 当前选中的起点；null 表示“不定”（没定起点时默认点不算起点）
const hotelPicked = computed<Hotel | null>(() => {
  if (changed('hotel')) return draft.value.hotel && (originKnown.value || !same(draft.value.hotel, props.understanding.profile.hotel)) ? draft.value.hotel as Hotel : null
  return originKnown.value ? props.understanding.profile.hotel as Hotel : null
})
const hotelPresets = computed(() => (props.meta?.city === props.understanding.profile.city ? props.meta?.hotels : []) ?? [])
// 起点组件里选了地点（常用起点或高德搜到的）或选回“不定”
function pickOrigin(place: Hotel | null) {
  if (!place) set('hotel', originKnown.value ? null : props.understanding.profile.hotel)
  else set('hotel', { name: place.name, lat: place.lat, lon: place.lon })
}

function change(key: string, raw: string) {
  if (key === 'days' || key === 'budget') {
    const number = Number(raw)
    if (!raw || !Number.isFinite(number) || number <= 0 || (key === 'budget' && number > 100000)) return
    set(key, number)
  } else if (key === 'start_date') {
    set(key, raw || null)
  } else {
    set(key, raw)
  }
}

// 一键修改也只是先放进草稿，和别的修改一起提交
const fixed = (fix: UnderstandingIssue['fixes'][number]) => Object.entries(fix.patch).every(([key, next]) => changed(key) && same(draft.value[key], next))
function applyFix(fix: UnderstandingIssue['fixes'][number]) {
  for (const [key, next] of Object.entries(fix.patch)) set(key, next)
}

function open(key: string) {
  editing.value = key
  picked.value = [...list(key)]
  adding.value = ''
}
function saveInterests() {
  set('interests', (props.meta?.interests ?? []).filter(item => picked.value.includes(item)))
  editing.value = null
}
function togglePick(item: string) {
  picked.value = picked.value.includes(item) ? picked.value.filter(other => other !== item) : [...picked.value, item]
}
function addPlace(key: ListKey) {
  const name = adding.value.trim()
  if (name && !list(key).includes(name)) set(key, [...list(key), name])
  adding.value = ''
}
</script>

<template>
  <div class="understanding" :aria-busy="busy">
    <ul v-if="understanding.issues.length" class="confirm-issues">
      <li v-for="issue in understanding.issues" :key="`${issue.code}-${issue.message}`" class="notice" :class="{ bad: issue.level === 'blocker', warn: issue.level === 'warning' }" :role="issue.level === 'blocker' ? 'alert' : undefined">
        <PhWarningCircle v-if="issue.level !== 'info'" :size="16" /><PhInfo v-else :size="16" />
        <div><p>{{ issue.message }}</p>
          <div v-if="issue.fixes.length" class="confirm-fixes"><button v-for="fix in issue.fixes" :key="fix.label" type="button" :class="{ on: fixed(fix) }" :aria-pressed="fixed(fix)" :disabled="busy" @click="applyFix(fix)">{{ fix.label }}</button></div>
        </div>
      </li>
    </ul>
    <table class="confirm-table">
      <thead><tr><th scope="col">项目</th><th scope="col">我的理解</th><th scope="col">依据</th></tr></thead>
      <tbody>
        <tr v-for="field in rows" :key="field.key" :class="[field.source ? `source-${field.source}` : '', { changed: changed(field.key) }]">
          <th scope="row">{{ field.label }}</th>
          <td>
            <select v-if="field.key === 'days'" :value="value('days')" :aria-label="field.label" :disabled="busy" @change="change('days', ($event.target as HTMLSelectElement).value)"><option v-for="n in 5" :key="n" :value="n">{{ n }} 天</option></select>
            <template v-else-if="field.key === 'start_date'"><input type="date" :value="value('start_date') || ''" :aria-label="field.label" :disabled="busy" @change="change('start_date', ($event.target as HTMLInputElement).value)" /><small v-if="!value('start_date')">{{ field.display }}</small></template>
            <template v-else-if="field.key === 'budget'"><label class="confirm-budget">人均共 <input type="number" min="1" max="100000" :value="budgetShown" aria-label="人均总预算" :disabled="busy" @change="change('budget', ($event.target as HTMLInputElement).value)" /> 元</label><small>{{ value('days') }} 天，平均每天 {{ perDay }} 元；含门票、餐饮、市内交通</small></template>
            <select v-else-if="field.key === 'transport'" :value="value('transport')" :aria-label="field.label" :disabled="busy" @change="change('transport', ($event.target as HTMLSelectElement).value)"><option v-for="(label, key) in TRANSPORT" :key="key" :value="key">{{ label }}</option></select>
            <select v-else-if="field.key === 'companions' && meta" :value="value('companions')" :aria-label="field.label" :disabled="busy" @change="change('companions', ($event.target as HTMLSelectElement).value)"><option v-for="(label, key) in meta.companions" :key="key" :value="key">{{ label }}</option></select>
            <select v-else-if="field.key === 'familiarity' && meta?.familiarity_levels" :value="value('familiarity')" :aria-label="field.label" :disabled="busy" @change="change('familiarity', ($event.target as HTMLSelectElement).value)"><option v-for="(label, key) in meta.familiarity_levels" :key="key" :value="key">{{ label }}</option></select>
            <select v-else-if="field.key === 'pace' && meta" :value="value('pace')" :aria-label="field.label" :disabled="busy" @change="change('pace', ($event.target as HTMLSelectElement).value)"><option v-for="(label, key) in meta.paces" :key="key" :value="key">{{ label }}</option></select>
            <select v-else-if="field.key === 'spice' && meta" :value="value('spice')" :aria-label="field.label" :disabled="busy" @change="change('spice', ($event.target as HTMLSelectElement).value)"><option v-for="(label, key) in meta.spice_levels" :key="key" :value="key">{{ label }}</option></select>
            <template v-else-if="field.key === 'hotel'">
              <OriginPicker compact :city="String(understanding.profile.city)" :presets="hotelPresets" :model-value="hotelPicked" :disabled="busy" @update:model-value="pickOrigin" />
            </template>
            <template v-else-if="field.key === 'interests' && meta">
              <div v-if="editing === 'interests'" class="confirm-editor">
                <div class="confirm-chips" role="group" aria-label="想玩"><button v-for="item in meta.interests" :key="item" type="button" class="confirm-chip" :class="{ on: picked.includes(item) }" :aria-pressed="picked.includes(item)" :disabled="busy" @click="togglePick(item)">{{ item }}</button></div>
                <div class="confirm-editor-actions"><button type="button" class="confirm-link" :disabled="busy" @click="editing = null">取消</button><button type="button" class="confirm-save" :disabled="busy" @click="saveInterests">好了</button></div>
              </div>
              <span v-else class="confirm-line">{{ list('interests').join('、') || '综合推荐' }}<button type="button" class="confirm-link" :disabled="busy" @click="open('interests')">改</button></span>
            </template>
            <template v-else-if="field.key === 'must_visit' || field.key === 'avoid'">
              <span class="confirm-line">
                <span v-if="!list(field.key).length">没有</span>
                <span v-for="name in list(field.key)" :key="name" class="confirm-tag">{{ name }}<button type="button" :aria-label="`去掉${name}`" :disabled="busy" @click="set(field.key, list(field.key).filter(other => other !== name))"><PhX :size="11" /></button></span>
                <button v-if="editing !== field.key" type="button" class="confirm-link" :disabled="busy" @click="open(field.key)">添加</button>
              </span>
              <form v-if="editing === field.key" class="confirm-add" @submit.prevent="addPlace(field.key)">
                <input v-model="adding" type="text" maxlength="40" :aria-label="`添加${field.label}的地方`" :placeholder="field.key === 'must_visit' ? '例如：灵隐寺' : '例如：宋城'" :disabled="busy" />
                <button type="submit" class="confirm-save" :disabled="busy || !adding.trim()">加上</button><button type="button" class="confirm-link" :disabled="busy" @click="editing = null">收起</button>
              </form>
            </template>
            <span v-else>{{ field.display }}</span>
          </td>
          <td>
            <span v-if="changed(field.key)" class="source-tag pending">已改，未提交</span>
            <template v-else-if="field.source"><span class="source-tag">{{ SOURCE[field.source] }}</span><small v-if="field.note">{{ field.note }}</small></template>
          </td>
        </tr>
      </tbody>
    </table>
    <div v-if="phrases.length" class="confirm-pending" role="status">
      <span>改了 {{ phrases.length }} 处，提交后{{ mode === 'agent' ? '我重新理解一遍' : '重新核对一遍' }}</span>
      <span class="confirm-pending-actions"><button type="button" class="confirm-link" :disabled="busy" @click="discard">撤销</button><button type="button" class="confirm-save" :disabled="busy" @click="submit">提交修改</button></span>
    </div>
  </div>
</template>

<style scoped>
.confirm-issues { list-style: none; margin: 0 0 14px; padding: 0; }
.confirm-issues p { margin: 0; }
.confirm-fixes { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 8px; }
.confirm-fixes button { font: inherit; font-size: 12px; padding: 4px 11px; border: 1px solid currentColor; border-radius: 3px; background: #ffffff; color: inherit; cursor: pointer; }
.confirm-fixes button.on { background: #151613; border-color: #151613; color: #ffffff; }
.confirm-fixes button:disabled { opacity: .5; cursor: default; }
.confirm-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.confirm-table th, .confirm-table td { text-align: left; padding: 9px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
.confirm-table thead th { font-size: 12px; font-weight: 500; color: var(--muted); }
.confirm-table tbody th { font-weight: 500; white-space: nowrap; width: 6.5em; }
.confirm-table tr.changed th { box-shadow: inset 3px 0 0 #151613; }
.confirm-table td small { display: block; font-size: 12px; color: var(--muted); margin-top: 3px; }
.confirm-table select, .confirm-table input { font: inherit; }
.confirm-budget input { width: 7em; }
.confirm-wide { max-width: 100%; }
.confirm-line { display: inline-flex; flex-wrap: wrap; align-items: center; gap: 6px; }
.confirm-link { font: inherit; font-size: 12px; padding: 0; border: 0; background: none; color: var(--muted); text-decoration: underline; text-underline-offset: 2px; cursor: pointer; }
.confirm-link:disabled, .confirm-save:disabled, .confirm-chip:disabled { opacity: .5; cursor: default; }
.confirm-save { font: inherit; font-size: 12px; padding: 3px 12px; border: 0; border-radius: 3px; background: #151613; color: #ffffff; cursor: pointer; }
.confirm-tag { display: inline-flex; align-items: center; gap: 3px; font-size: 13px; padding: 1px 4px 1px 9px; border-radius: 10px; background: var(--bg); }
.confirm-tag button { display: inline-flex; padding: 2px; border: 0; border-radius: 50%; background: none; color: var(--muted); cursor: pointer; }
.confirm-editor { display: flex; flex-direction: column; gap: 8px; }
.confirm-chips { display: flex; flex-wrap: wrap; gap: 6px; }
.confirm-chip { font: inherit; font-size: 12px; padding: 3px 10px; border: 1px solid var(--line); border-radius: 12px; background: #ffffff; color: inherit; cursor: pointer; }
.confirm-chip.on { background: #151613; border-color: #151613; color: #ffffff; }
.confirm-editor-actions, .confirm-add { display: flex; align-items: center; gap: 10px; }
.confirm-add { margin-top: 8px; }
.confirm-add input { width: 12em; min-width: 0; }
.confirm-pending { position: sticky; bottom: 0; display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: 8px 16px; margin-top: 10px; padding: 10px 12px; border: 1px solid #151613; border-radius: 4px; background: #ffffff; font-size: 13px; }
.confirm-pending-actions { display: inline-flex; align-items: center; gap: 14px; }
.confirm-pending .confirm-save { font-size: 13px; padding: 5px 14px; }
.source-tag { display: inline-block; white-space: nowrap; font-size: 12px; padding: 1px 7px; border-radius: 10px; background: var(--bg); color: var(--muted); }
.source-inferred .source-tag, .source-default .source-tag { background: var(--warn-bg); color: var(--warn); }
.confirm-table .source-tag.pending { background: #151613; color: #ffffff; }
@media (max-width: 560px) { .confirm-table tbody th { width: auto; } .confirm-table th, .confirm-table td { padding: 8px 4px; } .confirm-add input { flex: 1; } }
</style>
