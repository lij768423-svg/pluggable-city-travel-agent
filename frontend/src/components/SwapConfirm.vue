<script setup lang="ts">
import { computed } from 'vue'
import { AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogOverlay,
  AlertDialogPortal, AlertDialogRoot, AlertDialogTitle } from 'reka-ui'
import { PhArrowRight, PhWarningCircle } from '@phosphor-icons/vue'
import type { MealAlternative, MealEditPreview } from '../api'

const props = defineProps<{ open: boolean; day: number; meal: 'lunch' | 'dinner' | null; from: string; target: MealAlternative | null;
  preview: MealEditPreview | null; loading: boolean; busy: boolean; error: string; selfArranged: boolean; selfCost: number;
  edit: 'poi' | 'less' | 'more' | null; toName: string; canRetry: boolean }>()
const emit = defineEmits<{ 'update:open': [value: boolean]; confirm: []; retry: []; budget: [value: number] }>()
const clock = (value: number | null) => value == null ? '未安排' : `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`
const delta = (value: number, unit: string) => value === 0 ? '不变' : `${value > 0 ? '+' : '−'}${Math.abs(value)}${unit}`
const newTime = computed(() => {
  const day = props.preview?.plan.days.find(day => day.day === props.day)
  const stop = props.edit ? day?.items.find(item => item.kind === 'poi' && item.ref_id === props.preview?.change.poi_id)
    : props.selfArranged ? day?.reserved_meals?.find(slot => slot.meal === props.meal)
    : day?.items.find(item => item.kind === 'meal' && item.meal === props.meal && item.ref_id === props.target?.ref_id)
  return stop ? `${clock(stop.start)}–${clock(stop.end)}` : ''
})
// 和时间线的“已知步行约 X 公里”同一口径；旧行程没有这个数就不写
const walking = computed(() => {
  const change = props.preview?.change
  if (change?.walking_before_m == null || change.walking_after_m == null) return ''
  const [before, after] = [change.walking_before_m, change.walking_after_m].map(meters => (meters / 1000).toFixed(1))
  return before === after ? `已知步行 ${after} 公里` : `已知步行 ${before} → ${after} 公里`
})
// 换景点事先知道换成谁；调节奏要等预览算出来才知道去掉或加入哪一站
const fromName = computed(() => props.edit === 'less' ? props.preview?.change.from ?? '' : props.edit === 'more' ? '' : props.from)
const toName = computed(() => {
  if (props.edit === 'less') return ''
  if (props.edit === 'more') return props.preview?.change.to ?? ''
  if (props.edit === 'poi') return props.toName
  return props.selfArranged ? '自行安排用餐' : props.target?.name ?? ''
})
const eyebrow = computed(() => props.edit === 'poi' ? '景点调整' : props.edit ? '节奏调整' : `${props.meal === 'lunch' ? '午餐' : '晚餐'}调整`)
const title = computed(() => props.selfArranged ? '这餐，由你自己安排。' : ({ poi: '先看看，换景点后会怎样。', less: '先看看，少排一站会怎样。', more: '先看看，多排一站会怎样。' } as Record<string, string>)[props.edit ?? ''] ?? '先看看，换餐后会怎样。')
const retryHint = computed(() => props.edit === 'poi' ? '可以换一个景点再试' : props.edit ? '可以保留原行程，或者换掉其中一站' : '可以换一家餐厅再试')
</script>

