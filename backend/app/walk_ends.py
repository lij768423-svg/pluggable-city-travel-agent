"""从一头进、另一头出的长堤、长街和索道（2026-10-28 用户：苏堤这类长堤分起点和终点，走完后从终点出发，白堤、杨公堤同理；
2026-10-29 推广到其他城市的长街和重庆长江索道）。

景点表里苏堤、杨公堤只有一个坐标（苏堤在南端），走完全程后时间线还当人在原处，下一站要绕回去打车。
这里给它们两端：从离上一站近的一头进，走完从另一头出，下一段路从出口算；堤上步行计入当天步行量。
白堤已按断桥—白堤—平湖秋月的连续漫步处理（poi_reviews 的 visit_group），不在这里重复。
端点坐标来自高德地点检索，名单在 assets/walk_ends.json；不改景点表本身。

只能坐船到的岛（2026-10-29 三潭印月：原先按道路打车上岛）列为 boat：去程到离上下站最近的岸边码头，坐船往返，下一段路从同一码头算。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from .cities import current_city
from .geo import gcj02_to_bd09, haversine_km, travel

PATH = Path(__file__).parent / "assets" / "walk_ends.json"
Point = tuple[float, float]
WALK_PREFERENCE_MIN = 10  # 坐车要等车、换乘、花钱：两种走法差不到这么多分钟时，选能走过去的


@lru_cache(maxsize=None)
def _entries() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8"))


def _entry(name: str | None) -> dict | None:
    return next((entry for entry in _entries().get(current_city().key, []) if entry["name"] == name), None)


def walk_ends(place) -> list[tuple[str, Point]] | None:
    """两端（名称，BD-09 坐标）；不是长堤、长街或索道返回 None。"""
    entry = _entry(getattr(place, "name", None))
    if entry is None or entry.get("boat"):
        return None
    return [(end["label"], gcj02_to_bd09(end["gcj"][1], end["gcj"][0])) for end in entry["ends"]]


def boat_piers(place) -> list[tuple[str, Point]] | None:
    """只能坐船到的岛：岸边可上船的码头（名称，BD-09 坐标）；不是这类地点返回 None。"""
    entry = _entry(getattr(place, "name", None))
    if entry is None or not entry.get("boat"):
        return None
    return [(pier["label"], gcj02_to_bd09(pier["gcj"][1], pier["gcj"][0])) for pier in entry["piers"]]


def walk_center(place, near: Point | None = None) -> Point:
    """排顺序时用的位置：长堤取两端中点；岛取离 near 最近的码头（没给 near 取第一个）；其他地点就是自己的坐标。"""
    if piers := boat_piers(place):
        return min(piers, key=lambda pier: haversine_km(*pier[1], *near))[1] if near else piers[0][1]
    ends = walk_ends(place)
    if not ends:
        return (place.lat, place.lon)
    (_, a), (_, b) = ends
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def choose_direction(place, before: Point | None, after: Point | None, leg=None) -> tuple[Point, Point, str] | None:
    """返回 (进入处, 离开处, 说明)。before/after 是上一处、下一处；None 表示不计的起点或终点（起点不定时）。

    leg(lat1, lon1, lat2, lon2) -> (分钟, 方式, …)，缺省用估算模型；坐车的一段多算 WALK_PREFERENCE_MIN 分钟
    （2026-10-29 苏堤：北口坐车 15 分钟比南口步行 16 分钟“快”，于是从北口出，实际要打车折回南边）。"""
    def cost(a: Point | None, b: Point | None) -> int:
        if a is None or b is None:
            return 0
        minutes, mode = (leg or travel)(*a, *b)[:2]
        return minutes + (0 if mode in {"步行", "未计入"} else WALK_PREFERENCE_MIN)

    if piers := boat_piers(place):
        label, pier = min(piers, key=lambda item: cost(before, item[1]) + cost(item[1], after))
        return pier, pier, f"从{label}坐船上岛，游览后坐船回到{label}；船班和回程码头以现场为准；下一段路从{label}算"
    ends = walk_ends(place)
    if not ends:
        return None

    (label_a, a), (label_b, b) = ends
    forward = cost(before, a) + cost(b, after)
    backward = cost(before, b) + cost(a, after)
    (enter_label, enter), (leave_label, leave) = ((label_a, a), (label_b, b)) if forward <= backward else ((label_b, b), (label_a, a))
    if (_entry(place.name) or {}).get("ride"):
        return enter, leave, f"从{enter_label}坐到{leave_label}；下一段路从{leave_label}算"
    return enter, leave, f"从{enter_label}进、{leave_label}出，全程约 {haversine_km(*enter, *leave):.1f} 公里；下一段路从{leave_label}算"


def stop_points(item, place) -> tuple[Point, Point]:
    """这一站从哪到达、从哪离开：长堤是两端，其他地点都是自己的坐标。"""
    arrive = tuple(item.enter_point) if getattr(item, "enter_point", None) else (place.lat, place.lon)
    leave = tuple(item.leave_point) if getattr(item, "leave_point", None) else arrive
    return arrive, leave


def walk_length_m(item) -> int:
    """一头进、另一头出的那段步行距离；索道是坐过去的，不算。"""
    if not getattr(item, "enter_point", None) or not getattr(item, "leave_point", None) or (_entry(item.name) or {}).get("ride"):
        return 0
    return round(haversine_km(*item.enter_point, *item.leave_point) * 1000)
