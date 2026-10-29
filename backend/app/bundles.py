"""同一景区合成一次游览（2026-10-28 用户：灵隐寺和飞来峰这类同一景区的点合并，时间记得改）。

景点表里同一景区常有几条重叠记录（主景区和其中的寺庙、石窟、码头），分开排会一天里进出好几次、时长相加。
检索时成员一律换成主条目这一条；合并后的游览时长由名单给（hours），不给就沿用主条目自己的；必去、不去按成员名也认。
不改景点表本身（改了会让补充目录的原库摘要对不上，旧行程也改不了），名单在 assets/scenic_bundles.json，
排程、校验、选点、详情读时长一律经 visit_hours。
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import NamedTuple

from .cities import current_city
from .data import load_pois

PATH = Path(__file__).parent / "assets" / "scenic_bundles.json"


class Bundle(NamedTuple):
    umbrella: int
    names: list[str]
    note: str
    hours: tuple[float, float] | None
    member_ids: tuple[int, ...]
    fee_note: str | None = None  # 成员票价不是简单的“另收”时（如塔上更高一层是联票），由名单写明


@lru_cache(maxsize=None)
def _entries() -> dict:
    return json.loads(PATH.read_text(encoding="utf-8"))


_CACHE: dict[tuple[str, int], dict[int, Bundle]] = {}


def bundles() -> dict[int, Bundle]:
    """成员 id → 所在景区（主条目自己也在内）。名称在当前城市景点表里对不上的跳过。按目录缓存，排程里频繁调用。"""
    pois = load_pois()
    key = (current_city().key, id(pois))
    if key in _CACHE:
        return _CACHE[key]
    by_name: dict[str, int] = {}
    for identifier, place in pois.items():
        by_name.setdefault(place.name, identifier)
    out: dict[int, Bundle] = {}
    for entry in _entries().get(current_city().key, []):
        umbrella = by_name.get(entry["umbrella"])
        if umbrella is None:
            continue
        names = [name for name in entry["members"] if name in by_name]
        hours = tuple(entry["hours"]) if entry.get("hours") else None
        bundle = Bundle(umbrella, names, entry.get("note", ""), hours, tuple(by_name[name] for name in names), entry.get("fee_note"))
        for identifier in (umbrella, *bundle.member_ids):
            out[identifier] = bundle
    _CACHE[key] = out
    return out


def umbrella_of(identifier: int, mapping: dict[int, Bundle] | None = None) -> int:
    entry = (mapping if mapping is not None else bundles()).get(identifier)
    return entry.umbrella if entry else identifier


def visit_hours(place) -> tuple[float, float]:
    """建议游览时长（小时）：合并后的景区用名单给的时长，其他地点用自己的。"""
    entry = bundles().get(getattr(place, "id", None))
    if entry and entry.umbrella == place.id and entry.hours:
        return entry.hours
    return place.min_hours, place.max_hours


def bundle_notice(identifier: int) -> str | None:
    entry = bundles().get(identifier)
    if entry is None or entry.umbrella != identifier or not entry.names:
        return None
    pois = load_pois()
    paid = [f"{pois[member].name}约 {pois[member].price:.0f} 元" for member in entry.member_ids
            if member in pois and pois[member].price > pois[identifier].price]
    text = f"同一景区一次游览：含{'、'.join(entry.names)}。{entry.note}".rstrip("。") + "。"
    if entry.fee_note:
        return text + entry.fee_note.rstrip("。") + "。"
    return text + (f"其中另收门票：{'、'.join(paid)}。" if paid else "")
