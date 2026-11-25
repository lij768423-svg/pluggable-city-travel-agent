<script setup lang="ts">
import '@fontsource-variable/geist-mono'
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { PhArrowRight, PhMountains, PhBuildings, PhCamera, PhLeaf, PhScroll, PhSparkle, PhMoon, PhForkKnife, PhBank, PhCompass, PhFlame, PhPlus, PhSubway, PhTaxi } from '@phosphor-icons/vue'
import type { Meta, PlanRequest, Hotel, CityInfo } from '../api'
import OriginPicker from './OriginPicker.vue'
import { expandHooks, reducedMotion } from '../motion'
import DetentSlider from './DetentSlider.vue'
import DrawCheck from './DrawCheck.vue'
import QuestionOptions from './QuestionOptions.vue'
import SegControl from './SegControl.vue'
import TripTicket from './TripTicket.vue'

const props = defineProps<{ city: CityInfo; meta: Meta | null; loading: boolean; initialQuery?: string; initialRequest?: PlanRequest | null; quota?: { limit: number; remaining: number } | null; needsLogin?: boolean; departing?: boolean }>()
const outOfQuota = computed(() => !!props.quota && props.quota.remaining <= 0)
const emit = defineEmits<{ submit: [req: PlanRequest]; hotelChange: [hotel: Hotel | null] }>()

const query = ref(props.initialQuery || '')
// 留空 = 交给大模型从一句话里解析；填了就以表单为准
const form = reactive({
  days: 2 as '' | number,
  budget: '' as '' | number,
  pace: 'normal',
  companions: '',
  interests: [] as string[],
  spice: 'any' as 'no' | 'any' | 'love',
  familiarity: 'first' as 'first' | 'visited' | 'local',
  must_visit: '',
  avoid: '',
  transport: 'transit' as '' | 'taxi' | 'transit',
  start_date: '',
  day_start: '',
  day_end: '',
  max_walking_m: '' as '' | number,
  allow_taxi_fallback: true,
  include_evening: '' as '' | 'yes' | 'no',
  food_preference: '',
  meal_budget_min: '' as '' | number,
  meal_budget_max: '' as '' | number,
})
const dailyWindows = reactive(Array.from({ length: 5 }, (_, index) => ({ day: index + 1, enabled: false, start: '09:00', end: '20:00' })))
const minutes = (value: string) => Number(value.split(':')[0]) * 60 + Number(value.split(':')[1])
const selectMode = ref<'llm' | 'rule' | 'jev'>('llm')
const enableRepair = ref(true)
const useGuides = ref(false)
const origin = ref<Hotel | null>(null)
const originPresets = computed(() => props.meta?.city === props.city.key ? props.meta.hotels : [])
watch(() => props.city.key, () => {
  step.value = 0
  origin.value = null  // 起点默认不定（2026-10-28 用户决定），住酒店的人再选
  query.value = ''; form.must_visit = ''; form.avoid = ''
})
const queryPlaceholder = computed(() => props.city.key === 'hangzhou'
  ? '一定要去、不想去的地方和其他要求都写在这里，例如：一定要去灵隐寺，不去宋城；想在西湖边喝杯咖啡。'
  : '一定要去、不想去的地方和其他要求都写在这里。')
