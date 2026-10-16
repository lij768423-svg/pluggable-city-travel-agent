<script setup lang="ts">
import { computed, nextTick, reactive, ref, watch } from 'vue'
import { PhArrowRight, PhCheck, PhMountains, PhBuildings, PhCamera, PhLeaf, PhCoffee, PhSparkle, PhMoon, PhForkKnife, PhBank, PhPersonSimpleWalk, PhCompass, PhLightning, PhFish, PhFlame, PhStar, PhBowlFood } from '@phosphor-icons/vue'
import type { Meta, PlanRequest, Hotel } from '../api'
import OriginPicker from './OriginPicker.vue'
import QuestionOptions from './QuestionOptions.vue'
import QuestionnaireArt from './QuestionnaireArt.vue'

const props = defineProps<{ meta: Meta | null; loading: boolean; initialQuery?: string; initialRequest?: PlanRequest | null; quota?: { limit: number; remaining: number } | null; needsLogin?: boolean }>()
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
  dietary: [] as string[],
  must_visit: '',
  avoid: '',
  transport: 'transit' as '' | 'taxi' | 'transit',
  start_date: '',
  day_start: '',
  day_end: '',
  max_walking_m: '' as '' | number,
  allow_taxi_fallback: false,
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
  { label: '游玩偏好', hint: '兴趣、节奏、出行与夜间' },
  { label: '餐饮与预算', hint: '预算、口味、忌口' },
  { label: '出行与补充', hint: '天数、同行、起点、必去' },
]
const LAST = steps.length - 1
const paceLabels: Record<string, string> = { relaxed: '慢慢逛', normal: '刚刚好', intense: '多看一点' }
const paceLabel = computed(() => paceLabels[form.pace] || '自由节奏')
const headings = ['这趟旅行，想怎么过？', '吃得合口味，花得有数。', '最后，把出发的事定下来。']
const dayOptions = [...[1, 2, 3, 4, 5].map(day => ({ value: String(day), label: `${day} 天`, short: `${day} 天` })), { value: '', label: '按描述安排', short: '按描述' }]
const companionOptions = computed(() => [{ value: '', label: '不限同行人', short: '不限' }, ...Object.entries(props.meta?.companions || {}).map(([value, label]) => ({ value, label, short: label }))])
const foodOptions = [{ value: '', label: '顺路就好' }, { value: 'local', label: '当地特色' }, { value: 'quick', label: '快捷简餐' }, { value: 'quality', label: '品质正餐' }]
const selectedSummary = computed(() => {
  const companions = companionOptions.value.find(option => option.value === form.companions)?.label
  const parts = [form.days ? `${form.days} 天` : '按描述安排', companions, paceLabel.value]
  if (form.interests.length) parts.push(form.interests.join('、'))
  if (form.budget !== '') parts.push(`预算 ¥${form.budget} / 人`)
  if (form.dietary.length) parts.push(form.dietary.join('、'))
  if (origin.value) parts.push(`从${origin.value.name}出发`)
  return parts.filter(Boolean).join(' · ')
})
const interestTiles = [
  { value: '自然风光', icon: PhMountains },
  { value: '人文历史', icon: PhBuildings },
  { value: '拍照出片', icon: PhCamera },
  { value: '园林古镇', icon: PhLeaf },
  { value: '茶文化', icon: PhCoffee },
  { value: '亲子游乐', icon: PhSparkle },
  { value: '博物馆展览', icon: PhBank },
  { value: '夜景夜游', icon: PhMoon },
  { value: '美食探店', icon: PhForkKnife },
]
const visibleInterests = computed(() => interestTiles.filter(item => props.meta?.interests.includes(item.value)))
const moreInterests = computed(() => (props.meta?.interests || []).filter(value => !interestTiles.some(item => item.value === value)))
const moreSelected = computed(() => form.interests.filter(value => moreInterests.value.includes(value)))
const paceOptions = [
  { value: 'relaxed', label: '慢慢逛', hint: '轻松，留白多', icon: PhCoffee },
  { value: 'normal', label: '刚刚好', hint: '游玩与休息兼顾', icon: PhPersonSimpleWalk },
  { value: 'intense', label: '多看一点', hint: '充实紧凑', icon: PhLightning },
]
const dietaryIcons = { '不吃辣': PhFlame, '只吃中餐': PhBowlFood, '清真': PhStar, '不吃海鲜': PhFish }
const formError = ref('')
function goStep(target: number) {
  formError.value = ''
  if (target > step.value && step.value === 1 && budgetError.value) { formError.value = '请填写 1–100000 元的总预算，或留空。'; return }
  if (target > step.value && step.value === 1 && mealBudgetError.value) { formError.value = mealBudgetError.value; return }
  stepDirection.value = target >= step.value ? 'forward' : 'backward'
  step.value = target
  nextTick(() => {
    questionBody.value?.scrollTo({ top: 0, behavior: 'instant' })
    stepHeading.value?.focus({ preventScroll: true })
    if (stepHeading.value && stepHeading.value.getBoundingClientRect().top < 0) stepHeading.value.scrollIntoView({ block: 'start', behavior: 'instant' })
  })
}
const timeText = (value: unknown) => typeof value === 'number' ? `${Math.floor(value / 60).toString().padStart(2, '0')}:${(value % 60).toString().padStart(2, '0')}` : ''
watch(() => props.initialRequest, request => {
  if (!request) return
  query.value = request.query
  const fields = request.form
  Object.assign(form, { days: 2, budget: '', pace: 'normal', companions: '', interests: [], dietary: [], must_visit: '', avoid: '', transport: 'transit', start_date: '', day_start: '', day_end: '', max_walking_m: '', allow_taxi_fallback: false, include_evening: '', food_preference: '', meal_budget_min: '', meal_budget_max: '' })
  for (const key of Object.keys(form)) {
    if (fields[key] !== undefined && !['must_visit', 'avoid', 'day_start', 'day_end', 'include_evening'].includes(key)) Object.assign(form, { [key]: fields[key] })
  }
  const carried = [Array.isArray(fields.must_visit) && fields.must_visit.length ? `一定要去${fields.must_visit.join('、')}` : '',
    Array.isArray(fields.avoid) && fields.avoid.length ? `不去${fields.avoid.join('、')}` : ''].filter(Boolean).join('；')
  if (carried && !query.value.includes(carried)) query.value = [query.value.trim(), carried].filter(Boolean).join('；')
  form.must_visit = ''
  form.avoid = ''
  form.day_start = timeText(fields.day_start)
  form.day_end = timeText(fields.day_end)
  form.include_evening = fields.include_evening === undefined ? '' : fields.include_evening ? 'yes' : 'no'
  origin.value = fields.hotel ? fields.hotel as Hotel : props.meta?.hotels[0] || null
  dailyWindows.forEach(window => {
    const saved = (fields.daily_windows as { day: number; start: number; end: number }[] | undefined)?.find(item => item.day === window.day)
    window.enabled = !!saved; window.start = saved ? timeText(saved.start) : '09:00'; window.end = saved ? timeText(saved.end) : '20:00'
  })
  selectMode.value = request.select_mode
  enableRepair.value = request.enable_repair
  useGuides.value = request.guide_mode === 'pilot'
  step.value = 0
}, { immediate: true })


