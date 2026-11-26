// 选好 → 开始计算的衔接：票根撕落，左边的内容滑走，票面飞到生成页、直接撑满左边那个圆（中间不留空白）。
// 全靠盖在上面的副本来演，真页面照常立刻切换，不为动画多等；减少动态效果时整段不播。
// 要不闪：副本的第一帧必须和切走前的画面一样，所以副本里不重播任何进场动画、图片换成当场画好的画布、输入框带上已填的值、顶部那一行也算在内。
// 要不卡：只动 transform 和 opacity（合成线程能自己跑），不用 clip-path；等副本画好一帧再开始动。
import { nextTick } from 'vue'
import { reducedMotion } from './motion'

export interface Departure { form: HTMLElement; ticket: HTMLElement; stub: HTMLElement; main: HTMLElement; left: HTMLElement | null; boxes: Map<HTMLElement, DOMRect>; bar: HTMLElement | null; barBox: DOMRect | null; top: number }

// 把 picture 当场画到画布上，顶替 slot（样式标记照抄，组件内样式才管得到）
function paint(picture: HTMLImageElement, slot: HTMLImageElement) {
  const canvas = document.createElement('canvas')
  canvas.className = slot.className
  for (const { name, value } of slot.attributes) if (name.startsWith('data-v-')) canvas.setAttribute(name, value)
  ;[canvas.width, canvas.height] = [picture.naturalWidth, picture.naturalHeight]
  canvas.getContext('2d')?.drawImage(picture, 0, 0)
  return canvas
}
// 副本：带上滚动位置和控件当前的值
function copy(el: HTMLElement): HTMLElement {
  const node = el.cloneNode(true) as HTMLElement
  const from = el.querySelectorAll<HTMLInputElement>('input, textarea, select')
  node.querySelectorAll<HTMLInputElement>('input, textarea, select').forEach((field, index) => { if (field.type !== 'file') field.value = from[index]!.value })
  for (const named of [node, ...node.querySelectorAll('[id]')]) named.removeAttribute('id')
  // 复制出来的 img 会晚一帧才有图；换成画布，当场画好
  const pictures = el.querySelectorAll('img')
  node.querySelectorAll('img').forEach((image, index) => {
    const picture = pictures[index]!
    if (picture.offsetWidth && picture.naturalWidth) image.replaceWith(paint(picture, image))
    else image.remove()  // 没显示或还没取到的图不用画
  })
  return node
}
const scrolled = (el: HTMLElement) => el.querySelector<HTMLElement>('.tchat-scroll') ?? el

// 提交的那一刻先记下各块的位置：等页面真的切走时它们已经不在原位了。顶部那一行会比票先消失，副本也现在就留
export function captureDeparture(wrap: HTMLElement): Departure | null {
  const form = wrap.closest<HTMLElement>('.tform')
  const ticket = wrap.querySelector<HTMLElement>('.ticket')
  const stub = wrap.querySelector<HTMLElement>('.ticket-stub')
  const main = wrap.querySelector<HTMLElement>('.ticket-main')
  if (!form || !ticket || !stub || !main || reducedMotion() || !stub.offsetWidth) return null
  const left = form.querySelector<HTMLElement>(':scope > .tform-left, :scope > .tchat-main')
  const bar = document.querySelector<HTMLElement>('.panel-bar')
  const boxes = new Map([stub, main, ...(left ? [left] : [])].map(el => [el, el.getBoundingClientRect()]))
  return { form, ticket, stub, main, left, boxes, bar: bar && copy(bar), barBox: bar?.getBoundingClientRect() ?? null, top: document.querySelector('.journey-bar')?.getBoundingClientRect().bottom ?? 0 }
}

function layer(className: string, box?: DOMRect, top = 0) {
  const el = document.createElement('div')
  el.className = className
  el.inert = true
  el.setAttribute('aria-hidden', 'true')
  if (box) Object.assign(el.style, { left: `${box.left}px`, top: `${box.top - top}px`, width: `${box.width}px`, height: `${box.height}px` })
  return el
}

