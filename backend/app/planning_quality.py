from __future__ import annotations

from functools import lru_cache

from .calendar import closed_on
from .data import load_pois, load_restaurants
from .experiences import experience_key
from .rules import MEAL_MIN, activity_window, required_meals
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
    from .nodes.schedule import service_min

    places = load_pois()
    blocks = [experience_blocks(group) for group in groups]
    occupied_days = sum(bool(group) for group in groups)
    origin = (profile.hotel.lat, profile.hotel.lon)
    locations = {identifier: (places[identifier].lat, places[identifier].lon)
                 for group in groups for identifier in group}
    locations[-1] = origin

    @lru_cache(maxsize=None)
    def minutes(day: int, before: int, after: int) -> int:
        route.set_day(profile, day)
        return route.leg(*locations[before], *locations[after], live=False)[0]

    @lru_cache(maxsize=None)
    def day_cost(day: int, identifiers: tuple[int, ...]) -> tuple[float, float]:
        window = activity_window(profile, day)
        available = window["end"] - window["start"]
        remaining = set(identifiers)
        elapsed = window["start"]
        transport = 0
        previous = -1
        while remaining:
            following = min(remaining, key=lambda identifier: (minutes(day, previous, identifier), identifier))
            duration = minutes(day, previous, following)
            transport += duration
            elapsed = max(elapsed + duration, places[following].open_min) + service_min(places[following], profile.pace)
            previous = following
            remaining.remove(following)
        transport += minutes(day, previous, -1) if identifiers else 0
        elapsed += minutes(day, previous, -1) if identifiers else 0
        if identifiers and not any(places[identifier].onsite_dining for identifier in identifiers):
            elapsed += len(required_meals(profile, day)) * (MEAL_MIN + 15)
        load = max(0, elapsed - window["start"])
        unavailable = sum(closed_on(places[identifier], profile, day) for identifier in identifiers)
        overload = max(0, load - available)
        comfort = max(0, transport - min(180, max(120, available * 0.3)))
        return unavailable * 10000 + overload * 30 + transport * 2 + comfort * 4, load / available

    def cost(candidate: list[list[list[int]]]) -> float:
        totals = [day_cost(index + 1, tuple(sorted(identifier for block in day for identifier in block)))
                  for index, day in enumerate(candidate)]
        loads = [load for _, load in totals]
        mean = sum(loads) / len(loads)
        lost_days = max(0, occupied_days - sum(bool(day) for day in candidate))
        return lost_days * 10000 + sum(value for value, _ in totals) + sum((load - mean) ** 2 for load in loads) * 240

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


def quality_key(itinerary: Itinerary, report: ValidationReport) -> tuple[int, int, float]:
    weights = {"empty_day": 40, "missing_lunch": 20, "missing_dinner": 20,
               "excessive_transport": 12, "anchor_mixed": 30, "audience_mismatch": 30,
               "early_finish": 4, "long_idle": 3, "short_visit": 6}
    transport = sum(day.return_travel_min + sum(item.travel_min for item in day.items) for day in itinerary.days)
    long_legs = sum(max(0, item.travel_min - 40) for day in itinerary.days for item in day.items)
    long_legs += sum(max(0, day.return_travel_min - 40) for day in itinerary.days)
    return (len(report.hard), sum(weights.get(item.code, 0) for item in report.violations),
            round(transport * 2 + long_legs + report.totals.get("idle_min", 0) * 0.5, 2))


def meal_order_candidates(orders: list[list[int]], limit: int = 7) -> list[list[list[int]]]:
    places = load_pois()
    candidates = []
    seen = {tuple(tuple(day) for day in orders)}
    daily_variants = []
    for day_index, order in enumerate(orders):
        blocks = experience_blocks(order)
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
