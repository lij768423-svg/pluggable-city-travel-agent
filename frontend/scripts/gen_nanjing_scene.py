"""生成南京两幅原创主题插画：明城与梧桐、秦淮灯影（意象插画，不是实景测绘）。

沿用 gen_scene 的调色、云、水纹、画舫与低动态效果，不覆盖杭州资产。
在 frontend 执行：python3 scripts/gen_nanjing_scene.py src/assets/backdrop
"""
from __future__ import annotations

import sys
from pathlib import Path
from gen_scene import COMMON_DEFS, anim, bird, cloud, f, huafang, prefix_ids, ripple_mask, rng, svg


def lantern(x: float, y: float, scale: float = 1) -> str:
    return anim('breathe', f'<g transform="translate({x} {y}) scale({scale})"><circle r="43" fill="url(#glowWarm)" opacity=".5"/><path d="M0-42V-20" stroke="#3e5189" stroke-width="1.5"/><ellipse rx="14" ry="20" fill="#eea49a"/><path d="M0-20V20M-7-18Q-13 0-7 18M7-18Q13 0 7 18" stroke="#d9817c" stroke-width="1"/><path d="M-11-21H11M-11 21H11" stroke="#526bb0" stroke-width="4"/><path d="M0 23V39M-3 35V44M3 35V44" stroke="#efc77f" stroke-width="2"/></g>', x % 5)


def roof(x: float, y: float, width: float, height: float) -> str:
    a, b = x-width/2, x+width/2
    p=f'M{a-15} {y+height-13}Q{a+5} {y+height+2} {x-14} {y}H{x+14}Q{b-5} {y+height+2} {b+15} {y+height-13}Q{b+8} {y+height+10} {x} {y+height+5}Q{a-8} {y+height+10} {a-15} {y+height-13}Z'
    tiles=''.join(f'<path d="M{x+(i-5)*width/18} {y+4}Q{x+(i-5)*width/12} {y+height*.6} {x+(i-5)*width/10} {y+height}" stroke="#95abe2" stroke-width="1.2" opacity=".4"/>' for i in range(11))
    return f'<path d="{p}" fill="url(#roof)" stroke="#3e5189" stroke-width="2"/>{tiles}<path d="M{a-10} {y+height}Q{x} {y+height+14} {b+10} {y+height}" stroke="#8fa9e4" stroke-width="3"/>'


def water(seed: int) -> str:
    r=rng(seed)
    return ''.join(anim('shimmer',f'<path d="M{f(next(r)*800)} {y}h{f(20+next(r)*75)}" stroke="#fff" stroke-width="2.2" stroke-linecap="round" opacity=".65"/>',y%7) for y in range(708,1000,14))


def wall_scene() -> tuple[list[str], list[str]]:
    defs=[COMMON_DEFS, ripple_mask('lake-reflect',710,950,91)]
    o=[cloud(80,190,1.1),cloud(330,285,.7),
       '<path d="M0 550Q65 465 135 504Q214 362 302 482Q377 418 461 533L800 610V740H0Z" fill="#dce3f5"/>',
       '<path d="M0 580Q90 488 170 543Q258 463 330 553Q414 508 530 598L800 645V760H0Z" fill="#c4d2ef"/>',
       '<path d="M0 716Q280 670 800 733V1000H0Z" fill="url(#water)"/>']
    # 明城墙意象：砖石、垛口与城门；不是某座城门的测绘复原。
    wall='<path d="M-20 614Q120 587 280 623L535 690V742Q260 669-20 704Z" fill="#9fb4df"/>'
    for row in range(5):
        for col in range(16):
            x=-20+col*31+(row%2)*15
            y=633+row*12+max(0,x-100)*.16
            wall+=f'<path d="M{x} {f(y)}h27m-27 0v9" stroke="#819acb" stroke-opacity=".5" stroke-width="1"/>'
    for x in range(-10,440,27):
        y=604+max(0,x-100)*.19
        wall+=f'<path d="M{x} {f(y+16)}V{f(y)}h15v12h12" fill="#b4c7e9" stroke="#879fce" stroke-width="2"/>'
    o.append(wall)
    o += ['<path d="M65 598H231L244 705H52Z" fill="url(#stone)" stroke="#8ca5d7" stroke-width="2"/>',
          '<path d="M115 703V650a32 32 0 0 1 64 0v53" fill="#728bbd"/>',
          '<path d="M123 703V651a24 24 0 0 1 48 0v52" fill="#a6bee5"/>',
          '<path d="M82 594V519h130v75" fill="#d3dff3" stroke="#6b87c3" stroke-width="3"/>']
    for x in (96,124,153,182):
        o += [f'<rect x="{x}" y="539" width="16" height="32" rx="1" fill="#526ba8"/>',f'<path d="M{x+8} 539v32m-8-17h16" stroke="#f3d7a7" stroke-width="2"/>']
    o += [roof(147,479,174,43),'<path d="M101 480V447h92v33" fill="#c5d4ef" stroke="#7e99ce" stroke-width="2"/>',roof(147,408,134,42),'<path d="M62 582h171M84 575v17M107 575v17M130 575v17M153 575v17M176 575v17M199 575v17M222 575v17" stroke="#7691c8" stroke-width="3"/>']
    o += [lantern(86,565,.45),lantern(210,565,.45)]
    # 前景梧桐，叶片以抽象掌状叶绘制。
    o.append('<path d="M-12 0Q10 58 105 88Q61 75 68 0M50 61Q106 48 166 18M66 77Q107 104 132 159" stroke="#6c82b3" stroke-width="8" stroke-linecap="round"/>')
    leaf='M0 0L-17-13L-11-23L-28-29L-15-36L-17-49L-5-43L0-59L9-42L22-49L22-34L38-30L23-21L26-10L9-8Z'
    for i,(x,y,sc,rot) in enumerate([(17,34,1.1,-20),(56,51,.9,30),(103,72,1.15,7),(142,44,.85,28),(110,130,.8,-36),(182,28,.75,32),(26,96,.8,-20)]):
        o.append(anim('sway',f'<g transform="translate({x} {y}) rotate({rot}) scale({sc})"><path d="{leaf}" fill="{["#c4cbb2","#d8caab","#aec6c8"][i%3]}"/><path d="M0 4V-45M0-23L-16-32M0-17L19-28" stroke="#fff" stroke-opacity=".55" stroke-width="1.2"/></g>',i*.4))
    o.append(water(84))
    o.append('<path d="M0 915Q76 870 200 934L322 1000H0Z" fill="#b0c9d5" opacity=".65"/>')
    for x in range(-20,255,25):
        o.append(f'<path d="M{x} 1008q-8-80-28-111m25 80q17-31 35-44" stroke="#8aafb9" stroke-width="2"/>')
    o += [bird(260,320,.8),bird(298,303,.6),anim('mistmove','<ellipse cx="205" cy="734" rx="230" ry="25" fill="url(#mist)"/>')]
    return defs,o