function toggle(list: string[], v: string) {
  const i = list.indexOf(v)
  if (i >= 0) list.splice(i, 1)
  else list.push(v)
}

function submit() {
  if (step.value < LAST) { goStep(step.value + 1); return }
  formError.value = ''
  if (form.day_start && form.day_end && minutes(form.day_start) >= minutes(form.day_end)) { formError.value = '返回时间需要晚于出发时间。'; return }
  if (dailyWindows.some(window => window.enabled && window.day <= Number(form.days) && minutes(window.start) >= minutes(window.end))) { formError.value = '每天的返回时间需要晚于出发时间。'; return }
  if (mealBudgetError.value) { formError.value = mealBudgetError.value; return }
  if (!origin.value) { formError.value = '先选择一个出发地点。'; return }
  if (budgetError.value) return
  const f: Record<string, unknown> = {}
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
  if (form.dietary.length) f.dietary = [...form.dietary]
  if (form.must_visit.trim()) f.must_visit = form.must_visit.split(/[,，、\n]/).map(name => name.trim()).filter(Boolean)
  if (form.avoid.trim()) f.avoid = form.avoid.split(/[,，、\n]/).map(name => name.trim()).filter(Boolean)
  const h = origin.value
  if (h) f.hotel = { name: h.name, lat: h.lat, lon: h.lon }
  emit('submit', { query: query.value, form: f, select_mode: selectMode.value, enable_repair: enableRepair.value, guide_mode: useGuides.value && selectMode.value === 'llm' ? 'pilot' : 'off' })
}
</script>

