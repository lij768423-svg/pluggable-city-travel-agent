"""三城静态主题插画（2026-10-20 用户：“重画一下背景，精细一点要有城市特色”）。

意象插画，不是实景测绘。左右两幅并排展示，太阳只画在右幅。扁平填色、远近分层、建筑明暗两面与暖光门窗、水面倒影；不画柳树与单棵树，林木只用成片树冠。
静态：不带动画（旧版每幅约 70 组循环动画，持续重绘导致输入与滚动卡顿）。
在 frontend 执行：python3 scripts/gen_city_art.py src/assets/art
"""
from __future__ import annotations

import math
import random
import sys
from pathlib import Path

W, H = 800, 1000
# 站点主色系＋少量暖色
INK = "#24305c"
NAVY = "#34437a"
BLUE = "#4a64b8"
BLUE2 = "#6f86cc"
MIST = ["#eef2fb", "#e2e8f7", "#d2dbf2", "#bccbec", "#a5b8e3", "#8ea4d8"]
WALL = "#f8f9fd"
WALL_SHADE = "#dfe5f4"
TILE = "#3a4a82"
TILE_LIGHT = "#5d72b5"
WARM = "#f5d48f"
WARM_DEEP = "#eab567"
LANTERN = "#e6897b"
SUN = "#fbe6c2"
TEAL = "#9cc4c8"
TEAL_DEEP = "#6f9fae"
STONE = "#c9cfdf"
STONE_SHADE = "#a9b2c9"


def f(value: float) -> str:
    return f"{value:.1f}".rstrip("0").rstrip(".")


def pts(points) -> str:
    return " ".join(f"{f(x)},{f(y)}" for x, y in points)


class Art:
    def __init__(self, key: str, seed: int):
        self.key = key
        self.rand = random.Random(seed)
        self.defs: list[str] = []
        self.parts: list[str] = []

    def add(self, *items: str) -> None:
        self.parts.extend(items)

    def gradient(self, name: str, stops: list[tuple[float, str, float]], vertical: bool = True) -> str:
        ident = f"{self.key}-{name}"
        x2, y2 = ("0", "1") if vertical else ("1", "0")
        body = "".join(f'<stop offset="{f(o)}" stop-color="{c}" stop-opacity="{f(a)}"/>' for o, c, a in stops)
        self.defs.append(f'<linearGradient id="{ident}" x1="0" y1="0" x2="{x2}" y2="{y2}">{body}</linearGradient>')
        return f"url(#{ident})"

    def radial(self, name: str, stops: list[tuple[float, str, float]]) -> str:
        ident = f"{self.key}-{name}"
        body = "".join(f'<stop offset="{f(o)}" stop-color="{c}" stop-opacity="{f(a)}"/>' for o, c, a in stops)
        self.defs.append(f'<radialGradient id="{ident}">{body}</radialGradient>')
        return f"url(#{ident})"

    def svg(self) -> str:
        return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {W} {H}" '
                f'width="{W}" height="{H}"><defs>{"".join(self.defs)}</defs>{"".join(self.parts)}</svg>')


# ---------------- 通用元素 ----------------

def sky(art: Art, top: str, bottom: str, horizon: float = 700) -> None:
    art.add(f'<rect width="{W}" height="{H}" fill="{art.gradient("sky", [(0, top, 1), (horizon / H, bottom, 1), (1, bottom, 1)])}"/>')


def sun(art: Art, x: float, y: float, r: float, color: str = SUN) -> None:
    halo = art.radial("halo", [(0, color, .9), (.45, color, .45), (1, color, 0)])
    art.add(f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r * 3.2)}" fill="{halo}"/>',
            f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(r)}" fill="{color}"/>')


def cloud(x: float, y: float, s: float = 1, color: str = "#ffffff", shade: str = "#e3e9f7") -> str:
    """层叠云团：底边平、上沿三四个圆弧，下方一道阴影色。"""
    return (f'<g transform="translate({f(x)} {f(y)}) scale({f(s)})">'
            f'<path d="M-70 0Q-72-22-50-24Q-44-48-18-44Q-4-66 22-52Q48-60 54-34Q78-32 74 0Z" fill="{color}"/>'
            f'<path d="M-62 0Q-20-12 66 0Z" fill="{shade}"/></g>')


def hills(seed: int, base: float, height: float, color: str, wobble: int = 5, x0: float = -20, x1: float = W + 20) -> str:
    """平滑起伏的远山剪影。"""
    r = random.Random(seed)
    peaks = [(x0, base - r.uniform(.2, .5) * height)]
    step = (x1 - x0) / wobble
    for index in range(1, wobble + 1):
        peaks.append((x0 + index * step + r.uniform(-step * .2, step * .2), base - r.uniform(.35, 1) * height))
    d = f"M{f(x0)} {f(H)}L{f(peaks[0][0])} {f(peaks[0][1])}"
    for (ax, ay), (bx, by) in zip(peaks, peaks[1:]):
        mx = (ax + bx) / 2
        d += f"Q{f(ax + (bx - ax) * .25)} {f(min(ay, by) - height * .12)} {f(mx)} {f((ay + by) / 2)}T{f(bx)} {f(by)}"
    d += f"L{f(x1)} {f(H)}Z"
    return f'<path d="{d}" fill="{color}"/>'


def canopy(cx: float, cy: float, width: float, height: float, color: str, light: str, seed: int, dots: bool = True) -> str:
    """成片树冠：底层暗色团块＋上层亮色团块＋零星高光点，轮廓起伏不规则；不画树干。"""
    r = random.Random(seed)
    base, top, spots = [], [], []
    count = max(6, int(width / 18))
    for _ in range(count):
        t = r.random()
        x = cx - width / 2 + t * width
        envelope = math.sin(math.pi * t) ** .6
        rad = height * (.22 + .38 * envelope) * r.uniform(.7, 1.15)
        y = cy - height * .25 - envelope * height * .35 + r.uniform(-.12, .12) * height
        base.append(f'<circle cx="{f(x)}" cy="{f(y)}" r="{f(rad)}"/>')
        if r.random() < .75:
            top.append(f'<circle cx="{f(x - rad * .2)}" cy="{f(y - rad * .32)}" r="{f(rad * r.uniform(.45, .7))}"/>')
        if dots and r.random() < .18:
            spots.append(f'<circle cx="{f(x - rad * .35)}" cy="{f(y - rad * .55)}" r="{f(max(1.6, rad * .12))}"/>')
    floor = f'<rect x="{f(cx - width / 2)}" y="{f(cy - height * .3)}" width="{f(width)}" height="{f(height * .3)}"/>'
    return (f'<g fill="{color}">{"".join(base)}{floor}</g><g fill="{light}" opacity=".7">{"".join(top)}</g>'
            f'<g fill="#ffffff" opacity=".22">{"".join(spots)}</g>')


def ridge(seed: int, points: list[tuple[float, float]], base: float, light: str, dark: str) -> str:
    """有明暗面的山：主体亮色，每个山峰右坡叠一块暗面，形成体积。"""
    d = f"M{f(points[0][0])} {f(base)}L{f(points[0][0])} {f(points[0][1])}"
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        d += f"Q{f((ax + bx) / 2)} {f(min(ay, by) - 8)} {f(bx)} {f(by)}"
    d += f"L{f(points[-1][0])} {f(base)}Z"
    shades = []
    for (ax, ay), (bx, by), (cx2, cy2) in zip(points, points[1:], points[2:]):
        if by < ay and by < cy2:  # 峰顶：右坡暗面
            shades.append(f'<path d="M{f(bx)} {f(by)}Q{f(bx + (cx2 - bx) * .5)} {f(by + (cy2 - by) * .4)} {f(cx2)} {f(cy2)}L{f(cx2 - (base - cy2) * .35)} {f(base)}'
                          f'C{f(bx + (cx2 - bx) * .55)} {f(base - (base - by) * .2)} {f(bx + (cx2 - bx) * .05)} {f(by + (base - by) * .55)} {f(bx)} {f(by)}Z" fill="{dark}" opacity=".75"/>')
    return f'<path d="{d}" fill="{light}"/>{"".join(shades)}'


