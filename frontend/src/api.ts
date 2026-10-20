export interface Hotel { area?: string; name: string; lat: number; lon: number; gcj?: { lat: number; lon: number } }
export interface OriginPlace extends Hotel { id: string; address: string; source: 'amap'; coordinate_system: 'BD-09' }

export interface CityInfo {
  key: string
  name: string
  brand: string
  map_center: { lat: number; lon: number }
  default_origin: Hotel
}
export interface Meta {
  city: string
  cities: CityInfo[]
  interests: string[]
  dietary: string[]
  hotels: Hotel[]
  paces: Record<string, string>
  companions: Record<string, string>
  map?: { provider: 'amap' | 'leaflet'; amap_js_key: string | null }
  access?: { login_to_plan: boolean; invite_required: boolean }
}

export interface PlanItem {
  area?: string
  is_alternative?: boolean
  guide_evidence?: { id: string; title: string; facts: string; url: string; publisher: string; source_date: string | null; attribution: string; permission: string; duration_scope: string | null; queue_note: string | null; season_note: string | null; caveats: string[] }[]
  evening?: boolean
  detail?: PlaceDetail | null
  price_verified?: boolean
  kind: 'poi' | 'meal'
  ref_id: number | null
  name: string
  start: number
  end: number
  start_hm: string
  end_hm: string
  travel_min: number
  travel_mode: string
  cost: number
  reason: string
  meal: 'lunch' | 'dinner' | null
  type: string
  visit_group?: string
  visit_group_label?: string
  visit_role?: 'attraction' | 'walk_segment' | 'viewpoint'
  lat?: number
  lon?: number
  source: 'llm' | 'rule' | 'repair' | 'jev' | 'external_ai' | null
  score: Record<string, number> | null
  travel_evidence?: LegEvidence | null
  notices?: string[]
  restaurant?: { provider: string; address: string; rating: number | null; fetched_at: string | null; hours_known: boolean; opening_text: string } | null
  alternatives?: MealAlternative[]
}

export interface MealAlternative {
  ref_id: number; name: string; cuisine: string; price: number; rating: number | null; address: string
  detour_min: number; travel_source: string; provider: string; fetched_at?: string | null; start: number; end: number
  lat?: number; lon?: number; detail?: PlaceDetail | null
}
export interface MapFocus { name: string; lat: number; lon: number; alternative: boolean }
export interface AltMarker { ref_id: number; name: string; label: string; lat: number; lon: number }

export interface PlacePhoto { url: string; thumb_url: string; caption: string; source: 'manual' | 'amap' | 'amap_related'; source_label: string; attribution: string; license: string; source_url: string; fingerprint: string }
export interface PlaceMedia { kind: 'poi' | 'meal'; id: number; name: string; photos: PlacePhoto[]; status: 'ready' | 'partial' | 'empty' | 'unavailable'; fetched_at: string | null; policy: string }
export interface PlanChange { day: number; meal: 'lunch' | 'dinner'; from: string; to: string; shift_min: number | null; removed: string[]; reordered: boolean; passed: boolean; conflicts: string[]; seconds: number; live_calls: number }

export interface MealEditPreview {
  preview_id: string; base_revision: number; expires_at: number; can_commit: boolean; plan: PlanResponse
  change: PlanChange & { action: 'replace' | 'self_arranged'; budget_before: number; budget_after: number; budget_delta: number;
    travel_delta_min: number; ride_delta_min: number; end_before: number | null; end_after: number | null; route_source: 'amap' | 'estimate';
    affected_stops: { name: string; before_start: number; before_end: number; after_start: number | null; after_end: number | null }[] }
}
export interface MealEditInput { revision: number; day: number; action?: 'replace' | 'self_arranged'; item_index?: number; restaurant_id?: number; meal?: 'lunch' | 'dinner'; estimated_cost?: number }
export const previewMeal = (planId: string, body: MealEditInput, signal?: AbortSignal) =>
  fetch(`/api/plans/${encodeURIComponent(planId)}/meal-preview`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body), signal }).then(response => j<MealEditPreview>(response))
export const commitMeal = (planId: string, revision: number, previewId: string) =>
  fetch(`/api/plans/${encodeURIComponent(planId)}/meal-commit`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision, preview_id: previewId }) }).then(response => j<PlanResponse>(response))
export const getCurrentPlan = (planId: string) => fetch(`/api/plans/${encodeURIComponent(planId)}`, { cache: 'no-store' }).then(response => j<PlanResponse>(response))

