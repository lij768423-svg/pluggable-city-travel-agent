// 共用弹簧：把“刚度 阻尼”算成 CSS linear() 缓动和时长，写到 :root 的 --spring-<名字> 与 --spring-<名字>-ms，
// 过渡和关键帧直接引用。思路参考 DesignBookmark Motion 的 tokens.css，代码是这里重写的；
// 阻尼都比参考值调高（少回弹），对应 2026-10-14 定的动效强度 4。不支持 linear() 的浏览器用 motion.css 里的 cubic-bezier 兜底。
import { nextTick, watch, type Directive, type Ref, type WatchSource } from 'vue'

type Spring = readonly [stiffness: number, damping: number]
export const SPRINGS = {
  fade: [420, 34],   // 透明度与小位移
  settle: [300, 28], // 落到新值，最多一点点过冲
  soft: [260, 28],   // 较长的移动：进度线
  lead: [620, 44],   // 伸缩高亮朝前的一边
  trail: [220, 26],  // 伸缩高亮落后的一边，两边速度不同才有“拉长再收拢”
  pop: [520, 26],    // 打勾、编号选中时弹一下
  roll: [320, 28],   // 数字逐位翻动
  expand: [420, 42], // 展开收起的高度，过阻尼不回弹（回弹会露出一条空白）
} as const satisfies Record<string, Spring>
export type SpringName = keyof typeof SPRINGS

const durations = new Map<SpringName, number>()
const easings = new Map<SpringName, string>()

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
    easings.set(name, easing)
    root.setProperty(`--spring-${name}`, easing)
    root.setProperty(`--spring-${name}-ms`, `${ms}ms`)
  }
}

export const springMs = (name: SpringName) => durations.get(name) ?? 450
const springEasing = (name: SpringName) => easings.get(name) ?? 'cubic-bezier(.22, 1, .36, 1)'
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

// 高度从 from 动到 to；done 之后样式全部撤掉，交还给内容自己的高度
function growHeight(el: HTMLElement, from: number, to: number, done: () => void) {
  el.style.overflow = 'clip'
  const animation = el.animate([{ height: `${from}px` }, { height: `${to}px` }],
    { duration: springMs('expand'), easing: springEasing('expand'), fill: 'forwards' })
  animation.onfinish = () => { done(); animation.cancel(); el.style.overflow = '' }
  return animation
}

const px = (value: string) => Number.parseFloat(value) || 0

// 展开块：保留原生 <details>（语义、键盘、页内查找不变），点标题时把高度弹开；收起时先收完再真正关上。
// 连点从当前高度折返；脚本直接改 open 的不播。内容放进 .expand-body 的块（展开后才读内容），内容到了再从旧高度弹到新高度。
export const vExpand: Directive<HTMLDetailsElement & { expandCleanup?: () => void }> = {
  mounted(el) {
    const summary = el.querySelector<HTMLElement>(':scope > summary')
    if (!summary || typeof el.animate !== 'function') return
    const body = el.querySelector<HTMLElement>(':scope > .expand-body')
    let animation: Animation | null = null
    let target = 0
    let settled = 0
    let width = 0
    function stop() {
      if (!animation) return
      animation.onfinish = null
      animation.cancel()
      animation = null
      el.style.overflow = ''
      delete el.dataset.expanding
    }
    function run(from: number, to: number, opening: boolean) {
      stop()
      target = to
      if (Math.abs(from - to) < 1) { if (!opening) el.open = false; return }
      el.dataset.expanding = opening ? 'open' : 'close'
      animation = growHeight(el, from, to, () => {
        animation = null
        if (!opening) el.open = false
        delete el.dataset.expanding
      })
    }
    function closedHeight() {
      const box = getComputedStyle(el)
      const title = getComputedStyle(summary!)
      return summary!.offsetHeight + [title.marginTop, title.marginBottom, box.paddingTop, box.paddingBottom, box.borderTopWidth, box.borderBottomWidth].reduce((sum, value) => sum + px(value), 0)
    }
    function toggle(event: MouseEvent) {
      if (event.defaultPrevented || reducedMotion() || (event.target as HTMLElement).closest('a, button, input')) return
      event.preventDefault()
      const from = el.offsetHeight
      const opening = !el.open || el.dataset.expanding === 'close'
      stop()
      if (opening) {
        el.open = true
        settled = el.offsetHeight
        run(from, settled, true)
      } else run(from, closedHeight(), false)
    }
    const observer = body && new ResizeObserver(() => {
      if (!el.open) { settled = 0; width = 0; return }
      if (el.dataset.expanding === 'close') return
      const sameWidth = !width || width === body.offsetWidth  // 面板变宽变窄引起的换行不播
      width = body.offsetWidth
      const natural = closedHeight() + body.offsetHeight
      if (animation) { if (Math.abs(natural - target) >= 1) run(el.offsetHeight, natural, true) }
      else if (settled && sameWidth && !reducedMotion()) run(settled, natural, true)
      settled = natural
    })
    summary.addEventListener('click', toggle)
    if (body) observer?.observe(body)
    el.expandCleanup = () => { summary.removeEventListener('click', toggle); observer?.disconnect(); stop() }
  },
  unmounted(el) { el.expandCleanup?.() },
}

// 用 v-if 出入的块：配 <Transition :css="false" v-bind="expandHooks">
export const expandHooks = {
  onEnter(el: Element, done: () => void) {
    if (reducedMotion() || typeof el.animate !== 'function') { done(); return }
    growHeight(el as HTMLElement, 0, (el as HTMLElement).offsetHeight, done)
  },
  onLeave(el: Element, done: () => void) {
    if (reducedMotion() || typeof el.animate !== 'function') { done(); return }
    growHeight(el as HTMLElement, (el as HTMLElement).offsetHeight, 0, done)
  },
}
