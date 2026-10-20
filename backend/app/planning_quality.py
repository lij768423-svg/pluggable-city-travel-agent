from __future__ import annotations

from functools import lru_cache

from collections import Counter

from .areas import AREA_EXTRA_COST, AREA_FAR_COST, AREA_SPLIT_COST, area_index
from .calendar import closed_on
from .data import load_pois, load_restaurants
from .experiences import experience_key
from .poi_semantics import evening_only, visit_open_min
from .rules import (MAX_INTERSTOP_RIDE_MIN, MEAL_MIN, activity_window, interstop_ride_allowed, interstop_ride_long,
                     locality_transition_penalty, required_meals)
from .schemas import Itinerary, Selection, UserProfile, ValidationReport


class CachedRoute:
    def __init__(self, route):
        self.route = route

    def for_day(self, profile: UserProfile, day: int):
        return self.route.for_day(profile, day)

    def set_day(self, profile: UserProfile, day: int) -> None:
        self.route.set_day(profile, day)

    def matrix(self, locations: list[tuple[float, float]]) -> list[list[int]]:
        return [[self.leg(*origin, *destination)[0] if origin != destination else 0
                 for destination in locations] for origin in locations]

    def leg(self, *coordinates, live: bool = False):
        return self.route.leg(*coordinates, live=False)

    def detail(self, *coordinates):
        return self.route.detail(*coordinates, live=False)


def experience_blocks(order: list[int]) -> list[list[int]]:
    places = load_pois()
    blocks: dict[str, list[int]] = {}
    for identifier in order:
        blocks.setdefault(experience_key(places[identifier]), []).append(identifier)
    return list(blocks.values())