def water(art: Art, top: float, colors: tuple[str, str]) -> None:
    art.add(f'<rect y="{f(top)}" width="{W}" height="{f(H - top)}" fill="{art.gradient("water", [(0, colors[0], 1), (1, colors[1], 1)])}"/>')


def ripples(seed: int, top: float, bottom: float, color: str = "#ffffff", count: int = 26, opacity: float = .7) -> str:
    r = random.Random(seed)
    lines = []
    for _ in range(count):
        y = r.uniform(top, bottom)
        x = r.uniform(-20, W)
        length = r.uniform(18, 70) * (1 + (y - top) / (bottom - top))
        lines.append(f'<path d="M{f(x)} {f(y)}h{f(length)}"/>')
    return f'<g stroke="{color}" stroke-width="2" stroke-linecap="round" opacity="{f(opacity)}">{"".join(lines)}</g>'


def reflection(art: Art, ident: str, axis: float, top: float, bottom: float, opacity: float = .3) -> str:
    """把编号为 ident 的组按水平线 axis 翻转，裁到水面范围；横向条纹蒙版把倒影切碎成波光。"""
    clip, mask = f"{art.key}-clip-{ident}", f"{art.key}-mask-{ident}"
    art.defs.append(f'<clipPath id="{clip}"><rect y="{f(top)}" width="{W}" height="{f(bottom - top)}"/></clipPath>')
    r = random.Random(len(ident) * 97 + int(axis))
    stripes, y = [], top
    while y < bottom:
        h = 3 + (y - top) * .03
        fade = max(0, 1 - (y - top) / (bottom - top))
        x = r.uniform(-60, 0)
        while x < W:
            w = r.uniform(80, 260)
            stripes.append(f'<rect x="{f(x)}" y="{f(y)}" width="{f(w)}" height="{f(h)}" fill="#fff" fill-opacity="{f(fade * r.uniform(.6, 1))}"/>')
            x += w + r.uniform(6, 30)
        y += h + 1.5 + (y - top) * .012
    art.defs.append(f'<mask id="{mask}" maskUnits="userSpaceOnUse" x="0" y="{f(top)}" width="{W}" height="{f(bottom - top)}">{"".join(stripes)}</mask>')
    return (f'<g clip-path="url(#{clip})" mask="url(#{mask})" opacity="{f(opacity)}"><use href="#{art.key}-{ident}" '
            f'transform="translate(0 {f(axis * 2)}) scale(1 -1)"/></g>')


def mist_band(art: Art, y: float, height: float, name: str, color: str = "#ffffff", opacity: float = .5) -> str:
    fill = art.gradient(name, [(0, color, 0), (.5, color, opacity), (1, color, 0)])
    return f'<rect y="{f(y)}" width="{W}" height="{f(height)}" fill="{fill}"/>'


def birds(x: float, y: float, count: int = 3, color: str = NAVY) -> str:
    out = []
    for index in range(count):
        bx, by, s = x + index * 26 + (index % 2) * 8, y + (index % 2) * 10 - index * 3, .8 + (index % 3) * .15
        out.append(f'<path d="M{f(bx - 9 * s)} {f(by)}Q{f(bx - 4 * s)} {f(by - 5 * s)} {f(bx)} {f(by)}Q{f(bx + 4 * s)} {f(by - 5 * s)} {f(bx + 9 * s)} {f(by)}" '
                   f'stroke="{color}" stroke-width="1.6" fill="none" stroke-linecap="round"/>')
    return "".join(out)


def eave_roof(x: float, y: float, width: float, height: float, color: str = TILE, light: str = TILE_LIGHT, lift: float = 12) -> str:
    """飞檐屋顶：两端上翘，左半亮面、右半暗面，檐口一道浅色。"""
    a, b = x - width / 2, x + width / 2
    bottom = y + height
    left = (f"M{f(x)} {f(y)}L{f(x - width * .18)} {f(y)}Q{f(a + width * .12)} {f(bottom - 2)} {f(a - lift * .4)} {f(bottom - lift)}"
            f"Q{f(a + 4)} {f(bottom + 3)} {f(x)} {f(bottom + 2)}Z")
    right = (f"M{f(x)} {f(y)}L{f(x + width * .18)} {f(y)}Q{f(b - width * .12)} {f(bottom - 2)} {f(b + lift * .4)} {f(bottom - lift)}"
             f"Q{f(b - 4)} {f(bottom + 3)} {f(x)} {f(bottom + 2)}Z")
    ridge = f'<path d="M{f(x - width * .2)} {f(y)}H{f(x + width * .2)}" stroke="{color}" stroke-width="4" stroke-linecap="round"/>'
    rib = "".join(f'<path d="M{f(x + t * width * .17)} {f(y + 2)}Q{f(x + t * width * .32)} {f(y + height * .6)} {f(x + t * width * .44)} {f(bottom - 1)}" '
                  f'stroke="{light if t < 0 else color}" stroke-opacity=".55" stroke-width="1.2" fill="none"/>' for t in (-.9, -.55, -.2, .2, .55, .9))
    edge = f'<path d="M{f(a)} {f(bottom - lift * .6)}Q{f(x)} {f(bottom + 7)} {f(b)} {f(bottom - lift * .6)}" stroke="#c9d4f0" stroke-width="2" fill="none"/>'
    return f'<path d="{left}" fill="{light}"/><path d="{right}" fill="{color}"/>{rib}{ridge}{edge}'


def windows_row(x: float, y: float, count: int, width: float, height: float, gap: float, lit: set[int] | None = None) -> str:
    out = []
    for index in range(count):
        wx = x + index * (width + gap)
        warm = lit is None or index in lit
        out.append(f'<rect x="{f(wx)}" y="{f(y)}" width="{f(width)}" height="{f(height)}" rx="1.5" fill="{WARM if warm else NAVY}"/>')
        out.append(f'<path d="M{f(wx + width / 2)} {f(y)}v{f(height)}M{f(wx)} {f(y + height / 2)}h{f(width)}" stroke="{WARM_DEEP if warm else BLUE}" stroke-width="1"/>')
    return "".join(out)


