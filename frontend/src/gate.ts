// 登录页的横票和其他页面的竖票是同一张：进登录页时竖票展开成横票，返回或登录成功时横票收回竖票（2026-11-25 用户）。
// 从头到尾只有一张票在演——登录页那张真票；页面上的竖票在演完之前藏着，所以不会有两层阴影、两份字。
// 竖票上有、横票上没有的几样（格子、一行小字、虚线、按钮上的字）临时补在横票里，开头淡出、收尾淡入，和横票自己的内容错开。
// 两头要对得上：起止位置都按真票当场量，量到哪就从哪开始、停在哪，最后一帧换成真票时看不出来。
import { nextTick } from 'vue'
import { captureSwap, slideLeft, swapIn, visibleTicket, type Swap } from './departure'
import { reducedMotion } from './motion'

export interface GateFace { name: string; code: string; art: string | null; photo: boolean; bars: string[] }
interface Vertical { ticket: HTMLElement; sheet: DOMRect; seam: number; art: DOMRect; picture: Record<string, string> | null; mark: DOMRect | null; name: DOMRect; code: DOMRect; bars: DOMRect; widths: string[]; grid: DOMRect; note: DOMRect; go: DOMRect; ground: Record<string, string>; tilt: string }
export interface GateScene { face: GateFace; from: Vertical | null; swap: Swap | null }

const EASE = 'cubic-bezier(.55, 0, .12, 1)'
const [OPEN, CLOSE] = [640, 580]
const INK = 'rgb(21, 22, 19)'
const px = (value: number) => `${value.toFixed(2)}px`

function measure(ticket: HTMLElement): Vertical | null {
  const find = (selector: string) => ticket.querySelector<HTMLElement>(selector)
  const [main, stub, art, name, code, bars, grid, note, go] = ['.ticket-main', '.ticket-stub', '.ticket-art', '.ticket-city b', '.ticket-city > span', '.ticket-code', '.ticket-grid', '.ticket-note', '.ticket-go'].map(find)
  if (!main || !stub || !art || !name || !code || !bars || !grid || !note || !go) return null
  // 票可能正随指针微微倾斜：按放平的位置量，倾斜另外补
  const { transform: tilt } = getComputedStyle(ticket)
  const held = ticket.style.cssText
  ticket.style.transition = 'none'
  ticket.style.transform = 'none'
  const box = (el: Element) => el.getBoundingClientRect()
  const [top, bottom] = [box(main), box(stub)]
  const image = find('.ticket-art img')
  const mark = find('.ticket-art .ticket-mark')
  const picture = image && getComputedStyle(image)
  const { backgroundColor, color, opacity } = getComputedStyle(go)
  // 不可点的按钮是半透明的，飞过条码时会透出来：按叠在白底上的颜色算成实色
  const solid = (value: string) => { const [r, g, b] = (value.match(/[\d.]+/g) ?? []).map(Number); return `rgb(${[r!, g!, b!].map(one => Math.round(one * +opacity + 255 * (1 - +opacity))).join(', ')})` }
  const found: Vertical = {
    ticket, sheet: new DOMRect(top.left, top.top, top.width, bottom.bottom - top.top), seam: bottom.top - top.top, art: box(art),
    picture: picture && { right: picture.right, bottom: picture.bottom, height: picture.height }, mark: mark && box(mark),
    name: box(name), code: box(code), bars: box(bars), widths: [...bars.querySelectorAll<HTMLElement>('i')].map(bar => bar.style.width),
    grid: box(grid), note: box(note), go: box(go), ground: { backgroundColor: solid(backgroundColor), color }, tilt,
  }
  ticket.style.cssText = held
  return found
}

// 进登录页之前调用：记下票面是哪座城、竖票在哪，左边的内容留一份副本
export function captureGate(): GateScene | null {
  const ticket = visibleTicket()
  const swap = captureSwap()
  if (!ticket) return null
  const image = ticket.querySelector<HTMLImageElement>('.ticket-art img')
  const text = (selector: string) => ticket.querySelector(selector)?.textContent?.trim() ?? ''
  return {
    face: { name: text('.ticket-city b'), code: text('.ticket-city > span').split(' · ')[0]!, art: image?.getAttribute('src') ?? null, photo: !!image?.classList.contains('photo'), bars: [...ticket.querySelectorAll<HTMLElement>('.ticket-code i')].map(bar => bar.style.width) },
    from: reducedMotion() ? null : measure(ticket), swap,
  }
}

