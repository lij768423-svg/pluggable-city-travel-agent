<script setup lang="ts">
import { computed } from 'vue'
import type { PlanResponse } from '../api'

const props = defineProps<{ plan: PlanResponse }>()
const t = computed(() => props.plan.report.totals)
const isMealSwap = computed(() => props.plan.meta.operation === 'meal_swap' || Boolean(props.plan.change))
const CODE: Record<string, string> = {
  unknown_place: '库外地点', opening_hours: '营业时间', travel_time: '交通时间不足', time_order: '时间倒置',
  duplicate_poi: '景点重复', over_budget: '超预算', diet_conflict: '饮食冲突', diet_unverified: '饮食要求待确认', day_overlong: '超出每日活动时间',
  avoid_visited: '去了不想去的', must_visit_missing: '漏掉必去', missing_lunch: '缺午餐', missing_dinner: '缺晚餐',
  short_visit: '游玩时间偏短', early_finish: '下午安排偏少', long_idle: '空档过长',
  date_closed: '日期闭馆', walking_limit: '步行超限', transport_unavailable: '交通不可用', empty_day: '当日无景点',
  excessive_transport: '交通时间偏长', anchor_mixed: '大型园区混排', audience_mismatch: '人群不匹配', onsite_meal_unconfirmed: '园内用餐待确认',
}
const paces: Record<string, string> = { relaxed: '舒缓', normal: '均衡', intense: '充实' }
const rows = computed(() => [
  ['门票', t.value.tickets], ['餐饮（含园内预留）', t.value.meals], [props.plan.meta.travel?.source === 'amap' ? '市内交通' : '市内交通（估算）', t.value.transport],
] as [string, number][])
</script>

<template>
  <div class="report">
    <table class="cost-table">
      <tr v-for="[k, v] in rows" :key="k"><th>{{ k }}</th><td>¥{{ (v ?? 0).toFixed(0) }}</td></tr>
      <tr class="sum"><th>合计 / 预算</th><td>¥{{ t.total?.toFixed(0) }} / ¥{{ plan.profile.budget.toFixed(0) }}</td></tr>
    </table>

    <h4>校验结果</h4>
    <ul v-if="plan.report.violations.length" class="viol">
      <li v-for="(v, i) in plan.report.violations" :key="i" :class="v.severity">
        <b>{{ CODE[v.code] ?? v.code }}</b><span v-if="v.day"> · 第 {{ v.day }} 天{{ v.item ? ` · ${v.item}` : '' }}</span>
        <p>{{ v.message }}</p>
      </li>
    </ul>
    <p v-else class="muted">已知条件下未发现时间与预算冲突。数据缺口与预约状态见下方，校验通过不代表已获得实时营业或饮食认证。</p>
    <h4>仍需确认</h4>
    <ul class="data-unknowns"><li v-for="unknown in plan.report.unknowns" :key="unknown">{{ unknown }}</li></ul>

    <h4>系统理解的需求</h4>
    <p class="muted">
      {{ plan.profile.days }} 天 · {{ paces[plan.profile.pace] }} · 兴趣 {{ plan.profile.interests.join('、') || '无' }} ·
      饮食 {{ plan.profile.dietary.join('、') || '无' }} · 必去 {{ plan.profile.must_visit.join('、') || '无' }}
    </p>

    <h4>路程来源</h4>
    <p class="muted">
      <template v-if="plan.meta.travel?.source === 'amap'">
        规划阶段联网查询 {{ plan.meta.travel.live_calls }} 次（包含餐饮与路线，不含按需加载的地图路径和评分查询）。定稿行程去重后 {{ plan.meta.travel.final_legs }} 段，
        其中 {{ plan.meta.travel.approx_legs ?? 0 }} 段为估算。定稿路段数不包含重复校验和试探路段。
        交通费用可能仍是估算，未包含排队，也不保证未来交通状况。
      </template>
      <template v-else>估算模型（直线距离 × 1.3 + 分段速度），未经真实路线校准，仅供参考。</template>
    </p>

    <h4>Agent 执行过程</h4>
    <ol class="log">
      <li v-for="(l, i) in plan.log" :key="i">{{ l }}</li>
    </ol>
    <p v-if="!isMealSwap && plan.meta.selection?.provider === 'typesafe'" class="muted tiny">
      Jev 选点 · {{ plan.meta.selection.model || 'TypeSafe' }} · {{ plan.meta.selection.seconds }} 秒
      <template v-if="plan.meta.selection.status === 'fallback'"> · 已回退原模型（{{ plan.meta.selection.error }}）</template>
      <template v-else> · {{ plan.meta.selection.cached ? '缓存命中' : '实时评分' }} · {{ plan.meta.selection.confident_candidates }}/{{ plan.meta.selection.candidates }} 个候选采用模型评分，其余沿用规则</template>
    </p>
    <p v-if="isMealSwap" class="muted tiny">
      本次换餐局部重排 · {{ plan.seconds }} 秒 · 调度算法重新核对路线与时间，无需调用大模型。
      原行程的模型选点与修复记录见上方执行过程。
    </p>
    <p v-else class="muted tiny">
      {{ plan.meta.llm_model }}（思考 {{ plan.meta.llm_thinking }}）· LLM {{ plan.usage.calls }} 次（缓存 {{ plan.usage.cached_calls }}）·
      {{ plan.usage.total_tokens }} tokens · {{ plan.seconds }} 秒 · 选点 {{ plan.meta.select_mode }} · 修复 {{ plan.meta.repair_rounds }} 轮
    </p>
  </div>
</template>
