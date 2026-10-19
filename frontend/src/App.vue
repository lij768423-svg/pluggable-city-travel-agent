<script setup lang="ts">
import { computed, defineAsyncComponent, h, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { PhArrowLeft, PhCheck, PhCheckCircle, PhClockCounterClockwise, PhCopy, PhPencilSimple, PhSignOut, PhUserCircle, PhWarningCircle } from '@phosphor-icons/vue'
import { getMeta, postPlan, getNavigation, getPlaceDetails, getPlaceMedia, previewMeal, commitMeal, revertPlan, type MealEditPreview, type Hotel, type Meta, type PlanRequest, type PlanResponse, type NavigationResponse, type NavigationLeg, type PlaceDetail, type PlaceMedia, type PlanChange, type PlanItem, type MealAlternative, type MapFocus, type AltMarker } from './api'
import PlanForm from './components/PlanForm.vue'
import { getCurrentPlan } from './api'
import PlanProgress from './components/PlanProgress.vue'
import { advanceProgress, emptyProgress } from './planProgress'
import Timeline from './components/Timeline.vue'
import PlaceDrawer from './components/PlaceDrawer.vue'
import NavigationPanel from './components/NavigationPanel.vue'
import SwapConfirm from './components/SwapConfirm.vue'
import BrandMark from './components/BrandMark.vue'
import DayStrip from './components/DayStrip.vue'
import HistoryView from './components/HistoryView.vue'
import MCPConnect from './components/MCPConnect.vue'
import { getAccount, logoutAccount, getHistoryDetail, saveHistory, getLinkedTrip, ApiError, type AccountUser, type PlanQuota } from './api'
import './journey.css'
import './questionnaire.css'

const user = ref<AccountUser | null>(null)
const authReady = ref(false)
const accountOpen = ref(false)
const authReason = ref('')
// 公网：生成行程需要登录，未登录时先保留条件，登录后自动开始；quota 为当天剩余次数
const pendingRequest = ref<PlanRequest | null>(null)
const quota = ref<PlanQuota | null>(null)
// 公网开启“登录才能生成”时，未登录只显示登录页；启动时等账号与配置都读到再渲染，避免问卷先闪一下
const gated = computed(() => !!meta.value?.access?.login_to_plan && authReady.value && !user.value)
const booting = computed(() => !error.value && (!authReady.value || !meta.value))
async function refreshAccount() { const me = await getAccount(); user.value = me.user; quota.value = me.quota ?? null }
const historyOpen = ref(false)
const accountBusy = ref(false)
const historyBusy = ref(false)
const historyAfterLogin = ref(false)
const initialRequest = ref<PlanRequest | null>(null)
const outputText = ref('')
let identityRevision = 0
let outputTag = ''
// 模型思考中：只显示状态与字数，不显示思考内容
const modelThinking = ref<{ tag: string; started: number; chars: number } | null>(null)
// 思考内容只在生成页实时展示，且只保留最近 THINK_WINDOW 字
const THINK_WINDOW = 4000
const thinkText = ref('')
let outputAttempt = 0

const MapView = defineAsyncComponent({
  loader: () => import('./components/MapView.vue'),
  loadingComponent: { render: () => h('div', { class: 'map' }) },
  delay: 0,
})
const AmapView = defineAsyncComponent({
  loader: () => import('./components/AmapView.vue'),
  loadingComponent: { render: () => h('div', { class: 'map' }) },
  delay: 0,
})
const ReportPanel = defineAsyncComponent(() => import('./components/ReportPanel.vue'))
const QuestionnaireBackdrop = defineAsyncComponent(() => import('./components/QuestionnaireBackdrop.vue'))
const AuthView = defineAsyncComponent(() => import('./components/AuthView.vue'))

const meta = ref<Meta | null>(null)
const plan = ref<PlanResponse | null>(null)
const loading = ref(false)
const error = ref('')
const activeDay = ref(1)
const editing = ref(true)
const elapsed = ref(0)
const planProgress = ref(emptyProgress())
const progressVisible = ref(false)
const lastRequest = ref<PlanRequest | null>(null)
let planController: AbortController | null = null
const selectedIndex = ref<number | null>(null)
const selectedAlternative = ref<PlanItem | null>(null)
const selectedAlternativeParent = ref<number | null>(null) // 当前查看的备选属于哪一餐（当天 items 下标）
const expandedMeal = ref<number | null>(null) // 列表里展开了备选的那一餐
const placeMedia = ref<Record<string, PlaceMedia>>({})
const mediaLoading = ref(false)
let mediaController: AbortController | null = null
const swapTarget = ref<{ index: number; alternative: MealAlternative } | null>(null)
const swapOpen = ref(false)
const swapBusy = ref(false)
const swapError = ref('')
const mealPreview = ref<MealEditPreview | null>(null)
const previewLoading = ref(false)
const selfMeal = ref<'lunch' | 'dinner' | null>(null)
const selfMealCost = ref(80)
const swapContext = ref<{ planId: string; revision: number; day: number } | null>(null)
let swapPreviewController: AbortController | null = null
let swapPreviewSequence = 0
const toast = ref<{ text: string; warn: boolean; undoRevision: number | null } | null>(null)
let toastTimer: ReturnType<typeof setTimeout> | undefined
const focusRevision = ref(0)
const alternativeLoading = ref(false)
let alternativeController: AbortController | null = null
const mobileView = ref<'route' | 'map'>('route')
const copied = ref(false)
const previewHotel = ref<Hotel | null>(null)
const scroller = ref<HTMLElement>()
const amapFailed = ref(false)
const navigation = ref<NavigationResponse | null>(null)
const navigationLoading = ref(false)
const navigationError = ref('')
const placeDetails = ref<Record<string, PlaceDetail>>({})
const detailsLoading = ref(false)
const drawerOpen = ref(false)
const navigationCache = new Map<number, NavigationResponse>()
let extrasController: AbortController | null = null
// 有高德 Web端 key 时用高德 JS API；加载失败自动退回 Leaflet
const amapKey = computed(() => (!amapFailed.value && meta.value?.map?.provider === 'amap' ? meta.value.map.amap_js_key : null))
let timer: ReturnType<typeof setInterval> | undefined

const mcpConnectionId = new URLSearchParams(location.search).get('mcp_connect')
const mcpCompleted = location.hash === '#mcp-connected'
const initialQuery = new URLSearchParams(location.search).get('q') ?? ''
let pendingTrip = new URLSearchParams(location.search).get('trip')
async function resumeTripLink() {
  if (!pendingTrip) return
  if (!user.value) { openAuth('登录后查看这份行程。'); return }
  const id = pendingTrip; pendingTrip = null
  await openSaved(id, false, true)
}
const paceNames: Record<string, string> = { relaxed: '舒缓', normal: '均衡', intense: '充实' }
const showForm = computed(() => editing.value || !plan.value)
const poiCount = computed(() => plan.value?.days.reduce((count, day) => count + new Set(day.items.filter(item => item.kind === 'poi').map(item => item.visit_group || `poi:${item.ref_id}`)).size, 0) ?? 0)
const mealCount = computed(() => plan.value?.days.reduce((n, d) => n + d.items.filter(i => i.kind === 'meal').length, 0) ?? 0)
const onsiteMealCount = computed(() => plan.value?.days.reduce((count, day) => count + (day.reserved_meals || []).filter(slot => slot.inside_poi_id != null && slot.status !== 'self_arranged').length, 0) ?? 0)
const selfArrangedMealCount = computed(() => plan.value?.days.reduce((count, day) => count + (day.reserved_meals || []).filter(slot => slot.status === 'self_arranged').length, 0) ?? 0)
const currentDay = computed(() => plan.value?.days.find(d => d.day === activeDay.value))
const dayTravel = computed(() => currentDay.value ? currentDay.value.items.reduce((t, i) => t + i.travel_min, currentDay.value.return_travel_min) : 0)
const selectedItem = computed(() => selectedAlternative.value || (selectedIndex.value == null ? null : currentDay.value?.items[selectedIndex.value] || null))
const selectedDetail = computed(() => selectedItem.value ? placeDetails.value[`${selectedItem.value.kind}:${selectedItem.value.ref_id}`] || selectedItem.value.detail || null : null)
// 地图上标出"当前关注的那一餐"的备选：正在查看的备选 > 选中的餐厅 > 列表里展开的备选
const activeMealIndex = computed(() => {
  if (selectedAlternativeParent.value != null) return selectedAlternativeParent.value
  const index = selectedIndex.value
  const item = index == null ? null : currentDay.value?.items[index]
  if (index != null && item?.kind === 'meal' && item.alternatives?.length) return index
  return expandedMeal.value
})
const altMarkers = computed<AltMarker[]>(() => {
  const index = activeMealIndex.value
  const item = index == null || showForm.value ? null : currentDay.value?.items[index]
  return (item?.alternatives || []).flatMap((alternative, n) => alternative.lat != null && alternative.lon != null
    ? [{ ref_id: alternative.ref_id, name: alternative.name, label: `备${n + 1}`, lat: alternative.lat, lon: alternative.lon }] : [])
})
const altAnchor = computed(() => {
  const index = activeMealIndex.value
  const item = index == null ? null : currentDay.value?.items[index]
  return altMarkers.value.length && item?.lat != null && item.lon != null ? { lat: item.lat, lon: item.lon } : null
})
const selectedMedia = computed(() => selectedItem.value?.ref_id != null ? placeMedia.value[`${selectedItem.value.kind}:${selectedItem.value.ref_id}`] || null : null)
const swapFrom = computed(() => selfMeal.value ? '待确认的预留用餐' : swapTarget.value ? currentDay.value?.items[swapTarget.value.index]?.name || '' : '')
const swapMealKind = computed(() => selfMeal.value || (swapTarget.value ? currentDay.value?.items[swapTarget.value.index]?.meal || null : null))
const focusedPlace = computed<MapFocus | null>(() => {
  const item = selectedItem.value
  return !showForm.value && item?.lat != null && item.lon != null ? { name: item.name, lat: item.lat, lon: item.lon, alternative: !!item.is_alternative } : null
})

async function loadDayExtras(force = false) {
  extrasController?.abort()
  const controller = new AbortController()
  extrasController = controller
  const current = currentDay.value
  const result = plan.value
  if (!current || !result) return
  navigation.value = force ? null : navigationCache.get(current.day) || null
  navigationError.value = ''
  navigationLoading.value = !navigation.value
  const points = [{ ...result.profile.hotel_gcj }, ...current.items.map(item => ({ lat: item.lat!, lon: item.lon! })), { ...result.profile.hotel_gcj }]
  const legs: NavigationLeg[] = points.slice(1).map((destination, index) => ({
    origin: points[index]!, destination,
    mode: current.items[index]?.travel_mode || current.return_leg?.mode || (result.profile.transport === 'transit' ? '公交/地铁' : '打车'),
    departure: current.items[index] ? Math.max(0, current.items[index]!.start - current.items[index]!.travel_min) : Math.max(current.items.at(-1)?.end || 0, ...(current.reserved_meals || []).map(slot => slot.end)),
  }))
  const references = current.items.filter(item => item.ref_id != null && !placeDetails.value[`${item.kind}:${item.ref_id}`])
    .map(item => ({ kind: item.kind, id: item.ref_id! }))
  detailsLoading.value = references.length > 0
  await Promise.allSettled([
    (async () => {
      if (navigation.value) return
      try {
        if (!current.items.length) return
        const response = await getNavigation(current.date || null, legs, controller.signal)
        if (controller.signal.aborted) return
        navigation.value = response
        if (response.legs.every(leg => ['ready', 'same_place'].includes(leg.status))) navigationCache.set(current.day, response)
      } catch (failure) {
        if (!controller.signal.aborted) navigationError.value = (failure as Error).message || '路径查询失败'
      } finally { if (!controller.signal.aborted) navigationLoading.value = false }
    })(),
    (async () => {
      if (!references.length) return
      try {
        const response = await getPlaceDetails(references, controller.signal, plan.value?.plan_id)
        if (controller.signal.aborted) return
        for (const detail of response.places) placeDetails.value[`${detail.kind}:${detail.id}`] = detail
      } catch {
        if (!controller.signal.aborted) for (const item of current.items) if (item.detail) placeDetails.value[`${item.kind}:${item.ref_id}`] = { ...item.detail, rating_status: item.detail.rating != null ? 'ready' : 'unavailable' }
      } finally { if (!controller.signal.aborted) detailsLoading.value = false }
    })(),
  ])
}

watch([plan, activeDay], () => { void loadDayExtras() })

function scrollPanelTop() { scroller.value?.scrollTo({ top: 0 }) }

function pickDay(day: number) {
  drawerOpen.value = false
  clearAlternative()
  expandedMeal.value = null
  activeDay.value = day
  selectedIndex.value = null
  scrollPanelTop()
}

// 行程 → 地图：只移动地图，不动面板；地图 → 行程：面板内滚动到对应站点
function clearAlternative() {
  alternativeController?.abort()
  alternativeLoading.value = false
  selectedAlternative.value = null
  selectedAlternativeParent.value = null
}

function focusStop(index: number, fromMap = false, showMap = true) {
  clearAlternative()
  selectedIndex.value = index
  focusRevision.value++
  drawerOpen.value = false
  if (fromMap) {
    nextTick(() => document.getElementById(`stop-${activeDay.value}-${index}`)?.scrollIntoView({ block: 'nearest', behavior: 'smooth' }))
  } else if (showMap) {
    mobileView.value = 'map'
  }
}

function openStopDetails(index: number) {
  focusStop(index, false, false)
  drawerOpen.value = true
}

async function focusAlternative(index: number, alternative: MealAlternative, showDetails = false) {
  clearAlternative()
  selectedAlternativeParent.value = index
  selectedIndex.value = null
  const clock = (minutes: number) => `${String(Math.floor(minutes / 60)).padStart(2, '0')}:${String(minutes % 60).padStart(2, '0')}`
  selectedAlternative.value = {
    kind: 'meal', is_alternative: true, ref_id: alternative.ref_id, name: alternative.name,
    lat: alternative.lat, lon: alternative.lon, start: alternative.start, end: alternative.end,
    start_hm: clock(alternative.start), end_hm: clock(alternative.end), travel_min: 0, travel_mode: '',
    cost: alternative.price, type: alternative.cuisine, source: null, score: null,
    meal: currentDay.value?.items[index]?.meal || null, detail: alternative.detail,
    reason: `${alternative.cuisine} · 参考人均 ¥${alternative.price.toFixed(0)} · 绕路约 ${alternative.detour_min} 分钟${alternative.travel_source === 'estimate' ? '（估算）' : ''}`,
  }
  focusRevision.value++
  drawerOpen.value = showDetails
  if (!showDetails) { mobileView.value = 'map'; return }
  const key = `meal:${alternative.ref_id}`
  if (placeDetails.value[key] || alternative.detail?.rating_status === 'ready') return
  const controller = new AbortController()
  alternativeController = controller
  alternativeLoading.value = true
  try {
    const response = await getPlaceDetails([{ kind: 'meal', id: alternative.ref_id }], controller.signal, plan.value?.plan_id)
    if (!controller.signal.aborted) for (const detail of response.places) placeDetails.value[`meal:${detail.id}`] = detail
  } catch {
    if (!controller.signal.aborted && alternative.detail) placeDetails.value[key] = { ...alternative.detail, rating_status: 'unavailable' }
  } finally { if (!controller.signal.aborted) alternativeLoading.value = false }
}

// 地图上点了某个"备N"：选中它，并在列表里展开、滚动到这一家
function focusAlternativeFromMap(refId: number) {
  const index = activeMealIndex.value
  const alternative = index == null ? undefined : currentDay.value?.items[index]?.alternatives?.find(item => item.ref_id === refId)
  if (index == null || !alternative) return
  void focusAlternative(index, alternative)
  nextTick(() => {
    const element = document.getElementById(`alt-${activeDay.value}-${index}-${refId}`)
    const details = element?.closest('details')
    if (details && !details.open) details.open = true
    element?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
  })
}

function onAlternativesToggle(index: number, open: boolean) {
  if (open) expandedMeal.value = index
  else if (expandedMeal.value === index) expandedMeal.value = null
}

// 详情图片：打开抽屉时按需加载，前端按地点缓存（查询失败的下次打开重试）
async function loadMedia(item: PlanItem | null) {
  mediaController?.abort()
  mediaLoading.value = false
  if (!item || item.ref_id == null) return
  const key = `${item.kind}:${item.ref_id}`
  if (placeMedia.value[key] && placeMedia.value[key]!.status !== 'unavailable') return
  const controller = new AbortController()
  mediaController = controller
  mediaLoading.value = true
  try {
    const media = await getPlaceMedia(item.kind, item.ref_id, controller.signal, plan.value?.plan_id)
    if (!controller.signal.aborted) placeMedia.value[key] = media
  } catch {
    if (!controller.signal.aborted) placeMedia.value[key] = { kind: item.kind, id: item.ref_id, name: item.name, photos: [], status: 'unavailable', fetched_at: null, policy: '' }
  } finally { if (!controller.signal.aborted) mediaLoading.value = false }
}
watch([drawerOpen, selectedItem], ([open, item]) => { if (open) void loadMedia(item as PlanItem | null) })

// 换餐厅：先确认，再只重新规划这一天；成功后给出变化摘要和"撤销"
function requestSwap(index: number, alternative: MealAlternative) {
  const current = plan.value
  if (!current?.plan_id || current.revision == null) return
  swapContext.value = { planId: current.plan_id, revision: current.revision, day: activeDay.value }
  selfMeal.value = null
  swapTarget.value = { index, alternative }
  swapError.value = ''
  swapOpen.value = true
  void calculateMealPreview()
}
function requestSelfMeal(meal: 'lunch' | 'dinner', cost: number) {
  const current = plan.value
  if (!current?.plan_id || current.revision == null || current.history_readonly) return
  swapContext.value = { planId: current.plan_id, revision: current.revision, day: activeDay.value }
  swapTarget.value = null
  selfMeal.value = meal
  selfMealCost.value = cost
  swapOpen.value = true
  void calculateMealPreview()
}
async function calculateMealPreview() {
  swapPreviewController?.abort()
  const sequence = ++swapPreviewSequence
  const context = swapContext.value
  const target = swapTarget.value
  mealPreview.value = null
  swapError.value = ''
  previewLoading.value = false
  if (!context || (!target && !selfMeal.value)) return
  if (selfMeal.value && (!Number.isFinite(selfMealCost.value) || selfMealCost.value < 0 || selfMealCost.value > 2000)) {
    swapError.value = '请输入 0–2000 元的预留餐费'
    return
  }
  previewLoading.value = true
  swapPreviewController = new AbortController()
  try {
    const preview = await previewMeal(context.planId, { revision: context.revision, day: context.day,
      ...(selfMeal.value ? { action: 'self_arranged' as const, meal: selfMeal.value, estimated_cost: selfMealCost.value }
        : { action: 'replace' as const, item_index: target!.index, restaurant_id: target!.alternative.ref_id }) }, swapPreviewController.signal)
    if (sequence === swapPreviewSequence && swapOpen.value) mealPreview.value = preview
  } catch (failure) {
    if (sequence === swapPreviewSequence && failure instanceof ApiError && failure.status === 409) { await refreshEditedPlan(context.planId); return }
    if (sequence === swapPreviewSequence && (failure as Error).name !== 'AbortError') swapError.value = (failure as Error).message || '预览暂未完成，原行程保持不变'
  } finally { if (sequence === swapPreviewSequence) previewLoading.value = false }
}
watch(swapOpen, open => {
  if (!open) { swapPreviewController?.abort(); swapPreviewSequence++; previewLoading.value = false; mealPreview.value = null }
})
onBeforeUnmount(() => swapPreviewController?.abort())
function changeSelfMealCost(cost: number) { selfMealCost.value = cost; void calculateMealPreview() }
async function refreshEditedPlan(planId: string) {
  try {
    const updated = await getCurrentPlan(planId)
    if (plan.value?.plan_id !== planId) return
    plan.value = updated
    navigationCache.clear()
    clearAlternative()
    selectedIndex.value = null
    swapOpen.value = false
    showToast('行程已被其他操作更新，已同步最新版本。请重新选择餐厅查看预览。', true, null)
  } catch { swapError.value = '无法同步当前行程，请稍后重新打开预览' }
}
function requestSwapFromDrawer() {
  const index = selectedAlternativeParent.value
  const alternative = index == null ? undefined : currentDay.value?.items[index]?.alternatives?.find(item => item.ref_id === selectedAlternative.value?.ref_id)
  if (index == null || !alternative) return
  drawerOpen.value = false
  requestSwap(index, alternative)
}
function describeChange(change: PlanChange | null) {
  if (!change) return '已更换餐厅'
  const parts = [`已把第 ${change.day} 天${change.meal === 'lunch' ? '午餐' : '晚餐'}换成「${change.to}」`]
  if (change.shift_min) parts.push(change.shift_min > 0 ? `当天结束时间推迟 ${change.shift_min} 分钟` : `当天提前 ${-change.shift_min} 分钟结束`)
  if (change.reordered) parts.push('调整了景点顺序')
  if (change.removed.length) parts.push(`移出 ${change.removed.join('、')}`)
  if (!change.passed) parts.push(change.conflicts.length ? `仍有 ${change.conflicts.length} 处冲突，可撤销` : '行程仍有待处理的冲突，请查看每日提示')
  return parts.join('；')
}
function showToast(text: string, warn: boolean, undoRevision: number | null) {
  clearTimeout(toastTimer)
  toast.value = { text, warn, undoRevision }
  if (undoRevision == null) toastTimer = setTimeout(() => (toast.value = null), 12000)
}
async function confirmSwap() {
  const current = plan.value
  const context = swapContext.value
  const preview = mealPreview.value
  if (!context || !preview?.can_commit || !current?.plan_id || current.revision == null) return
  if (context.planId !== current.plan_id || context.revision !== current.revision) {
    mealPreview.value = null
    swapError.value = '行程已变化，请重新打开预览'
    return
  }
  if (preview.expires_at * 1000 <= Date.now()) { mealPreview.value = null; swapError.value = '预览已过期，请重新计算'; return }
  swapBusy.value = true
  swapError.value = ''
  const meal = swapMealKind.value
  try {
    const updated = await commitMeal(context.planId, context.revision, preview.preview_id)
    navigationCache.delete(context.day)
    clearAlternative()
    drawerOpen.value = false
    expandedMeal.value = null
    plan.value = updated
    swapOpen.value = false
    const newIndex = updated.days.find(day => day.day === activeDay.value)?.items.findIndex(item => item.kind === 'meal' && item.meal === meal) ?? -1
    selectedIndex.value = newIndex >= 0 ? newIndex : null
    focusRevision.value++
    showToast(selfMeal.value ? '已确认自行安排用餐，预留费用已计入，可撤销' : describeChange(updated.change), false, current.revision)
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 409) { await refreshEditedPlan(context.planId); return }
    swapError.value = (failure as Error).message || '重新规划失败，原行程保持不变'
    mealPreview.value = null
  } finally { swapBusy.value = false }
}
async function undoSwap() {
  if (swapBusy.value) return
  const undo = toast.value?.undoRevision
  const current = plan.value
  if (undo == null || !current?.plan_id || current.revision == null) return
  swapBusy.value = true
  try {
    const restored = await revertPlan(current.plan_id, undo, current.revision)
    navigationCache.delete(activeDay.value)
    clearAlternative()
    plan.value = restored
    selectedIndex.value = null
    showToast('已撤销，恢复为更换前的安排', false, null)
  } catch (failure) {
    showToast((failure as Error).message || '撤销失败，请重新生成', true, null)
  } finally { swapBusy.value = false }
}