const budgetError = computed(() => form.budget !== '' && (!Number.isFinite(Number(form.budget)) || Number(form.budget) <= 0 || Number(form.budget) > 100000))
const mealBudgetError = computed(() => {
  if (form.meal_budget_min !== '' && (!Number.isFinite(Number(form.meal_budget_min)) || Number(form.meal_budget_min) < 0 || Number(form.meal_budget_min) > 2000)) return '每餐预算下限需在 0–2000 元之间。'
  if (form.meal_budget_max !== '' && (!Number.isFinite(Number(form.meal_budget_max)) || Number(form.meal_budget_max) < 1 || Number(form.meal_budget_max) > 2000)) return '每餐预算上限需在 1–2000 元之间。'
  if (form.meal_budget_min !== '' && form.meal_budget_max !== '' && Number(form.meal_budget_min) > Number(form.meal_budget_max)) return '每餐预算下限不能高于上限。'
  return ''
})
watch(origin, hotel => emit('hotelChange', hotel))
const step = ref(0)
const stepHeading = ref<HTMLElement>()
const questionBody = ref<HTMLElement>()
const stepDirection = ref('forward')
// 三步：先选兴趣（最直观），再定餐饮预算，最后一页集中填天数、同行、日期、起点与补充
const steps = [
  { label: '游玩偏好' },
  { label: '餐饮与预算' },
  { label: '出行与补充' },
]
const LAST = steps.length - 1
const paceLabels: Record<string, string> = { relaxed: '慢慢逛', normal: '刚刚好', intense: '多看一点' }
const paceLabel = computed(() => paceLabels[form.pace] || '自由节奏')
const headings = ['这趟旅行，想怎么过？', '吃得合口味，花得有数。', '最后，把出发的事定下来。']
const dayOptions: { value: '' | number; label: string }[] = [...[1, 2, 3, 4, 5].map(day => ({ value: day, label: `${day} 天` })), { value: '', label: '按描述' }]
const companionOptions = computed(() => [{ value: '', label: '不限' }, ...Object.entries(props.meta?.companions || {}).map(([value, label]) => ({ value, label }))])
const transportOptions = [{ value: 'transit', label: '公交地铁', icon: PhSubway }, { value: 'taxi', label: '打车', icon: PhTaxi }] as const
const eveningOptions = [{ value: '', label: '顺路就好' }, { value: 'yes', label: '安排夜景' }, { value: 'no', label: '早点休息' }] as const
const budgetOptions: { value: '' | number; label: string }[] = [{ value: '', label: '不限' }, ...[300, 500, 800, 1200, 1800, 2500].map(value => ({ value, label: `¥${value}` }))]
const foodOptions = [{ value: '', label: '顺路就好' }, { value: 'local', label: '当地特色' }, { value: 'quick', label: '快捷简餐' }, { value: 'quality', label: '品质正餐' }]
// 预算滑杆按位置取值：千元以下取整十，以上取整五十；最左一小段算不限
const BUDGET_STOPS = budgetOptions.map(option => Number(option.value))
function budgetAt(index: number): '' | number {
  const left = Math.min(Math.floor(index), BUDGET_STOPS.length - 2)
  const raw = BUDGET_STOPS[left]! + (BUDGET_STOPS[left + 1]! - BUDGET_STOPS[left]!) * (index - left)
  const step = raw < 1000 ? 10 : 50
  return raw < 50 ? '' : Math.round(raw / step) * step
}
const labelOf = (options: readonly { value: string | number; label: string }[], value: string | number) => options.find(option => option.value === value)?.label
const daysLabel = computed(() => labelOf(dayOptions, form.days) ?? `${form.days} 天`)
// 票面六格：始终是当前的实际选择
const ticketCells = computed(() => [
  { label: '天数', value: daysLabel.value },
  { label: '同行', value: labelOf(companionOptions.value, form.companions) ?? '不限' },
  { label: '节奏', value: paceLabel.value },
  { label: '出行', value: labelOf(transportOptions, form.transport) ?? '不限' },
  { label: '预算', value: form.budget === '' || budgetError.value ? '不限' : `¥${form.budget}` },
  { label: '口味', value: labelOf(spiceOptions, form.spice) ?? '' },
])
const ticketRows = computed(() => [
  form.interests.length ? { label: '想看', words: form.interests, span: 2 as const, title: form.interests.join('、') } : { label: '想看', value: '还没选', span: 2 as const, muted: true },
  { label: '起点', value: origin.value?.name ?? '不定' },
])
const ticketDate = computed(() => form.start_date ? form.start_date.slice(5).replace('-', '.') : '')
// 手机上收起时的一行摘要
const selectedSummary = computed(() => [props.city.name, daysLabel.value, form.companions ? labelOf(companionOptions.value, form.companions) : '', paceLabel.value,
  form.interests.length ? `想看 ${form.interests.length} 类` : '', form.budget !== '' ? `¥${form.budget}` : ''].filter(Boolean).join(' · '))
