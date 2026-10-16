// 地图镜头动画：平滑、偏慢、随时可打断。
// - 路径用 van Wijk & Nuij《Smooth and efficient zooming and panning》（2003）的缩放-平移插值（d3 interpolateZoom、
//   Mapbox flyTo 同源）：远距离先拉远再推近，近距离只平移，屏幕上的感知速度大致恒定。
// - 时间轴再套一条缓入缓出曲线，起止不突兀；整体比高德默认动画慢（约 0.9–2.2 秒，随距离增加）。
// - 用户一按下、滚轮、触摸或用键盘操作地图，动画立即停止，控制权交还用户；新的目标会从当前画面无缝接上。
// - 系统开启"减少动态效果"时直接跳到目标。
// 与地图引擎解耦：通过 CameraAdapter 读写中心点和缩放级，高德 JS API 2.0 支持小数缩放级。

export type LngLat = [number, number]

export interface CameraAdapter {
  container: HTMLElement
  getCenter(): LngLat
  getZoom(): number
  setView(center: LngLat, zoom: number): void
}

export interface Padding { top: number; right: number; bottom: number; left: number }

const TILE = 256
const RHO = Math.SQRT2
const SPEED = 0.85 // 越小越慢；1.0 约等于 d3 默认速度（用户要求"慢一点"）
const MIN_MS = 900
const MAX_MS = 2200

const easeInOutCubic = (t: number) => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2)
const prefersReducedMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches

// 墨卡托世界坐标（缩放级 0 时整个世界宽 256）
export function project([lng, lat]: LngLat): [number, number] {
  const s = Math.sin((Math.max(-85, Math.min(85, lat)) * Math.PI) / 180)
  return [((lng + 180) / 360) * TILE, (0.5 - Math.log((1 + s) / (1 - s)) / (4 * Math.PI)) * TILE]
}

export function unproject([x, y]: [number, number]): LngLat {
  const n = Math.PI - (2 * Math.PI * y) / TILE
  return [(x / TILE) * 360 - 180, (180 / Math.PI) * Math.atan(Math.sinh(n))]
}

type ZoomPath = ((t: number) => [number, number, number]) & { S: number }

// p = [ux, uy, w]：中心点的世界坐标 + 视口宽度（世界坐标单位）
function zoomPath(p0: [number, number, number], p1: [number, number, number]): ZoomPath {
  const [ux0, uy0, w0] = p0
  const [ux1, uy1, w1] = p1
  const dx = ux1 - ux0
  const dy = uy1 - uy0
  const d2 = dx * dx + dy * dy
  let path: (t: number) => [number, number, number]
  let S: number
  if (d2 < 1e-12) {
    S = Math.log(w1 / w0) / RHO
    path = t => [ux0 + t * dx, uy0 + t * dy, w0 * Math.exp(RHO * t * S)]
  } else {
    const d1 = Math.sqrt(d2)
    const b0 = (w1 * w1 - w0 * w0 + RHO ** 4 * d2) / (2 * w0 * RHO ** 2 * d1)
    const b1 = (w1 * w1 - w0 * w0 - RHO ** 4 * d2) / (2 * w1 * RHO ** 2 * d1)
    const r0 = Math.log(Math.sqrt(b0 * b0 + 1) - b0)
    const r1 = Math.log(Math.sqrt(b1 * b1 + 1) - b1)
    S = (r1 - r0) / RHO
    path = t => {
      const s = t * S
      const coshR0 = Math.cosh(r0)
      const u = (w0 / (RHO ** 2 * d1)) * (coshR0 * Math.tanh(RHO * s + r0) - Math.sinh(r0))
      return [ux0 + u * dx, uy0 + u * dy, (w0 * coshR0) / Math.cosh(RHO * s + r0)]
    }
  }
  return Object.assign(path, { S: Math.abs(S) })
}

export interface Camera {
  flyTo(center: LngLat, zoom: number): void
  jumpTo(center: LngLat, zoom: number): void
  fit(points: LngLat[], padding: Padding, maxZoom: number, minZoom?: number): { center: LngLat; zoom: number } | null
  cancel(): void
  destroy(): void
  readonly busy: boolean
}

export function createCamera(adapter: CameraAdapter): Camera {
  let frame = 0
  const stop = () => {
    if (frame) cancelAnimationFrame(frame)
    frame = 0
  }
  // 用户的任何直接操作都会打断动画
  const events: (keyof HTMLElementEventMap)[] = ['pointerdown', 'wheel', 'touchstart', 'keydown']
  events.forEach(name => adapter.container.addEventListener(name, stop, { passive: true }))

  const width = () => Math.max(1, adapter.container.clientWidth)

  function jumpTo(center: LngLat, zoom: number) {
    stop()
    adapter.setView(center, zoom)
  }

  function flyTo(center: LngLat, zoom: number) {
    stop()
    const startCenter = adapter.getCenter()
    const startZoom = adapter.getZoom()
    if (prefersReducedMotion()) return adapter.setView(center, zoom)
    const W = width()
    const [x0, y0] = project(startCenter)
    const [x1, y1] = project(center)
    const path = zoomPath([x0, y0, W / 2 ** startZoom], [x1, y1, W / 2 ** zoom])
    const duration = Math.min(MAX_MS, Math.max(MIN_MS, (path.S * 1000) / SPEED))
    if (path.S < 1e-3) return adapter.setView(center, zoom)
    const t0 = performance.now()
    const step = (now: number) => {
      const t = Math.min(1, (now - t0) / duration)
      const [ux, uy, w] = path(easeInOutCubic(t))
      adapter.setView(t >= 1 ? center : unproject([ux, uy]), t >= 1 ? zoom : Math.log2(W / w))
      frame = t < 1 ? requestAnimationFrame(step) : 0
    }
    frame = requestAnimationFrame(step)
  }

  // 根据一组点计算能完整显示它们的中心与缩放级（考虑面板/浮层遮挡的边距）
  function fit(points: LngLat[], padding: Padding, maxZoom: number, minZoom = 3) {
    if (!points.length) return null
    const projected = points.map(project)
    const xs = projected.map(p => p[0])
    const ys = projected.map(p => p[1])
    const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)]
    const W = width()
    const H = Math.max(1, adapter.container.clientHeight)
    const usableW = Math.max(40, W - padding.left - padding.right)
    const usableH = Math.max(40, H - padding.top - padding.bottom)
    const spanX = Math.max(maxX - minX, 1e-9)
    const spanY = Math.max(maxY - minY, 1e-9)
    const zoom = Math.max(minZoom, Math.min(maxZoom, Math.log2(Math.min(usableW / spanX, usableH / spanY))))
    const scale = 2 ** zoom
    // 让点集落在"去掉边距后的可用区域"中间
    const cx = (minX + maxX) / 2 + (padding.right - padding.left) / 2 / scale
    const cy = (minY + maxY) / 2 + (padding.bottom - padding.top) / 2 / scale
    return { center: unproject([cx, cy]), zoom }
  }

  return {
    flyTo,
    jumpTo,
    fit,
    cancel: stop,
    destroy() {
      stop()
      events.forEach(name => adapter.container.removeEventListener(name, stop))
    },
    get busy() { return frame !== 0 },
  }
}
