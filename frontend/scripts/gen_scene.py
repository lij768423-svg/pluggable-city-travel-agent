"""生成问卷背景的两幅静态杭州插画：左景（北望宝石山：保俶塔、断桥、荷）与右景（雷峰夕照：雷峰塔、画舫、三潭印月）。

每幅 800×1000，贴屏幕底边，左景靠左、右景靠右。
动画用到的类名都带 qb- 前缀，样式写在 src/questionnaire.css；ids 按幅加 qbw-/qbs- 前缀，便于两幅同时内联。
用法（在 frontend 目录）：python3 scripts/gen_scene.py src/assets/backdrop
"""
import math
import re
import sys
from pathlib import Path

W, H = 800, 1000


def f(v):
    return f"{round(v, 1):g}"


def cubic(p, t):
    u = 1 - t
    return tuple(u ** 3 * p[0][k] + 3 * u * u * t * p[1][k] + 3 * u * t * t * p[2][k] + t ** 3 * p[3][k] for k in (0, 1))


def lerp_curve(segs, n):
    """多段三次曲线均匀取样（按段数平均）。"""
    pts = []
    for i, seg in enumerate(segs):
        for j in range(n):
            if i and not j:
                continue
            pts.append(cubic(seg, j / (n - 1)))
    return pts


def rng(seed):
    x = seed
    while True:
        x = (x * 1103515245 + 12345) % 2147483648
        yield x / 2147483648


def anim(cls, content, delay=0.0, dur=None):
    """包一层动画分组；delay 取负值让各元素错开节奏。"""
    style = f"animation-delay:-{f(delay)}s" + (f";animation-duration:{f(dur)}s" if dur else "")
    return f'<g class="qb-{cls}" style="{style}">{content}</g>'




GOLD = "#f2c46d"
WARM = "#ffd9a0"
CORAL = "#ec8f86"

COMMON_DEFS = """
  <linearGradient id="cloud" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffffff"/><stop offset="1" stop-color="#dfe7f9"/></linearGradient>
  <linearGradient id="stone" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#f6f8fe"/><stop offset="1" stop-color="#d3ddf4"/></linearGradient>
  <linearGradient id="roof" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5a79cf"/><stop offset="1" stop-color="#34509e"/></linearGradient>
  <linearGradient id="hull" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#5d74b4"/><stop offset="1" stop-color="#3e5189"/></linearGradient>
  <linearGradient id="water" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d9e3f8"/><stop offset=".5" stop-color="#e4ebfa"/><stop offset="1" stop-color="#eef2fc" stop-opacity="0"/></linearGradient>
  <linearGradient id="leaf" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#9fcad3"/><stop offset="1" stop-color="#5f97ab"/></linearGradient>
  <linearGradient id="leafB" x1="1" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#b6d8de"/><stop offset="1" stop-color="#78aabb"/></linearGradient>
  <linearGradient id="petal" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#fff5f7"/><stop offset=".55" stop-color="#fbd3dd"/><stop offset="1" stop-color="#ec8fa8"/></linearGradient>
  <linearGradient id="sphere" x1="0" y1="0" x2="1" y2="1"><stop offset="0" stop-color="#c3d3f6"/><stop offset=".55" stop-color="#8ea8e6"/><stop offset="1" stop-color="#6683cf"/></linearGradient>
  <radialGradient id="mist"><stop offset="0" stop-color="#fff" stop-opacity=".95"/><stop offset=".6" stop-color="#fff" stop-opacity=".5"/><stop offset="1" stop-color="#fff" stop-opacity="0"/></radialGradient>
  <radialGradient id="glowWarm"><stop offset="0" stop-color="#ffe2b0" stop-opacity=".95"/><stop offset="1" stop-color="#ffe2b0" stop-opacity="0"/></radialGradient>
"""


def ripple_mask(mid, y0, y1, seed):
    """倒影用的水纹遮罩：一行行长短不一的横线，越往下越稀；每行左右轻摆、相邻行反向，表现水波。"""
    r = rng(seed)
    rows = []
    y = y0
    k = 0
    while y < y1:
        depth = (y - y0) / (y1 - y0)
        x = -60
        line = []
        while x < W + 60:
            w = 30 + next(r) * 140
            if next(r) > .18 + depth * .3:
                line.append(f'<rect x="{f(x)}" y="{f(y)}" width="{f(w)}" height="{f(2.2 + next(r) * 2)}" rx="1.5" fill="#fff" opacity="{f(1 - depth * .8)}"/>')
            x += w + 6 + next(r) * 30
        rows.append("".join(line))
        y += 5.5
        k += 1
    # 每 4 行编成一组一起摆动，相邻组反向，控制动画元素数量
    bands = ["".join(rows[i:i + 4]) for i in range(0, len(rows), 4)]
    body = "".join(anim("wave" if j % 2 else "wave-r", band, delay=(j * .45) % 2, dur=1.9 + (j % 4) * .3) for j, band in enumerate(bands))
    return f'<mask id="{mid}" maskUnits="userSpaceOnUse" x="0" y="0" width="{W}" height="{H}">{body}</mask>'