<template>
  <form class="plan-form questionnaire qform" @submit.prevent="submit">
    <div class="qform-card" :data-direction="stepDirection">
      <ol class="qform-steps" aria-label="规划步骤">
        <li v-for="(item, index) in steps" :key="item.label" :class="{ active: step === index, complete: step > index }"><button type="button" :disabled="index > step || loading" :aria-current="step === index ? 'step' : undefined" @click="goStep(index)"><span class="qform-num"><PhCheck v-if="step > index" :size="12" /><template v-else>{{ index + 1 }}</template></span><em>{{ item.label }}</em></button></li>
      </ol>
      <header class="qform-head"><QuestionnaireArt :step="[1, 2, 0][step] ?? 0" /><h2 ref="stepHeading" tabindex="-1">{{ headings[step] }}</h2><div class="qform-track" aria-hidden="true"><span :style="{ transform: `scaleX(${(step + 1) / steps.length})` }" /></div></header>
      <div ref="questionBody" class="qform-body" :data-step="step">
        <fieldset v-show="step === 0" :disabled="loading" class="qstep" :class="{ entering: step === 0 }"><legend class="sr-only">游玩偏好</legend>
          <div class="qfield">
            <div class="interest-heading"><span class="field-label">想做些什么 <span class="optional">可多选</span></span><QuestionOptions title="更多兴趣" :trigger="moreSelected.length ? `更多兴趣 · 已选 ${moreSelected.length}` : '更多兴趣'">
              <div class="more-interest-grid"><button v-for="interest in moreInterests" :key="interest" type="button" class="interest-tile" :class="{ on: form.interests.includes(interest) }" :aria-pressed="form.interests.includes(interest)" :disabled="loading" @click="toggle(form.interests, interest)"><PhCompass :size="22" aria-hidden="true" /><span>{{ interest }}</span><PhCheck v-if="form.interests.includes(interest)" class="tile-check" :size="12" aria-hidden="true" /></button></div>
            </QuestionOptions></div>
            <div class="interest-tile-grid"><button v-for="interest in visibleInterests" :key="interest.value" type="button" class="interest-tile" :class="{ on: form.interests.includes(interest.value) }" :aria-pressed="form.interests.includes(interest.value)" :disabled="loading" @click="toggle(form.interests, interest.value)"><component :is="interest.icon" :size="24" aria-hidden="true" /><span>{{ interest.value }}</span><PhCheck v-if="form.interests.includes(interest.value)" class="tile-check" :size="12" aria-hidden="true" /></button></div>
          </div>
          <div class="qfield"><span class="field-label">游玩节奏</span><div class="pace-tile-grid"><button v-for="pace in paceOptions" :key="pace.value" type="button" class="pace-tile" :class="{ on: form.pace === pace.value }" :aria-pressed="form.pace === pace.value" :disabled="loading" @click="form.pace = pace.value"><component :is="pace.icon" :size="22" aria-hidden="true" /><span><strong>{{ pace.label }}</strong><small>{{ pace.hint }}</small></span></button></div></div>
          <div class="qfield qfield-half"><div><span class="field-label">市内出行</span><div class="pick-row"><button type="button" class="pick" :aria-pressed="form.transport === 'transit'" :class="{ on: form.transport === 'transit' }" :disabled="loading" @click="form.transport = 'transit'">公交地铁</button><button type="button" class="pick" :aria-pressed="form.transport === 'taxi'" :class="{ on: form.transport === 'taxi' }" :disabled="loading" @click="form.transport = 'taxi'">打车</button></div></div><div><span class="field-label">夜间安排</span><div class="pick-row"><button type="button" class="pick" :aria-pressed="form.include_evening === ''" :class="{ on: form.include_evening === '' }" :disabled="loading" @click="form.include_evening = ''">顺路就好</button><button type="button" class="pick" :aria-pressed="form.include_evening === 'yes'" :class="{ on: form.include_evening === 'yes' }" :disabled="loading" @click="form.include_evening = 'yes'">安排夜景</button><button type="button" class="pick" :aria-pressed="form.include_evening === 'no'" :class="{ on: form.include_evening === 'no' }" :disabled="loading" @click="form.include_evening = 'no'">早点休息</button></div></div></div>
        </fieldset>
        <fieldset v-show="step === 1" :disabled="loading" class="qstep" :class="{ entering: step === 1 }"><legend class="sr-only">餐饮与预算</legend>
          <div class="qfield"><span class="field-label">人均总预算 <span class="optional">选填</span></span><div class="pick-row"><button v-for="v in [300, 500, 800, 1200, 1800, 2500]" :key="v" type="button" class="pick" :aria-pressed="form.budget === v" :class="{ on: form.budget === v }" :disabled="loading" @click="form.budget = v">¥{{ v }}</button><input class="pick-input" aria-label="人均总预算" type="number" v-model.number="form.budget" min="1" max="100000" placeholder="自定义" :disabled="loading" /></div><p class="field-help" :class="{ 'budget-error': budgetError }">{{ budgetError ? '总预算需在 1–100000 元之间，不确定时请留空。' : '含门票、餐饮、市内交通；不含住宿与往返杭州交通。' }}</p></div>
          <div class="qfield"><span class="field-label">怎么吃</span><div class="pick-row"><button v-for="opt in foodOptions" :key="opt.value" type="button" class="pick" :aria-pressed="form.food_preference === opt.value" :class="{ on: form.food_preference === opt.value }" :disabled="loading" @click="form.food_preference = opt.value">{{ opt.label }}</button></div></div>
          <div class="qfield"><span class="field-label">饮食忌口 <span class="optional">可多选</span></span><div class="dietary-tile-grid"><button v-for="diet in meta?.dietary" :key="diet" type="button" class="dietary-tile" :class="{ on: form.dietary.includes(diet) }" :aria-pressed="form.dietary.includes(diet)" :disabled="loading" @click="toggle(form.dietary, diet)"><component :is="dietaryIcons[diet as keyof typeof dietaryIcons] || PhForkKnife" :size="25" aria-hidden="true" /><span>{{ diet }}</span><span class="dietary-check" aria-hidden="true"><PhCheck v-if="form.dietary.includes(diet)" :size="11" /></span></button></div></div>
          <div class="qfield"><QuestionOptions title="每餐预算" trigger="单独设置每餐预算" description="给午餐、晚餐留出合适的花费" art="meal"><div class="qfield"><span class="field-label">每餐预算下限</span><input type="number" v-model.number="form.meal_budget_min" aria-label="每餐预算下限" min="0" max="2000" placeholder="¥" /></div><div class="qfield"><span class="field-label">每餐预算上限</span><input type="number" v-model.number="form.meal_budget_max" aria-label="每餐预算上限" min="1" max="2000" placeholder="¥" /></div><p v-if="mealBudgetError" role="alert" class="notice bad">{{ mealBudgetError }}</p></QuestionOptions></div>
        </fieldset>
        <fieldset v-show="step === 2" :disabled="loading" class="qstep" :class="{ entering: step === 2 }"><legend class="sr-only">出行与补充</legend>
          <div class="qfield-half travel-picks">
            <div><span id="days-label" class="field-label">玩几天</span><div class="pick-row compact-picks" role="group" aria-labelledby="days-label"><button v-for="opt in dayOptions" :key="opt.value" type="button" class="pick" :class="{ on: String(form.days) === opt.value }" :aria-pressed="String(form.days) === opt.value" :disabled="loading" @click="form.days = opt.value ? Number(opt.value) : ''">{{ opt.short }}</button></div></div>
            <div><span id="companions-label" class="field-label">和谁同行</span><div class="pick-row compact-picks" role="group" aria-labelledby="companions-label"><button v-for="opt in companionOptions" :key="opt.value" type="button" class="pick" :class="{ on: form.companions === opt.value }" :aria-pressed="form.companions === opt.value" :disabled="loading" @click="form.companions = opt.value">{{ opt.short }}</button></div></div>
          </div>
          <div class="qfield-pair">
            <div class="qfield"><span class="field-label">出游日期 <span class="optional">选填</span></span><input v-model="form.start_date" aria-label="出游日期" type="date" class="qinput-date" /></div>
            <div class="qfield qfield-origin"><OriginPicker v-model="origin" :presets="meta?.hotels ?? []" :disabled="loading" /></div>
          </div>
          <div class="qfield"><label for="trip-query" class="field-label">补充说明 <span class="optional">选填 · 必去、不想去的地方也写在这里</span></label><textarea id="trip-query" v-model="query" rows="2" placeholder="一定要去、不想去的地方和其他要求都写在这里，例如：一定要去灵隐寺，不去宋城；想在西湖边喝杯咖啡。" /></div>
          <div class="qfield qfield-half"><QuestionOptions title="活动时段与步行" trigger="活动时段与步行" description="出发、返回时间与体力安排" art="time"><div class="question-advanced-fields"><div class="pref-grid"><label><span class="field-label">每天出发</span><input v-model="form.day_start" type="time" /></label><label><span class="field-label">最晚返回</span><input v-model="form.day_end" type="time" /></label></div><label><span class="field-label">每日步行上限</span><select v-model="form.max_walking_m"><option value="">按同行人建议</option><option :value="3000">3 公里</option><option :value="5000">5 公里</option><option :value="8000">8 公里</option><option :value="12000">12 公里</option></select></label><label class="check-row"><input v-model="form.allow_taxi_fallback" type="checkbox" />公交无方案时，允许改打车</label><details v-if="form.days" class="daily-windows"><summary>分别设置每天时段</summary><div v-for="window in dailyWindows.slice(0, Number(form.days))" :key="window.day" class="day-window-row"><label class="check-row"><input v-model="window.enabled" type="checkbox" />第 {{ window.day }} 天</label><input v-model="window.start" type="time" :aria-label="`第 ${window.day} 天出发`" :disabled="!window.enabled" /><input v-model="window.end" type="time" :aria-label="`第 ${window.day} 天返回`" :disabled="!window.enabled" /></div></details></div></QuestionOptions><QuestionOptions title="实验选项（论文对比用）" trigger="规划设置" description="选点方式与实验选项" art="settings"><div class="question-advanced-fields"><label class="check-row"><input v-model="selectMode" type="radio" value="llm" />大模型选点（附推荐理由）</label><label class="check-row"><input v-model="selectMode" type="radio" value="jev" />Jev 快速选点（试验）</label><label class="check-row"><input v-model="selectMode" type="radio" value="rule" />纯规则打分选点</label><label class="check-row"><input v-model="enableRepair" type="checkbox" />校验未通过时自动修复</label><label class="check-row"><input v-model="useGuides" :disabled="selectMode !== 'llm'" type="checkbox" />参考公开攻略选点（试验）</label><p class="field-help">Jev 批量评估候选，推荐说明来自地点数据与规则；失败回退原模型。试验功能尚未完成用户盲评。</p></div></QuestionOptions></div>
        </fieldset>
      </div>
      <p v-if="formError" class="notice bad" role="alert">{{ formError }}</p>
      <footer class="qform-foot"><div class="qform-recap"><span>已选</span><p :title="selectedSummary">{{ selectedSummary }}</p></div><div class="qform-actions"><span v-if="step === LAST && quota" class="qform-quota" :class="{ empty: outOfQuota }">{{ outOfQuota ? '今天的生成次数已用完' : `今天还可生成 ${quota.remaining}/${quota.limit} 次` }}</span><button v-if="step > 0" type="button" class="journey-secondary" :disabled="loading" @click="goStep(step - 1)">上一步</button><button type="submit" class="journey-primary" :disabled="loading || !meta || (step === LAST && (!origin || budgetError || outOfQuota))">{{ step !== LAST ? '继续' : needsLogin ? '登录并生成' : '生成行程' }}<PhArrowRight :size="18" /></button></div></footer>
    </div>
  </form>
</template>