<template>
  <AlertDialogRoot :open="open" @update:open="value => !busy && emit('update:open', value)">
    <AlertDialogPortal>
      <AlertDialogOverlay class="confirm-overlay" />
      <AlertDialogContent class="confirm-dialog meal-preview-dialog" aria-describedby="meal-preview-description">
        <p class="preview-eyebrow">第 {{ day }} 天 · {{ eyebrow }}</p>
        <AlertDialogTitle>{{ title }}</AlertDialogTitle>
        <AlertDialogDescription id="meal-preview-description" class="preview-description">{{ loading ? '正在重算当天安排，原行程保持不变。' : preview || selfArranged ? '这是重算后的临时方案，确认后才会更新你的行程。' : '原行程保持不变。' }}</AlertDialogDescription>
        <div class="confirm-body preview-content" :aria-busy="loading">
          <p v-if="fromName || toName" class="confirm-swap"><span v-if="edit === 'less'">去掉</span><span v-if="edit === 'more'">加入</span><s v-if="fromName">{{ fromName }}</s><PhArrowRight v-if="fromName && toName" :size="16" /><strong v-if="toName">{{ toName }}</strong></p>
          <label v-if="selfArranged" class="self-cost-field">预留餐费（元 / 人）<input type="number" min="0" max="2000" :value="selfCost" :disabled="busy" @input="emit('budget', ($event.target as HTMLInputElement).valueAsNumber)"><small>按你的预留金额计入预算，不计作已推荐餐厅。</small></label>
          <div v-if="loading" class="preview-loading" role="status"><span class="spinner" />正在核对时间、路线与用餐预算…</div>
          <template v-else-if="preview">
            <p v-if="newTime" class="preview-source">{{ edit ? '新景点时段' : '新用餐时段' }} · {{ newTime }}</p>
            <dl class="preview-numbers">
              <div><dt>人均总费用</dt><dd>¥{{ preview.change.budget_after.toFixed(0) }}<small>{{ delta(preview.change.budget_delta, ' 元') }}</small></dd></div>
              <div><dt>全天路程耗时</dt><dd>{{ delta(preview.change.travel_delta_min, ' 分钟') }}<small>其中乘车 {{ delta(preview.change.ride_delta_min, ' 分钟') }}</small><small v-if="walking">{{ walking }}</small></dd></div>
              <div><dt>预计回到起点</dt><dd>{{ clock(preview.change.end_after) }}<small>原安排 {{ clock(preview.change.end_before) }}</small></dd></div>
            </dl>
            <p class="preview-source">{{ preview.change.route_source === 'estimate' ? '部分交通使用估算，实际路况与营业信息仍需确认。' : '按预览中的导航路线核对；提交时使用同一份路线证据。' }}</p>
            <section v-if="preview.change.affected_stops.length" class="preview-stops"><h3>这些安排会变化</h3>
              <ul><li v-for="(stop, index) in preview.change.affected_stops" :key="`${stop.name}-${index}`"><span>{{ stop.name }}</span><small>{{ clock(stop.before_start) }}–{{ clock(stop.before_end) }}<PhArrowRight :size="12" />{{ stop.after_start == null ? '移出当天' : `${clock(stop.after_start)}–${clock(stop.after_end)}` }}</small><small v-if="stop.after_name">改在 {{ stop.after_name }}</small></li></ul>
            </section>
            <p v-else class="preview-source">{{ edit ? '其余景点的时间和顺序保持不变。' : '景点时间和顺序保持不变。' }}</p>
            <p v-if="preview.change.reordered" class="preview-source">当天景点的先后顺序有调整。</p>
            <p v-if="preview.change.removed.length" class="notice warn">将移出：{{ preview.change.removed.join('、') }}</p>
            <p v-if="preview.change.added?.length" class="notice warn">同时加入：{{ preview.change.added.join('、') }}</p>
            <p v-if="preview.change.new_notes?.length" class="notice warn">采用后这一天还会提示：{{ preview.change.new_notes.join('；') }}</p>
            <div v-if="!preview.can_commit" class="notice bad" role="alert"><strong>这份调整暂时无法采用</strong><ul><li v-for="(issue, index) in preview.change.conflicts" :key="index">{{ issue }}</li></ul><p>原行程保持不变，{{ retryHint }}。</p></div>
            <p v-else class="preview-ready">已通过时间、预算与路线硬约束核对。其他日期保持不变，确认后支持撤销。</p>
          </template>
          <p v-if="error" class="notice bad" role="alert"><PhWarningCircle :size="16" />{{ error }}</p>
        </div>
        <div class="confirm-actions">
          <AlertDialogCancel class="ghost-btn" :disabled="busy">保留原行程</AlertDialogCancel>
          <button v-if="canRetry && !loading && (!preview || error)" type="button" class="ghost-btn" :disabled="busy" @click="emit('retry')">重新计算</button>
          <AlertDialogAction v-if="canRetry || preview" class="primary-btn" :disabled="busy || loading || !preview?.can_commit" @click.prevent="emit('confirm')"><span v-if="busy" class="spinner light" />{{ busy ? '正在保存…' : '确认采用此方案' }}</AlertDialogAction>
        </div>
      </AlertDialogContent>
    </AlertDialogPortal>
  </AlertDialogRoot>
</template>