// 城市名单独一层：从票上城市名的位置和大小，滑到圆里的位置；票和圆各自的那份藏起来，免得叠字
function carry(real: HTMLElement, inner: HTMLElement, flying: HTMLElement) {
  const target = real.querySelector<HTMLElement>('p')
  const name = target?.querySelector<HTMLElement>('b')
  const shadow = flying.querySelector<HTMLElement>('.ticket-city b')
  if (!target || !name || !shadow) return null
  const node = layer('departure-title', target.getBoundingClientRect())
  node.innerHTML = target.innerHTML
  ;[...target.children].forEach((from, index) => {
    const style = getComputedStyle(from)
    const to = node.children[index] as HTMLElement
    for (const key of ['display', 'margin', 'fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing', 'color', 'whiteSpace'] as const) to.style[key] = style[key]
  })
  // 票上的字此刻多大、在哪：副本和原件同一位置
  const [a, b] = [shadow.getBoundingClientRect(), name.getBoundingClientRect()]
  shadow.style.visibility = 'hidden'
  inner.querySelector('p')!.style.visibility = 'hidden'
  const scale = a.height / b.height
  const from = `translate(${a.left - b.left}px, ${a.top - b.top}px) scale(${scale.toFixed(4)})`
  const note = node.querySelector<HTMLElement>('span')
  node.style.transform = from  // 开始动之前就要待在票上
  if (note) note.style.opacity = '0'
  return { node, from, note }
}

export async function playDeparture({ form, ticket, stub, main, left, boxes, bar, barBox, top }: Departure) {
  const stubBox = boxes.get(stub)!
  const box = boxes.get(main)!
  // 原来的白底网格、顶部一行和左边内容：底子淡出，内容往左滑走
  const sheet = layer('questionnaire-view departure-sheet')
  sheet.style.top = `${top}px`
  sheet.style.backgroundPosition = `0 ${-top}px`
  if (bar && barBox) { const row = layer('departure-bar', barBox, top); row.append(bar); sheet.append(row) }
  const leftBox = left && boxes.get(left)
  const frame = leftBox ? layer(`${form.className.replace(/\bmode-\S+/g, '')} departure-left`, leftBox, top) : null
  const leftCopy = left && copy(left)
  if (frame && leftCopy) { frame.append(leftCopy); sheet.append(frame) }
  // 票面和票根各自一层，阴影放在外层（票自己有齿孔遮罩，阴影加在里面会被裁掉）
  const ghost = () => layer(`${ticket.className.replace(/\b(tearing|opened|mode-\S+)\b/g, '')} departure-ticket`)
  const flying = ghost()
  const falling = ghost()
  const mainCopy = copy(main)
  flying.append(mainCopy)
  falling.append(copy(stub))
  falling.firstElementChild!.classList.add('ticket-falling')
  for (const [el, rect] of [[flying, box], [falling, stubBox]] as const) Object.assign(el.style, { left: `${rect.left}px`, top: `${rect.top}px`, width: `${rect.width}px`, height: `${rect.height}px` })
  const held = getComputedStyle(stub).transform  // 票根此刻掀到哪
  falling.style.transform = held
  document.body.append(sheet, flying, falling)
  if (left && leftCopy) scrolled(leftCopy).scrollTop = scrolled(left).scrollTop
  const pieces: HTMLElement[] = [sheet, flying, falling]
  setTimeout(() => { for (const el of pieces) el.remove() }, 3000)  // 页面在后台动画被暂停时，也不会一直盖着
  await nextTick()
  // 圆的副本跟票同一条路径：从票面大小长到整个圆，路上和票交叉淡化；到位后换成真的那个圆，两者一模一样
  const real = document.querySelector<HTMLElement>('.plan-progress .generation-ticket')
  const circle = real?.getBoundingClientRect()
  const morph = !!real && !!circle && circle.width > 240 && box.height > 160
  let round: HTMLElement | null = null
  let title: ReturnType<typeof carry> = null
  if (morph) {
    real.style.visibility = 'hidden'
    round = layer('departure-round', circle)
    const inner = real.cloneNode(true) as HTMLElement
    inner.style.visibility = ''
    const picture = main.querySelector('img')
    const image = inner.querySelector('img')
    if (picture && image) image.replaceWith(paint(picture, image))
    round.append(inner)
    flying.before(round)
    pieces.push(round)
    title = carry(real, inner, flying)
    if (title) { document.body.append(title.node); pieces.push(title.node) }
  }
  const settle = () => { if (real) real.style.visibility = '' }
  setTimeout(settle, 2600)
  // 让这一帧先画出来，再开始动
  await new Promise<void>(resolve => { requestAnimationFrame(() => requestAnimationFrame(() => resolve())); setTimeout(resolve, 250) })
  const ends: Animation[] = []
  frame?.animate([{ transform: 'none', opacity: 1 }, { transform: 'translateX(-40px)', opacity: 0 }], { duration: 320, easing: 'cubic-bezier(.4, 0, 1, 1)', fill: 'forwards' })
  ends.push(sheet.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 480, delay: 140, easing: 'ease-in-out', fill: 'both' }))
  // 位置只用首尾两帧、一条缓动，中间没有折点；透明度另起一条
  const fade = (el: HTMLElement, from: number, to: number, delay: number, duration: number) => el.animate([{ opacity: from }, { opacity: to }], { delay, duration, easing: 'ease-in-out', fill: 'both' })
  ends.push(falling.animate([{ transform: held === 'none' ? 'translateY(0)' : held }, { transform: 'translate(28px, 230px) rotate(17deg)' }], { duration: 760, easing: 'cubic-bezier(.5, 0, .85, .55)', fill: 'forwards' }))
  ends.push(fade(falling, 1, 0, 420, 300))
  if (morph && round) {
    const [x, y] = [circle.left + circle.width / 2 - (box.left + box.width / 2), circle.top + circle.height / 2 - (box.top + box.height / 2)]
    const path = { duration: 820, delay: 40, easing: 'cubic-bezier(.6, 0, .18, 1)', fill: 'both' as const }
    ends.push(flying.animate([{ transform: 'none' }, { transform: `translate(${x}px, ${y}px) scale(${(circle.width / box.width * 0.94).toFixed(3)})` }], path))
    // 票化进圆里；城市名不淡出，从票上一路滑到圆里
    if (title) {
      ends.push(title.node.animate([{ transform: title.from }, { transform: 'none' }], path))
      if (title.note) ends.push(fade(title.note, 0, 1, 420, 300))
    }
    ends.push(fade(flying, 1, 0, 320, 240))
    ends.push(round.animate([{ transform: `translate(${-x}px, ${-y}px) scale(${(Math.min(box.width, box.height) / circle.width).toFixed(3)})` }, { transform: 'none' }], path))
    ends.push(fade(round, 0, 1, 180, 260))
  } else ends.push(flying.animate([{ opacity: 1, transform: 'none' }, { opacity: 0, transform: 'translateY(16px)' }], { duration: 280, easing: 'ease-in', fill: 'forwards' }))
  await Promise.allSettled(ends.map(end => end.finished))
  await real?.querySelector('img')?.decode().catch(() => undefined)
  settle()
  await new Promise<void>(resolve => { requestAnimationFrame(() => resolve()); setTimeout(resolve, 100) })
  for (const el of pieces) el.remove()
}