def pagoda(x: float, base: float, tiers: int, width: float, tier_h: float, shrink: float = .1,
           body: str = "#f1f3fb", shade: str = "#cdd6ee", lit: bool = True, spire: float = 40) -> str:
    """楼阁式塔：石台基；每层塔身左亮右暗、平座栏杆、拱门与窗透暖光、飞檐出挑（下沿一道檐影）；塔刹相轮。"""
    out = [f'<path d="M{f(x - width * .62)} {f(base)}h{f(width * 1.24)}l-8-10h{f(-width * 1.24 + 16)}Z" fill="{STONE_SHADE}"/>',
           f'<path d="M{f(x - width * .55)} {f(base - 10)}h{f(width * 1.1)}l-6-8h{f(-width * 1.1 + 12)}Z" fill="{STONE}"/>']
    y = base - 18
    for level in range(tiers):
        w = width * (1 - shrink * level)
        h = tier_h * (1 - .06 * level)
        bw = w * .6
        top = y - h
        out.append(f'<rect x="{f(x - bw / 2)}" y="{f(top + h * .3)}" width="{f(bw / 2)}" height="{f(h * .7)}" fill="{body}"/>')
        out.append(f'<rect x="{f(x)}" y="{f(top + h * .3)}" width="{f(bw / 2)}" height="{f(h * .7)}" fill="{shade}"/>')
        out.append(f'<rect x="{f(x - bw / 2)}" y="{f(top + h * .3)}" width="{f(bw)}" height="{f(h * .07)}" fill="{NAVY}" opacity=".35"/>')
        if lit:
            dw = bw * .22
            out.append(f'<path d="M{f(x - dw / 2)} {f(y)}v{f(-h * .34)}a{f(dw / 2)} {f(dw / 2)} 0 0 1 {f(dw)} 0v{f(h * .34)}Z" fill="{WARM}"/>')
            for side in (-1, 1):
                wx = x + side * bw * .32 - bw * .06
                out.append(f'<rect x="{f(wx)}" y="{f(top + h * .48)}" width="{f(bw * .12)}" height="{f(h * .24)}" rx="1" '
                           f'fill="{WARM if side < 0 else WARM_DEEP}" opacity="{".9" if side < 0 else ".75"}"/>')
        # 平座：塔身底部一圈栏杆
        out.append(f'<rect x="{f(x - bw * .62)}" y="{f(y - 4)}" width="{f(bw * 1.24)}" height="4" fill="{BLUE2}"/>')
        out.append(f'<g stroke="{BLUE2}" stroke-width="1.2">' + "".join(
            f'<path d="M{f(x - bw * .6 + k * bw * 1.2 / 8)} {f(y - 4)}v-6"/>' for k in range(9)) + f'<path d="M{f(x - bw * .62)} {f(y - 10)}h{f(bw * 1.24)}"/></g>')
        roof_h = h * .3
        out.append(f'<path d="M{f(x - w / 2)} {f(top + roof_h + 3)}Q{f(x)} {f(top + roof_h + 10)} {f(x + w / 2)} {f(top + roof_h + 3)}" stroke="{NAVY}" stroke-width="3" opacity=".25" fill="none"/>')
        out.append(eave_roof(x, top, w, roof_h, lift=6 + w * .06))
        y = top + roof_h * .15
    out.append(f'<path d="M{f(x)} {f(y - spire)}V{f(y)}" stroke="{NAVY}" stroke-width="3"/>')
    out.extend(f'<ellipse cx="{f(x)}" cy="{f(y - spire * t)}" rx="{f(6 - t * 3)}" ry="2" fill="{NAVY}"/>' for t in (.2, .35, .5, .65))
    out.append(f'<circle cx="{f(x)}" cy="{f(y - spire - 3)}" r="3.5" fill="{WARM_DEEP}"/>')
    return "".join(out)


def slender_pagoda(x: float, base: float, height: float, width: float, tiers: int = 7, body: str = "#e9ecf6", shade: str = "#c3cce6") -> str:
    """实心砖塔（如保俶塔）：瘦高收分的塔身，左亮右暗，每层一道出挑薄檐，顶部塔刹。"""
    top_w = width * .55
    out = [f'<path d="M{f(x - width / 2)} {f(base)}L{f(x - top_w / 2)} {f(base - height)}H{f(x)}V{f(base)}Z" fill="{body}"/>',
           f'<path d="M{f(x)} {f(base)}V{f(base - height)}H{f(x + top_w / 2)}L{f(x + width / 2)} {f(base)}Z" fill="{shade}"/>']
    for level in range(tiers):
        t = (level + 1) / (tiers + 1)
        ey = base - height * t
        ew = width - (width - top_w) * t + 12
        out.append(f'<path d="M{f(x - ew / 2)} {f(ey)}Q{f(x)} {f(ey - 4)} {f(x + ew / 2)} {f(ey)}l-3 4H{f(x - ew / 2 + 3)}Z" fill="{NAVY}"/>')
    out.append(f'<path d="M{f(x)} {f(base - height - 30)}V{f(base - height)}" stroke="{NAVY}" stroke-width="2.4"/>')
    out.extend(f'<ellipse cx="{f(x)}" cy="{f(base - height - 30 * t)}" rx="{f(4.5 - t * 2)}" ry="1.6" fill="{NAVY}"/>' for t in (.2, .45, .7))
    return "".join(out)


def arch_bridge(x: float, y: float, span: float, rise: float, color: str = "#eef1f9", shade: str = STONE_SHADE,
                rail: bool = True, opening: float = .46) -> str:
    """单孔石拱桥：桥面弧线、桥身挖出半圆拱洞（evenodd），拱圈券石、桥身石缝，桥栏随桥面起伏。"""
    a, b = x - span / 2, x + span / 2
    r = span * opening / 2
    deck = f"M{f(a)} {f(y)}Q{f(x)} {f(y - rise * 2)} {f(b)} {f(y)}"
    body = f"{deck}Z M{f(x - r)} {f(y)}A{f(r)} {f(r * .95)} 0 0 1 {f(x + r)} {f(y)}Z"
    out = [f'<path d="{body}" fill="{color}" fill-rule="evenodd"/>',
           f'<path d="M{f(x)} {f(y - rise * 2 * .5)}Q{f(x + span * .3)} {f(y - rise * .7)} {f(b)} {f(y)}L{f(x + r)} {f(y)}A{f(r)} {f(r * .95)} 0 0 0 {f(x)} {f(y - r * .95)}Z" '
           f'fill="{shade}" opacity=".35"/>']
    # 拱圈：券石放射线
    ring = []
    for index in range(13):
        ang = math.pi - index * math.pi / 12
        cx1, cy1 = x + r * math.cos(ang), y - r * .95 * math.sin(ang)
        cx2, cy2 = x + (r + 9) * math.cos(ang), y - (r * .95 + 9) * math.sin(ang)
        ring.append(f'<path d="M{f(cx1)} {f(cy1)}L{f(cx2)} {f(cy2)}"/>')
    out.append(f'<path d="M{f(x - r - 9)} {f(y)}A{f(r + 9)} {f(r * .95 + 9)} 0 0 1 {f(x + r + 9)} {f(y)}" fill="none" stroke="{shade}" stroke-width="2"/>'
               f'<g stroke="{shade}" stroke-width="1.3">{"".join(ring)}</g>')
    out.append(f'<path d="{deck}" fill="none" stroke="{shade}" stroke-width="3"/>')
    if rail:
        posts = []
        for index in range(15):
            t = index / 14
            px = a + span * .04 + t * span * .92
            py = y - 4 * t * (1 - t) * rise * 2 * .5 * 2 + (1 - 4 * t * (1 - t)) * 0
            py = (1 - t) ** 2 * y + 2 * (1 - t) * t * (y - rise * 2) + t ** 2 * y
            posts.append(f'<path d="M{f(px)} {f(py - 1)}v-11"/>')
        rail_line = f"M{f(a + span * .04)} {f(y - 12)}Q{f(x)} {f(y - rise * 2 - 12)} {f(b - span * .04)} {f(y - 12)}"
        out.append(f'<g stroke="{BLUE2}" stroke-width="2.2" fill="none" stroke-linecap="round">{"".join(posts)}<path d="{rail_line}"/></g>')
    return "".join(out)