async function copyPlan() {
  if (!plan.value) return
  const lines = ['杭州行程', ...plan.value.days.flatMap(d => [`第 ${d.day} 天`, ...d.items.map(i => `${i.start_hm}–${i.end_hm} ${i.name}${i.cost > 0 ? ` ¥${i.cost.toFixed(0)}` : ''}`)]),
    `已安排费用约 ¥${plan.value.report.totals.total?.toFixed(0)}/人，不含住宿和城际交通。`]
  try { await navigator.clipboard.writeText(lines.join('\n')); copied.value = true; setTimeout(() => (copied.value = false), 1800) } catch { /* 无剪贴板权限时静默 */ }
}

async function run(req: PlanRequest) {
  if (loading.value) return
  if (meta.value?.access?.login_to_plan && !user.value) { pendingRequest.value = req; openAuth('登录后就能生成行程，已填的条件会保留。'); return }
  loading.value = true
  historyOpen.value = false
  outputText.value = ''; outputTag = ''; outputAttempt = 0; modelThinking.value = null; thinkText.value = ''
  progressVisible.value = true
  planProgress.value = emptyProgress()
  lastRequest.value = req
  const controller = new AbortController()
  planController = controller
  error.value = ''
  elapsed.value = 0
  timer = setInterval(() => elapsed.value++, 1000)
  await nextTick()
  scrollPanelTop()
  try {
    navigationCache.clear()
    placeDetails.value = {}
    drawerOpen.value = false
    clearAlternative()
    expandedMeal.value = null
    toast.value = null
    const result = await postPlan(req, event => {
      if (event.type === 'model') {
        if (event.tag !== outputTag || event.attempt !== outputAttempt || event.source === 'cache') {
          outputText.value += `\n\n—— ${event.tag} · ${event.source === 'cache' ? '已缓存的回答' : `实时回答${event.attempt > 1 ? ` · 第 ${event.attempt} 次尝试` : ''}`} ——\n`
          outputTag = event.tag; outputAttempt = event.attempt; thinkText.value = ''
        }
        outputText.value = (outputText.value + event.text).slice(-32000)
        if (event.think) thinkText.value = (thinkText.value + event.think).slice(-THINK_WINDOW)
        if (event.source === 'live' && event.phase === 'thinking' && !event.text) {
          if (modelThinking.value?.tag !== event.tag) modelThinking.value = { tag: event.tag, started: Date.now(), chars: event.thought ?? 0 }
          else modelThinking.value = { ...modelThinking.value, chars: event.thought ?? modelThinking.value.chars }
        } else modelThinking.value = null
      } else {
        if (event.status === 'completed') modelThinking.value = null
        planProgress.value = advanceProgress(planProgress.value, event)
      }
    }, controller.signal)
    if (controller.signal.aborted) return
    plan.value = result
    progressVisible.value = false
    activeDay.value = 1
    selectedIndex.value = null
    mobileView.value = 'route'
    editing.value = false
    await nextTick()
    scrollPanelTop()
  } catch (failure) {
    if (failure instanceof ApiError && failure.status === 401) {
      user.value = null; pendingRequest.value = req; progressVisible.value = false
      openAuth('登录后就能生成行程，已填的条件会保留。')
      return
    }
    if (!controller.signal.aborted) error.value = failure instanceof TypeError
      ? '连接暂时中断。你的出行条件已保留，请检查网络后重试。'
      : (failure as Error).message || '规划失败，请稍后重试'
  } finally {
    clearInterval(timer)
    loading.value = false
    if (user.value) refreshAccount().catch(() => {})
  }
}

