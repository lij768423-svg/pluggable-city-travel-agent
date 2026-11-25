<script setup lang="ts">
// 费用构成条（2026-11-24 第二批）：整条是人均预算，已安排的门票、餐饮、交通依次占去一段，剩下的留空；超预算时整条是已安排的总额，另画一道预算线。
// 数字全部来自行程的校验合计（report.totals），不另算；换点、换餐后各段滑到新宽度。换一份行程时直接到位（外层按行程 key 重建）。
// 配色沿用时间线：景点墨色、餐饮棕色，交通用灰蓝；图例带名称和金额，不只靠颜色区分。
import { computed } from 'vue'
import RollValue from './RollValue.vue'

const props = defineProps<{ totals: Record<string, number>; budget: number; scope: string; estimatedTransport?: boolean }>()

const PARTS = [['tickets', '门票'], ['meals', '餐饮'], ['transport', '交通']] as const
const known = computed(() => PARTS.every(([key]) => Number.isFinite(props.totals[key])) && Number.isFinite(props.totals.total) && props.totals.total! > 0)
const whole = computed(() => Math.max(props.totals.total ?? 0, props.budget > 0 ? props.budget : 0))
const over = computed(() => props.budget > 0 && (props.totals.total ?? 0) > props.budget + 1e-6)
const segments = computed(() => PARTS.map(([key, name]) => {
  const amount = props.totals[key] ?? 0
  return { key, name: key === 'transport' && props.estimatedTransport ? '交通（估算）' : name, amount, share: whole.value ? amount / whole.value * 100 : 0 }
}))
const budgetAt = computed(() => over.value ? props.budget / whole.value * 100 : null)
const label = computed(() => `人均已安排 ${props.totals.total?.toFixed(0)} 元，预算 ${props.budget.toFixed(0)} 元${over.value ? '，已超预算' : ''}：${segments.value.map(part => `${part.name} ${part.amount.toFixed(0)} 元`).join('，')}`)
</script>

<template>
  <div v-if="known" class="cost-bar" :class="{ over }">
    <div class="cost-track" role="img" :aria-label="label">
      <span class="cost-parts"><i v-for="part in segments" :key="part.key" :class="[`cost-${part.key}`, { none: part.amount <= 0 }]" :style="{ width: `${part.share}%` }" :title="`${part.name} ¥${part.amount.toFixed(0)}`" /></span>
      <b v-if="budgetAt != null" class="cost-limit" :style="{ left: `${budgetAt}%` }" :title="`预算 ¥${budget.toFixed(0)}`" />
    </div>
    <ul class="cost-legend" aria-hidden="true">
      <li v-for="part in segments" :key="part.key"><i :class="`cost-${part.key}`" />{{ part.name }}<span>¥<RollValue :value="part.amount.toFixed(0)" :scope="scope" :flash="false" /></span></li>
    </ul>
  </div>
</template>

<style scoped>
.cost-bar { margin: 10px 0 8px; }
.cost-track { position: relative; height: 8px; }
.cost-parts { display: flex; height: 100%; border-radius: 999px; overflow: hidden; background: var(--line-soft); }
.cost-parts i { flex: none; height: 100%; background-clip: padding-box; border-right: 2px solid var(--panel); transition: width var(--spring-soft-ms) var(--spring-soft); }
.cost-parts i.none, .over .cost-parts i:last-child { border-right-width: 0; }
.cost-tickets { background-color: var(--accent); }
.cost-meals { background-color: var(--meal); }
.cost-transport { background-color: #7d97b3; }
.cost-limit { position: absolute; top: -3px; bottom: -3px; width: 2px; margin-left: -1px; background: var(--bad); box-shadow: 0 0 0 1px var(--panel); transition: left var(--spring-soft-ms) var(--spring-soft); }
.cost-legend { display: flex; flex-wrap: wrap; gap: 2px 14px; margin-top: 6px; font-size: 11px; color: var(--muted); }
.cost-legend li { display: inline-flex; align-items: center; gap: 5px; white-space: nowrap; }
.cost-legend li > i { width: 8px; height: 8px; border-radius: 2px; }
.cost-legend span { color: var(--text); font-variant-numeric: tabular-nums; }
@media (max-width: 860px) { .cost-bar { margin: 6px 0 5px; } .cost-track { height: 6px; } .cost-legend { margin-top: 4px; gap: 2px 10px; } }
</style>