def cloud(x, y, s, flip=False):
    sx = -s if flip else s
    return anim("drift", f'<g transform="translate({f(x)} {f(y)}) scale({f(sx)} {f(s)})">'
            '<path d="M6 0C-8 0-10-16 2-20C0-36 20-42 30-32C36-50 66-52 76-34C86-44 106-40 106-26C120-26 124-8 112 0Z" fill="url(#cloud)"/>'
            '<path d="M14-4C40-2 80-2 106-6" stroke="#d3def6" stroke-width="2" stroke-linecap="round" fill="none"/>'
            '<ellipse cx="54" cy="-36" rx="12" ry="5" fill="#fff"/></g>', delay=x % 47)


def bird(x, y, s=1.0, color="#5f78c0"):
    return anim("flap", f'<path transform="translate({f(x)} {f(y)}) scale({f(s)})" d="M-11-3C-7-8-3-7 0-1C3-8 8-9 13-5C7-5 3-2 0 2C-3-2-6-4-11-3Z" fill="{color}"/>', delay=(x * .013) % 1.3)


def treeline(pts, base_col, shade_col, seed, rmin=8, rmax=18, skip=None):
    """沿曲线排一行树冠（圆形剪影 + 左上高光），只表现成片的树林。"""
    r = rng(seed)
    out = []
    for (x, y) in pts:
        if skip and skip[0] < x < skip[1]:
            continue
        rad = rmin + next(r) * (rmax - rmin)
        cy = y + rad * .35
        out.append(f'<circle cx="{f(x)}" cy="{f(cy)}" r="{f(rad)}" fill="{base_col}"/>')
        out.append(f'<circle cx="{f(x + rad * .25)}" cy="{f(cy + rad * .25)}" r="{f(rad * .7)}" fill="{shade_col}" opacity=".55"/>')
    return "".join(out)


def lotus_flower(x, y, s=1.0, rot=0):
    petals = [
        ("M0 0C-26-4-38-22-34-40C-20-34-8-18 0 0Z", "url(#petal)"),
        ("M0 0C26-4 38-22 34-40C20-34 8-18 0 0Z", "url(#petal)"),
        ("M-4-2C-20-14-22-40-10-56C-4-40-2-20-4-2Z", "url(#petal)"),
        ("M4-2C20-14 22-40 10-56C4-40 2-20 4-2Z", "url(#petal)"),
        ("M0 2C-12-14-12-44 0-62C12-44 12-14 0 2Z", "url(#petal)"),
    ]
    body = "".join(f'<path d="{d}" fill="{c}"/>' for d, c in petals)
    vein = '<path d="M0-4V-52M-6-6C-14-20-16-36-12-48M6-6C14-20 16-36 12-48" stroke="#f3b7c6" stroke-width=".8" fill="none" opacity=".7"/>'
    seed = f'<ellipse cx="0" cy="-4" rx="9" ry="4" fill="#f4cf74"/><ellipse cx="0" cy="-5" rx="5" ry="2" fill="#e9b94f"/>'
    stem = '<path d="M0 2C4 40-4 80 2 130" stroke="#6d9fb0" stroke-width="3.4" stroke-linecap="round" fill="none"/>'
    return anim("sway", f'<g transform="translate({f(x)} {f(y)}) rotate({rot}) scale({f(s)})">{stem}{body}{vein}{seed}</g>', delay=x % 7)


def lotus_bud(x, y, s=1.0, rot=0):
    return anim("sway", (f'<g transform="translate({f(x)} {f(y)}) rotate({rot}) scale({f(s)})">'
            '<path d="M0 2C3 40-3 80 1 120" stroke="#6d9fb0" stroke-width="3" stroke-linecap="round" fill="none"/>'
            '<path d="M0 2C-14-8-14-34 0-50C14-34 14-8 0 2Z" fill="url(#petal)"/>'
            '<path d="M0 2C-6-12-6-30 0-44" stroke="#f3b7c6" stroke-width=".8" fill="none"/></g>'), delay=x % 7)


def lily_pad(cx, cy, rx, ry, rot=0, grad="leaf"):
    veins = "".join(f'M0 0L{f(rx * .9 * math.cos(a))} {f(ry * .9 * math.sin(a))}' for a in [k * math.pi / 5 + .35 for k in range(10)])
    return (f'<g transform="translate({f(cx)} {f(cy)}) rotate({rot})">'
            f'<ellipse cx="0" cy="3" rx="{f(rx)}" ry="{f(ry)}" fill="#4f8297" opacity=".35"/>'
            f'<path d="M0 0L{f(rx * .28)} {f(ry * .98)}A{f(rx)} {f(ry)} 0 1 1 {f(rx * .02)} {f(ry)}Z" fill="url(#{grad})"/>'
            f'<path d="{veins}" stroke="#d9eef1" stroke-width="1.1" stroke-linecap="round" opacity=".8"/>'
            f'<ellipse cx="{f(-rx * .3)}" cy="{f(-ry * .35)}" rx="{f(rx * .35)}" ry="{f(ry * .22)}" fill="#fff" opacity=".18"/></g>')


