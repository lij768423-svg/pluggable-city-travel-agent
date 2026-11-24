// 共用弹簧：把“刚度 阻尼”算成 CSS linear() 缓动和时长，写到 :root 的 --spring-<名字> 与 --spring-<名字>-ms，
// 过渡和关键帧直接引用。思路参考 DesignBookmark Motion 的 tokens.css，代码是这里重写的；
// 阻尼都比参考值调高（少回弹），对应 2026-10-14 定的动效强度 4。不支持 linear() 的浏览器用 motion.css 里的 cubic-bezier 兜底。
import { nextTick, watch, type Ref, type WatchSource } from 'vue'

type Spring = readonly [stiffness: number, damping: number]
export const SPRINGS = {
  fade: [420, 34],   // 透明度与小位移
  settle: [300, 28], // 落到新值，最多一点点过冲
  soft: [260, 28],   // 较长的移动：进度线
  lead: [620, 44],   // 伸缩高亮朝前的一边
  trail: [220, 26],  // 伸缩高亮落后的一边，两边速度不同才有“拉长再收拢”
  pop: [520, 26],    // 打勾、编号选中时弹一下
  roll: [320, 28],   // 数字逐位翻动
} as const satisfies Record<string, Spring>
export type SpringName = keyof typeof SPRINGS

const durations = new Map<SpringName, number>()

// 单位质量弹簧从 0 走到 1，按 60 帧采样，停稳就结束
function sample([stiffness, damping]: Spring) {
  const dt = 1 / 1000
  const frame = 1 / 60
  let x = 0
  let v = 0
  let t = 0
  let next = frame
  const points = [0]
  while (t < 2) {
    v += (-stiffness * (x - 1) - damping * v) * dt
    x += v * dt
    t += dt
    if (t >= next) {
      points.push(x)
      next += frame
      if (Math.abs(x - 1) < 0.002 && Math.abs(v) < 0.02) break
    }
  }
  points[points.length - 1] = 1
  return { easing: `linear(${points.map(point => +point.toFixed(4)).join(', ')})`, ms: Math.round((points.length - 1) * 1000 / 60) }
}

export function installSprings() {
  const linear = typeof CSS !== 'undefined' && CSS.supports('transition-timing-function', 'linear(0, 1)')
  const root = document.documentElement.style
  for (const [name, spring] of Object.entries(SPRINGS) as [SpringName, Spring][]) {
    const { easing, ms } = sample(spring)
    durations.set(name, ms)
    if (!linear) continue
    root.setProperty(`--spring-${name}`, easing)
    root.setProperty(`--spring-${name}-ms`, `${ms}ms`)
  }
}

export const springMs = (name: SpringName) => durations.get(name) ?? 450
export const reducedMotion = () => typeof matchMedia !== 'undefined' && matchMedia('(prefers-reduced-motion: reduce)').matches

// 伸缩高亮：容器里放一个 <span class="ink">，跟到带 .on 的那一项下面。
// 往右走时右边沿用快弹簧、左边沿用慢弹簧（往左反过来），所以会先拉长再收拢。
// 首次出现、容器尺寸变化、减少动态效果时直接到位，不播过渡。inset 是左右各缩进多少像素（下划线用）。
export function useInk(container: Ref<HTMLElement | null | undefined>, active: WatchSource<unknown>, inset = 0) {
  let lastLeft: number | null = null
  function place(instant: boolean) {
    const el = container.value
    const ink = el?.querySelector<HTMLElement>(':scope > .ink')
    if (!el || !ink) return
    const on = el.querySelector<HTMLElement>(':scope > .on')
    if (!on) { ink.style.visibility = 'hidden'; lastLeft = null; return }
    const left = on.offsetLeft + inset
    const right = el.clientWidth - on.offsetLeft - on.offsetWidth + inset
    const jump = instant || lastLeft == null || reducedMotion()
    if (jump) el.classList.add('ink-instant')
    else el.dataset.inkDir = left >= lastLeft! ? 'right' : 'left'
    ink.style.visibility = ''
    ink.style.left = `${left}px`
    ink.style.right = `${right}px`
    if (jump) { void ink.offsetWidth; el.classList.remove('ink-instant') }
    lastLeft = left
  }
  watch(active, () => nextTick(() => place(false)))
  watch(container, (el, _old, onCleanup) => {
    lastLeft = null
    if (!el) return
    const observer = new ResizeObserver(() => place(true))
    observer.observe(el)
    void document.fonts?.ready.then(() => place(true))
    onCleanup(() => observer.disconnect())
  }, { flush: 'post', immediate: true })
}