def boat(x: float, y: float, s: float = 1, roof_color: str = NAVY, lantern: bool = False) -> str:
    """乌篷/画舫小船：船身、篷顶（左亮右暗）、船头人影不画。"""
    hull = f'<path d="M-62 0Q-40 14 0 15Q42 14 66-4L58 3Q30 18-2 18Q-44 18-62 0Z" fill="{NAVY}"/>'
    deck = '<path d="M-60 0H64Q40 12 0 13Q-40 12-60 0Z" fill="#5368ad"/>'
    cabin = (f'<path d="M-34 0V-16Q-10-34 18-30Q34-26 40-14V0Z" fill="{roof_color}"/>'
             f'<path d="M-30-1V-14Q-10-29 14-26V-1Z" fill="{TILE_LIGHT}"/>'
             f'<rect x="-22" y="-14" width="10" height="9" fill="{WARM}"/><rect x="-6" y="-17" width="10" height="11" fill="{WARM}"/><rect x="10" y="-15" width="10" height="10" fill="{WARM_DEEP}"/>')
    lamp = (f'<path d="M50-4V-22" stroke="{NAVY}" stroke-width="1.5"/><ellipse cx="50" cy="-26" rx="5" ry="7" fill="{LANTERN}"/>' if lantern else "")
    oar = '<path d="M60-2L84 18" stroke="#5368ad" stroke-width="2.4" stroke-linecap="round"/>'
    wake = '<path d="M-74 22h44M-20 26h70M40 21h30" stroke="#ffffff" stroke-width="2" stroke-linecap="round" opacity=".8"/>'
    return f'<g transform="translate({f(x)} {f(y)}) scale({f(s)})">{wake}{hull}{deck}{cabin}{lamp}{oar}</g>'


def lantern(x: float, y: float, s: float = 1, string: float = 18) -> str:
    glow = f'<circle cx="0" cy="0" r="22" fill="{WARM}" opacity=".22"/>'
    return (f'<g transform="translate({f(x)} {f(y)}) scale({f(s)})">{glow}<path d="M0 {f(-string - 12)}V-12" stroke="{NAVY}" stroke-width="1.4"/>'
            f'<rect x="-7" y="-14" width="14" height="3" rx="1" fill="{NAVY}"/><ellipse rx="10" ry="12" fill="{LANTERN}"/>'
            f'<path d="M0-12V12M-5-11Q-9 0-5 11M5-11Q9 0 5 11" stroke="#c96a60" stroke-width="1" fill="none"/>'
            f'<rect x="-7" y="11" width="14" height="3" rx="1" fill="{NAVY}"/><path d="M0 14v9" stroke="{WARM_DEEP}" stroke-width="2"/></g>')


def lotus_leaf(x: float, y: float, rx: float, ry: float, color: str = TEAL, vein: str = "#d5e8e8", rot: float = 0, light: str = "#c3dfdf") -> str:
    """荷叶（斜看成椭圆）：前缘留一个 V 形缺口，后半边提亮，叶脉从中心放射。"""
    notch = .2
    rx1, ry1 = rx * math.cos(math.pi / 2 - notch), ry * math.sin(math.pi / 2 - notch)
    rx2, ry2 = rx * math.cos(math.pi / 2 + notch), ry * math.sin(math.pi / 2 + notch)
    # 从缺口右侧出发，逆时针（经右、上、左）绕到缺口左侧
    d = f"M0 0L{f(rx1)} {f(ry1)}A{f(rx)} {f(ry)} 0 1 0 {f(rx2)} {f(ry2)}Z"
    back = f"M{f(-rx)} 0A{f(rx)} {f(ry)} 0 0 1 {f(rx)} 0Q0 {f(ry * .25)} {f(-rx)} 0Z"
    veins = "".join(f'<path d="M0 0L{f(rx * .9 * math.cos(a))} {f(ry * .9 * math.sin(a))}"/>'
                    for a in [math.pi / 2 + notch + k * (2 * math.pi - 2 * notch) / 10 for k in range(1, 10)])
    rim = f'<path d="M{f(rx1)} {f(ry1)}A{f(rx)} {f(ry)} 0 0 1 {f(rx * .7)} {f(ry * .72)}" stroke="{TEAL_DEEP}" stroke-width="2" fill="none" opacity=".5"/>'
    return (f'<g transform="translate({f(x)} {f(y)}) rotate({f(rot)})"><path d="{d}" fill="{color}"/><path d="{back}" fill="{light}" opacity=".55"/>'
            f'<g stroke="{vein}" stroke-width="1.1" opacity=".75">{veins}</g>{rim}<circle r="3" fill="{vein}"/></g>')


def lotus_flower(x: float, y: float, s: float = 1, stem: float = 70) -> str:
    """荷花：外层两片张开、内层三片合拢，粉色由浅到深，花心一点黄。"""
    outer = (f'<path d="M0 0Q-26-6-30-26Q-14-24 0 0Z" fill="#f6c3cd"/><path d="M0 0Q26-6 30-26Q14-24 0 0Z" fill="#f2b2bf"/>')
    inner = (f'<path d="M0 0Q-18-14-12-38Q-2-26 0 0Z" fill="#f9d6dd"/><path d="M0 0Q18-14 12-38Q2-26 0 0Z" fill="#ee9fb0"/>'
             f'<path d="M0 0Q-9-22 0-44Q9-22 0 0Z" fill="#f7c6d0"/>')
    return (f'<g transform="translate({f(x)} {f(y)}) scale({f(s)})"><path d="M0 0Q4 {f(stem * .5)} 0 {f(stem)}" stroke="{TEAL_DEEP}" stroke-width="2.4" fill="none"/>'
            f'{outer}{inner}<path d="M-6-38Q0-44 6-38" stroke="#e48499" stroke-width="1.2" fill="none"/></g>')


def lotus_bud(x: float, y: float, s: float = 1, stem: float = 60) -> str:
    return (f'<g transform="translate({f(x)} {f(y)}) scale({f(s)})"><path d="M0 0Q-3 {f(stem * .5)} 0 {f(stem)}" stroke="{TEAL_DEEP}" stroke-width="2.2" fill="none"/>'
            f'<path d="M0 2Q-12-10 0-30Q12-10 0 2Z" fill="#f4bcc7"/><path d="M0 2Q8-10 0-30Z" fill="#ec9cae"/></g>')


def white_house(x: float, y: float, width: float, height: float, roof_h: float = 26, gable: bool = False, lit: tuple[int, ...] = (0, 2),
                window_count: int = 3, door: bool = True) -> str:
    """粉墙黛瓦民居：墙面（左亮右暗分界在山墙转角）、黛瓦屋顶、窗；gable=True 画马头墙。"""
    out = [f'<rect x="{f(x)}" y="{f(y)}" width="{f(width)}" height="{f(height)}" fill="{WALL}"/>',
           f'<rect x="{f(x + width * .82)}" y="{f(y)}" width="{f(width * .18)}" height="{f(height)}" fill="{WALL_SHADE}"/>',
           f'<rect x="{f(x)}" y="{f(y + height - 10)}" width="{f(width)}" height="10" fill="{STONE}"/>']
    roof = f'<path d="M{f(x - 8)} {f(y + 2)}L{f(x + 10)} {f(y - roof_h)}H{f(x + width - 10)}L{f(x + width + 8)} {f(y + 2)}Z" fill="{TILE}"/>'
    roof += f'<path d="M{f(x - 8)} {f(y + 2)}L{f(x + 10)} {f(y - roof_h)}H{f(x + width * .5)}L{f(x + width * .5 - 6)} {f(y + 2)}Z" fill="{TILE_LIGHT}"/>'
    roof += "".join(f'<path d="M{f(x + i * width / 9)} {f(y - roof_h + 3)}V{f(y)}" stroke="{NAVY}" stroke-opacity=".35" stroke-width="1"/>' for i in range(1, 9))
    out.append(roof)
    if gable:
        out.append(horse_head_gable(x - 4, y, roof_h, 1) + horse_head_gable(x + width + 4, y, roof_h, -1))
    gap = (width - window_count * 18) / (window_count + 1)
    for index in range(window_count):
        wx = x + gap + index * (18 + gap)
        warm = index in lit
        out.append(f'<rect x="{f(wx)}" y="{f(y + height * .22)}" width="18" height="22" fill="{WARM if warm else NAVY}"/>'
                   f'<path d="M{f(wx)} {f(y + height * .22 + 11)}h18M{f(wx + 9)} {f(y + height * .22)}v22" stroke="{WARM_DEEP if warm else BLUE}" stroke-width="1.2"/>')
    if door:
        out.append(f'<rect x="{f(x + width * .5 - 9)}" y="{f(y + height - 34)}" width="18" height="24" fill="{NAVY}"/>')
    return "".join(out)