def standing_leaf(x, y, s=1.0, rot=0):
    return anim("sway", (f'<g transform="translate({f(x)} {f(y)}) rotate({rot}) scale({f(s)})">'
            '<path d="M2 0C6 40-2 80 2 140" stroke="#6d9fb0" stroke-width="3" stroke-linecap="round" fill="none"/>'
            '<path d="M-46-6C-30-34 30-38 50-10C34-16 20-14 2 0C-14-12-28-14-46-6Z" fill="url(#leafB)"/>'
            '<path d="M2 0C-10-14-26-20-40-12M2 0C8-16 22-24 40-16M2 0V-26" stroke="#e1f1f3" stroke-width="1" fill="none" opacity=".8"/></g>'), delay=x % 7)


def wupeng_boat(x, y, s=1.0, flip=False):
    sx = -s if flip else s
    return anim("bob", f'<g transform="translate({f(x)} {f(y)}) scale({f(sx)} {f(s)})">'
            '<path d="M-70-8C-50 10 50 12 74-14L66-8H-62Z" fill="url(#hull)"/>'
            '<path d="M-62-8H66" stroke="#8ea4dc" stroke-width="2"/>'
            '<path d="M-36-8C-36-36 30-38 32-8Z" fill="#4c63a6"/>'
            '<path d="M-28-10C-27-30 24-31 25-10M-16-10C-15-30 12-31 13-10" stroke="#7d95d6" stroke-width="1.6" fill="none"/>'
            '<path d="M48-8L74 22" stroke="#3e5189" stroke-width="2.4" stroke-linecap="round"/>'
            '<path d="M42-10L44-34H52L54-10Z" fill="#3e5189"/><circle cx="48" cy="-38" r="5" fill="#3e5189"/>'
            '<path d="M36-40L48-50L60-40Z" fill="#e6d2a2"/></g>', delay=x % 6)