export interface LegEvidence { minutes: number; mode: string; source: 'amap' | 'estimate' | 'same_place'; walking_m: number; available: boolean; fallback_reason: string | null }
export interface PlaceDetail {
  kind: 'poi' | 'meal'; id: number; name: string; introduction: string; highlights: string[]; audiences: string[]; tags: string[]
  duration: string; opening: string; tips: string[]; content_source: string; address: string; source_url: string | null
  rating: number | null; rating_source: string | null; rating_status: 'pending' | 'ready' | 'unrated' | 'unavailable'; rating_at: string | null
}
export interface RouteSegment { mode: string; path: [number, number][]; instruction: string; distance_m: number; minutes: number }
export interface NavigationResult {
  status: 'ready' | 'partial' | 'unavailable' | 'same_place'; segments: RouteSegment[]; minutes?: number; distance_m?: number; message?: string
  fetched_at?: string; departure?: string; date?: string | null
}
export interface NavigationResponse { legs: NavigationResult[]; queries: number; coordinate_system: string }
export interface NavigationLeg { origin: { lat: number; lon: number }; destination: { lat: number; lon: number }; mode: string; departure: number }
export interface DayPlan { day: number; date?: string | null; items: PlanItem[]; return_travel_min: number; return_leg?: LegEvidence | null; walking_m?: number; areas?: string[]; notes: string[]; reserved_meals?: { meal: 'lunch' | 'dinner'; start: number; end: number; reason: string; inside_poi_id?: number | null; estimated_cost?: number; status?: 'pending' | 'self_arranged' }[] }

export interface Violation { code: string; severity: 'hard' | 'soft'; day: number | null; item: string | null; message: string }

export interface PlanResponse {
  city_info?: CityInfo
  history_id?: string
  history_error?: string
  delivery_status?: 'ready' | 'draft'
  verification_notice?: string | null
  history_readonly?: boolean
  profile: {
    city?: string
    days: number; budget: number; pace: string; interests: string[]; dietary: string[]; companions: string; transport: 'taxi' | 'transit'
    must_visit: string[]; avoid: string[]; notes: string
    hotel: Hotel; hotel_gcj: { lat: number; lon: number }
    start_date?: string | null
  }
  days: DayPlan[]
  report: { passed: boolean; experience_status: 'complete' | 'needs_attention'; data_status: 'verified' | 'partial'; unknowns: string[]; violations: Violation[]; totals: Record<string, number> }
  candidates: { id: number; name: string; score: number; interest: number; distance_km: number; selected: boolean; lat: number; lon: number }[]
  dropped: string[]
  plan_id: string | null
  revision: number | null
  change: PlanChange | null
  log: string[]
  usage: { calls: number; cached_calls: number; total_tokens: number; seconds: number }
  seconds: number
  meta: { operation?: 'plan' | 'meal_swap' | 'meal_self_arranged'; travel: { source: string; mode_name?: string; live_calls?: number; approx_legs?: number; approx_planning?: number; final_legs?: number; errors?: string[] }; select_mode: string; enable_repair: boolean; repair_rounds: number; llm_model: string; llm_thinking: string | null; tag_source: string; selection?: { provider?: string; model?: string; status?: string; cached?: boolean; seconds?: number; confident_candidates?: number; candidates?: number; error?: string } }
}

export interface PlanRequest {
  guide_mode?: 'off' | 'pilot'
  query: string
  form: Record<string, unknown>
  select_mode: 'llm' | 'rule' | 'jev'
  enable_repair: boolean
}

