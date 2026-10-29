<script setup lang="ts">
// Leaflet 兜底地图（高德 JS API 加载失败时使用），接口与镜头行为与 AmapView.vue 一致。
import L from 'leaflet'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import type { Hotel, PlanResponse, NavigationResponse, MapFocus, AltMarker } from '../api'
import { createCamera, type Camera, type LngLat } from '../map/camera'

const props = defineProps<{
  mapCenter: { lat: number; lon: number }
  plan: PlanResponse | null; activeDay: number; selectedIndex: number | null; previewHotel?: Hotel | null
  navigation: NavigationResponse | null; focusedPlace: MapFocus | null; focusRevision: number
  alternatives: AltMarker[]; alternativeAnchor: { lat: number; lon: number } | null; selectedAlternativeId: number | null
}>()
const emit = defineEmits<{ focus: [index: number]; focusAlternative: [refId: number] }>()
const el = ref<HTMLDivElement>()
let map: L.Map | null = null
let camera: Camera | null = null
let stops: L.LayerGroup | null = null
let routes: L.LayerGroup | null = null
let alts: L.LayerGroup | null = null
let markers: L.Marker[] = []
let resizeObserver: ResizeObserver | null = null
let pendingFit = false
let lastFramedKey = ''

// 高德公开瓦片（GCJ-02）；后端已把 BD-09 数据坐标转为 GCJ-02。仅用于开发演示（docs/设计决策记录.md A8）
const AMAP_TILES = 'https://webrd0{s}.is.autonavi.com/appmaptile?lang=zh_cn&size=1&scale=1&style=8&x={x}&y={y}&z={z}'
const ROUTE = '#b93b28'
const MEAL = '#a0662a'
const PADDING = { top: 120, right: 70, bottom: 70, left: 70 }

const pin = (text: string, cls = '') =>
  L.divIcon({ className: '', html: `<div class="pin ${cls}">${text}</div>`, iconSize: [28, 28], iconAnchor: [14, 14] })
const visible = () => (el.value?.clientWidth ?? 0) > 50 && (el.value?.clientHeight ?? 0) > 50

function frame(points: LngLat[], maxZoom: number, animate: boolean) {
  if (!camera || !points.length) return
  if (!visible()) { pendingFit = true; return }
  pendingFit = false
  const target = points.length === 1 ? { center: points[0]!, zoom: maxZoom } : camera.fit(points, PADDING, maxZoom)
  if (!target) return
  if (animate) camera.flyTo(target.center, target.zoom)
  else camera.jumpTo(target.center, target.zoom)
}

function drawStops(animate: boolean) {
  if (!map || !stops) return
  stops.clearLayers()
  markers = []
  const p = props.plan
  if (!p) {
    const h = props.previewHotel
    if (h?.gcj) {
      L.marker([h.gcj.lat, h.gcj.lon], { icon: pin('起', 'home') }).bindTooltip(h.name).addTo(stops)
      frame([[h.gcj.lon, h.gcj.lat]], 13, animate)
    }
    return
  }
  const day = p.days.find(d => d.day === props.activeDay)
  const home: LngLat = [p.profile.hotel_gcj.lon, p.profile.hotel_gcj.lat]
  // 起点不定时默认点不是真的起点，不画“起”，取景也不算它
  const open = p.profile.origin_known === false
  if (!open) L.marker([home[1], home[0]], { icon: pin('起', 'home'), zIndexOffset: 1000 }).bindTooltip(p.profile.hotel.name).addTo(stops)
  const points: LngLat[] = open ? [] : [home]
  let n = 0
  day?.items.forEach((it, i) => {
    const label = it.kind === 'meal' ? '餐' : String(++n)
    if (it.lat == null || it.lon == null) return
    points.push([it.lon, it.lat])
    markers[i] = L.marker([it.lat, it.lon], { icon: pin(label, it.kind === 'meal' ? 'meal' : ''), title: it.name, riseOnHover: true })
      .bindTooltip(`${it.start_hm} ${it.name}`, { direction: 'top', offset: [0, -12] })
      .on('click', () => emit('focus', i))
      .addTo(stops!)
  })
  const key = `${p.plan_id ?? ''}:${p.revision ?? ''}:${props.activeDay}:${points.map(point => point.join(',')).join('|')}`
  if (key !== lastFramedKey) {
    lastFramedKey = key
    frame(points, 15, animate)
  }
  highlight(false)
}