// 竖票上的格子、小字、虚线、按钮字，照原位补在横票里
function verticalFace(card: HTMLElement, go: HTMLElement, v: Vertical, origin: DOMRect) {
  const holder = document.createElement('div')
  holder.className = 'gate-vertical'
  holder.inert = true
  holder.setAttribute('aria-hidden', 'true')
  const put = (el: HTMLElement, rect: DOMRect) => { Object.assign(el.style, { position: 'absolute', margin: '0', left: px(rect.left - origin.left), top: px(rect.top - origin.top), width: px(rect.width), height: px(rect.height) }); holder.append(el) }
  const shell = document.createElement('div')
  shell.className = v.ticket.className.replace(/\b(tearing|opened)\b/g, '')
  Object.assign(shell.style, { filter: 'none', transform: 'none', transition: 'none' })
  shell.append(v.ticket.querySelector('.ticket-grid')!.cloneNode(true))
  put(shell, v.grid)
  put(v.ticket.querySelector('.ticket-note')!.cloneNode(true) as HTMLElement, v.note)
  const dash = document.createElement('i')
  dash.className = 'gate-seam'
  put(dash, new DOMRect(v.sheet.left + 20, v.sheet.top + v.seam, v.sheet.width - 40, 2))
  for (const el of holder.querySelectorAll<HTMLElement>('*')) el.style.animation = 'none'
  for (const el of holder.querySelectorAll('[id]')) el.removeAttribute('id')
  card.append(holder)
  const label = document.createElement('span')
  label.className = 'gate-label vertical'
  label.setAttribute('aria-hidden', 'true')
  label.innerHTML = v.ticket.querySelector('.ticket-go')!.innerHTML
  go.append(label)
  return { holder, label }
}

interface Run { page: HTMLElement; list: Animation[]; clear: () => void }
let current: Run | null = null

// opening：true 竖票 → 横票，false 横票 → 竖票
function morph(page: HTMLElement, v: Vertical, opening: boolean): Run | null {
  const find = (selector: string) => page.querySelector<HTMLElement>(selector)
  const [card, sheet, art, city, name, code, bars, go, label] = ['.gate-ticket', '.gate-sheet', '.gate-art', '.gate-city', '.gate-city b', '.gate-city span', '.gate-code', '.gate-go', '.gate-label'].map(find)
  if (!card || !sheet || !art || !city || !name || !code || !bars || !go || !label) return null
  const origin = card.getBoundingClientRect()
  const list: Animation[] = []
  const total = opening ? OPEN : CLOSE
  const run = (el: HTMLElement | null, upright: Keyframe, flat: Keyframe, options: KeyframeAnimationOptions = {}) => { if (el) list.push(el.animate(opening ? [upright, flat] : [flat, upright], { duration: total, easing: EASE, fill: 'both', ...options })) }
  // 横票自己的内容：展开后半段依次浮现，收回时一开始就让开；竖票的内容正好反过来
  const own = (el: HTMLElement | null, at = 0) => run(el, { opacity: 0, transform: opening ? 'translateX(14px)' : 'none' }, { opacity: 1, transform: 'none' }, opening ? { delay: 340 + at * 22, duration: 320, easing: 'cubic-bezier(.16, 1, .3, 1)' } : { duration: 120, easing: 'ease-out' })
  const theirs = (el: HTMLElement) => run(el, { opacity: 1 }, { opacity: 0 }, opening ? { duration: 190, easing: 'ease-out' } : { delay: total - 200, duration: 240, easing: 'ease-in-out' })
  const rect = (r: DOMRect, base: { left: number; top: number }) => ({ left: px(r.left - base.left), top: px(r.top - base.top), width: px(r.width), height: px(r.height) })

  // 横票各处的位置要在动之前一次量完：一动起来就量不到原样了
  const natural = (el: Element) => el.getBoundingClientRect()
  const image = find('.gate-art img')
  const mark = find('.gate-mark')
  const flat = { sheet: natural(sheet), art: natural(art), name: natural(name), code: natural(code), bars: natural(bars), go: natural(go), mark: mark && natural(mark) }
  const style = (el: HTMLElement, ...keys: ('right' | 'bottom' | 'height' | 'width' | 'color' | 'backgroundColor')[]) => { const all = getComputedStyle(el); return Object.fromEntries(keys.map(key => [key, all[key]])) }
  const flatImage = image && style(image, 'right', 'bottom', 'height')
  const flatGround = style(go, 'backgroundColor', 'color')
  const flatBars = [...bars.querySelectorAll<HTMLElement>('i')].map(bar => ({ bar, ...style(bar, 'backgroundColor', 'width') }) as { bar: HTMLElement; backgroundColor: string; width: string })

  run(sheet, rect(v.sheet, origin), rect(flat.sheet, origin))
  run(art, { ...rect(v.art, origin), borderRadius: '16px 16px 0 0' }, { ...rect(flat.art, origin), borderRadius: '16px 0 0 16px' })
  if (image && v.picture) run(image, v.picture, flatImage!)
  if (mark && v.mark) run(mark, rect(v.mark, v.art), rect(flat.mark!, flat.art))
  run(city, { left: px(v.code.left - v.art.left), bottom: px(v.art.bottom - v.code.bottom) }, { left: px(flat.code.left - flat.art.left), bottom: px(flat.art.bottom - flat.code.bottom) })
  run(name, { transform: `translateY(${px(flat.code.bottom - flat.name.bottom - (v.code.bottom - v.name.bottom))}) scale(${(v.name.height / flat.name.height).toFixed(4)})` }, { transform: 'translateY(0px) scale(1)' })
  // 条码整根转过去：横着量是宽，竖起来是长，转的过程里不变形
  const [dx, dy] = [v.bars.left + v.bars.width / 2 - (flat.bars.left + flat.bars.width / 2), v.bars.top + v.bars.height / 2 - (flat.bars.top + flat.bars.height / 2)]
  run(bars, { transform: `translate(${px(dx)}, ${px(dy)}) rotate(0deg) scale(${(v.bars.width / bars.offsetWidth).toFixed(4)}, ${(v.bars.height / bars.offsetHeight).toFixed(4)})` }, { transform: 'translate(0px, 0px) rotate(-90deg) scale(1, 1)' })
  // 转到斜着的时候两头会顶出票边、蹭到按钮，半路往里让一让
  list.push(bars.animate([{ translate: '0px 0px', easing: 'ease-in-out' }, { translate: '-30px -26px', offset: opening ? .4 : .42, easing: 'ease-in-out' }, { translate: '0px 0px' }], { duration: total }))
  flatBars.forEach(({ bar, backgroundColor, width }, index) => {
    const from = v.widths[index]
    const moved = from && from !== width
    if (moved || backgroundColor !== INK) run(bar, { backgroundColor: INK, ...moved && { width: from } }, { backgroundColor, ...moved && { width } }, { easing: 'ease-in-out' })
  })
  run(go, { transform: `translate(${px(v.go.left - flat.go.left)}, ${px(v.go.top - flat.go.top)})`, width: px(v.go.width), ...v.ground }, { transform: 'translate(0px, 0px)', width: px(flat.go.width), ...flatGround })
  const face = verticalFace(card, go, v, origin)
  theirs(face.holder)
  theirs(face.label)
  run(label, { opacity: 0 }, { opacity: 1 }, opening ? { delay: 190, duration: 220, easing: 'ease-out' } : { duration: 120, easing: 'ease-out' })
  // 齿孔：竖票的两个先合上，横票的两个最后打开
  sheet.style.setProperty('--seam', px(v.seam))
  run(sheet, { '--notch-v': '11px' }, { '--notch-v': '0px' }, opening ? { duration: 200, easing: 'ease-in' } : { delay: total - 200, duration: 200, easing: 'ease-out' })
  run(sheet, { '--notch-h': '0px' }, { '--notch-h': '11px' }, opening ? { delay: total - 220, duration: 220, easing: 'ease-out' } : { duration: 180, easing: 'ease-in' })
  page.querySelectorAll<HTMLElement>('[data-rise]').forEach((el, index) => own(el, index))
  page.querySelectorAll<HTMLElement>('.gate-caption, .gate-dash').forEach(el => own(el, 6))
  if (opening && v.tilt !== 'none') {
    card.style.transformOrigin = `${px(v.sheet.left + v.sheet.width / 2 - origin.left)} ${px(v.sheet.top + v.sheet.height / 2 - origin.top)}`
    list.push(card.animate([{ transform: `perspective(1100px) ${v.tilt}` }, { transform: 'none' }], { duration: 320, easing: 'cubic-bezier(.16, 1, .3, 1)' }))
  }
  page.classList.add('morphing')
  return { page, list, clear: () => { for (const one of list) one.cancel(); face.holder.remove(); face.label.remove(); page.classList.remove('morphing'); card.style.transformOrigin = '' } }
}