# —————————————————— 左景 ——————————————————
def left_scene():
    defs = [COMMON_DEFS,
            '<radialGradient id="skyL" cx=".3" cy=".3" r=".6"><stop offset="0" stop-color="#e9f0fd"/><stop offset="1" stop-color="#e9f0fd" stop-opacity="0"/></radialGradient>',
            '<linearGradient id="farL" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#d9e3f8"/><stop offset="1" stop-color="#d9e3f8" stop-opacity="0"/></linearGradient>',
            '<linearGradient id="hillL" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#b3c6f0"/><stop offset=".7" stop-color="#d4dff7"/><stop offset="1" stop-color="#e4ebfa" stop-opacity="0"/></linearGradient>',
            '<linearGradient id="shoreL" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#c2d2f3"/><stop offset="1" stop-color="#d6e1f7"/></linearGradient>',
            '<linearGradient id="towerL" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#b9cbf3"/><stop offset=".5" stop-color="#93ace8"/><stop offset="1" stop-color="#6f8bd6"/></linearGradient>',
            ripple_mask("rippleL", 702, 1000, 7)]
    o = ['<rect width="800" height="1000" fill="url(#skyL)"/>']
    o += [cloud(70, 200, 1.15), cloud(420, 132, .8, True), cloud(600, 250, .55)]
    o.append(anim("flock", bird(470, 300, 1.1) + bird(500, 284, .9) + bird(522, 312, .75)))
    # 远山
    o.append('<path d="M0 520C100 500 180 480 260 492C360 506 420 470 520 466C620 462 700 482 800 494V720H0Z" fill="url(#farL)"/>')
    # 宝石山
    hill = [[(0, 562), (60, 532), (120, 470), (170, 450)], [(170, 450), (190, 442), (200, 432), (214, 432)],
            [(214, 432), (232, 432), (262, 444), (300, 462)], [(300, 462), (380, 500), (480, 530), (580, 546)], [(580, 546), (660, 558), (740, 562), (800, 566)]]
    d = f"M{hill[0][0][0]} {hill[0][0][1]}" + "".join(f"C{seg[1][0]} {seg[1][1]} {seg[2][0]} {seg[2][1]} {seg[3][0]} {seg[3][1]}" for seg in hill)
    o.append(treeline(lerp_curve(hill, 9), "#b3c6f0", "#a2b8ea", 11, 7, 15, skip=(186, 244)))
    o.append(f'<path d="{d}V720H0Z" fill="url(#hillL)"/>')
    # 山腰的雾
    o.append(anim("mistmove", '<ellipse cx="330" cy="560" rx="300" ry="26" fill="url(#mist)"/>', delay=26))
    # 保俶塔
    x, base = 214, 432
    o.append(f'<path d="M{x-30} {base+4}C{x-20} {base-6} {x+20} {base-6} {x+30} {base+4}Z" fill="#a2b8ea"/>')
    o.append(f'<rect x="{x-16}" y="{base-6}" width="32" height="7" rx="1.5" fill="#5b74b8"/>')
    top = base - 160
    o.append(f'<path d="M{x-11} {base-6}L{x-5} {top}H{x}V{base-6}Z" fill="#b9cbf3"/><path d="M{x} {base-6}V{top}H{x+5}L{x+11} {base-6}Z" fill="#7d97da"/>')
    for k in range(8):
        yy = base - 18 - k * 19
        hw = 11 - (base - 6 - yy) / (base - 6 - top) * 6
        o.append(f'<path d="M{f(x-hw-4)} {f(yy+2)}Q{f(x-hw)} {f(yy-2)} {f(x)} {f(yy-2.5)}Q{f(x+hw)} {f(yy-2)} {f(x+hw+4)} {f(yy+2)}L{f(x+hw+1)} {f(yy+1)}H{f(x-hw-1)}Z" fill="#3e5aa8"/>')
        if k % 2 == 0:
            o.append(f'<rect x="{f(x-1.4)}" y="{f(yy+5)}" width="2.8" height="6" rx="1.4" fill="{WARM}"/>')
    o.append(f'<path d="M{x-5} {top}Q{x} {top-14} {x} {top-16}Q{x} {top-14} {x+5} {top}Z" fill="#3e5aa8"/>')
    o.append(f'<path d="M{x} {top-16}V{top-46}" stroke="#3e5aa8" stroke-width="1.8" stroke-linecap="round"/>')
    for k in range(5):
        o.append(f'<ellipse cx="{x}" cy="{top-20-k*5}" rx="{f(4.2-k*.5)}" ry="1.3" fill="#3e5aa8"/>')
    o.append(f'<circle cx="{x}" cy="{top-49}" r="2.6" fill="{GOLD}"/>')
    # 孤山 / 北山路 岸线与树林
    shore = [[(0, 612), (120, 600), (260, 606), (400, 598)], [(400, 598), (520, 592), (660, 600), (800, 594)]]
    o.append(treeline(lerp_curve(shore, 14), "#a9bdec", "#97afe6", 23, 9, 20))
    o.append('<path d="M0 612C120 600 260 606 400 598C520 592 660 600 800 594V650H0Z" fill="url(#shoreL)"/>')
    for hx, hy in [(470, 596), (512, 592), (560, 594)]:
        o.append(f'<rect x="{hx}" y="{hy}" width="20" height="11" fill="#f7f9fe"/><path d="M{hx-3} {hy+1}L{hx+3} {hy-5}H{hx+17}L{hx+23} {hy+1}Z" fill="#4f6cbd"/><rect x="{hx+8}" y="{hy+4}" width="4" height="7" fill="#9fb4e8"/>')
    o.append(anim("mistmove", '<ellipse cx="420" cy="646" rx="420" ry="18" fill="url(#mist)"/>', delay=2))
    # 水面
    o.append('<rect x="0" y="650" width="800" height="350" fill="url(#water)"/>')
    # 断桥（本体 + 倒影）
    deck = [[(56, 690), (150, 690), (230, 628), (330, 628)], [(330, 628), (430, 628), (510, 690), (604, 690)]]
    deck_d = "M56 690C150 690 230 628 330 628C430 628 510 690 604 690"
    body = f'{deck_d}L612 704H48ZM266 704C266 672 296 656 330 656C364 656 394 672 394 704Z'
    bridge = [f'<path d="{body}" fill="url(#stone)" fill-rule="evenodd"/>']
    defs.append('<clipPath id="bridgeClip"><path d="' + body + '" fill-rule="evenodd" clip-rule="evenodd"/></clipPath>')
    courses = "".join(f"M40 {y}H620" for y in range(648, 704, 9))
    bridge.append(f'<path d="{courses}" stroke="#dfe6f7" stroke-width="1" clip-path="url(#bridgeClip)"/>')
    bridge.append('<path d="M252 704C252 664 290 644 330 644C370 644 408 664 408 704" stroke="#c9d6f3" stroke-width="10" fill="none"/>')
    ring = ""
    for k in range(1, 12):
        a = math.pi * k / 12
        ring += f"M{f(330 - 64 * math.cos(a))} {f(704 - 48 * math.sin(a))}L{f(330 - 78 * math.cos(a))} {f(704 - 60 * math.sin(a))}"
    bridge.append(f'<path d="{ring}" stroke="#b5c6ee" stroke-width="1.2"/>')
    bridge.append('<path d="M266 704C266 672 296 656 330 656C364 656 394 672 394 704" stroke="#aebfe9" stroke-width="2" fill="none"/>')
    bridge.append(f'<path d="{deck_d}" stroke="#7f98d8" stroke-width="2.6" fill="none" stroke-linecap="round"/>')
    bridge.append('<path d="M60 700C150 700 232 638 330 638C428 638 510 700 600 700" stroke="#c3d0f0" stroke-width="1.6" fill="none"/>')
    rail = [[(62, 676), (154, 676), (232, 614), (330, 614)], [(330, 614), (428, 614), (506, 676), (598, 676)]]
    posts = ""
    for seg_r, seg_d in zip(rail, deck):
        for k in range(15):
            t = k / 14
            a, b = cubic(seg_r, t), cubic(seg_d, t)
            posts += f"M{f(a[0])} {f(a[1])}L{f(b[0])} {f(b[1])}"
    bridge.append(f'<path d="{posts}" stroke="#9db1e4" stroke-width="2.2" stroke-linecap="round"/>')
    bridge.append('<path d="M62 676C154 676 232 614 330 614C428 614 506 676 598 676" stroke="#879fdc" stroke-width="2.6" fill="none" stroke-linecap="round"/>')
    bridge.append('<path d="M62 683C154 683 232 621 330 621C428 621 506 683 598 683" stroke="#c3d0f0" stroke-width="1.2" fill="none"/>')
    bridge.append('<path d="M604 690H800V704H612Z" fill="url(#stone)"/><path d="M604 690H800" stroke="#7f98d8" stroke-width="2.6"/>')
    bridge.append(treeline([(640 + i * 22, 668) for i in range(8)], "#a2b8ea", "#8fa9e4", 41, 10, 18))
    o.append(f'<g mask="url(#rippleL)" opacity=".2"><g transform="translate(0 1408) scale(1 -1)">{"".join(bridge)}</g></g>')
    o += bridge
    # 水面光
    r = rng(5)
    streaks = ""
    for k in range(26):
        yy = 720 + k * 10 + next(r) * 6
        xx = next(r) * 760
        streaks += anim("shimmer", f'<rect x="{f(xx)}" y="{f(yy)}" width="{f(24 + next(r) * 70)}" height="2.2" rx="1.1" fill="#fff" opacity="{f(.5 + next(r) * .4)}"/>', delay=k * 1.1 % 3, dur=2.2 + k % 3 * .6)
    o.append(streaks)
    # 乌篷船
    o.append(f'<g mask="url(#rippleL)" opacity=".28"><g transform="translate(0 1600) scale(1 -1)">{wupeng_boat(470, 800, .9)}</g></g>')
    o.append(wupeng_boat(470, 800, .9))
    o.append('<path d="M396 806C430 812 510 812 540 806" stroke="#fff" stroke-width="2.4" stroke-linecap="round" opacity=".9" fill="none"/>')
    # 前景荷塘
    o += [standing_leaf(300, 880, 1, 4), lotus_bud(42, 846, .9, -6)]
    for args in [(20, 880, 64, 18, -10), (150, 904, 56, 16, 6), (276, 948, 52, 14, -4), (64, 952, 96, 26, -6), (196, 988, 84, 22, 4), (330, 990, 60, 16, 3)]:
        o.append(lily_pad(*args, grad="leaf" if args[2] > 60 else "leafB"))
    o += [lotus_flower(122, 896, 1.15, -4), lotus_flower(236, 924, .85, 6)]
    o.append(anim("mistmove", '<ellipse cx="120" cy="1010" rx="260" ry="40" fill="url(#mist)" opacity=".6"/>', delay=6))
    return defs, o