def wall_with_tiles(x0: float, x1: float, y: float, height: float, cap: float = 10) -> str:
    return (f'<rect x="{f(x0)}" y="{f(y)}" width="{f(x1 - x0)}" height="{f(height)}" fill="{WALL}"/>'
            f'<path d="M{f(x0 - 6)} {f(y + 2)}L{f(x0)} {f(y - cap)}H{f(x1)}L{f(x1 + 6)} {f(y + 2)}Z" fill="{TILE}"/>'
            f'<path d="M{f(x0 - 6)} {f(y + 2)}H{f(x1 + 6)}" stroke="{TILE_LIGHT}" stroke-width="2"/>')


def horse_head_gable(x: float, y: float, roof_h: float, inward: int) -> str:
    """马头墙：山墙随屋面分三级跌落，每级压一道黛瓦，瓦头微翘；inward=1 表示向右收进（左山墙）。"""
    steps = [(0, roof_h * .35), (18, roof_h + 4), (34, roof_h + 20)]
    out = []
    for index, (offset, lift) in enumerate(steps):
        sx = x + inward * offset
        top = y - lift
        wall_w = 22
        x0 = sx if inward > 0 else sx - wall_w
        out.append(f'<rect x="{f(x0)}" y="{f(top)}" width="{f(wall_w)}" height="{f(lift + 4)}" fill="{WALL}"/>')
        out.append(f'<rect x="{f(x0 + (wall_w - 6 if inward > 0 else 0))}" y="{f(top)}" width="6" height="{f(lift + 4)}" fill="{WALL_SHADE}"/>')
        out.append(f'<path d="M{f(x0 - 5)} {f(top + 2)}L{f(x0 - 8)} {f(top - 5)}H{f(x0 + wall_w + 8)}L{f(x0 + wall_w + 5)} {f(top + 2)}Z" fill="{TILE}"/>')
    return "".join(out)


def taihu_rock(x: float, y: float, s: float = 1, seed: int = 1) -> str:
    """太湖石（瘦、漏、透）：几块不规则石体叠起，左亮右暗，深色孔洞带亮边。"""
    r = random.Random(seed)
    lumps = []
    cy = 0
    for index in range(4):
        w = r.uniform(34, 52) * (1 - index * .12)
        h = r.uniform(30, 42)
        cx = r.uniform(-10, 10)
        top = cy - h
        d = (f"M{f(cx - w / 2)} {f(cy)}C{f(cx - w * .7)} {f(cy - h * .5)} {f(cx - w * .3)} {f(top - 6)} {f(cx)} {f(top)}"
             f"C{f(cx + w * .45)} {f(top + 4)} {f(cx + w * .7)} {f(cy - h * .45)} {f(cx + w / 2)} {f(cy)}Z")
        lumps.append((d, cx, cy, w, h))
        cy = top + h * .35
    out = [f'<g transform="translate({f(x)} {f(y)}) scale({f(s)})">']
    for d, cx, cy2, w, h in lumps:
        out.append(f'<path d="{d}" fill="#d9dde9"/>')
        out.append(f'<path d="{d}" fill="{STONE_SHADE}" opacity=".55" transform="translate({f(w * .18)} 0) scale(.8 1)" />')
    for d, cx, cy2, w, h in lumps:
        if r.random() < .8:
            hx, hy = cx + r.uniform(-w * .2, w * .15), cy2 - h * r.uniform(.35, .6)
            rx, ry = r.uniform(4, 8), r.uniform(5, 9)
            out.append(f'<ellipse cx="{f(hx)}" cy="{f(hy)}" rx="{f(rx + 1.5)}" ry="{f(ry + 1.5)}" fill="#eef0f6"/>'
                       f'<ellipse cx="{f(hx + .8)}" cy="{f(hy + .8)}" rx="{f(rx)}" ry="{f(ry)}" fill="#6f7ba6"/>')
    out.append("</g>")
    return "".join(out)


# ---------------- 场景 ----------------

def hangzhou_west() -> str:
    """宝石山与断桥：保俶塔立在宝石山脊，白堤与断桥横过湖面，近处荷叶荷花。主体偏左（右侧会被渐隐）。"""
    art = Art("hzw", 11)
    sky(art, "#f0f3fc", "#dfe6f7")
    art.add(cloud(150, 170, 1.0), cloud(560, 230, .65), cloud(330, 330, .45))
    art.add(birds(470, 300, 3, "#6f82bd"))
    art.add(ridge(1, [(-20, 560), (120, 470), (260, 520), (420, 450), (600, 540), (820, 500)], 720, "#d6def3", "#c8d2ee"))
    art.add(ridge(2, [(-20, 600), (160, 540), (330, 590), (520, 560), (700, 610), (820, 590)], 720, "#c4d0ee", "#b3c2e8"))
    art.add('<g id="hzw-mid">'
            + ridge(3, [(-30, 640), (90, 470), (210, 400), (330, 470), (470, 600), (560, 700)], 705, "#a9bbe6", "#93a8dc")
            + slender_pagoda(212, 408, 150, 26)
            + canopy(120, 600, 300, 120, "#8ea4d9", "#adc0ea", 21)
            + canopy(330, 660, 260, 70, "#879dd4", "#a9bbe6", 22)
            + arch_bridge(400, 705, 210, 30)
            + '<path d="M500 705Q620 694 820 698V708H496Z" fill="#cfd9f1"/>'
            + canopy(660, 700, 330, 46, "#8fa6da", "#b4c4ec", 23)
            + canopy(60, 705, 160, 40, "#8098d2", "#a6b8e6", 24)
            + "</g>")
    water(art, 705, ("#dce4f6", "#eef2fb"))
    art.add(reflection(art, "mid", 705, 705, 905, .32))
    art.add(mist_band(art, 688, 34, "mist1", opacity=.55))
    art.add(ripples(31, 720, 960, count=30, opacity=.75))
    art.add(boat(520, 808, 1.0), boat(250, 862, .7, roof_color=BLUE))
    art.add(lotus_leaf(70, 925, 74, 24, rot=-6), lotus_leaf(190, 965, 60, 20, color="#8dbabd", rot=8),
            lotus_leaf(10, 985, 86, 28, color="#7fb0b6", rot=2), lotus_leaf(300, 990, 70, 22, color="#9cc4c8", rot=-4),
            lotus_flower(118, 915, 1.0, 60), lotus_bud(230, 940, .9, 50), lotus_flower(40, 950, .75, 50))
    return art.svg()


