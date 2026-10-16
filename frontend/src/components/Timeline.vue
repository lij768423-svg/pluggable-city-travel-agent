<script setup lang="ts">
import { computed } from 'vue'
import { PhBus, PhCar, PhHouseLine, PhPersonSimpleWalk } from '@phosphor-icons/vue'
import type { DayPlan, MealAlternative } from '../api'

const props = defineProps<{ day: DayPlan; hotelName: string; selectedIndex: number | null; selectedAlternativeId: number | null }>()
const emit = defineEmits<{ focus: [index: number]; details: [index: number]; focusAlternative: [index: number, alternative: MealAlternative]; detailsAlternative: [index: number, alternative: MealAlternative]; alternativesToggle: [index: number, open: boolean] }>()
const startsGroup = (index: number) => props.day.items[index]?.visit_group && !props.day.items.slice(0, index).some(item => item.visit_group === props.day.items[index]?.visit_group)

const clock = (m: number) => `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`
const dur = (m: number) => (m >= 60 ? `${Math.floor(m / 60)} 小时${m % 60 ? ` ${m % 60} 分` : ''}` : `${m} 分钟`)
const gapBefore = (i: number) => i > 0 ? props.day.items[i]!.start - props.day.items[i - 1]!.end - props.day.items[i]!.travel_min - (props.day.reserved_meals || []).filter(slot => slot.start >= props.day.items[i - 1]!.end && slot.end <= props.day.items[i]!.start).reduce((sum, slot) => sum + slot.end - slot.start, 0) : 0
const returnAt = computed(() => Math.max(props.day.items.at(-1)?.end || 0, ...(props.day.reserved_meals || []).map(slot => slot.end)) + props.day.return_travel_min)
// 景点按 1、2、3 编号，餐厅统一标"餐"（与地图标记一致）
const labels = computed(() => {
  let n = 0
  return props.day.items.map(it => (it.kind === 'meal' ? '餐' : String(++n)))
})
</script>