# —————————————————— 右景 ——————————————————
def leifeng(x, base):
    tiers = [(base - 8, base - 52, 48), (base - 60, base - 98, 42), (base - 106, base - 140, 36), (base - 148, base - 180, 30), (base - 188, base - 216, 24)]
    o = [f'<rect x="{x-66}" y="{base-8}" width="132" height="10" rx="2" fill="#4f67ad"/>',
         f'<rect x="{x-58}" y="{base-14}" width="116" height="8" rx="2" fill="#7690d6"/>']
    roofs = []
    for i, (yb, yt, w) in enumerate(tiers):
        lw = w * .42
        o.append(f'<rect x="{x-w}" y="{yt}" width="{f(w-lw)}" height="{yb-yt}" fill="#b5c7f2"/>')
        o.append(f'<rect x="{f(x-lw)}" y="{yt}" width="{f(2*lw)}" height="{yb-yt}" fill="#94ade9"/>')
        o.append(f'<rect x="{f(x+lw)}" y="{yt}" width="{f(w-lw)}" height="{yb-yt}" fill="#7591d9"/>')
        o.append(f'<path d="M{f(x-lw)} {yt}V{yb}M{f(x+lw)} {yt}V{yb}M{x-w} {yt}V{yb}M{x+w} {yt}V{yb}" stroke="#5f7cc9" stroke-width="1.6"/>')
        dw, dh = max(4, w * .16), (yb - yt) * .62
        o.append(f'<circle class="qb-glow" style="animation-delay:-{i * .9}s" cx="{x}" cy="{f(yb-dh*.6)}" r="{f(dw*2.4)}" fill="url(#glowWarm)"/>')
        o.append(f'<path d="M{f(x-dw)} {yb}V{f(yb-dh+dw)}A{f(dw)} {f(dw)} 0 0 1 {f(x+dw)} {f(yb-dh+dw)}V{yb}Z" fill="{WARM}"/>')
        for side in (-1, 1):
            cx = x + side * (lw + (w - lw) / 2)
            o.append(f'<rect x="{f(cx-2.6)}" y="{f(yb-dh*.9)}" width="5.2" height="{f(dh*.55)}" rx="2.6" fill="{WARM}" opacity="{.95 if side < 0 else .75}"/>')
        o.append(f'<rect x="{x-w-4}" y="{yb-4}" width="{2*w+8}" height="4" fill="#dfe7fa"/>')
        o.append(f'<path d="' + "".join(f"M{f(x-w-2+k*6)} {yb-4}v4" for k in range(int((2 * w + 4) / 6) + 1)) + '" stroke="#9fb3e6" stroke-width="1"/>')
        L, R = x - w - 14, x + w + 14
        nxt = tiers[i + 1] if i + 1 < len(tiers) else None
        if nxt:
            wn = nxt[2]
            roof = f'M{x-wn-4} {yt-10}H{x+wn+4}L{R-4} {yt-2}Q{R+3} {yt} {R+9} {yt-8}Q{R+6} {yt+4} {R-4} {yt+4}H{L+4}Q{L-6} {yt+4} {L-9} {yt-8}Q{L-3} {yt} {L+4} {yt-2}Z'
        else:
            roof = f'M{x} {yt-34}Q{x-10} {yt-12} {L+4} {yt-2}Q{L-3} {yt} {L-9} {yt-8}Q{L-6} {yt+4} {L+4} {yt+4}H{R-4}Q{R+6} {yt+4} {R+9} {yt-8}Q{R+3} {yt} {R-4} {yt-2}Q{x+10} {yt-12} {x} {yt-34}Z'
        roofs.append(f'<path d="{roof}" fill="url(#roof)"/>')
        roofs.append(f'<path d="M{L+6} {yt+2}H{R-6}" stroke="#8ea6e4" stroke-width="1.4"/>')
        tiles = "".join(f"M{f(x-w+k*5)} {yt-1}V{yt-6}" for k in range(int(2 * w / 5) + 1))
        roofs.append(f'<path d="{tiles}" stroke="#fff" stroke-opacity=".18" stroke-width="1.2"/>')
        roofs.append(f'<path d="M{L-9} {yt-8}v6M{R+9} {yt-8}v6" stroke="#34509e" stroke-width="1"/><circle cx="{L-9}" cy="{yt-1}" r="2" fill="{GOLD}"/><circle cx="{R+9}" cy="{yt-1}" r="2" fill="{GOLD}"/>')
    top = tiers[-1][1] - 34
    roofs.append(f'<path d="M{x} {top}V{top-40}" stroke="#34509e" stroke-width="2.4" stroke-linecap="round"/>')
    for k in range(6):
        roofs.append(f'<ellipse cx="{x}" cy="{top-6-k*5.5}" rx="{f(6-k*.7)}" ry="1.8" fill="#34509e"/>')
    roofs.append(f'<circle cx="{x}" cy="{top-44}" r="3.6" fill="{GOLD}"/>')
    return "".join(o + roofs)