def hangzhou_south() -> str:
    """雷峰夕照：雷峰塔立在夕照山，夕阳在塔后，三潭印月石塔立于水中，画舫与荷。主体偏右。"""
    art = Art("hzs", 12)
    sky(art, "#f3f1fa", "#f4e3dd")
    sun(art, 560, 360, 44, "#f9d6ac")
    art.add(cloud(300, 190, .9, "#fdfafc", "#efe6ee"), cloud(650, 170, .55, "#fdfafc", "#efe6ee"), cloud(150, 300, .5, "#fdfafc", "#efe6ee"))
    art.add(birds(330, 280, 4, "#7d86b8"))
    art.add(ridge(4, [(-20, 580), (180, 500), (360, 560), (560, 480), (820, 560)], 720, "#ded9ee", "#d0cde9"))
    art.add(ridge(5, [(-20, 620), (200, 590), (400, 620), (600, 570), (820, 610)], 720, "#cbcbea", "#bcbee5"))
    art.add('<g id="hzs-mid">'
            + ridge(6, [(300, 700), (420, 610), (560, 580), (700, 620), (830, 600)], 705, "#a9b6e2", "#97a6da")
            + canopy(560, 670, 380, 90, "#8b9fd6", "#aebde9", 31)
            + pagoda(585, 640, 5, 132, 62, .1, spire=46)
            + canopy(700, 700, 260, 50, "#8299d3", "#a9b9e6", 32)
            + canopy(420, 705, 220, 40, "#8ea2d8", "#b0bfe8", 33)
            + "</g>")
    water(art, 705, ("#e9e2f0", "#f3f0f8"))
    art.add(reflection(art, "mid", 705, 705, 925, .32))
    art.add('<g stroke="#f6cf9c" stroke-linecap="round" opacity=".8">' + "".join(
        f'<path d="M{f(560 - w / 2)} {y}h{f(w)}" stroke-width="3"/>' for y, w in ((722, 140), (740, 100), (758, 160), (778, 80), (798, 120), (820, 60))) + "</g>")
    art.add(mist_band(art, 690, 34, "mist1", "#fbf3f1", .6))
    art.add(ripples(41, 720, 960, count=28))
    for x, y, s in ((300, 850, 1.0), (430, 828, .8), (200, 892, .65)):
        art.add(f'<g transform="translate({x} {y}) scale({s})">'
                f'<rect x="-5" y="-30" width="10" height="30" fill="{STONE}"/><rect y="-30" width="5" height="30" fill="{STONE_SHADE}"/>'
                f'<circle cy="-48" r="20" fill="#eef1f9"/><path d="M0-68A20 20 0 0 1 0-28Z" fill="{STONE_SHADE}" opacity=".55"/>'
                + "".join(f'<circle cx="{dx}" cy="-48" r="3.6" fill="{WARM}"/>' for dx in (-10, 0, 10))
                + f'<path d="M-17-67h34l-6-10h-22Z" fill="{NAVY}"/><path d="M0-77v-18" stroke="{NAVY}" stroke-width="3"/><circle cy="-98" r="4" fill="{NAVY}"/>'
                f'<path d="M-14 4h28M-8 10h16" stroke="#fff" stroke-width="2" stroke-linecap="round"/></g>')
    art.add(boat(470, 790, 1.2, lantern=True))
    art.add(lotus_leaf(740, 935, 80, 26, rot=6), lotus_leaf(620, 975, 64, 21, color="#8dbabd", rot=-8),
            lotus_leaf(800, 985, 80, 26, color="#7fb0b6"), lotus_flower(690, 905, 1.0, 64), lotus_bud(770, 930, .9, 50))
    return art.svg()


def plane_leaf(x: float, y: float, s: float, rot: float, color: str, vein: str = "#fff6e2") -> str:
    """梧桐（悬铃木）掌状叶：五裂，叶柄，叶脉。"""
    d = ("M0 0L-6-8L-24-10L-14-18L-28-34L-10-30L-8-48L2-34L12-50L14-30L32-36L20-20L30-10L8-8Z")
    return (f'<g transform="translate({f(x)} {f(y)}) rotate({f(rot)}) scale({f(s)})"><path d="{d}" fill="{color}"/>'
            f'<path d="M0 0V-40M0-14L-20-28M0-14L22-30M0-6L-20-12M0-6L22-12M0 0L-2 12" stroke="{vein}" stroke-width="1.3" fill="none" opacity=".8"/></g>')


def city_wall(x0: float, x1: float, top: float, bottom: float, light: str = "#dfe5f5", face: str = "#c8d2ee", seam: str = "#aebde5") -> str:
    """明城墙：垛口、砖缝、墙顶压一道亮边。"""
    out = [f'<rect x="{f(x0)}" y="{f(top)}" width="{f(x1 - x0)}" height="{f(bottom - top)}" fill="{face}"/>',
           f'<rect x="{f(x0)}" y="{f(top)}" width="{f(x1 - x0)}" height="8" fill="{light}"/>']
    out.append("".join(f'<rect x="{f(x)}" y="{f(top - 14)}" width="15" height="14" fill="{light}"/><rect x="{f(x + 10)}" y="{f(top - 14)}" width="5" height="14" fill="{face}"/>'
                       for x in range(int(x0) + 4, int(x1) - 10, 26)))
    rows = int((bottom - top - 10) / 14)
    out.append(f'<g stroke="{seam}" stroke-width="1" opacity=".8">' + "".join(
        f'<path d="M{f(x0)} {f(top + 10 + k * 14)}H{f(x1)}"/>' for k in range(1, rows)) + "".join(
        f'<path d="M{f(x + (k % 2) * 20)} {f(top + 10 + k * 14)}v14"/>' for k in range(rows - 1) for x in range(int(x0), int(x1), 40)) + "</g>")
    return "".join(out)


def gate_tower(x: float, base: float, width: float) -> str:
    """城门：厚重城台开三道券门，台上两层城楼，左亮右暗，门窗暖光。"""
    h = width * .42
    out = [f'<path d="M{f(x - width / 2)} {f(base)}L{f(x - width / 2 + 8)} {f(base - h)}H{f(x)}V{f(base)}Z" fill="#e6ebf7"/>',
           f'<path d="M{f(x)} {f(base)}V{f(base - h)}H{f(x + width / 2 - 8)}L{f(x + width / 2)} {f(base)}Z" fill="#c9d3ee"/>',
           f'<rect x="{f(x - width / 2 + 6)}" y="{f(base - h - 6)}" width="{f(width - 12)}" height="8" fill="#d6def2"/>']
    out.append("".join(f'<rect x="{f(gx)}" y="{f(base - h - 18)}" width="14" height="12" fill="#d6def2"/>'
                       for gx in [x - width / 2 + 8 + k * 24 for k in range(int((width - 16) / 24) + 1)]))
    for gx, gw in ((x, width * .17), (x - width * .3, width * .11), (x + width * .3, width * .11)):
        out.append(f'<path d="M{f(gx - gw / 2)} {f(base)}V{f(base - gw * 1.1)}a{f(gw / 2)} {f(gw / 2)} 0 0 1 {f(gw)} 0V{f(base)}Z" fill="{NAVY}"/>'
                   f'<path d="M{f(gx - gw / 2 + 5)} {f(base)}V{f(base - gw * 1.05)}a{f(gw / 2 - 5)} {f(gw / 2 - 5)} 0 0 1 {f(gw - 10)} 0V{f(base)}Z" fill="#5a6fb0"/>')
    # 两层城楼
    hall_base = base - h - 18
    for level, (hw, hh) in enumerate(((width * .62, 46), (width * .44, 40))):
        top = hall_base - hh
        out.append(f'<rect x="{f(x - hw / 2)}" y="{f(top + 10)}" width="{f(hw / 2)}" height="{f(hh - 10)}" fill="#f3f5fb"/>'
                   f'<rect x="{f(x)}" y="{f(top + 10)}" width="{f(hw / 2)}" height="{f(hh - 10)}" fill="#d3dbf0"/>')
        out.append(f'<g fill="{NAVY}">' + "".join(f'<rect x="{f(x - hw / 2 + 4 + k * (hw - 12) / 6)}" y="{f(top + 10)}" width="4" height="{f(hh - 10)}"/>' for k in range(7)) + "</g>")
        out.append(windows_row(x - hw * .36, top + 18, 4, hw * .14, hh * .38, hw * .07, lit={0, 1, 3}))
        out.append(eave_roof(x, top - 16 + level * 2, hw + 36, 28, lift=12))
        hall_base = top - 10
    return "".join(out)


