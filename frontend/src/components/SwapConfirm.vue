<script setup lang="ts">
import { computed } from 'vue'
import { AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogOverlay,
  AlertDialogPortal, AlertDialogRoot, AlertDialogTitle } from 'reka-ui'
import { PhArrowRight, PhWarningCircle } from '@phosphor-icons/vue'
import type { MealAlternative, MealEditPreview } from '../api'

const props = defineProps<{ open: boolean; day: number; meal: 'lunch' | 'dinner' | null; from: string; target: MealAlternative | null;
  preview: MealEditPreview | null; loading: boolean; busy: boolean; error: string; selfArranged: boolean; selfCost: number }>()
const emit = defineEmits<{ 'update:open': [value: boolean]; confirm: []; retry: []; budget: [value: number] }>()
const clock = (value: number | null) => value == null ? '未安排' : `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`
const delta = (value: number, unit: string) => value === 0 ? '不变' : `${value > 0 ? '+' : '−'}${Math.abs(value)}${unit}`
const mealTime = computed(() => {
  const day = props.preview?.plan.days.find(day => day.day === props.day)
  const meal = props.selfArranged ? day?.reserved_meals?.find(slot => slot.meal === props.meal)
    : day?.items.find(item => item.kind === 'meal' && item.meal === props.meal && item.ref_id === props.target?.ref_id)
  return meal ? `${clock(meal.start)}–${clock(meal.end)}` : ''
})
</script>

<template>
  <AlertDialogRoot :open="open" @update:open="value => !busy && emit('update:open', value)">
    <AlertDialogPortal>
      <AlertDialogOverlay class="confirm-overlay" />
      <AlertDialogContent class="confirm-dialog meal-preview-dialog" aria-describedby="meal-preview-description">
        <p class="preview-eyebrow">第 {{ day }} 天 · {{ meal === 'lunch' ? '午餐' : '晚餐' }}调整</p>
        <AlertDialogTitle>{{ selfArranged ? '这餐，由你自己安排。' : '先看看，换餐后会怎样。' }}</AlertDialogTitle>
        <AlertDialogDescription id="meal-preview-description" class="preview-description">{{ loading ? '正在重算当天安排，原行程保持不变。' : '这是重算后的临时方案，确认后才会更新你的行程。' }}</AlertDialogDescription>
        <div class="confirm-body preview-content" :aria-busy="loading">
          <p class="confirm-swap"><s>{{ from }}</s><PhArrowRight :size="16" /><strong>{{ selfArranged ? '自行安排用餐' : target?.name }}</strong></p>
          <label v-if="selfArranged" class="self-cost-field">预留餐费（元 / 人）<input type="number" min="0" max="2000" :value="selfCost" :disabled="busy" @input="emit('budget', ($event.target as HTMLInputElement).valueAsNumber)"><small>按你的预留金额计入预算，不计作已推荐餐厅。</small></label>
          <div v-if="loading" class="preview-loading" role="status"><span class="spinner" />正在核对时间、路线与用餐预算…</div>
          <template v-else-if="preview">
            <p v-if="mealTime" class="preview-source">新用餐时段 · {{ mealTime }}</p>
            <dl class="preview-numbers">
              <div><dt>人均总费用</dt><dd>¥{{ preview.change.budget_after.toFixed(0) }}<small>{{ delta(preview.change.budget_delta, ' 元') }}</small></dd></div>
              <div><dt>全天路程耗时</dt><dd>{{ delta(preview.change.travel_delta_min, ' 分钟') }}<small>其中乘车 {{ delta(preview.change.ride_delta_min, ' 分钟') }}</small></dd></div>
              <div><dt>预计回到起点</dt><dd>{{ clock(preview.change.end_after) }}<small>原安排 {{ clock(preview.change.end_before) }}</small></dd></div>
            </dl>
            <p class="preview-source">{{ preview.change.route_source === 'estimate' ? '部分交通使用估算，实际路况与营业信息仍需确认。' : '按预览中的导航路线核对；提交时使用同一份路线证据。' }}</p>
            <section v-if="preview.change.affected_stops.length" class="preview-stops"><h3>这些安排会变化</h3>
              <ul><li v-for="(stop, index) in preview.change.affected_stops" :key="`${stop.name}-${index}`"><span>{{ stop.name }}</span><small>{{ clock(stop.before_start) }}–{{ clock(stop.before_end) }}<PhArrowRight :size="12" />{{ stop.after_start == null ? '移出当天' : `${clock(stop.after_start)}–${clock(stop.after_end)}` }}</small></li></ul>
            </section>
            <p v-else class="preview-source">景点时间和顺序保持不变。</p>
            <p v-if="preview.change.removed.length" class="notice warn">将移出：{{ preview.change.removed.join('、') }}</p>
            <div v-if="!preview.can_commit" class="notice bad" role="alert"><strong>这份调整暂时无法采用</strong><ul><li v-for="(issue, index) in preview.plan.report.violations.filter(issue => issue.severity === 'hard')" :key="index">{{ issue.message }}</li></ul><p>原行程保持不变，可以换一家餐厅再试。</p></div>
            <p v-else class="preview-ready">已通过时间、预算与路线硬约束核对。其他日期保持不变，确认后支持撤销。</p>
          </template>
          <p v-if="error" class="notice bad" role="alert"><PhWarningCircle :size="16" />{{ error }}</p>
        </div>
        <div class="confirm-actions">
          <AlertDialogCancel class="ghost-btn" :disabled="busy">保留原行程</AlertDialogCancel>
          <button v-if="!loading && (!preview || error)" type="button" class="ghost-btn" :disabled="busy" @click="emit('retry')">重新计算</button>
          <AlertDialogAction class="primary-btn" :disabled="busy || loading || !preview?.can_commit" @click.prevent="emit('confirm')"><span v-if="busy" class="spinner light" />{{ busy ? '正在保存…' : '确认采用此方案' }}</AlertDialogAction>
        </div>
      </AlertDialogContent>
    </AlertDialogPortal>
  </AlertDialogRoot>
</template>