def stone_lantern(x, y, s):
    return (f'<g transform="translate({f(x)} {f(y)}) scale({f(s)})">'
            '<ellipse cx="0" cy="4" rx="34" ry="6" fill="#c2d1f3" opacity=".8"/>'
            + anim("ring", '<ellipse cx="0" cy="8" rx="30" ry="5" fill="none" stroke="#fff" stroke-width="1.8"/>', delay=x % 4) +
            anim("ring", '<ellipse cx="0" cy="8" rx="30" ry="5" fill="none" stroke="#fff" stroke-width="1.8"/>', delay=x % 4 + 2) +
            '<rect x="-5" y="-18" width="10" height="22" fill="#5d74b4"/><rect x="-5" y="-18" width="4" height="22" fill="#8399d4"/>'
            '<path d="M-18-24H18L15-17H-15Z" fill="#6f87cc"/>'
            '<circle cx="0" cy="-46" r="23" fill="url(#sphere)"/>'
            '<ellipse cx="-8" cy="-56" rx="9" ry="5" fill="#fff" opacity=".35"/>'
            f'<circle class="qb-glow" style="animation-delay:-{f(x % 4.5)}s" cx="0" cy="-46" r="9" fill="url(#glowWarm)"/>'
            f'<circle cx="0" cy="-45" r="4.6" fill="{WARM}"/><circle cx="0" cy="-44" r="2.4" fill="#ffc373"/>'
            f'<path d="M-17-50C-19-46-19-42-17-38L-14-39C-15-42-15-46-14-49Z" fill="{WARM}" opacity=".85"/>'
            f'<path d="M17-50C19-46 19-42 17-38L14-39C15-42 15-46 14-49Z" fill="{WARM}" opacity=".7"/>'
            '<path d="M-24-68C-10-78 10-78 24-68L20-64H-20Z" fill="url(#roof)"/>'
            '<circle cx="0" cy="-77" r="7" fill="#6f87cc"/><circle cx="-2" cy="-79" r="2.4" fill="#a9bdf0"/>'
            '<circle cx="0" cy="-88" r="4.4" fill="#6f87cc"/>'
            '<path d="M0-92V-102" stroke="#34509e" stroke-width="2" stroke-linecap="round"/>'
            f'<path d="M-2 10h4M-1.5 18h3M-1 26h2" stroke="{WARM}" stroke-width="2.4" stroke-linecap="round" opacity=".7"/></g>')