// 动画在后台标签页会被浏览器暂停，到点也要收场
const ended = (list: Animation[], limit: number) => Promise.race([Promise.allSettled(list.map(one => one.finished)), new Promise(resolve => setTimeout(resolve, limit))])

export async function openGate(page: HTMLElement, scene: GateScene | null) {
  if (reducedMotion()) return
  if (scene?.swap) { scene.swap.sheet.style.zIndex = '1401'; void slideLeft(scene.swap, 1) }
  const run = scene?.from && innerWidth > 900 ? morph(page, scene.from, true) : null
  if (!run) { page.querySelector('.gate-ticket')?.animate([{ opacity: 0, transform: 'translateY(26px)' }, { opacity: 1, transform: 'none' }], { duration: 520, easing: 'cubic-bezier(.16, 1, .3, 1)', fill: 'backwards' }); return }
  current = run
  await ended(run.list, 1600)
  if (current === run) { current = null; run.clear() }
}

// dir：-1 返回，1 登录成功往前走。在底下的页面已经换好、还没画出来的时候调用
export async function closeGate(page: HTMLElement, dir: 1 | -1) {
  if (reducedMotion()) return
  const shell = document.querySelector<HTMLElement>('.journey-shell')!
  page.inert = true
  page.classList.add('leaving')
  shell.dataset.gate = 'run'
  swapIn(dir)
  await nextTick()
  const ticket = visibleTicket()
  const opening = current?.page === page ? current : null
  current = null
  const to = !opening && ticket && innerWidth > 900 ? measure(ticket) : null
  const run = opening ?? (to && morph(page, to, false))
  if (opening) for (const one of opening.list) one.reverse()
  if (run) await ended(run.list, 1600)
  else await ended([page.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 200, easing: 'ease-out', fill: 'forwards' })], 400)
  delete shell.dataset.gate
}
