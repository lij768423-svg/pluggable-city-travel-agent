// “我是这么理解的”上的修改：先攒成草稿，一次提交（2026-10-28 用户：不要改一个就调整）。
// 确认表（UnderstandingTable）和「跟 AI 说」的行程票、选项托盘共用这一份；跟 AI 说把草稿合成一句话发出去，
// 列表类整份说“改成”，免得被理解成追加。
import { computed, ref, watch } from 'vue'
import type { Hotel, Meta, Understanding, UnderstandingField, UnderstandingIssue } from './api'

export const SOURCE = { said: '你说的', form: '你填的', inferred: '推断', default: '默认' } as const
export const TRANSPORT: Record<string, string> = { taxi: '打车', transit: '公交地铁' }
export const FOOD: Record<string, string> = { balanced: '顺路就好', local: '当地特色', quick: '快捷简餐', quality: '品质正餐' }
const LISTS = { must_visit: '必去', avoid: '不去' } as const
export type ListKey = keyof typeof LISTS
const LABELS: Record<string, string> = { transport: '出行', companions: '同行', familiarity: '对这座城市', pace: '节奏', spice: '口味' }
// 解析认得、表里没列的条件；每餐预算和活动时段各由两个字段组成
export const EXTRAS = [
  { key: 'evening', label: '夜间', fields: ['include_evening'] },
  { key: 'food', label: '怎么吃', fields: ['food_preference'] },
  { key: 'meal', label: '每餐预算', fields: ['meal_budget_min', 'meal_budget_max'] },
  { key: 'window', label: '活动时段', fields: ['day_start', 'day_end'] },
  { key: 'walk', label: '步行上限', fields: ['max_walking_m'] },
  { key: 'fallback', label: '公交不便', fields: ['allow_taxi_fallback'] },
] as const
export type ExtraKey = typeof EXTRAS[number]['key']
export const clock = (minutes: unknown) => typeof minutes === 'number' ? `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}` : ''
export const minutesOf = (text: string) => text ? Number(text.split(':')[0]) * 60 + Number(text.split(':')[1]) : null

// 这些条件的短文字；get 取某一份画像（或带草稿）的字段
export function extraTextFrom(get: (field: string) => unknown, key: ExtraKey): string {
  if (key === 'evening') return get('include_evening') === false ? '早点休息' : '顺路安排'
  if (key === 'food') return FOOD[String(get('food_preference'))] ?? FOOD.balanced!
  if (key === 'fallback') return get('allow_taxi_fallback') === false ? '也不打车' : '可以打车'
  if (key === 'walk') return get('max_walking_m') ? `${Number(get('max_walking_m')) / 1000} 公里` : '按同行人'
  if (key === 'meal') {
    const [low, high] = [get('meal_budget_min'), get('meal_budget_max')]
    return low && high ? `¥${low}–${high}` : high ? `¥${high} 以内` : low ? `¥${low} 起` : '不限'
  }
  const [start, end] = [clock(get('day_start')), clock(get('day_end'))]
  return start && end ? `${start}–${end}` : start ? `${start} 起` : end ? `到 ${end}` : '不限'
}

export type UnderstandingRow = UnderstandingField | { key: ListKey; label: string; display: string; source: null; note: string }
export type UnderstandingDraft = ReturnType<typeof useUnderstandingDraft>