const interestTiles = [
  { value: '自然风光', icon: PhMountains },
  { value: '人文历史', icon: PhBuildings },
  { value: '拍照出片', icon: PhCamera },
  { value: '园林古镇', icon: PhLeaf },
  { value: '特色文化', icon: PhScroll },
  { value: '亲子游乐', icon: PhSparkle },
  { value: '博物馆展览', icon: PhBank },
  { value: '夜景夜游', icon: PhMoon },
  { value: '美食探店', icon: PhForkKnife },
]
const visibleInterests = computed(() => interestTiles.filter(item => props.meta?.interests.includes(item.value)))
const moreInterests = computed(() => (props.meta?.interests || []).filter(value => !interestTiles.some(item => item.value === value)))
const moreSelected = computed(() => form.interests.filter(value => moreInterests.value.includes(value)))
const moreOpen = ref(false)
const mealOpen = ref(false)
const paceOptions = [
  { value: 'relaxed', label: '慢慢逛', hint: '轻松，留白多' },
  { value: 'normal', label: '刚刚好', hint: '游玩与休息兼顾' },
  { value: 'intense', label: '多看一点', hint: '充实紧凑' },
]
const paceHint = computed(() => paceOptions.find(option => option.value === form.pace)?.hint)
// 口味（辣度）单选：不吃辣会排除辣味菜系，爱吃辣只是优先推荐；2026-10-26 起替代原来的饮食忌口
const spiceOptions = [{ value: 'no', label: '不吃辣', icon: PhLeaf }, { value: 'any', label: '微辣都行', icon: PhForkKnife }, { value: 'love', label: '爱吃辣', icon: PhFlame }] as const
// 对城市的熟悉程度：第一次来更看重地标，本地人少推热门；默认第一次来
const familiarityOptions = [{ value: 'first', label: '第一次来' }, { value: 'visited', label: '来过' }, { value: 'local', label: '本地人' }] as const
const formError = ref('')
function goStep(target: number) {
  formError.value = ''
  if (target > 1) {
    const problem = budgetError.value ? '请填写 1–100000 元的总预算，或留空。' : mealBudgetError.value
    if (problem) { if (step.value !== 1) goStep(1); formError.value = problem; return }
  }
  stepDirection.value = target >= step.value ? 'forward' : 'backward'
  step.value = target
  nextTick(() => {
    questionBody.value?.scrollTo({ top: 0, behavior: 'instant' })
    stepHeading.value?.focus({ preventScroll: true })
  })
}
const timeText = (value: unknown) => typeof value === 'number' ? `${Math.floor(value / 60).toString().padStart(2, '0')}:${(value % 60).toString().padStart(2, '0')}` : ''
watch(() => props.initialRequest, request => {
  if (!request) return
  query.value = request.query
  const fields = request.form
  Object.assign(form, { days: 2, budget: '', pace: 'normal', companions: '', interests: [], spice: 'any', familiarity: 'first', must_visit: '', avoid: '', transport: 'transit', start_date: '', day_start: '', day_end: '', max_walking_m: '', allow_taxi_fallback: true, include_evening: '', food_preference: '', meal_budget_min: '', meal_budget_max: '' })
  // 确认后规划带的是整份画像，没填的项是 null：当成没填，否则数字框会被当成 0
  for (const key of Object.keys(form)) {
    if (fields[key] != null && !['must_visit', 'avoid', 'day_start', 'day_end', 'include_evening'].includes(key)) Object.assign(form, { [key]: fields[key] })
  }
  const carried = [Array.isArray(fields.must_visit) && fields.must_visit.length ? `一定要去${fields.must_visit.join('、')}` : '',
    Array.isArray(fields.avoid) && fields.avoid.length ? `不去${fields.avoid.join('、')}` : ''].filter(Boolean).join('；')
  if (carried && !query.value.includes(carried)) query.value = [query.value.trim(), carried].filter(Boolean).join('；')
  form.must_visit = ''
  form.avoid = ''
  form.day_start = timeText(fields.day_start)
  form.day_end = timeText(fields.day_end)
  form.include_evening = fields.include_evening == null ? '' : fields.include_evening ? 'yes' : 'no'
  origin.value = fields.hotel && fields.origin_known !== false ? fields.hotel as Hotel : null
  dailyWindows.forEach(window => {
    const saved = (fields.daily_windows as { day: number; start: number; end: number }[] | undefined)?.find(item => item.day === window.day)
    window.enabled = !!saved; window.start = saved ? timeText(saved.start) : '09:00'; window.end = saved ? timeText(saved.end) : '20:00'
  })
  selectMode.value = request.select_mode
  enableRepair.value = request.enable_repair
  useGuides.value = request.guide_mode === 'pilot'
  moreOpen.value = moreSelected.value.length > 0
  mealOpen.value = form.meal_budget_min !== '' || form.meal_budget_max !== ''
  step.value = 0
}, { immediate: true })


function toggle(list: string[], v: string) {
  const i = list.indexOf(v)
  if (i >= 0) list.splice(i, 1)
  else list.push(v)
}