def nanjing_wall() -> str:
    """明城与梧桐：紫金山为远景，城墙与城门（两层城楼）为主体，护城河倒影，前后成片秋色梧桐。主体偏左。"""
    art = Art("njw", 21)
    sky(art, "#f0f3fc", "#e0e7f7")
    art.add(cloud(180, 170, .95), cloud(600, 300, .6), cloud(400, 120, .45))
    art.add(birds(430, 260, 3, "#6f82bd"))
    # 紫金山三峰
    art.add(ridge(11, [(-20, 560), (110, 430), (230, 480), (360, 380), (500, 470), (640, 430), (820, 520)], 720, "#ccd6f1", "#bac8ec"))
    art.add(ridge(12, [(-20, 600), (150, 560), (320, 590), (520, 550), (820, 600)], 720, "#b9c7ec", "#a9bae6"))
    art.add('<g id="njw-mid">'
            + canopy(560, 640, 420, 70, "#c9b993", "#e2d5b0", 41)
            + city_wall(-20, 820, 600, 712)
            + gate_tower(250, 712, 300)
            + canopy(560, 712, 260, 50, "#cdb78b", "#e6d6ac", 42)
            + canopy(40, 712, 140, 46, "#c4ae83", "#e0cfa5", 43)
            + "</g>")
    water(art, 712, ("#dbe3f5", "#eef2fb"))
    art.add(reflection(art, "mid", 712, 712, 905, .3))
    art.add(mist_band(art, 696, 34, "mist1", opacity=.5))
    art.add(ripples(51, 728, 960, count=28))
    art.add(boat(470, 820, .95, roof_color=BLUE))
    # 前景：左下角梧桐落叶与一角树冠（秋色）
    art.add(canopy(-10, 1010, 340, 120, "#c7b084", "#e2d0a2", 44, dots=False))
    leaves = [(40, 900, 1.1, -20, "#e2b866"), (130, 930, .9, 18, "#d9a35a"), (230, 960, 1.0, -8, "#e8c47c"),
              (80, 960, .8, 40, "#cf9a52"), (300, 985, .75, -30, "#e2b866")]
    art.add(*(plane_leaf(*leaf) for leaf in leaves))
    return art.svg()


def nanjing_qinhuai() -> str:
    """秦淮灯影：夫子庙河畔粉墙黛瓦与马头墙、奎星阁，文德桥、画舫，檐下成串灯笼，薄暮暖光。主体偏右。"""
    art = Art("njq", 22)
    sky(art, "#e7e9f7", "#f3e5e0")
    sun(art, 300, 300, 30, "#f8dcbc")
    art.add(cloud(520, 170, .8, "#fbf8fb", "#ece6f0"), cloud(200, 220, .55, "#fbf8fb", "#ece6f0"))
    art.add(ridge(13, [(-20, 580), (200, 520), (420, 560), (620, 510), (820, 560)], 720, "#dcdcf0", "#d0d1ec"))
    art.add('<g id="njq-mid">'
            + '<path d="M-20 700H820V712H-20Z" fill="#b9c3df"/>'
            + white_house(-20, 600, 130, 100, lit=(1,), window_count=2)
            + white_house(110, 615, 120, 85, lit=(0,), window_count=2)
            + pagoda(560, 700, 3, 150, 66, .18, body="#f4f5fb", shade="#d5dbf0", spire=30)
            + white_house(390, 590, 110, 110, gable=True, lit=(0, 2))
            + white_house(650, 580, 180, 120, roof_h=30, gable=True, lit=(0, 1, 2))
            + arch_bridge(330, 712, 300, 26, color="#e6eaf6")
            + "</g>")
    water(art, 712, ("#d8dcf1", "#eceaf6"))
    art.add(reflection(art, "mid", 712, 712, 925, .34))
    art.add('<g fill="#f3c98f" opacity=".6">' + "".join(f'<ellipse cx="{x}" cy="{y}" rx="{rx}" ry="3"/>' for x, y, rx in
                                                        ((450, 760, 16), (560, 790, 22), (700, 768, 18), (760, 800, 12), (620, 830, 14))) + "</g>")
    art.add(ripples(61, 728, 960, count=30))
    # 檐下灯笼串（右侧两排）
    art.add('<path d="M380 560Q600 640 830 560" stroke="#6b78ad" stroke-width="1.4" fill="none"/>')
    for t in (.1, .26, .42, .58, .74, .9):
        x = 380 + t * 450
        y = (1 - t) ** 2 * 560 + 2 * (1 - t) * t * 640 + t * t * 560
        art.add(lantern(x, y + 22, .75, 8))
    art.add(boat(520, 845, 1.4, lantern=True), boat(250, 900, .85, lantern=True))
    return art.svg()


def moon_gate_wall(x0: float, x1: float, top: float, bottom: float, gate_x: float, gate_r: float, windows: list[float]) -> str:
    """园林粉墙：黛瓦压顶，月洞门（门内见远景），几扇漏窗（冰裂纹、海棠纹）。"""
    out = [wall_with_tiles(x0, x1, top, bottom - top)]
    out.append(f'<path d="M{f(gate_x - gate_r)} {f(bottom)}V{f(bottom - gate_r * .9)}A{f(gate_r)} {f(gate_r)} 0 1 1 {f(gate_x + gate_r)} {f(bottom - gate_r * .9)}V{f(bottom)}Z" fill="#dbe4f4"/>')
    out.append(f'<path d="M{f(gate_x - gate_r)} {f(bottom)}V{f(bottom - gate_r * .9)}A{f(gate_r)} {f(gate_r)} 0 1 1 {f(gate_x + gate_r)} {f(bottom - gate_r * .9)}V{f(bottom)}" '
               f'fill="none" stroke="{STONE_SHADE}" stroke-width="7"/>')
    for index, wx in enumerate(windows):
        size = 58
        pattern = ("M-29 -6L-8-29M-29 14L14-29M-20 29L29-20M0 29L29 0M-29-29L29 29" if index % 2 == 0
                   else "M0-29V29M-29 0H29M-20-20A28 28 0 0 0 20 20M20-20A28 28 0 0 0 -20 20")
        out.append(f'<g transform="translate({f(wx)} {f(top + (bottom - top) * .42)})"><rect x="-29" y="-29" width="{size}" height="{size}" fill="#4d5f9e"/>'
                   f'<path d="{pattern}" stroke="{WALL}" stroke-width="3.2" fill="none"/>'
                   f'<rect x="-29" y="-29" width="{size}" height="{size}" fill="none" stroke="{STONE_SHADE}" stroke-width="6"/></g>')
    return "".join(out)