def balance_days(groups: list[list[int]], profile: UserProfile, route) -> list[list[int]]:
    from .nodes.schedule import day_capacity, service_min

    places = load_pois()
    areas = area_index(places)
    home = areas.home_areas(profile.hotel.lat, profile.hotel.lon)
    area_load = Counter()
    for group in groups:
        for identifier in group:
            area_load[areas.area_of(identifier)] += service_min(places[identifier], profile.pace)
    capacity = max(day_capacity(profile, day) for day in range(1, len(groups) + 1))

    def area_cost(candidate: list[list[list[int]]]) -> float:
        """片区为主：当天多一个片区、出现不相邻片区、一天装得下的片区被拆到多天都加成本。"""
        value = 0.0
        spread = Counter()
        for day in candidate:
            zones = areas.areas_of([identifier for block in day for identifier in block])
            value += AREA_EXTRA_COST * max(0, len(zones) - 1)
            value += AREA_FAR_COST * sum(not areas.compatible(first, second, home)
                                         for index, first in enumerate(zones) for second in zones[index + 1:])
            spread.update(zones)
        return value + sum(AREA_SPLIT_COST * (count - 1) for area, count in spread.items()
                           if count > 1 and area_load[area] <= capacity)
    blocks = [experience_blocks(group) for group in groups]
    occupied_days = sum(bool(group) for group in groups)
    daytime_days = sum(any(not evening_only(places[i]) for i in group) for group in groups)
    origin = (profile.hotel.lat, profile.hotel.lon)
    locations = {identifier: (places[identifier].lat, places[identifier].lon)
                 for group in groups for identifier in group}
    locations[-1] = origin

    @lru_cache(maxsize=None)
    def connection(day: int, before: int, after: int) -> tuple[int, str, float]:
        route.set_day(profile, day)
        return route.leg(*locations[before], *locations[after], live=False)

    @lru_cache(maxsize=None)
    def day_cost(day: int, identifiers: tuple[int, ...]) -> tuple[float, float]:
        window = activity_window(profile, day)
        available = window["end"] - window["start"]
        remaining = set(identifiers)
        elapsed = window["start"]
        transport = 0
        riding = 0
        locality_cost = 0
        overlong_transfers = 0
        previous = -1
        while remaining:
            following = min(remaining, key=lambda identifier: (evening_only(places[identifier]), connection(day, previous, identifier)[0], identifier))
            duration, mode, _ = connection(day, previous, following)
            transport += duration
            riding += duration if previous != -1 and mode not in {"步行", "原地"} else 0
            locality_cost += locality_transition_penalty(duration, mode)
            overlong_transfers += int(previous != -1 and not interstop_ride_allowed(duration, mode))
            elapsed = max(elapsed + duration, visit_open_min(places[following])) + service_min(places[following], profile.pace)
            previous = following
            remaining.remove(following)
        if identifiers:
            returning, return_mode, _ = connection(day, previous, -1)
            transport += returning
            elapsed += returning
            locality_cost += locality_transition_penalty(returning, return_mode)
        if identifiers and not any(places[identifier].onsite_dining for identifier in identifiers):
            elapsed += len(required_meals(profile, day)) * (MEAL_MIN + 15)
        load = max(0, elapsed - window["start"])
        unavailable = sum(closed_on(places[identifier], profile, day) for identifier in identifiers)
        overload = max(0, load - available)
        comfort = max(0, riding - min(180, max(120, available * 0.3)))
        return ((unavailable + overlong_transfers) * 10000 + overload * 30
                + transport * 2 + comfort * 4 + locality_cost * 2, load / available)

    def cost(candidate: list[list[list[int]]]) -> float:
        totals = [day_cost(index + 1, tuple(sorted(identifier for block in day for identifier in block)))
                  for index, day in enumerate(candidate)]
        loads = [load for _, load in totals]
        mean = sum(loads) / len(loads)
        lost_days = max(0, occupied_days - sum(bool(day) for day in candidate))
        lost_daytime = max(0, daytime_days - sum(any(not evening_only(places[i]) for block in day for i in block)
                                                 for day in candidate))
        return ((lost_days + lost_daytime) * 10000 + sum(value for value, _ in totals) + sum((load - mean) ** 2 for load in loads) * 240
                + area_cost(candidate))

    def locked(day: list[list[int]]) -> bool:
        return any(places[identifier].day_anchor for block in day for identifier in block)

    for _ in range(8):
        best, best_cost = blocks, cost(blocks)
        for source in range(len(blocks)):
            for destination in range(source + 1, len(blocks)):
                swapped = [[list(block) for block in day] for day in blocks]
                swapped[source], swapped[destination] = swapped[destination], swapped[source]
                value = cost(swapped)
                if value < best_cost - 0.01:
                    best, best_cost = swapped, value
        for source, day in enumerate(blocks):
            if locked(day):
                continue
            for block_index, block in enumerate(day):
                for destination, target in enumerate(blocks):
                    if source == destination or locked(target):
                        continue
                    for other_index in [-1] + list(range(len(target))):
                        candidate = [[list(entry) for entry in daily] for daily in blocks]
                        candidate[source].pop(block_index)
                        if other_index >= 0:
                            candidate[source].append(candidate[destination].pop(other_index))
                        candidate[destination].append(block)
                        value = cost(candidate)
                        if value < best_cost - 0.01:
                            best, best_cost = candidate, value
        if best is blocks:
            break
        blocks = best
    return [[identifier for block in day for identifier in block] for day in blocks]


# 修复回路挑最佳方案时：总空档超过 IDLE_FREE_MIN 的部分，每满一小时计 IDLE_HOUR_POINTS 分（2026-10-20 用户同意）。
# 3 小时内不额外计分，保持“宁可空档也不片区折返”（area_scattered 10 > early_finish4＋long_idle3）；
# 按整小时计，十几分钟的空档差不压过交通时间。分天/餐序比较时尚未补点，空档不是最终值，不计这一项。
IDLE_FREE_MIN = 180
IDLE_HOUR_POINTS = 4


def quality_key(itinerary: Itinerary, report: ValidationReport, *, count_idle: bool = False) -> tuple[int, float, float]:
    weights = {"empty_day": 40, "missing_lunch": 20, "missing_dinner": 20,
               "excessive_transport": 12, "anchor_mixed": 30, "audience_mismatch": 30,
               "early_finish": 4, "long_idle": 3, "short_visit": 6, "interstop_ride_long": 2, "area_scattered": 10}
    transport = sum(day.return_travel_min + sum(item.travel_min for item in day.items) for day in itinerary.days)
    long_legs = sum(max(0, item.travel_min - MAX_INTERSTOP_RIDE_MIN)
                    for day in itinerary.days for item in day.items[1:]
                    if interstop_ride_long(item.travel_min, item.travel_mode))
    zones = area_index(load_pois())
    sequences = [[zones.area_of(item.ref_id) for item in day.items if item.kind == "poi"] for day in itinerary.days]
    moves = sum(before != after for sequence in sequences for before, after in zip(sequence, sequence[1:]))
    # 删景点换来的大段空档不应比缺一餐更划算
    idle = max(0, report.totals.get("idle_min", 0) - IDLE_FREE_MIN) // 60 * IDLE_HOUR_POINTS if count_idle else 0
    return (len(report.hard), round(sum(weights.get(item.code, 0) for item in report.violations) + idle, 2),
            round(transport * 2 + long_legs + report.totals.get("idle_min", 0) * 0.5 + moves * 20, 2))


