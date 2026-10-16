from __future__ import annotations

from collections.abc import Iterable

from .schemas import POI


def experience_key(place: POI) -> str:
    return f"walk:{place.visit_group}" if place.visit_group else f"poi:{place.id}"


def experience_count(place_ids: Iterable[int], places: dict[int, POI]) -> int:
    return len({experience_key(places[identifier]) for identifier in place_ids if places[identifier].visit_role != "viewpoint"})


def keep_walks_together(order: list[int], places: dict[int, POI]) -> list[int]:
    blocks: dict[str, list[int]] = {}
    for identifier in order:
        blocks.setdefault(experience_key(places[identifier]), []).append(identifier)
    return [identifier for block in blocks.values() for identifier in block]


def walk_boundary(order: list[int], position: int, places: dict[int, POI]) -> bool:
    if position == 0 or position == len(order):
        return True
    before, after = places[order[position - 1]], places[order[position]]
    return not before.visit_group or before.visit_group != after.visit_group