export interface AccountUser { id: string; username: string }
export interface PlanQuota { limit: number; used: number; remaining: number }
export interface HistoryEntry { id: string; title: string; created: number; updated: number }
export interface HistoryDetail extends HistoryEntry { request: PlanRequest; plan: PlanResponse }
export class ApiError extends Error { constructor(message: string, public status: number) { super(message) } }
export const getAccount = async () => j<{ user: AccountUser | null; quota?: PlanQuota | null }>(await fetch('/api/auth/me', { cache: 'no-store' }))
export const authenticate = async (mode: 'login' | 'register', username: string, password: string, inviteCode?: string) => j<{ user: AccountUser }>(await fetch(`/api/auth/${mode}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password, ...(inviteCode ? { invite_code: inviteCode } : {}) }) }))
export const logoutAccount = async () => j<{ ok: boolean }>(await fetch('/api/auth/logout', { method: 'POST' }))
export const getHistory = async (offset = 0) => j<{ items: HistoryEntry[]; has_more: boolean }>(await fetch(`/api/history?offset=${offset}`, { cache: 'no-store' }))
export const getHistoryDetail = async (id: string) => j<HistoryDetail>(await fetch(`/api/history/${encodeURIComponent(id)}`, { cache: 'no-store' }))
export const deleteHistory = async (id: string) => j<{ ok: boolean }>(await fetch(`/api/history/${encodeURIComponent(id)}`, { method: 'DELETE' }))
export const saveHistory = async (id: string) => j<PlanResponse>(await fetch(`/api/plans/${encodeURIComponent(id)}/save`, { method: 'POST' }))

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let msg = `${r.status}`
    try {
      const detail = (await r.json()).detail
      msg = Array.isArray(detail) ? [...new Set(detail.map(item => item.msg))].join('；') : detail ?? msg
    } catch { /* ignore */ }
    throw new ApiError(msg, r.status)
  }
  return r.json() as Promise<T>
}

export const getMeta = (city?: string, signal?: AbortSignal) => fetch(`/api/meta${city ? `?city=${encodeURIComponent(city)}` : ''}`, { signal }).then(r => j<Meta>(r))
export const searchPlaces = (query: string, city: string, signal?: AbortSignal) =>
  fetch(`/api/places/search?q=${encodeURIComponent(query)}&city=${encodeURIComponent(city)}`, { signal }).then(r => j<{ places: OriginPlace[] }>(r))
export interface ModelOutputEvent { type: 'model'; tag: string; text: string; think?: string; source: 'live' | 'cache'; attempt: number; phase?: 'thinking' | 'answer'; thought?: number }
export async function postPlan(req: PlanRequest, onProgress: (event: import('./planProgress').PlanProgressEvent | ModelOutputEvent) => void,
  signal: AbortSignal): Promise<PlanResponse> {
  const response = await fetch('/api/plan/stream', {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(req), signal,
  })
  if (!response.ok) return j<PlanResponse>(response)
  if (!response.body) throw new Error('浏览器未收到进度数据，请重试。')
  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  const parse = (line: string): PlanResponse | undefined => {
    if (!line.trim()) return
    const event = JSON.parse(line)
    if (event.type === 'progress' || event.type === 'model') onProgress(event)
    if (event.type === 'error') throw new Error(event.message)
    if (event.type === 'result') return event.plan as PlanResponse
  }
  try {
    while (true) {
      const { done, value } = await reader.read()
      buffer += decoder.decode(value, { stream: !done })
      const lines = buffer.split('\n')
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        const result = parse(line)
        if (result) return result
      }
      if (done) {
        const result = parse(buffer)
        if (result) return result
        throw new Error('连接中断，尚未收到完整行程。你的条件已保留，请重试。')
      }
    }
  } finally {
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}

export const getNavigation = (date: string | null, legs: NavigationLeg[], signal?: AbortSignal, context: { city?: string; plan_id?: string | null } = {}) =>
  fetch('/api/navigation', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ date, legs, ...context }), signal })
    .then(response => j<NavigationResponse>(response))
export const getPlaceDetails = (places: { kind: 'poi' | 'meal'; id: number }[], signal?: AbortSignal, planId?: string | null) =>
  fetch('/api/place-details', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ places, plan_id: planId || null }), signal })
    .then(response => j<{ places: PlaceDetail[]; queries: number }>(response))

export const getPlaceMedia = (kind: 'poi' | 'meal', id: number, signal?: AbortSignal, planId?: string | null) =>
  fetch(`/api/place-media?kind=${kind}&id=${id}${planId ? `&plan_id=${encodeURIComponent(planId)}` : ''}`, { signal }).then(response => j<PlaceMedia>(response))
export const swapMeal = (planId: string, body: { revision: number; day: number; item_index: number; restaurant_id: number }) =>
  fetch(`/api/plans/${encodeURIComponent(planId)}/swap-meal`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
    .then(response => j<PlanResponse>(response))
export const revertPlan = (planId: string, revision: number, baseRevision: number) =>
  fetch(`/api/plans/${encodeURIComponent(planId)}/revert`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ revision, base_revision: baseRevision }) })
    .then(response => j<PlanResponse>(response))

export const DAY_COLORS = ['#2563eb', '#dc2626', '#16a34a', '#9333ea', '#ea580c']

export const localQueryDate = (value: string) => {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(0, 10)
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' }).format(date)
}

// MCP 保存的同账号深链；活跃时返回共享会话，否则返回只读历史。
export const getLinkedTrip = async (id: string) => j<HistoryDetail>(await fetch(`/api/trips/${encodeURIComponent(id)}`, { cache: 'no-store' }))