// 选城页 ⇄ 问卷页：票原地不动，只换左边。左边的旧内容用副本滑走；票不盖副本（两份阴影会叠黑、两份字会重影），
// 真票当帧就在原位，只让变了的那几处（格子里的值、条码、一行小字、按钮）从旧的样子动到新的样子
interface Face { cells: Map<string, string>; note: string; ground: string; dim: string; bars: string[]; tilt: string }
export interface Swap { sheet: HTMLElement; frame: HTMLElement; face: Face | null; scroll: number }
const visible = (selector: string) => [...document.querySelectorAll<HTMLElement>(selector)].find(el => el.offsetParent)
export const visibleTicket = () => { const ticket = visible('.tform .ticket, .landing-form .ticket'); return ticket && ticket.offsetHeight > 240 ? ticket : null }
const cellsOf = (ticket: HTMLElement) => [...ticket.querySelectorAll<HTMLElement>('.ticket-grid > div')].map(cell => ({ label: cell.querySelector('dt')?.textContent ?? '', value: cell.querySelector<HTMLElement>('dd') }))
const barsOf = (ticket: HTMLElement) => [...ticket.querySelectorAll<HTMLElement>('.ticket-code i')]
function faceOf(ticket: HTMLElement): Face {
  const go = ticket.querySelector<HTMLElement>('.ticket-go')
  return {
    cells: new Map(cellsOf(ticket).map(({ label, value }) => [label, value?.textContent ?? ''])),
    note: ticket.querySelector('.ticket-note')?.textContent ?? '',
    ground: go ? getComputedStyle(go).backgroundColor : '',
    dim: go ? getComputedStyle(go).opacity : '',
    bars: barsOf(ticket).map(bar => bar.style.width),
    tilt: getComputedStyle(ticket).transform,
  }
}
export function captureSwap(): Swap | null {
  if (reducedMotion()) return null
  const form = visible('.tform, .landing-form')
  const left = form?.querySelector<HTMLElement>(':scope > .landing-main, :scope > .tform-left, :scope > .tchat-main')
  if (!form || !left) return null
  const top = document.querySelector('.journey-bar')?.getBoundingClientRect().bottom ?? 0
  const sheet = layer('questionnaire-view landing swap-sheet')
  Object.assign(sheet.style, { top: `${top}px`, background: 'none' })
  const frame = layer(`${form.className.replace(/\bmode-\S+/g, '')} swap-left`, left.getBoundingClientRect(), top)
  for (const { name, value } of form.attributes) if (name.startsWith('data-v-')) frame.setAttribute(name, value)
  frame.append(copy(left))
  sheet.append(frame)
  const ticket = visibleTicket()
  return { sheet, frame, face: ticket && faceOf(ticket), scroll: scrolled(left).scrollTop }
}