function leaveProgress() {
  planController?.abort()
  progressVisible.value = false
  error.value = ''
  editing.value = true
  nextTick(scrollPanelTop)
}

function startEdit() { drawerOpen.value = false; clearAlternative(); selectedIndex.value = null; editing.value = true; nextTick(scrollPanelTop) }

function clearPrivateView() {
  identityRevision++
  planController?.abort(); extrasController?.abort(); mediaController?.abort(); alternativeController?.abort()
  plan.value = null; lastRequest.value = null; historyOpen.value = false; progressVisible.value = false
  drawerOpen.value = false; swapOpen.value = false; toast.value = null; navigation.value = null
  placeDetails.value = {}; placeMedia.value = {}; navigationCache.clear(); clearAlternative()
  expandedMeal.value = null; selectedIndex.value = null; outputText.value = ''; error.value = ''
  initialRequest.value = { query: '', form: {}, select_mode: 'llm', enable_repair: true }; editing.value = true
}
function openAuth(reason = '') { authReason.value = reason; accountOpen.value = true }
function closeAuth() { accountOpen.value = false; historyAfterLogin.value = false; pendingRequest.value = null }
function expired() { user.value = null; clearPrivateView(); openAuth('登录已过期，请重新登录。') }
function signedIn(account: AccountUser) {
  accountOpen.value = false
  if (user.value && user.value.id !== account.id) clearPrivateView()
  user.value = account; identityRevision++
  refreshAccount().catch(() => {})
  if (pendingTrip) { resumeTripLink(); return }
  if (pendingRequest.value) { const request = pendingRequest.value; pendingRequest.value = null; nextTick(() => run(request)); return }
  if (historyAfterLogin.value) { historyOpen.value = true; historyAfterLogin.value = false }
  else if (plan.value?.plan_id && !plan.value.history_id) void saveCurrent()
}
function openHistory() {
  if (!user.value) { historyAfterLogin.value = true; openAuth('登录后查看历史行程。'); return }
  drawerOpen.value = false; historyOpen.value = true
}
async function signOut() {
  if (accountBusy.value || loading.value || swapBusy.value) return
  accountBusy.value = true
  try { await logoutAccount(); user.value = null; quota.value = null; authReason.value = ''; clearPrivateView() }
  catch (failure) { showToast((failure as Error).message, true, null) }
  finally { accountBusy.value = false }
}
async function saveCurrent() {
  if (!user.value) { openAuth('登录后行程会保存到你的账号。'); return }
  if (!plan.value?.plan_id || accountBusy.value) return
  accountBusy.value = true
  const revision = identityRevision
  try { const saved = await saveHistory(plan.value.plan_id); if (revision === identityRevision) { plan.value = saved; showToast(saved.history_error || '行程已保存到你的账号', !!saved.history_error, null) } }
  catch (failure) { if (failure instanceof ApiError && failure.status === 401) expired(); else showToast((failure as Error).message, true, null) }
  finally { accountBusy.value = false }
}
function newJourney() {
  historyOpen.value = false
  initialRequest.value = { query: '', form: {}, select_mode: 'llm', enable_repair: true }
  startEdit()
}
async function openSaved(id: string, replan = false, linked = false) {
  if (historyBusy.value) return
  historyBusy.value = true
  const revision = identityRevision
  try {
    const saved = await (linked ? getLinkedTrip(id) : getHistoryDetail(id))
    if (revision !== identityRevision) return
    initialRequest.value = saved.request; lastRequest.value = saved.request
    drawerOpen.value = false; clearAlternative(); navigationCache.clear(); placeDetails.value = {}; placeMedia.value = {}
    selectedIndex.value = null; expandedMeal.value = null; toast.value = null; activeDay.value = 1
    plan.value = saved.plan; historyOpen.value = false; editing.value = replan; mobileView.value = 'route'
    await nextTick(); scrollPanelTop()
  } catch (failure) { if (failure instanceof ApiError && failure.status === 401) expired(); else showToast((failure as Error).message, true, null) }
  finally { historyBusy.value = false }
}