function drawRoutes() {
  if (!routes) return
  routes.clearLayers()
  if (!props.plan) return
  for (const leg of props.navigation?.legs || []) for (const segment of leg.segments) {
    const geometry: L.LatLngExpression[] = segment.path.map(([longitude, latitude]) => [latitude, longitude])
    L.polyline(geometry, { color: segment.mode === '步行' ? '#696b64' : ROUTE, weight: segment.mode === '步行' ? 4 : 5, opacity: 0.9 }).addTo(routes)
  }
}

function drawAlternatives() {
  if (!alts) return
  alts.clearLayers()
  const anchor = props.alternativeAnchor
  for (const alt of props.alternatives) {
    const on = alt.ref_id === props.selectedAlternativeId
    if (anchor) L.polyline([[anchor.lat, anchor.lon], [alt.lat, alt.lon]], { color: MEAL, weight: 2, opacity: on ? 0.75 : 0.45, dashArray: '4 6' }).addTo(alts)
    const marker = L.marker([alt.lat, alt.lon], { icon: pin(alt.label, `alt ${on ? 'on' : ''}`), title: `备选 · ${alt.name}`, zIndexOffset: on ? 1500 : 600 })
      .on('click', () => emit('focusAlternative', alt.ref_id)).addTo(alts)
    if (on) marker.bindTooltip(`${alt.label} · ${alt.name}`, { direction: 'top', offset: [0, -12], permanent: true })
  }
}

function highlight(move: boolean) {
  if (!map) return
  markers.forEach((m, i) => {
    m?.getElement()?.classList.toggle('on', i === props.selectedIndex)
    if (i === props.selectedIndex) m?.openTooltip()
    else m?.closeTooltip()
  })
  const focused = props.focusedPlace
  if (!move || !focused || !camera) return
  if (!visible()) { pendingFit = true; return }
  camera.flyTo([focused.lon, focused.lat], Math.max(map.getZoom(), 15))
}

onMounted(() => {
  // zoomSnap 0：允许小数缩放级，动画每一帧都能连续缩放
  map = L.map(el.value!, { zoomControl: false, attributionControl: true, zoomSnap: 0 }).setView([props.mapCenter.lat, props.mapCenter.lon], 12)
  L.control.zoom({ position: 'bottomright' }).addTo(map)
  L.tileLayer(AMAP_TILES, { subdomains: '1234', maxZoom: 18, attribution: '底图 © 高德地图 · 数据 ChinaTravel (CC BY 4.0)' }).addTo(map)
  routes = L.layerGroup().addTo(map)
  alts = L.layerGroup().addTo(map)
  stops = L.layerGroup().addTo(map)
  camera = createCamera({
    container: el.value!,
    getCenter: () => { const c = map!.getCenter(); return [c.lng, c.lat] },
    getZoom: () => map!.getZoom(),
    setView: ([lng, lat], zoom) => { map!.setView([lat, lng], zoom, { animate: false }) },
  })
  resizeObserver = new ResizeObserver(() => {
    map?.invalidateSize()
    if (pendingFit && visible()) { lastFramedKey = ''; drawStops(false) }
  })
  resizeObserver.observe(el.value!)
  drawStops(false)
  drawRoutes()
  drawAlternatives()
})
onBeforeUnmount(() => { camera?.destroy(); resizeObserver?.disconnect(); map?.remove() })
watch(() => [props.plan, props.activeDay, props.previewHotel], () => { drawStops(true); drawRoutes(); drawAlternatives() })
watch(() => props.navigation, drawRoutes)
watch(() => [props.alternatives, props.selectedAlternativeId, props.alternativeAnchor], drawAlternatives)
watch(() => [props.selectedIndex, props.focusedPlace, props.focusRevision], () => highlight(true))
</script>

<template>
  <div ref="el" class="map" />
</template>