// 真票从旧的样子动到新的样子：没变的不动，变了的字当帧换掉、往上滑几像素到位，不留空白，也只有一份
function settleFace(ticket: HTMLElement, face: Face) {
  const spring = 'cubic-bezier(.16, 1, .3, 1)'
  const rise = (el: Element | null | undefined, delay: number) => el?.animate([{ opacity: .4, transform: 'translateY(6px)' }, { opacity: 1, transform: 'none' }], { duration: 300, delay, easing: spring, fill: 'backwards' })
  let at = 0
  for (const { label, value } of cellsOf(ticket)) if (value && (value.textContent ?? '') !== face.cells.get(label)) rise(value, at++ * 14)
  const note = ticket.querySelector('.ticket-note')
  if (note && (note.textContent ?? '') !== face.note) rise(note, 0)
  barsOf(ticket).forEach((bar, index) => { const from = face.bars[index]; if (from && from !== bar.style.width) bar.animate([{ width: from }, { width: bar.style.width }], { duration: 420, easing: spring }) })
  const go = ticket.querySelector<HTMLElement>('.ticket-go')
  if (go) {
    const { backgroundColor, opacity } = getComputedStyle(go)
    if (backgroundColor !== face.ground || opacity !== face.dim) go.animate([{ backgroundColor: face.ground, opacity: face.dim }, { backgroundColor, opacity }], { duration: 220, easing: 'ease-out' })
  }
  if (face.tilt !== 'none' && face.tilt !== getComputedStyle(ticket).transform) ticket.animate([{ transform: face.tilt }, { transform: 'none' }], { duration: 420, easing: spring })
}

// 新的左边滑进来（样式在 ticket.css 的 [data-swap]），各页面自己的进场不再播
export function swapIn(dir: 1 | -1) {
  const shell = document.querySelector<HTMLElement>('.journey-shell')
  if (!shell) return
  shell.style.setProperty('--swap-dir', String(dir))
  shell.dataset.swap = 'run'
  setTimeout(() => { if (shell.dataset.swap) shell.dataset.swap = 'rest' }, 1000)
}
// 旧的左边滑走
export async function slideLeft({ sheet, frame, scroll }: Swap, dir: 1 | -1) {
  const left = frame.firstElementChild as HTMLElement
  document.body.append(sheet)
  scrolled(left).scrollTop = scroll
  setTimeout(() => sheet.remove(), 2000)
  await new Promise<void>(resolve => { requestAnimationFrame(() => requestAnimationFrame(() => resolve())); setTimeout(resolve, 250) })
  await Promise.allSettled([frame.animate([{ transform: 'none', opacity: 1 }, { transform: `translateX(${dir * -32}px)`, opacity: 0 }], { duration: 170, easing: 'ease-out', fill: 'forwards' }).finished])
  sheet.remove()
}
// dir：1 进城，-1 回来换城。在页面已经切过去、还没画出来的时候调用
export async function playSwap(swap: Swap, dir: 1 | -1) {
  swapIn(dir)
  const ticket = visibleTicket()
  if (ticket && swap.face) settleFace(ticket, swap.face)
  await slideLeft(swap, dir)
}
// 离开问卷页（开始规划等）后清掉，下次回到问卷照常播进场
export function clearSwap() { delete document.querySelector<HTMLElement>('.journey-shell')?.dataset.swap }