// 窄屏上底部的票会挡住候选：点开搜索时把起点这一块滚到最上面
function showOrigin(event: FocusEvent) {
  if (!(event.target instanceof HTMLInputElement) || !matchMedia('(max-width: 900px)').matches) return
  (event.currentTarget as HTMLElement).scrollIntoView({ block: 'start', behavior: reducedMotion() ? 'instant' : 'smooth' })
}
// 提交后、等核对结果这段时间票根掀开；没登录时是去登录，不掀
const submitted = ref(false)
watch(() => props.loading, loading => { if (!loading) submitted.value = false })
const ticketNote = computed(() => {
  if (step.value < LAST) return `${step.value + 1} / ${steps.length} · 下一步：${steps[step.value + 1]!.label}`
  if (!props.quota) return `${LAST + 1} / ${steps.length}`
  return outOfQuota.value ? '今天的生成次数已用完' : `今天还可生成 ${props.quota.remaining}/${props.quota.limit} 次`
})

function submit() {
  if (step.value < LAST) { goStep(step.value + 1); return }
  formError.value = ''
  if (form.day_start && form.day_end && minutes(form.day_start) >= minutes(form.day_end)) { formError.value = '返回时间需要晚于出发时间。'; return }
  if (dailyWindows.some(window => window.enabled && window.day <= Number(form.days) && minutes(window.start) >= minutes(window.end))) { formError.value = '每天的返回时间需要晚于出发时间。'; return }
  if (mealBudgetError.value) { formError.value = mealBudgetError.value; return }
  if (budgetError.value) { goStep(2); return }
  const f: Record<string, unknown> = { city: props.city.key }
  if (form.days !== '') f.days = Number(form.days)
  if (form.budget !== '') f.budget = Number(form.budget)
  if (form.pace) f.pace = form.pace
  if (form.companions) f.companions = form.companions
  if (form.transport) f.transport = form.transport
  if (form.start_date) f.start_date = form.start_date
  if (form.day_start) f.day_start = minutes(form.day_start)
  if (form.day_end) f.day_end = minutes(form.day_end)
  if (form.max_walking_m !== '') f.max_walking_m = Number(form.max_walking_m)
  if (form.food_preference) f.food_preference = form.food_preference
  if (form.meal_budget_min !== '') f.meal_budget_min = Number(form.meal_budget_min)
  if (form.meal_budget_max !== '') f.meal_budget_max = Number(form.meal_budget_max)
  f.allow_taxi_fallback = form.allow_taxi_fallback
  if (form.include_evening) f.include_evening = form.include_evening === 'yes'
  f.daily_windows = dailyWindows.filter(window => window.enabled && form.days && window.day <= form.days)
    .map(window => ({ day: window.day, start: minutes(window.start), end: minutes(window.end) }))
  if (form.interests.length) f.interests = [...form.interests]
  if (form.spice !== 'any') f.spice = form.spice
  if (form.familiarity !== 'first') f.familiarity = form.familiarity
  if (form.must_visit.trim()) f.must_visit = form.must_visit.split(/[,，、\n]/).map(name => name.trim()).filter(Boolean)
  if (form.avoid.trim()) f.avoid = form.avoid.split(/[,，、\n]/).map(name => name.trim()).filter(Boolean)
  submitted.value = !props.needsLogin
  const h = origin.value
  if (h) f.hotel = { name: h.name, lat: h.lat, lon: h.lon }
  emit('submit', { query: query.value, form: f, select_mode: selectMode.value, enable_repair: enableRepair.value, guide_mode: useGuides.value && selectMode.value === 'llm' ? 'pilot' : 'off' })
}
</script>

