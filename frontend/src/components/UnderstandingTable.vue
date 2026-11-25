<script setup lang="ts">
// “我是这么理解的”（问卷模式的确认表）：上面是规划前就能看出的问题和一键修改，下面逐项列出理解与依据；除城市外每项都能就地改（2026-10-28 用户：能改的太少）。
// 改动先攒着，用户改完点“提交修改”才一次交出去；「跟 AI 说」用行程票，不用这张表（2026-11-25），修改逻辑共用 understandingDraft.ts。
import { ref, watch } from 'vue'
import { PhMinus, PhPlus, PhX } from '@phosphor-icons/vue'
import type { Meta, Understanding } from '../api'
import { SOURCE, TRANSPORT, useUnderstandingDraft, type ListKey } from '../understandingDraft'
import IssueList from './IssueList.vue'
import OriginPicker from './OriginPicker.vue'
import RollValue from './RollValue.vue'

const props = defineProps<{ understanding: Understanding; meta: Meta | null; busy: boolean }>()
const emit = defineEmits<{ apply: [patch: Record<string, unknown>]; pending: [phrases: string[]] }>()
const edits = useUnderstandingDraft(() => props.understanding, () => props.meta)
const { rows, draft, changed, value, list, set, change, phrases, budgetShown, perDay, hotelPicked, hotelPresets, pickOrigin } = edits
watch(phrases, value => emit('pending', value))

const editing = ref<string | null>(null)
const picked = ref<string[]>([])
const adding = ref('')
watch(() => props.understanding, () => { editing.value = null })

function submit() {
  if (!phrases.value.length || props.busy) return
  emit('apply', { ...draft.value })
}
function discard() {
  edits.discard()
  editing.value = null
}

// 天数用步进：数字逐位翻；到 1 天或 5 天再按，按钮朝按的方向轻推一下，不改值
const DAYS_MIN = 1
const DAYS_MAX = 5
function stepDays(delta: number, event: MouseEvent) {
  const next = Number(value('days') || DAYS_MIN) + delta
  if (next >= DAYS_MIN && next <= DAYS_MAX) { change('days', String(next)); return }
  const button = event.currentTarget as HTMLElement
  button.classList.remove('kick')
  void button.offsetWidth
  button.classList.add('kick')
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
    <IssueList :issues="understanding.issues" :edits="edits" :busy="busy" />
    <table class="confirm-table">
      <thead><tr><th scope="col">项目</th><th scope="col">我的理解</th><th scope="col">依据</th></tr></thead>
      <tbody>
        <tr v-for="field in rows" :key="field.key" :class="[field.source ? `source-${field.source}` : '', { changed: changed(field.key) }]">
          <th scope="row">{{ field.label }}</th>
          <td>
            <div v-if="field.key === 'days'" class="num-stepper" role="group" :aria-label="field.label"><button type="button" class="kick-left" aria-label="少一天" :aria-disabled="Number(value('days')) <= DAYS_MIN" :disabled="busy" @click="stepDays(-1, $event)" @animationend="($event.currentTarget as HTMLElement).classList.remove('kick')"><PhMinus :size="13" weight="bold" /></button><output aria-live="polite"><RollValue :value="Number(value('days'))" :flash="false" /> 天</output><button type="button" aria-label="多一天" :aria-disabled="Number(value('days')) >= DAYS_MAX" :disabled="busy" @click="stepDays(1, $event)" @animationend="($event.currentTarget as HTMLElement).classList.remove('kick')"><PhPlus :size="13" weight="bold" /></button></div>
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
      <span>改了 {{ phrases.length }} 处，提交后重新核对一遍</span>
      <span class="confirm-pending-actions"><button type="button" class="confirm-link" :disabled="busy" @click="discard">撤销</button><button type="button" class="confirm-save" :disabled="busy" @click="submit">提交修改</button></span>
    </div>
  </div>
</template>

<style scoped>
.confirm-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.confirm-table th, .confirm-table td { text-align: left; padding: 9px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
.confirm-table thead th { font-size: 12px; font-weight: 500; color: var(--muted); }
.confirm-table tbody th { font-weight: 500; white-space: nowrap; width: 6.5em; }
.confirm-table tr.changed th { box-shadow: inset 3px 0 0 #151613; }
.confirm-table td small { display: block; font-size: 12px; color: var(--muted); margin-top: 3px; }
.confirm-table select, .confirm-table input { font: inherit; }
.confirm-budget input { width: 7em; }
.num-stepper { display: inline-flex; align-items: center; border: 1px solid var(--line); border-radius: 4px; background: #ffffff; }
.num-stepper button { display: grid; place-items: center; width: 36px; height: 36px; border: 0; background: none; color: var(--muted); cursor: pointer; }
.num-stepper button:hover:not(:disabled) { color: var(--text); }
.num-stepper button[aria-disabled=true] { color: #c4c5c1; }
.num-stepper output { min-width: 3.4em; text-align: center; font-variant-numeric: tabular-nums; }
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