def meal_order_candidates(orders: list[list[int]], limit: int = 7) -> list[list[list[int]]]:
    places = load_pois()
    candidates = []
    seen = {tuple(tuple(day) for day in orders)}
    daily_variants = []
    for day_index, order in enumerate(orders):
        blocks = experience_blocks(order)
        zones = area_index(places)
        grouped: dict[str, list[int]] = {}
        for block in blocks:
            grouped.setdefault(zones.area_of(block[0]), []).extend(block)
        if len(grouped) > 1:
            blocks = list(grouped.values())
        if len(blocks) < 2 or any(places[identifier].day_anchor for identifier in order):
            continue
        variants = [list(reversed(blocks)), blocks[1:] + blocks[:1], blocks[-1:] + blocks[:-1]]
        daily_variants.append((day_index, variants))
    for variant_index in range(3):
        for day_index, variants in daily_variants:
            variant = variants[variant_index]
            candidate = [list(day) for day in orders]
            candidate[day_index] = [identifier for block in variant for identifier in block]
            fingerprint = tuple(tuple(day) for day in candidate)
            if fingerprint not in seen:
                candidates.append(candidate)
                seen.add(fingerprint)
    return candidates[:limit]


def choose_meal_aware_plan(orders: list[list[int]], profile: UserProfile, selected: list[Selection],
                           factor: float = 1.0, route=None, catalog=None, live: bool = True
                           ) -> tuple[list[list[int]], Itinerary, list[str]]:
    from .nodes.meals import build_itinerary
    from .nodes.validate import validate_itinerary

    def build(candidate: list[list[int]], fetch: bool) -> Itinerary:
        return build_itinerary(candidate, profile, selected, factor, route, live=fetch, catalog=catalog)

    def check(itinerary: Itinerary) -> ValidationReport:
        restaurants = catalog.restaurants if catalog is not None else load_restaurants()
        if route is None:
            return validate_itinerary(itinerary, profile, restaurants=restaurants)
        return validate_itinerary(itinerary, profile, travel_fn=CachedRoute(route).leg, restaurants=restaurants)

    baseline = build(orders, live)
    baseline_report = check(baseline)
    baseline_key = quality_key(baseline, baseline_report)
    candidates = meal_order_candidates(orders)
    shortlist = []
    restaurants = catalog.restaurants if catalog is not None else load_restaurants()
    for candidate in candidates:
        itinerary = build(candidate, False)
        travel_fn = CachedRoute(route).leg if route is not None else None
        report = validate_itinerary(itinerary, profile, travel_fn=travel_fn, restaurants=restaurants)
        score = quality_key(itinerary, report)
        if score < baseline_key:
            shortlist.append((score, candidate, itinerary))
    for _, candidate, cached in sorted(shortlist, key=lambda row: row[0])[:2]:
        itinerary = build(candidate, True) if live else cached
        report = check(itinerary)
        old_hard = {item.code for item in baseline_report.hard}
        new_hard = {item.code for item in report.hard}
        old_missing = {(item.day, item.code) for item in baseline_report.violations if item.code.startswith("missing_")}
        new_missing = {(item.day, item.code) for item in report.violations if item.code.startswith("missing_")}
        if (new_hard <= old_hard and new_missing <= old_missing
                and quality_key(itinerary, report) < baseline_key):
            return candidate, itinerary, [f"餐饮联合比较：比较原方案与{len(candidates)}个顺序候选，采纳通过复核的更优方案"]
    return orders, baseline, [f"餐饮联合比较：比较原方案与{len(candidates)}个顺序候选，保留原方案"]