export function useUnderstandingDraft(understanding: () => Understanding | null, meta: () => Meta | null) {
  // 没说必去、不去时服务端不列这两行；这里补成“没有”，好让用户能加（顺序固定：必去在前）
  const rows = computed<UnderstandingRow[]>(() => {
    const fields = understanding()?.fields ?? []
    return [...fields.filter(field => !(field.key in LISTS)),
      ...(Object.keys(LISTS) as ListKey[]).map(key => fields.find(field => field.key === key) ?? { key, label: LISTS[key], display: '没有', source: null, note: '' })]
  })
  const row = (key: string) => rows.value.find(item => item.key === key)

  // 还没提交的修改；显示的都是“改过的值，没改就用理解出来的值”
  const draft = ref<Record<string, unknown>>({})
  const profile = (key: string) => understanding()?.profile[key]
  const changed = (key: string) => key in draft.value
  const current = (key: string) => (changed(key) ? draft.value[key] : profile(key))
  const value = (key: string) => current(key) as string | number | null | undefined
  const list = (key: string) => (current(key) as string[] | undefined) ?? []
  const same = (a: unknown, b: unknown) => JSON.stringify(a ?? null) === JSON.stringify(b ?? null)
  const guessed = (key: string) => ['inferred', 'default'].includes(row(key)?.source ?? '')

  function set(key: string, next: unknown) {
    const rest = { ...draft.value }
    delete rest[key]
    // 改回原样就不算改
    draft.value = same(next, profile(key)) ? rest : { ...rest, [key]: next }
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

  function phrase(key: string, next: unknown): string {
    if (key === 'days') return `玩 ${next} 天`
    if (key === 'budget') return `预算改成人均共 ${next} 元`
    if (key === 'start_date') return next ? `日期改成 ${next}` : '日期还没定'
    if (key === 'hotel') return next ? `从${(next as Hotel).name}出发` : '起点不定，不用回起点'
    const names = next as string[]
    if (key === 'interests') return names.length ? `想玩的改成：${names.join('、')}` : '想玩的不限，综合推荐'
    if (key === 'must_visit') return names.length ? `一定要去的改成：${names.join('、')}` : '没有一定要去的地方了'
    if (key === 'avoid') return names.length ? `不想去的改成：${names.join('、')}` : '没有不想去的地方了'
    const labels: Record<string, Record<string, string> | undefined> = { transport: TRANSPORT, companions: meta()?.companions,
      familiarity: meta()?.familiarity_levels, pace: meta()?.paces, spice: meta()?.spice_levels }
    return `${LABELS[key] ?? key}改成${labels[key]?.[next as string] ?? next}`
  }
  function extraPhrase(key: ExtraKey): string {
    if (key === 'evening') return current('include_evening') === false ? '晚上不安排夜游' : '晚上顺路安排夜游'
    if (key === 'food') return value('food_preference') === 'balanced' ? '餐饮偏好改成均衡' : `餐饮偏好改成${FOOD[String(value('food_preference'))]}`
    if (key === 'fallback') return current('allow_taxi_fallback') === false ? '公交不方便也不要打车' : '公交不方便时可以改打车'
    if (key === 'walk') return value('max_walking_m') ? `每天步行最多 ${value('max_walking_m')} 米` : '每天步行不设上限'
    if (key === 'meal') {
      const [low, high] = [value('meal_budget_min'), value('meal_budget_max')]
      return low && high ? `每餐预算改成 ${low} 到 ${high} 元` : high ? `每餐预算最多 ${high} 元` : low ? `每餐预算至少 ${low} 元` : '每餐预算不限'
    }
    const [start, end] = [clock(value('day_start')), clock(value('day_end'))]
    return start && end ? `每天 ${start} 开始活动，${end} 结束` : start ? `每天 ${start} 开始活动` : end ? `每天 ${end} 前结束` : '每天的活动时间不限'
  }
  const extraChanged = (key: ExtraKey) => EXTRAS.find(item => item.key === key)!.fields.some(changed)
  // 按表格顺序出句子，同一份修改每次说法一样
  const phrases = computed(() => [...rows.value.filter(item => changed(item.key)).map(item => phrase(item.key, draft.value[item.key])),
    ...EXTRAS.filter(item => extraChanged(item.key)).map(item => extraPhrase(item.key))])

  function discard() { draft.value = {} }
  // 重新理解后旧的草稿作废
  watch(understanding, discard)

  // 预算是整趟的人均总额。没说预算时它随天数估（每天同样多），所以只改了天数时先按新天数算给用户看
  const budgetShown = computed(() => {
    const [budget, days] = [Number(profile('budget')), Number(profile('days'))]
    if (changed('budget') || row('budget')?.source !== 'default' || !changed('days')) return Number(value('budget'))
    return Math.round(budget / days * Number(value('days')))
  })
  const perDay = computed(() => Math.round(budgetShown.value / Number(value('days') || 1)))

  const originKnown = computed(() => profile('origin_known') !== false)
  // 当前选中的起点；null 表示“不定”（没定起点时默认点不算起点）
  const hotelPicked = computed<Hotel | null>(() => {
    if (changed('hotel')) return draft.value.hotel && (originKnown.value || !same(draft.value.hotel, profile('hotel'))) ? draft.value.hotel as Hotel : null
    return originKnown.value ? (profile('hotel') as Hotel | undefined) ?? null : null
  })
  const hotelPresets = computed(() => (meta()?.city === profile('city') ? meta()?.hotels : []) ?? [])
  // 起点组件里选了地点（常用起点或高德搜到的）或选回“不定”
  function pickOrigin(place: Hotel | null) {
    if (!place) set('hotel', originKnown.value ? null : profile('hotel'))
    else set('hotel', { name: place.name, lat: place.lat, lon: place.lon })
  }

  // 一键修改也只是先放进草稿，和别的修改一起提交
  const fixed = (fix: UnderstandingIssue['fixes'][number]) => Object.entries(fix.patch).every(([key, next]) => changed(key) && same(draft.value[key], next))
  function applyFix(fix: UnderstandingIssue['fixes'][number]) {
    for (const [key, next] of Object.entries(fix.patch)) set(key, next)
  }

  // 票面上写的短文字
  function text(key: string): string {
    if (key === 'days') return `${value('days')} 天`
    if (key === 'budget') return `¥${budgetShown.value}`
    if (key === 'start_date') return value('start_date') ? String(value('start_date')).slice(5).replace('-', '.') : '没定'
    if (key === 'hotel') return hotelPicked.value?.name ?? '不定'
    if (key === 'interests') return list('interests').join('、') || '综合推荐'
    if (key === 'must_visit' || key === 'avoid') return list(key).join('、') || '没有'
    const labels: Record<string, Record<string, string> | undefined> = { transport: TRANSPORT, companions: meta()?.companions,
      familiarity: meta()?.familiarity_levels, pace: meta()?.paces, spice: meta()?.spice_levels }
    return labels[key]?.[String(value(key))] ?? String(value(key) ?? '')
  }
  const extraText = (key: ExtraKey) => extraTextFrom(current, key)
  // 跟默认不同的才写到票上
  const extraSet = (key: ExtraKey) => ({ evening: current('include_evening') === false, food: !!value('food_preference') && value('food_preference') !== 'balanced',
    fallback: current('allow_taxi_fallback') === false, walk: !!value('max_walking_m'), meal: !!value('meal_budget_min') || !!value('meal_budget_max'),
    window: value('day_start') != null || value('day_end') != null })[key]

  return { rows, row, draft, changed, current, value, list, guessed, set, change, phrases, extraChanged, extraText, extraSet, discard, budgetShown, perDay,
    hotelPicked, hotelPresets, pickOrigin, fixed, applyFix, text }
}