def huafang(x, y, s):
    windows = "".join(f'<rect x="{wx}" y="-50" width="20" height="18" rx="2" fill="{WARM}"/><path d="M{wx+10}-50V-32M{wx}-41H{wx+20}" stroke="#e8c48a" stroke-width="1"/>' for wx in range(-76, 64, 28))
    return anim("bob", f'<g transform="translate({f(x)} {f(y)}) scale({f(s)})">'
            '<path d="M-130-6C-100 18 100 20 136-18L126-10H-120Z" fill="url(#hull)"/>'
            '<path d="M-122-10H128C142-14 148-28 142-40L134-36C136-24 130-18 122-16H-122Z" fill="#6c86cc"/>'
            '<path d="M-118-12H124" stroke="#a9bdf0" stroke-width="2"/>'
            '<rect x="-86" y="-58" width="168" height="46" fill="#b0c3f1"/>'
            f'{windows}'
            '<path d="M-100-56C-60-80 60-80 96-56Q106-52 116-60Q112-48 98-48H-102Q-116-48-120-60Q-110-52-100-56Z" fill="url(#roof)"/>'
            '<path d="M-60-72C-30-86 30-86 56-72" stroke="#7d95d6" stroke-width="2" fill="none"/>'
            f'<path d="M-104-48V-40M100-48V-40" stroke="#34509e" stroke-width="1.2"/><ellipse cx="-104" cy="-34" rx="5" ry="6.5" fill="{CORAL}"/><ellipse cx="100" cy="-34" rx="5" ry="6.5" fill="{CORAL}"/>'
            f'<path d="M-122-10V-70" stroke="#3e5189" stroke-width="2.4"/><path d="M-121-70L-96-62L-121-54Z" fill="{CORAL}"/></g>', delay=x % 6)