<template>
  <form class="tform" :data-direction="stepDirection" @submit.prevent="submit">
    <div ref="questionBody" class="tform-left">
      <header class="tform-head">
        <ol class="tform-steps" aria-label="规划步骤"><li v-for="(item, index) in steps" :key="item.label" :class="{ active: step === index, complete: step > index }"><button type="button" :disabled="loading" :aria-current="step === index ? 'step' : undefined" @click="goStep(index)">{{ item.label }}</button></li></ol>
        <h2 ref="stepHeading" tabindex="-1">{{ headings[step] }}</h2>
      </header>
      <fieldset v-show="step === 0" :disabled="loading" class="tstep" :class="{ entering: step === 0 }"><legend class="sr-only">游玩偏好</legend>
        <div class="tfield">
          <p class="tlabel">想做些什么<small>可多选</small></p>
          <div class="pill-row"><button v-for="interest in visibleInterests" :key="interest.value" type="button" class="pill" :class="{ on: form.interests.includes(interest.value) }" :aria-pressed="form.interests.includes(interest.value)" :disabled="loading" @click="toggle(form.interests, interest.value)"><span class="pill-icon" aria-hidden="true"><component :is="interest.icon" :size="20" class="pill-glyph" /><DrawCheck v-if="form.interests.includes(interest.value)" :size="17" :weight="1.8" /></span>{{ interest.value }}</button><button v-if="moreInterests.length" type="button" class="pill plain" :aria-expanded="moreOpen" :disabled="loading" @click="moreOpen = !moreOpen"><PhPlus :size="17" aria-hidden="true" />{{ moreOpen ? '收起' : moreSelected.length ? `更多 · 已选 ${moreSelected.length}` : '更多' }}</button></div>
          <Transition :css="false" v-bind="expandHooks"><div v-if="moreOpen" class="tfold"><div class="pill-row"><button v-for="interest in moreInterests" :key="interest" type="button" class="pill" :class="{ on: form.interests.includes(interest) }" :aria-pressed="form.interests.includes(interest)" :disabled="loading" @click="toggle(form.interests, interest)"><span class="pill-icon" aria-hidden="true"><PhCompass :size="20" class="pill-glyph" /><DrawCheck v-if="form.interests.includes(interest)" :size="17" :weight="1.8" /></span>{{ interest }}</button></div></div></Transition>
        </div>
        <div class="tfield"><p class="tlabel">游玩节奏<small>{{ paceHint }}</small></p><DetentSlider v-model="form.pace" :options="paceOptions" label="游玩节奏" :value-text="paceHint ? `${paceLabel}，${paceHint}` : paceLabel" :disabled="loading" dense /></div>
        <div class="tpair short-long">
          <div class="tfield"><p class="tlabel">市内出行</p><SegControl v-model="form.transport" :options="transportOptions" label="市内出行" :disabled="loading" /></div>
          <div class="tfield"><p class="tlabel">夜间安排</p><SegControl v-model="form.include_evening" :options="eveningOptions" label="夜间安排" :disabled="loading" /></div>
        </div>
      </fieldset>
      <fieldset v-show="step === 1" :disabled="loading" class="tstep" :class="{ entering: step === 1 }"><legend class="sr-only">餐饮与预算</legend>
        <div class="tfield">
          <p class="tlabel">人均总预算<small>选填</small><input v-model.number="form.budget" class="tinput tinput-mini" aria-label="人均总预算" type="number" min="1" max="100000" placeholder="自己填 ¥" :disabled="loading" /></p>
          <DetentSlider v-model="form.budget" :options="budgetOptions" label="人均总预算" :value-at="budgetAt" :format="value => `¥${value}`" :value-text="form.budget === '' ? '不限' : `${form.budget} 元`" :disabled="loading" mono />
          <p class="thelp" :class="{ bad: budgetError }">{{ budgetError ? '总预算需在 1–100000 元之间，不确定时请留空。' : `含门票、餐饮、市内交通；不含住宿与往返${city.name}交通。` }}</p>
        </div>
        <div class="tfield"><p class="tlabel">怎么吃</p><SegControl v-model="form.food_preference" :options="foodOptions" label="怎么吃" :disabled="loading" /></div>
        <div class="tfield"><p class="tlabel">口味</p><SegControl v-model="form.spice" :options="spiceOptions" label="口味" :disabled="loading" /></div>
        <div class="tfield">
          <button type="button" class="pill plain flush" :aria-expanded="mealOpen" :disabled="loading" @click="mealOpen = !mealOpen"><PhPlus :size="17" aria-hidden="true" />单独设置每餐预算</button>
          <Transition :css="false" v-bind="expandHooks"><div v-if="mealOpen" class="tfold"><div class="tpair"><label class="tfield"><span class="tlabel">每餐最少</span><input v-model.number="form.meal_budget_min" class="tinput" type="number" aria-label="每餐预算下限" min="0" max="2000" placeholder="¥" /></label><label class="tfield"><span class="tlabel">每餐最多</span><input v-model.number="form.meal_budget_max" class="tinput" type="number" aria-label="每餐预算上限" min="1" max="2000" placeholder="¥" /></label></div><p v-if="mealBudgetError" class="thelp bad" role="alert">{{ mealBudgetError }}</p></div></Transition>
        </div>
      </fieldset>
      <fieldset v-show="step === 2" :disabled="loading" class="tstep dense" :class="{ entering: step === 2 }"><legend class="sr-only">出行与补充</legend>
        <div class="tfield"><p class="tlabel">玩几天</p><SegControl v-model="form.days" :options="dayOptions" label="玩几天" :disabled="loading" /></div>
        <div class="tfield"><p class="tlabel">和谁同行</p><SegControl v-model="form.companions" :options="companionOptions" label="和谁同行" :disabled="loading" /></div>
        <div class="tpair">
          <div class="tfield"><p class="tlabel">对这座城市</p><SegControl v-model="form.familiarity" :options="familiarityOptions" label="对这座城市" :disabled="loading" /></div>
          <div class="tfield"><p class="tlabel">出游日期<small>选填</small></p><input v-model="form.start_date" class="tinput" :class="{ blank: !form.start_date }" aria-label="出游日期" type="date" /></div>
        </div>
        <div class="tfield tfield-origin" @focusin="showOrigin"><p id="origin-label" class="tlabel">每天的起点与终点</p><OriginPicker :key="city.key" v-model="origin" :city="city.key" :presets="originPresets" :disabled="loading" compact /></div>
        <div class="tfield"><label for="trip-query" class="tlabel">补充说明<small>选填，必去、不想去的地方也写在这里</small></label><textarea id="trip-query" v-model="query" class="tinput" rows="2" :placeholder="queryPlaceholder" /></div>
        <div class="tlinks"><QuestionOptions title="活动时段与步行" trigger="活动时段与步行"><div class="question-advanced-fields"><div class="pref-grid"><label><span class="field-label">每天出发</span><input v-model="form.day_start" type="time" /></label><label><span class="field-label">最晚返回</span><input v-model="form.day_end" type="time" /></label></div><label><span class="field-label">每日步行上限</span><select v-model="form.max_walking_m"><option value="">按同行人建议</option><option :value="3000">3 公里</option><option :value="5000">5 公里</option><option :value="8000">8 公里</option><option :value="12000">12 公里</option></select></label><label class="check-row"><input v-model="form.allow_taxi_fallback" type="checkbox" />公交太慢或没有时，允许改打车</label><details v-if="form.days" class="daily-windows"><summary>分别设置每天时段</summary><div v-for="window in dailyWindows.slice(0, Number(form.days))" :key="window.day" class="day-window-row"><label class="check-row"><input v-model="window.enabled" type="checkbox" />第 {{ window.day }} 天</label><input v-model="window.start" type="time" :aria-label="`第 ${window.day} 天出发`" :disabled="!window.enabled" /><input v-model="window.end" type="time" :aria-label="`第 ${window.day} 天返回`" :disabled="!window.enabled" /></div></details></div></QuestionOptions><QuestionOptions title="实验选项（论文对比用）" trigger="规划设置"><div class="question-advanced-fields"><label class="check-row"><input v-model="selectMode" type="radio" value="llm" />大模型选点（附推荐理由）</label><label class="check-row"><input v-model="selectMode" type="radio" value="jev" />Jev 快速选点（试验）</label><label class="check-row"><input v-model="selectMode" type="radio" value="rule" />纯规则打分选点</label><label class="check-row"><input v-model="enableRepair" type="checkbox" />校验未通过时自动修复</label><label class="check-row"><input v-model="useGuides" :disabled="selectMode !== 'llm'" type="checkbox" />参考公开攻略选点（试验）</label><p class="field-help">Jev 批量评估候选，推荐说明来自地点数据与规则；失败回退原模型。试验功能尚未完成用户盲评。</p></div></QuestionOptions></div>
      </fieldset>
    </div>
    <TripTicket :city="city" :cells="ticketCells" :rows="ticketRows" :date="ticketDate" :summary="selectedSummary" :note="ticketNote" :error="formError" :tearing="submitted && loading" :departing="!!departing">
      <button v-if="step > 0" type="button" class="ticket-back" :disabled="loading" @click="goStep(step - 1)">上一步</button>
      <button type="submit" class="ticket-go" :disabled="loading || !meta || (step === LAST && (budgetError || outOfQuota))">{{ submitted && loading ? '正在核对…' : step !== LAST ? '继续' : needsLogin ? '登录并生成' : '生成行程' }}<PhArrowRight :size="18" aria-hidden="true" /></button>
    </TripTicket>
  </form>
</template>