def ting(x: float, base: float, width: float, height: float) -> str:
    """园林亭：攒尖顶飞檐，四柱，美人靠，檐下暖光。"""
    roof_top = base - height
    out = [f'<path d="M{f(x - width * .55)} {f(base - height * .45)}Q{f(x - width * .2)} {f(base - height * .5)} {f(x)} {f(roof_top)}'
           f'Q{f(x + width * .2)} {f(base - height * .5)} {f(x + width * .55)} {f(base - height * .45)}Q{f(x)} {f(base - height * .38)} {f(x - width * .55)} {f(base - height * .45)}Z" fill="{TILE}"/>',
           f'<path d="M{f(x - width * .55)} {f(base - height * .45)}Q{f(x - width * .2)} {f(base - height * .5)} {f(x)} {f(roof_top)}V{f(base - height * .4)}Q{f(x - width * .3)} {f(base - height * .41)} {f(x - width * .55)} {f(base - height * .45)}Z" fill="{TILE_LIGHT}"/>',
           f'<path d="M{f(x)} {f(roof_top)}v-14" stroke="{NAVY}" stroke-width="3"/><circle cx="{f(x)}" cy="{f(roof_top - 16)}" r="4" fill="{NAVY}"/>',
           f'<rect x="{f(x - width * .36)}" y="{f(base - height * .4)}" width="{f(width * .72)}" height="{f(height * .22)}" fill="{WARM}" opacity=".5"/>']
    for px in (x - width * .38, x - width * .13, x + width * .13, x + width * .38):
        out.append(f'<rect x="{f(px - 3)}" y="{f(base - height * .42)}" width="6" height="{f(height * .42)}" fill="{NAVY}"/>')
    out.append(f'<path d="M{f(x - width * .42)} {f(base - height * .14)}H{f(x + width * .42)}" stroke="{BLUE2}" stroke-width="4"/>'
               f'<rect x="{f(x - width * .48)}" y="{f(base - 6)}" width="{f(width * .96)}" height="8" fill="{STONE_SHADE}"/>')
    return "".join(out)


def suzhou_garden() -> str:
    """拙政园意象：粉墙月洞门与漏窗，墙后亭与树冠，湖石、荷塘与曲桥。主体偏左。"""
    art = Art("szg", 31)
    sky(art, "#f1f4fc", "#e2e9f6")
    art.add(cloud(170, 170, .9), cloud(560, 300, .55))
    art.add(birds(360, 250, 3, "#6f82bd"))
    art.add(ridge(21, [(-20, 560), (160, 500), (340, 540), (520, 490), (820, 540)], 720, "#d6def3", "#c8d2ee"))
    art.add(canopy(300, 560, 760, 120, "#a8bbe6", "#c4d1f0", 51))
    art.add(ting(140, 548, 170, 120))
    art.add(canopy(420, 545, 260, 70, "#98ade0", "#b8c7ec", 52))
    art.add('<g id="szg-mid">'
            + moon_gate_wall(-20, 600, 540, 712, 300, 68, [100, 470])
            + canopy(300, 712, 110, 34, "#9db1e2", "#bccaee", 53)
            + taihu_rock(210, 712, .55, 4) + taihu_rock(560, 712, .5, 9)
            + "</g>")
    water(art, 712, ("#d6e1ef", "#eaf0f8"))
    art.add(reflection(art, "mid", 712, 712, 900, .3))
    art.add(ripples(71, 728, 960, count=22))
    # 曲桥（折线石板）
    art.add(f'<path d="M260 790L360 774L420 794L530 776L600 790" stroke="#e8ecf7" stroke-width="16" fill="none" stroke-linejoin="round"/>'
            f'<path d="M260 798L360 782L420 802L530 784L600 798" stroke="{STONE_SHADE}" stroke-width="4" fill="none" stroke-linejoin="round"/>'
            f'<path d="M260 781L360 765L420 785L530 767L600 781" stroke="{BLUE2}" stroke-width="2" fill="none"/>'
            + "".join(f'<path d="M{x} {y}v14" stroke="{STONE_SHADE}" stroke-width="3"/>' for x, y in ((280, 788), (360, 776), (420, 796), (480, 785), (560, 783))))
    art.add(taihu_rock(80, 840, 1.05, 3), taihu_rock(20, 860, .7, 5))
    art.add(lotus_leaf(220, 900, 72, 24, rot=-4), lotus_leaf(340, 940, 84, 28, color="#8dbabd", rot=6), lotus_leaf(470, 905, 62, 21, rot=-8),
            lotus_leaf(150, 975, 80, 27, color="#7fb0b6", rot=4), lotus_leaf(560, 970, 70, 24, color="#9cc4c8"),
            lotus_flower(285, 880, 1.0, 60), lotus_bud(410, 900, .85, 50), lotus_flower(510, 920, .8, 50))
    return art.svg()


def suzhou_canal() -> str:
    """平江水巷：两岸粉墙黛瓦枕河人家与马头墙，石拱桥，乌篷船，远处略斜的虎丘塔。主体偏右。"""
    art = Art("szc", 32)
    sky(art, "#f1f3fb", "#e3e8f6")
    sun(art, 260, 240, 26)
    art.add(cloud(480, 180, .8), cloud(700, 280, .5), cloud(140, 300, .45))
    art.add(birds(380, 260, 3, "#6f82bd"))
    art.add(ridge(31, [(-20, 600), (260, 540), (480, 560), (640, 500), (820, 560)], 720, "#d4dcf2", "#c6d0ee"))
    art.add(f'<g transform="rotate(-3 640 520)">{pagoda(640, 520, 7, 56, 30, .07, body="#e6e8f2", shade="#c9cfe3", lit=False, spire=16)}</g>')
    art.add(canopy(640, 540, 300, 60, "#a6b8e5", "#c2cff0", 61))
    art.add('<g id="szc-mid">'
            + white_house(-30, 610, 130, 90, lit=(1,), window_count=2)
            + white_house(90, 625, 110, 75, lit=(0,), window_count=2)
            + arch_bridge(360, 700, 250, 40, color="#eceff8")
            + white_house(470, 580, 140, 120, roof_h=30, gable=True, lit=(0, 2))
            + white_house(600, 560, 230, 140, roof_h=34, gable=True, lit=(0, 1, 3), window_count=4)
            + "".join(f'<rect x="{f(520 - i * 10)}" y="{f(700 + i * 6)}" width="{f(46 + i * 10)}" height="6" fill="{STONE if i % 2 else STONE_SHADE}"/>' for i in range(4))
            + "</g>")
    water(art, 700, ("#d7e0f2", "#ebf0f9"))
    art.add(reflection(art, "mid", 700, 700, 925, .34))
    art.add(ripples(81, 716, 960, count=30))
    art.add(lantern(500, 612, .8, 6), lantern(640, 596, .8, 6), lantern(770, 596, .8, 6))
    art.add(boat(560, 830, 1.35), boat(300, 900, .8, roof_color=BLUE))
    return art.svg()


SCENES = {"hangzhou-west": hangzhou_west, "hangzhou-south": hangzhou_south, "nanjing-wall": nanjing_wall,
          "nanjing-qinhuai": nanjing_qinhuai, "suzhou-garden": suzhou_garden, "suzhou-canal": suzhou_canal}


if __name__ == "__main__":
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "src/assets/art")
    out.mkdir(parents=True, exist_ok=True)
    only = set(sys.argv[2:])
    for name, build in SCENES.items():
        if only and name not in only:
            continue
        (out / f"{name}.svg").write_text(build(), encoding="utf-8")
        print(name, len((out / f"{name}.svg").read_bytes()) // 1024, "KB")