onMounted(async () => {
  try { await refreshAccount() } catch { error.value = '账号服务暂不可用，你仍可以先规划行程。' } finally { authReady.value = true }
  try { meta.value = await getMeta(); previewHotel.value = meta.value.hotels[0] ?? null } catch { error.value = '暂时连接不上规划服务，请刷新后重试。' }
  if (mcpConnectionId && !user.value) openAuth('首次配置 MCP：登录后确认授权，以后即可直接使用。')
  await resumeTripLink()
})
onBeforeUnmount(() => { planController?.abort(); clearInterval(timer); clearTimeout(toastTimer); extrasController?.abort(); alternativeController?.abort(); mediaController?.abort() })
</script>

<template>
  <div class="journey-shell" :class="{ 'questionnaire-view': showForm && !progressVisible && !historyOpen }">
    <header class="journey-bar"><a href="/" class="brand"><BrandMark />杭州行记<span>HANGZHOU, YOUR WAY.</span></a>
      <nav v-if="!gated && !booting" aria-label="账号与行程">
        <button class="bar-link" :class="{ on: historyOpen }" aria-label="历史行程" :disabled="loading || swapBusy || accountBusy" @click="openHistory"><PhClockCounterClockwise :size="18" /><span>历史行程</span></button>
        <template v-if="user">
          <span class="account-chip" :title="`已登录：${user.username}`"><span class="account-avatar" aria-hidden="true">{{ user.username.slice(0, 1).toUpperCase() }}</span><span class="account-name">{{ user.username }}</span></span>
          <button class="bar-link" aria-label="退出登录" :disabled="loading || swapBusy || accountBusy" @click="signOut"><PhSignOut :size="18" /><span>退出</span></button>
        </template>
        <button v-else class="account-signin" :class="{ on: accountOpen }" :disabled="!authReady || loading" @click="historyAfterLogin = false; openAuth()"><PhUserCircle :size="18" />登录 / 注册</button>
      </nav>
    </header>
  <QuestionnaireBackdrop v-if="showForm && !progressVisible && !historyOpen && !accountOpen && !gated && !booting" />
  <div v-if="!gated && !booting" class="app" :class="[`view-${mobileView}`, { 'has-plan': plan && !showForm && !progressVisible && !historyOpen, 'journey-fullscreen': showForm || progressVisible || historyOpen }]">
    <HistoryView v-if="historyOpen && user" :key="user.id" :username="user.username" @back="historyOpen = false" @create="newJourney" @open="id => openSaved(id)" @replan="id => openSaved(id, true)" @expired="expired" />
    <div v-if="historyBusy" class="history-opening" role="status">正在读取行程…</div>
    <aside v-show="!historyOpen" class="panel">
      <header class="panel-bar">
        <span class="panel-caption">{{ progressVisible ? '行程工作台' : showForm ? '新的旅程' : '我的行程' }}</span>
        <template v-if="plan && !showForm">
          <div class="bar-actions">
            <button class="ghost-btn" @click="startEdit"><PhPencilSimple :size="16" />修改</button>
            <button class="ghost-btn" @click="copyPlan"><component :is="copied ? PhCheck : PhCopy" :size="16" />{{ copied ? '已复制' : '复制' }}</button>
          </div>
        </template>
        <button v-else-if="plan && !progressVisible" class="ghost-btn" @click="editing = false"><PhArrowLeft :size="16" />返回行程</button>
      </header>

      <!-- 行程摘要与日期切换固定在面板顶部，滚动时不消失 -->
      <div v-if="plan && !showForm && !progressVisible" class="trip-head">
        <h1>杭州 {{ plan.profile.days }} 日<span>{{ plan.profile.interests.join(' · ') || '随心逛' }} · {{ paceNames[plan.profile.pace] }} · {{ plan.profile.transport === 'transit' ? '公交地铁' : '打车' }}</span></h1>
        <dl class="trip-stats">
          <div><dt>人均已安排</dt><dd>¥{{ plan.report.totals.total?.toFixed(0) }}<small>/ {{ plan.profile.budget.toFixed(0) }}</small></dd></div>
          <div><dt>游览段</dt><dd>{{ poiCount }}</dd></div>
          <div><dt>正餐</dt><dd>{{ mealCount }}<small v-if="onsiteMealCount">＋{{ onsiteMealCount }} 餐园内预留</small><small v-if="selfArrangedMealCount">＋{{ selfArrangedMealCount }} 餐自行安排</small></dd></div>
          <div class="check" :class="{ bad: !plan.report.passed }">
            <component :is="plan.report.passed && plan.report.experience_status === 'complete' ? PhCheckCircle : PhWarningCircle" :size="16" weight="fill" />
            {{ plan.report.passed ? (plan.report.experience_status === 'complete' ? '已知时间与预算可行' : '仍有待完善项') : `${plan.report.violations.filter(v => v.severity === 'hard').length} 处冲突` }}
          </div>
        </dl>
        <p class="quality-line" :class="{ 'needs-attention': plan.report.experience_status !== 'complete' }">
          {{ plan.report.experience_status === 'complete' ? '用餐与活动安排完整' : '行程仍有待完善项，请查看每日提示' }}
          <span>· {{ plan.report.data_status === 'partial' ? '开放、价格等信息仍需确认' : '已核验数据完整' }}</span>
        </p>
        <nav class="day-tabs" role="tablist" aria-label="选择日期">
          <button v-for="d in plan.days" :key="d.day" role="tab" :aria-selected="activeDay === d.day" :class="{ on: activeDay === d.day }" @click="pickDay(d.day)">
            第 {{ d.day }} 天<small>{{ d.date ? d.date.slice(5) : `${d.items.filter(i => i.kind === 'poi').length} 景点` }}<span v-if="d.areas?.length" class="day-area" :title="d.areas.join('、')">{{ d.areas[0] }}{{ d.areas.length > 1 ? ` 等 ${d.areas.length} 片` : '' }}</span></small>
          </button>
        </nav>
      </div>

      <div ref="scroller" class="panel-scroll" :class="{ 'progress-scroll': progressVisible }">
        <PlanProgress v-if="progressVisible" :progress="planProgress" :elapsed="elapsed" :error="error" :busy="loading" :output="outputText" :think="thinkText" :thinking="modelThinking" @back="leaveProgress" @retry="lastRequest && run(lastRequest)" />
        <p v-if="error && !progressVisible" class="notice bad" role="alert"><PhWarningCircle :size="16" />{{ error }}</p>

        <section v-show="showForm && !progressVisible" class="form-wrap">
          <PlanForm :meta="meta" :loading="loading" :quota="quota" :needs-login="!!meta?.access?.login_to_plan && !user" :initial-query="initialQuery" :initial-request="initialRequest" @submit="run" @hotel-change="previewHotel = $event" />
        </section>

        <template v-if="plan && !showForm && currentDay && !progressVisible">
          <div v-if="plan.delivery_status === 'draft'" class="history-status warning" role="status"><strong>测试草稿 · 待核实</strong><span>{{ plan.verification_notice || '开放时间、票价等尚未全部核实，请出发前确认。' }}</span></div>
          <div class="history-status" :class="{ warning: plan.history_error }"><template v-if="plan.history_readonly"><span>历史行程快照 · 时间与价格以当时安排为准</span><button @click="startEdit">按这些偏好重新规划</button></template><template v-else-if="plan.history_id && !plan.history_error"><span>已保存到 {{ user?.username }} 的历史行程</span></template><template v-else><span>{{ plan.history_error || (user ? '保存后，下次可以继续查看' : '登录账号，收藏这次旅程') }}</span><button :disabled="accountBusy" @click="saveCurrent">{{ accountBusy ? '保存中…' : user ? '保存行程' : '登录并保存' }}</button></template></div>
          <Timeline :key="`${plan.plan_id ?? 'plan'}-${currentDay.day}`" :day="currentDay" :hotel-name="plan.profile.hotel.name" :selected-index="selectedIndex" :selected-alternative-id="selectedAlternative?.ref_id ?? null" @focus="focusStop" @details="openStopDetails" @focus-alternative="focusAlternative" @details-alternative="(index, alternative) => focusAlternative(index, alternative, true)"
            @alternatives-toggle="onAlternativesToggle" />
          <p v-for="issue in plan.report.violations.filter(issue => issue.day === currentDay!.day)" :key="`${issue.code}-${issue.item}`" class="notice warn">{{ issue.message }}</p>
          <p v-if="plan.dropped.length" class="aside-note">结合当天主项目、时间与路线，未排入：{{ plan.dropped.join('、') }}</p>
          <div v-for="slot in (currentDay.reserved_meals || []).filter(slot => slot.status !== 'self_arranged')" :key="`self-${slot.meal}`" class="self-meal-action">
            <span>{{ slot.inside_poi_id != null ? '园内' : '预留' }}{{ slot.meal === 'lunch' ? '午餐' : '晚餐' }}尚待确认</span>
            <button v-if="plan.plan_id && !plan.history_readonly" type="button" @click="requestSelfMeal(slot.meal, slot.estimated_cost || 60)">我自行安排这餐 ↗</button>
          </div>
          <details class="report-block">
            <summary>费用明细与校验详情</summary>
            <ReportPanel :plan="plan" />
          </details>
          <p class="fine-print">费用不含住宿与往返杭州交通。地点来源包括 ChinaTravel（CC BY 4.0）、官方资料及高德查询，各餐厅单独标明来源；价格、预约与临时营业变化请出发前确认。</p>
        </template>
      </div>
    </aside>

    <main v-if="plan && !showForm && !progressVisible && !historyOpen" class="map-area" aria-label="路线地图">
      <div class="map-stage">
      <AmapView v-if="amapKey" :amap-key="amapKey" :plan="showForm ? null : plan" :active-day="activeDay" :selected-index="selectedIndex" :preview-hotel="previewHotel" :navigation="navigation"
        :focused-place="focusedPlace" :focus-revision="focusRevision"
        :alternatives="altMarkers" :alternative-anchor="altAnchor" :selected-alternative-id="selectedAlternative?.ref_id ?? null"
        @focus="i => focusStop(i, true)" @focus-alternative="focusAlternativeFromMap" @failed="amapFailed = true" />
      <MapView v-else-if="meta || error" :plan="showForm ? null : plan" :active-day="activeDay" :selected-index="selectedIndex" :preview-hotel="previewHotel" :navigation="navigation" :focused-place="focusedPlace" :focus-revision="focusRevision"
        :alternatives="altMarkers" :alternative-anchor="altAnchor" :selected-alternative-id="selectedAlternative?.ref_id ?? null"
        @focus="i => focusStop(i, true)" @focus-alternative="focusAlternativeFromMap" />
      </div>
      <DayStrip v-if="plan && !showForm && currentDay" :day="currentDay" :hotel-name="plan.profile.hotel.name" :selected-index="selectedIndex" :travel-minutes="dayTravel"
        @focus="i => focusStop(i, true)" @details="openStopDetails">
        <NavigationPanel :day="currentDay" :home="plan.profile.hotel.name" :navigation="navigation" :loading="navigationLoading" :error="navigationError" @retry="loadDayExtras(true)" />
      </DayStrip>
    </main>

    <nav v-if="plan && !showForm && !progressVisible && !historyOpen" class="mobile-switch" aria-label="切换视图">
      <button :class="{ on: mobileView === 'route' }" @click="mobileView = 'route'">行程</button>
      <button :class="{ on: mobileView === 'map' }" @click="mobileView = 'map'">地图</button>
    </nav>
    <PlaceDrawer v-model:open="drawerOpen" :item="selectedItem" :detail="selectedDetail" :loading="selectedAlternative ? alternativeLoading : detailsLoading"
      :media="selectedMedia" :media-loading="mediaLoading" @swap="requestSwapFromDrawer" />
    <SwapConfirm v-model:open="swapOpen" :day="swapContext?.day ?? activeDay" :meal="swapMealKind" :from="swapFrom" :target="swapTarget?.alternative ?? null"
      :preview="mealPreview" :loading="previewLoading" :self-arranged="!!selfMeal" :self-cost="selfMealCost"
      :busy="swapBusy" :error="swapError" @confirm="confirmSwap" @retry="calculateMealPreview" @budget="changeSelfMealCost" />
    <div v-if="toast" class="toast" :class="{ warn: toast.warn }" role="status" aria-live="polite">
      <span>{{ toast.text }}</span>
      <button v-if="toast.undoRevision != null" type="button" class="toast-action" :disabled="swapBusy" @click="undoSwap">{{ swapBusy ? '恢复中…' : '撤销' }}</button>
      <button type="button" class="toast-close" aria-label="关闭提示" @click="toast = null">×</button>
    </div>
  </div>
  <MCPConnect v-if="mcpConnectionId && user" :connection-id="mcpConnectionId" :user="user" @connected="refreshAccount" @switch-account="openAuth('请选择要绑定 MCP 的账号。')" />
  <section v-else-if="mcpCompleted && user" class="mcp-complete-note">账号已连接，回到聊天即可使用。<a href="/">进入杭州行记</a></section>
  <AuthView v-if="accountOpen || gated" :gate="gated" :reason="authReason" :invite-required="!!meta?.access?.invite_required" @close="closeAuth" @signed-in="signedIn" />
  </div>
</template>