<template>
  <ol v-if="day.items.length" class="route">
    <li class="endpoint">
      <span class="t">{{ clock(day.items[0]!.start - day.items[0]!.travel_min) }}</span>
      <span class="dot home"><PhHouseLine :size="13" weight="bold" /></span>
      <span class="ep-text">从 {{ hotelName }} 出发</span>
    </li>
    <template v-for="(item, i) in day.items" :key="i">
      <li v-if="startsGroup(i)" class="walk-group"><PhPersonSimpleWalk :size="15" /><span>{{ item.visit_group_label }}<small>同一段漫步 · 下列地点为沿途停留</small></span></li>
      <li class="leg">
        <component :is="item.travel_mode === '步行' ? PhPersonSimpleWalk : item.travel_mode === '打车' ? PhCar : PhBus" :size="13" />
        {{ item.travel_mode || '步行' }} {{ item.travel_min }} 分钟
        <span v-if="item.travel_evidence?.source === 'estimate'" class="source-note">估算</span>
        <em v-if="gapBefore(i) >= 30">· 空档 {{ dur(gapBefore(i)) }}</em>
      </li>
      <li :id="`stop-${day.day}-${i}`" class="stop" :class="[item.kind, { on: selectedIndex === i }]"
        tabindex="0" :aria-label="`在地图上定位${item.name}`" @click="emit('focus', i)" @keydown.enter.self="emit('focus', i)" @keydown.space.self.prevent="emit('focus', i)">
        <span class="t">{{ item.start_hm }}<small>{{ item.end_hm }}</small></span>
        <span class="dot">{{ labels[i] }}</span>
        <div class="stop-body">
          <div class="stop-title">
            <h3>{{ item.kind === 'meal' ? (item.meal === 'lunch' ? '午餐 · ' : '晚餐 · ') : '' }}{{ item.name }}</h3>
            <span class="price">{{ item.cost > 0 ? `参考 ¥${item.cost.toFixed(0)}` : item.price_verified ? '免费' : '票价待核' }}</span>
          </div>
          <p class="stop-meta">{{ item.visit_role === 'viewpoint' ? '沿途观景' : item.type }} · {{ item.visit_group ? '停留 ' : '' }}{{ dur(item.end - item.start) }}<span v-if="item.evening" class="evening-label">夜游</span></p>
          <div v-for="slot in (day.reserved_meals || []).filter(slot => item.kind === 'poi' && slot.inside_poi_id === item.ref_id)" :key="slot.meal" class="notice">
            {{ clock(slot.start) }}–{{ clock(slot.end) }} · 园内{{ slot.meal === 'lunch' ? '午餐' : '晚餐' }}<br>
            {{ slot.reason }}<br>预留预算 ¥{{ slot.estimated_cost?.toFixed(0) }} / 人，已计入合计，非商户报价。
          </div>
          <div class="stop-actions"><span>{{ selectedIndex === i ? '已在地图定位' : '点击此项定位地图' }}</span><button type="button" :aria-label="`查看${item.name}详情`" @click.stop="emit('details', i)">查看详情 <span aria-hidden="true">↗</span></button></div>
          <details v-if="item.alternatives?.length" :id="`alts-${day.day}-${i}`" class="meal-alternatives" @click.stop @keydown.enter.stop @keydown.space.stop
            @toggle="emit('alternativesToggle', i, ($event.target as HTMLDetailsElement).open)">
            <summary>另外 {{ item.alternatives.length }} 家备选 <small>· 展开可在地图比较</small></summary>
            <div v-for="(alternative, n) in item.alternatives" :id="`alt-${day.day}-${i}-${alternative.ref_id}`" :key="alternative.ref_id" class="meal-alternative" :class="{ on: selectedAlternativeId === alternative.ref_id }">
              <button type="button" class="alternative-location" :disabled="alternative.lat == null || alternative.lon == null" :aria-label="`在地图上定位备选餐厅${alternative.name}`" @click.stop="emit('focusAlternative', i, alternative)"><span class="alt-badge">备{{ n + 1 }}</span><strong>{{ alternative.name }}</strong></button><span>¥{{ alternative.price.toFixed(0) }} / 人</span>
              <p>{{ alternative.cuisine }}<template v-if="alternative.travel_source === 'previous'"> · 原先安排的餐厅</template><template v-else> · 绕路约 {{ alternative.detour_min }} 分钟{{ alternative.travel_source === 'estimate' ? '（估算）' : '' }}</template></p>
              <p v-if="alternative.address">{{ alternative.address }}</p>
              <div class="stop-actions">
                <span>{{ selectedAlternativeId === alternative.ref_id ? '已在地图定位' : '点击店名定位地图' }}</span>
                <button type="button" :aria-label="`查看备选餐厅${alternative.name}详情`" @click.stop="emit('detailsAlternative', i, alternative)">查看详情 <span aria-hidden="true">↗</span></button>
              </div>
            </div>
            <p class="field-help">换成备选后会按真实路线重新核对当天时间，其他日期不变，可撤销。</p>
          </details>
        </div>
      </li>
    </template>
    <li class="leg">返回起点 {{ day.return_travel_min }} 分钟 <span v-if="day.return_leg?.source === 'estimate'" class="source-note">估算</span></li>
    <li class="endpoint">
      <span class="t">{{ clock(returnAt) }}</span>
      <span class="dot home"><PhHouseLine :size="13" weight="bold" /></span>
      <span class="ep-text">回到 {{ hotelName }}</span>
    </li>
  </ol>
  <p v-else class="notice">当前条件下这一天没有排出景点，可以放宽预算或减少必去地点后重试。</p>
  <p v-for="note in day.notes" :key="note" class="notice warn">{{ note }}</p>
  <div v-for="slot in (day.reserved_meals || []).filter(slot => slot.inside_poi_id == null)" :key="slot.meal" class="notice warn">
    {{ clock(slot.start) }}–{{ clock(slot.end) }} · 预留{{ slot.meal === 'lunch' ? '午餐' : '晚餐' }}时间<br>{{ slot.reason }}；费用尚未计入。
  </div>
  <p v-if="day.walking_m != null" class="aside-note">已知步行约 {{ (day.walking_m / 1000).toFixed(1) }} 公里，场所内部步行可能未完整计入。</p>
</template>
