from __future__ import annotations

from collections.abc import Iterable
import math

from .schemas import POI


LOCALITY_CELL_DEGREES = 0.02


def experience_key(place: POI) -> str:
    return f"walk:{place.visit_group}" if place.visit_group else f"poi:{place.id}"


def locality_key(place: POI) -> str:
    """Return a deterministic soft locality used only for initial day grouping.

    A declared walk group stays atomic. Other places use a small geographic
    cell so the scheduler starts from nearby clusters without making a hard
    district constraint.
    """
    if place.visit_group:
        return f"experience:{place.visit_group}"
    return f"grid:{math.floor(place.lat / LOCALITY_CELL_DEGREES)}:{math.floor(place.lon / LOCALITY_CELL_DEGREES)}"


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