def right_scene():
    defs = [COMMON_DEFS,
            '<radialGradient id="sunGlow"><stop offset="0" stop-color="#fff1e4"/><stop offset=".25" stop-color="#ffe0cc"/><stop offset=".6" stop-color="#fde6dc" stop-opacity=".55"/><stop offset="1" stop-color="#fde6dc" stop-opacity="0"/></radialGradient>',
            '<radialGradient id="sunDisc"><stop offset="0" stop-color="#fff6ec"/><stop offset=".7" stop-color="#ffe2cc"/><stop offset="1" stop-color="#fbd2bd"/></radialGradient>',
            '<linearGradient id="farR" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#dcdcf3"/><stop offset="1" stop-color="#e3e9f9" stop-opacity="0"/></linearGradient>',
            '<linearGradient id="midR" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#bfcdf1"/><stop offset="1" stop-color="#dbe4f8" stop-opacity="0"/></linearGradient>',
            '<linearGradient id="hillR" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#a6bbed"/><stop offset=".8" stop-color="#cfdbf6"/><stop offset="1" stop-color="#dfe7f9" stop-opacity="0"/></linearGradient>',
            '<linearGradient id="sunPath" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffd9c4"/><stop offset="1" stop-color="#ffd9c4" stop-opacity="0"/></linearGradient>',
            ripple_mask("rippleR", 600, 1000, 19)]
    o = [anim("breathe", '<circle cx="560" cy="340" r="260" fill="url(#sunGlow)"/>'), '<circle cx="560" cy="340" r="58" fill="url(#sunDisc)"/>']
    o.append('<rect x="440" y="352" width="210" height="9" rx="4.5" fill="#fff" opacity=".75"/><rect x="520" y="378" width="190" height="7" rx="3.5" fill="#fde3d6"/><rect x="470" y="318" width="80" height="6" rx="3" fill="#fff" opacity=".7"/>')
    o += [cloud(150, 190, .9, True), cloud(300, 120, .6)]
    o.append(anim("flock", bird(380, 290, 1) + bird(406, 272, .8) + bird(424, 300, .7), delay=9))
    o.append('<path d="M0 470C120 440 220 420 320 432C420 444 480 410 560 404C660 396 730 420 800 432V700H0Z" fill="url(#farR)"/>')
    o.append('<path d="M0 540C100 516 200 500 290 512C360 520 420 500 470 496C560 490 660 506 800 520V700H0Z" fill="url(#midR)"/>')
    o.append(anim("mistmove", '<ellipse cx="420" cy="540" rx="380" ry="26" fill="url(#mist)"/>', delay=2))
    hill = [[(300, 612), (380, 586), (440, 566), (500, 560)], [(500, 560), (560, 554), (640, 560), (700, 576)], [(700, 576), (740, 586), (770, 596), (800, 602)]]
    o.append(treeline(lerp_curve(hill, 10), "#a6bbed", "#94ade8", 31, 8, 17, skip=(500, 620)))
    o.append('<path d="M300 612C380 586 440 566 500 560C560 554 640 560 700 576C740 586 770 596 800 602V660H300Z" fill="url(#hillR)"/>')
    o.append(leifeng(560, 560))
    o.append(treeline([(470 + k * 16, 566 + abs(k - 2) * 2) for k in range(5)] + [(630 + k * 16, 566 + k * 3) for k in range(5)], "#9bb2ea", "#8aa5e4", 37, 8, 13))
    o.append(anim("mistmove", '<ellipse cx="560" cy="600" rx="320" ry="16" fill="url(#mist)"/>', delay=28))
    # 水面
    o.append('<rect x="0" y="600" width="800" height="400" fill="url(#water)"/>')
    o.append(f'<g mask="url(#rippleR)" opacity=".26"><g transform="translate(0 1210) scale(1 -1)">{leifeng(560, 560)}</g></g>')
    o.append('<g mask="url(#rippleR)">' + anim("shimmer", '<rect x="520" y="606" width="80" height="300" fill="url(#sunPath)" opacity=".55"/>', dur=3.5) + '</g>')
    r = rng(9)
    streaks = ""
    for k in range(30):
        yy = 616 + k * 11 + next(r) * 6
        xx = next(r) * 760
        streaks += anim("shimmer", f'<rect x="{f(xx)}" y="{f(yy)}" width="{f(20 + next(r) * 80)}" height="2.2" rx="1.1" fill="#fff" opacity="{f(.45 + next(r) * .45)}"/>', delay=k * .9 % 3, dur=2.2 + k % 4 * .5)
    o.append(streaks)
    # 画舫
    o.append(f'<g mask="url(#rippleR)" opacity=".26"><g transform="translate(0 1432) scale(1 -1)">{huafang(250, 716, .8)}</g></g>')
    o.append(huafang(250, 716, .8))
    # 三潭印月
    for lx, ly, sc in [(360, 868, 1.05), (520, 900, 1.35), (690, 862, .95)]:
        o.append(stone_lantern(lx, ly, sc))
    # 右上角亭檐与灯笼：檐口自右向左上翘，带瓦垄、封檐板和椽头
    eave_line = "M800 124C720 124 652 120 612 104C592 96 578 84 568 68"
    o.append(f'<path d="M800 0V124C720 124 652 120 612 104C592 96 578 84 568 68C584 72 598 50 606 0Z" fill="url(#roof)"/>')
    tiles = ""
    for k, x0 in enumerate(range(616, 800, 13)):
        t = (x0 - 600) / 200
        y_end = 124 - (1 - t) ** 2 * 50
        tiles += f"M{x0} 0C{x0 - 2} {f(y_end * .5)} {x0 - 4} {f(y_end * .8)} {f(x0 - 6)} {f(y_end - 4)}"
    o.append(f'<path d="{tiles}" stroke="#7d95d6" stroke-opacity=".45" stroke-width="2.2" fill="none"/>')
    o.append(f'<path d="{eave_line}" stroke="#2c4588" stroke-width="9" fill="none" stroke-linecap="round"/>')
    o.append(f'<path d="{eave_line}" stroke="#8ea6e4" stroke-width="1.6" fill="none" transform="translate(0 -5)"/>')
    ends = "".join(f'<circle cx="{x}" cy="{f(124 - (1 - (x - 600) / 200) ** 2 * 50 + 5)}" r="3.2" fill="#4f6cbd"/>' for x in range(624, 800, 16))
    o.append(ends)
    o.append('<path d="M568 68C562 60 562 50 570 46C566 54 570 60 576 62Z" fill="#2c4588"/>')
    lantern = []
    lantern.append(f'<path d="M590 98V146" stroke="#34509e" stroke-width="1.4"/>')
    lantern.append(f'<circle cx="590" cy="168" r="34" fill="url(#glowWarm)" opacity=".7"/>')
    lantern.append(f'<rect x="582" y="146" width="16" height="4" rx="1" fill="#34509e"/><ellipse cx="590" cy="166" rx="13" ry="16" fill="{CORAL}"/>'
             f'<path d="M583 152C580 160 580 172 583 180M597 152C600 160 600 172 597 180M590 150V182" stroke="#d9746d" stroke-width="1" fill="none"/>'
             f'<rect x="582" y="181" width="16" height="4" rx="1" fill="#34509e"/><path d="M590 185V200" stroke="{GOLD}" stroke-width="2" stroke-linecap="round"/>')
    o.append(anim("swing", "".join(lantern)))
    o.append(anim("mistmove", '<ellipse cx="560" cy="1012" rx="300" ry="40" fill="url(#mist)" opacity=".6"/>', delay=28))
    return defs, o


def prefix_ids(text, prefix):
    for name in sorted(set(re.findall(r'id="([^"]+)"', text)), key=len, reverse=True):
        text = text.replace(f'id="{name}"', f'id="{prefix}{name}"').replace(f'url(#{name})', f'url(#{prefix}{name})')
    return text


def svg(defs, body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" fill="none">'
            f'<defs>{"".join(defs)}</defs>{"".join(body)}</svg>\n')


if __name__ == "__main__":
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    (out / "hangzhou-west.svg").write_text(prefix_ids(svg(*left_scene()), "qbw-"), encoding="utf-8")
    (out / "hangzhou-south.svg").write_text(prefix_ids(svg(*right_scene()), "qbs-"), encoding="utf-8")
    print("ok", sorted(p.name for p in out.glob("*.svg")))
