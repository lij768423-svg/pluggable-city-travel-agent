<script setup lang="ts">
// 高德 JS API 2.0 地图（设计决策 A8）。与 MapView.vue（Leaflet 兜底）接口一致。
// 安全密钥不在前端：只设置 serviceHost，由后端 /_AMapService 代理附加 jscode。
// 镜头移动统一交给 map/camera.ts：平滑、偏慢、可打断；路线几何晚到时只换线，不动镜头。
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Hotel, PlanResponse, NavigationResponse, MapFocus, AltMarker } from '../api'
import { createCamera, type Camera, type LngLat } from '../map/camera'

/* eslint-disable @typescript-eslint/no-explicit-any */
declare global { interface Window { AMap?: any; _AMapSecurityConfig?: { serviceHost: string } } }

const props = defineProps<{
  plan: PlanResponse | null; activeDay: number; selectedIndex: number | null; previewHotel?: Hotel | null; amapKey: string
  navigation: NavigationResponse | null; focusedPlace: MapFocus | null; focusRevision: number
  alternatives: AltMarker[]; alternativeAnchor: { lat: number; lon: number } | null; selectedAlternativeId: number | null
}>()
const emit = defineEmits<{ focus: [index: number]; focusAlternative: [refId: number]; failed: [] }>()
const el = ref<HTMLDivElement>()
let map: any = null
let camera: Camera | null = null
let resizeObserver: ResizeObserver | null = null
let pendingFit = false
const stopLayer: any[] = []
const routeLayer: any[] = []
const altLayer: any[] = []
let stopMarkers: any[] = []
let labels: string[] = []
let kinds: string[] = []
let altMarkers = new Map<number, any>()

const ROUTE = '#2f5bd3'
const MEAL = '#a0662a'
// 左上角有行程浮层，四周留出边距，保证点不会被遮住
const PADDING = { top: 120, right: 70, bottom: 70, left: 70 }

let loading: Promise<any> | null = null
function loadAmap(key: string): Promise<any> {
  if (window.AMap) return Promise.resolve(window.AMap)
  if (loading) return loading
  window._AMapSecurityConfig = { serviceHost: `${location.origin}/_AMapService` }
  loading = new Promise((resolve, reject) => {
    const s = document.createElement('script')
    s.src = `https://webapi.amap.com/maps?v=2.0&key=${encodeURIComponent(key)}&plugin=AMap.ToolBar`
    s.onload = () => (window.AMap ? resolve(window.AMap) : reject(new Error('AMap 未定义')))
    s.onerror = () => reject(new Error('高德地图脚本加载失败'))
    document.head.appendChild(s)
  })
  return loading
}