def qinhuai_scene() -> tuple[list[str], list[str]]:
    defs=[COMMON_DEFS,ripple_mask('river-reflect',705,985,37)]
    o=['<circle cx="672" cy="347" r="168" fill="url(#glowWarm)" opacity=".5"/><circle cx="672" cy="347" r="62" fill="#ffe5bc" opacity=".55"/>',cloud(675,262,.8),'<path d="M0 603Q240 457 450 513Q630 428 800 538V754H0Z" fill="#dedff1"/>','<path d="M0 728Q400 666 800 706V1000H0Z" fill="url(#water)"/>']
    houses=''
    for x,y,w in [(524,548,110),(663,485,154),(800,563,104)]:
        houses+=f'<path d="M{x-w/2} {y+20}h{w}v148h-{w}Z" fill="#eef0f8" stroke="#a7b7d9" stroke-width="2"/>'
        houses+=roof(x,y-25,w+18,42)
        for dx in [-w*.32,-w*.1,w*.12,w*.34]:
            houses+=f'<path d="M{x+dx} {y+41}v111" stroke="#8e9fc4" stroke-width="4"/>'
        for floor in [y+49,y+99]:
            for dx in [-w*.3,-w*.03,w*.24]:
                houses+=f'<rect x="{x+dx}" y="{floor}" width="{w*.2}" height="28" rx="2" fill="#f6d7a5"/><path d="M{x+dx+w*.1} {floor}v28m-{w*.1}-14h{w*.2}" stroke="#b4b0be" stroke-width="1.5"/>'
        houses+=f'<path d="M{x-w/2} {y+92}h{w}m-{w} 47h{w}" stroke="#9aabcf" stroke-width="3"/>'
        houses+=lantern(x-w*.38,y+111,.48)+lantern(x+w*.38,y+111,.48)
    o += [f'<g mask="url(#river-reflect)" opacity=".25"><g transform="translate(0 1428) scale(1 -1)">{houses}</g></g>',houses]
    o.append('<path d="M472 708Q638 694 820 723" stroke="#a3b7de" stroke-width="14"/><path d="M474 701Q638 687 820 716" stroke="#dbe5f7" stroke-width="4"/>')
    o += [water(73),huafang(637,858,.82),lantern(590,835,.32),lantern(680,835,.32)]
    # 右上角秦淮檐口与灯笼，延续杭州插画的构图与配色。
    o.append('<path d="M800 0V128Q664 128 600 89Q579 75 577 59Q596 80 614 0Z" fill="url(#roof)"/><path d="M800 128Q664 128 600 89Q579 75 577 59" stroke="#3e5189" stroke-width="8" stroke-linecap="round"/>')
    for x in range(625,805,13):
        o.append(f'<path d="M{x} 0Q{x-5} 77 {x-10} {f(112+(x-625)*.07)}" stroke="#91a8df" stroke-width="2" opacity=".4"/>')
    o += [lantern(658,197,.95),lantern(747,235,.75),anim('mistmove','<ellipse cx="610" cy="995" rx="290" ry="35" fill="url(#mist)" opacity=".6"/>')]
    return defs,o


def main() -> None:
    out=Path(sys.argv[1]);out.mkdir(parents=True,exist_ok=True)
    for name,scene,prefix in [('nanjing-wall',wall_scene,'njw-'),('nanjing-qinhuai',qinhuai_scene,'njq-')]:
        (out/f'{name}.svg').write_text(prefix_ids(svg(*scene()),prefix),encoding='utf-8')
    print('南京两幅主题插画已生成')


if __name__ == '__main__':
    main()
