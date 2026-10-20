from __future__ import annotations

from typing import TYPE_CHECKING

from .cities import current_city
from .areas import area_fill_conflicts, area_index
from .calendar import closed_on, date_notices
from .data import load_pois, load_restaurants
from .geo import travel as estimated_travel
from .rules import activity_window, interstop_ride_long
from .poi_semantics import visit_open_min
from .schemas import Item, Itinerary, UserProfile, ValidationReport

if TYPE_CHECKING:
    from .travel import Travel
    from .restaurants import RestaurantCatalog


NIGHT_STREETS = current_city().evening_streets  # 兼容旧脚本；运行时按任务城市读取


def add_evening_visits(itinerary: Itinerary, profile: UserProfile, route: Travel | None = None,
                       catalog: RestaurantCatalog | None = None,
                       only_day: int | None = None,
                       excluded_ids: set[int] | None = None) -> tuple[Itinerary, list[str]]:
    from .nodes.validate import validate_itinerary

    if not profile.include_evening:
        return itinerary, []
    from .planning_context import allowed_pois
    allowed = allowed_pois.get()
    pois = load_pois()
    restaurants = catalog.restaurants if catalog else load_restaurants()
    hotel = (profile.hotel.lat, profile.hotel.lon)
    added = []

    def check(candidate: Itinerary) -> ValidationReport:
        return validate_itinerary(candidate, profile, travel_fn=route.leg if route else None, restaurants=restaurants)

    if not check(itinerary).passed:
        return itinerary, added
    for day_index in range(len(itinerary.days)):
        day = itinerary.days[day_index]
        if any(item.kind == "poi" and pois.get(item.ref_id) and pois[item.ref_id].day_anchor for item in day.items):
            continue
        if only_day is not None and day.day != only_day:
            continue
        window = activity_window(profile, day.day)
        if window["end"] < 19 * 60 + 30 or not day.items:
            continue
        if any(item.kind == "poi" and item.start >= 18 * 60 for item in day.items):
            continue
        if route:
            route.set_day(profile, day.day)
        last = day.items[-1]
        place = pois.get(last.ref_id) if last.kind == "poi" else restaurants.get(last.ref_id)
        if place is None:
            continue
        position = (place.lat, place.lon)
        departure = max(last.end, *(slot.end for slot in day.reserved_meals), 0)
        seen = {item.ref_id for planned in itinerary.days for item in planned.items if item.kind == "poi"}
        candidates = [poi for identifier, name in current_city().evening_streets.items() if (poi := pois.get(identifier))
                      and (allowed is None or poi.id in allowed)
                      and poi.name == name and poi.id not in seen and poi.id not in (excluded_ids or set()) and poi.recommendable
                      and not closed_on(poi, profile, day.day)
                      and not any(text and text in poi.name for text in profile.avoid)]
        zones = area_index(pois)
        home = zones.home_areas(profile.hotel.lat, profile.hotel.lon)
        day_zones = {zones.area_of(item.ref_id) for item in day.items if item.kind == "poi" and item.ref_id in pois}
        if day_zones:  # 片区为主：夜游只在当天片区或相邻片区
            candidates = [poi for poi in candidates if any(zones.compatible(zones.area_of(poi.id), zone, home) for zone in day_zones)]
        last_zone = next((zones.area_of(item.ref_id) for item in reversed(day.items)
                          if item.kind == "poi" and item.ref_id in pois), None)
        candidates.sort(key=lambda poi: (zones.area_of(poi.id) != last_zone,
                        estimated_travel(*position, poi.lat, poi.lon)[0]
                        + estimated_travel(poi.lat, poi.lon, *hotel)[0]))
        direct, _, old_return = route.leg(*position, *hotel) if route else estimated_travel(*position, *hotel)
        for poi in candidates:
            destination = (poi.lat, poi.lon)
            coarse_minutes, coarse_mode, _ = estimated_travel(*position, *destination)
            if route is None and interstop_ride_long(coarse_minutes, coarse_mode):
                continue
            go, mode, fare = route.leg(*position, *destination) if route else estimated_travel(*position, *destination)
            back, _, return_fare = route.leg(*destination, *hotel) if route else estimated_travel(*destination, *hotel)
            extra = max(0, go + back - direct)
            if zones.area_of(poi.id) != last_zone and extra > 15:
                continue
            start = max(departure + go, 18 * 60 + 30, visit_open_min(poi))
            duration = max(60, round(poi.min_hours * 60))
            if interstop_ride_long(go, mode) or start + duration + back > window["end"] or start + duration > poi.close_min:
                continue
            trial = itinerary.model_copy(deep=True)
            trial_day = trial.days[day_index]
            trial_day.items.append(Item(kind="poi", ref_id=poi.id, name=poi.name, start=start, end=start + duration,
                travel_min=go, travel_mode=mode, cost=poi.price, evening=True,
                reason="晚饭后沿街散步，按兴趣看看街景和店铺；在最晚返回时间内安排一小时夜游。",
                notices=date_notices(poi, profile, day.day) + ["夜间开放采用现有地点记录，商铺营业和临时管制需现场确认；不包含收费演出、船班或灯光秀承诺"]))
            trial.transport_cost = round(trial.transport_cost - old_return + fare + return_fare, 1)
            trial_day.return_travel_min = back
            if area_fill_conflicts(trial, pois):
                continue
            trial_report = check(trial)
            if trial_report.passed and not any(v.code == "excessive_transport" for v in trial_report.violations):
                itinerary = trial
                added.append(f"第{day.day}天 {poi.name}")
                break
        else:
            day.notes.append("已尝试补充沿途夜游，但在交通、体力或最晚返回条件内没有合适方案；可调整活动时段再试")
    return itinerary, added