const pinHtml = (text: string, cls: string) => `<div class="pin ${cls}">${text}</div>`
const escaped = (value: string) => value.replace(/[&<>"']/g, character => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[character]!)
const tip = (text: string) => ({ content: `<span class="amap-tip">${escaped(text)}</span>`, direction: 'top', offset: [0, -6] })

function clear(layer: any[]) {
  if (layer.length) map.remove(layer)
  layer.length = 0
}

function visible() { return (el.value?.clientWidth ?? 0) > 50 && (el.value?.clientHeight ?? 0) > 50 }

// 镜头：第一次直接到位，之后都平滑移动；容器不可见（手机行程视图）时等显示后再对准
function frame(points: LngLat[], maxZoom: number, animate: boolean) {
  if (!camera || !points.length) return
  if (!visible()) { pendingFit = true; return }
  pendingFit = false
  const target = points.length === 1 ? { center: points[0]!, zoom: maxZoom } : camera.fit(points, PADDING, maxZoom)
  if (!target) return
  if (animate) camera.flyTo(target.center, target.zoom)
  else camera.jumpTo(target.center, target.zoom)
}

let lastFramedKey = ''
function drawStops(animate: boolean) {
  const AMap = window.AMap
  if (!map || !AMap) return
  clear(stopLayer)
  stopMarkers = []
  labels = []
  kinds = []
  const p = props.plan
  if (!p) {
    const h = props.previewHotel
    if (h?.gcj) {
      stopLayer.push(new AMap.Marker({ position: [h.gcj.lon, h.gcj.lat], content: pinHtml('起', 'home'), anchor: 'center', title: h.name }))
      map.add(stopLayer)
      frame([[h.gcj.lon, h.gcj.lat]], 13, animate)
    }
    return
  }
  const day = p.days.find(d => d.day === props.activeDay)
  const home: LngLat = [p.profile.hotel_gcj.lon, p.profile.hotel_gcj.lat]
  stopLayer.push(new AMap.Marker({ position: home, content: pinHtml('起', 'home'), anchor: 'center', title: p.profile.hotel.name, zIndex: 130 }))
  const points: LngLat[] = [home]
  let n = 0
  day?.items.forEach((it, i) => {
    const label = it.kind === 'meal' ? '餐' : String(++n)
    if (it.lat == null || it.lon == null) return
    labels[i] = label
    kinds[i] = it.kind === 'meal' ? 'meal' : ''
    const m = new AMap.Marker({ position: [it.lon, it.lat], content: pinHtml(label, kinds[i]!), anchor: 'center', title: `${it.start_hm} ${it.name}`, zIndex: 120 })
    m.on('click', () => emit('focus', i))
    m.setExtData({ name: `${it.start_hm} ${it.name}` })
    stopMarkers[i] = m
    stopLayer.push(m)
    points.push([it.lon, it.lat])
  })
  map.add(stopLayer)
  // 同一天、同一组点（例如只换了路线几何或重新渲染）不重复移动镜头
  const key = `${p.plan_id ?? ''}:${p.revision ?? ''}:${props.activeDay}:${points.map(point => point.join(',')).join('|')}`
  if (key !== lastFramedKey) {
    lastFramedKey = key
    frame(points, 15, animate)
  }
  highlight(false)
}

function drawRoutes() {
  const AMap = window.AMap
  if (!map || !AMap) return
  clear(routeLayer)
  if (!props.plan) return
  for (const leg of props.navigation?.legs || []) for (const segment of leg.segments) {
    routeLayer.push(new AMap.Polyline({ path: segment.path, strokeColor: segment.mode === '步行' ? '#6e8ec8' : ROUTE,
      strokeWeight: segment.mode === '步行' ? 4 : 5, strokeOpacity: 0.9, outlineColor: '#ffffff', borderWeight: 1,
      isOutline: true, showDir: true, lineJoin: 'round', lineCap: 'round', zIndex: 50 }))
  }
  if (routeLayer.length) map.add(routeLayer)
}

// 备选餐厅：在主餐厅旁用"备1/备2"标出，并用细虚线连回主餐厅
function drawAlternatives() {
  const AMap = window.AMap
  if (!map || !AMap) return
  clear(altLayer)
  altMarkers = new Map()
  const anchor = props.alternativeAnchor
  for (const alt of props.alternatives) {
    const on = alt.ref_id === props.selectedAlternativeId
    const marker = new AMap.Marker({ position: [alt.lon, alt.lat], content: pinHtml(alt.label, `alt ${on ? 'on' : ''}`), anchor: 'center',
      title: `备选 · ${alt.name}`, zIndex: on ? 150 : 110 })
    marker.on('click', () => emit('focusAlternative', alt.ref_id))
    if (on) marker.setLabel(tip(`${alt.label} · ${alt.name}`))
    altMarkers.set(alt.ref_id, marker)
    altLayer.push(marker)
    if (anchor) altLayer.push(new AMap.Polyline({ path: [[anchor.lon, anchor.lat], [alt.lon, alt.lat]], strokeColor: MEAL,
      strokeOpacity: on ? 0.75 : 0.45, strokeWeight: 2, strokeStyle: 'dashed', strokeDasharray: [4, 6], zIndex: 45 }))
  }
  if (altLayer.length) map.add(altLayer)
}

function highlight(move: boolean) {
  if (!map || !window.AMap) return
  stopMarkers.forEach((m, i) => {
    if (!m) return
    const on = i === props.selectedIndex
    m.setContent(pinHtml(labels[i]!, `${kinds[i]} ${on ? 'on' : ''}`))
    m.setzIndex(on ? 140 : 120)
    m.setLabel(on ? tip(m.getExtData().name) : null)
  })
  const focused = props.focusedPlace
  if (!move || !focused || !camera) return
  if (!visible()) { pendingFit = true; return }
  camera.flyTo([focused.lon, focused.lat], Math.max(map.getZoom(), 15))
}

onMounted(async () => {
  try {
    const AMap = await loadAmap(props.amapKey)
    map = new AMap.Map(el.value!, { zoom: 12, center: [120.16, 30.25], resizeEnable: true, viewMode: '2D' })
    map.addControl(new AMap.ToolBar({ position: 'RB' }))
    camera = createCamera({
      container: el.value!,
      getCenter: () => { const c = map.getCenter(); return [c.getLng(), c.getLat()] },
      getZoom: () => map.getZoom(),
      setView: (center, zoom) => map.setZoomAndCenter(zoom, center, true),
    })
    resizeObserver = new ResizeObserver(() => {
      if (pendingFit && visible()) { lastFramedKey = ''; drawStops(false) }
    })
    resizeObserver.observe(el.value!)
    drawStops(false)
    drawRoutes()
    drawAlternatives()
  } catch {
    emit('failed')
  }
})
onBeforeUnmount(() => { camera?.destroy(); resizeObserver?.disconnect(); map?.destroy() })
watch(() => [props.plan, props.activeDay, props.previewHotel], () => { drawStops(true); drawRoutes(); drawAlternatives() })
watch(() => props.navigation, drawRoutes)
watch(() => [props.alternatives, props.selectedAlternativeId, props.alternativeAnchor], drawAlternatives)
watch(() => [props.selectedIndex, props.focusedPlace, props.focusRevision], () => highlight(true))
</script>

<template>
  <div ref="el" class="map" />
</template>
