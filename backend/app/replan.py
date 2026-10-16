"""换餐厅后的局部重规划（设计原则"可编辑"：用户反馈后只重算受影响的那一天）。

流程：
1. 把所有正餐固定为当前餐厅（pins），被换的那一餐改为用户选的餐厅；所有景点顺序保持不变。
2. 只重建目标日期；复制其他日期的完整结果，再校验整份行程。
3. 若被换的那一天出现硬约束冲突，依次尝试：
   ① 用 OR-Tools 重排这一天的景点顺序（求解器可能舍弃放不下的点）；
   ② 移出这一天得分最低的非必去景点，最多 2 个。
   仍不通过就如实返回冲突，由用户决定是否撤销。
不调用大模型；只为新增路段查询高德（受单次规划额度约束）。
"""
from __future__ import annotations

import time
from typing import Any

from .data import load_pois
from .evening import add_evening_visits
from .nodes.meals import build_itinerary
from .nodes.schedule import route_ortools
from .nodes.validate import validate_itinerary
from .restaurants import RestaurantCatalog
from .schemas import DayPlan, Itinerary, MealAlternative
from .travel import make_travel

MAX_REMOVALS = 2


class ReplanError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def day_end(day: DayPlan) -> int | None:
    ends = [item.end for item in day.items] + [slot.end for slot in day.reserved_meals]
    return max(ends) + day.return_travel_min if ends else None


def _poi_orders(itinerary) -> list[list[int]]:
    return [[item.ref_id for item in day.items if item.kind == "poi" and not item.evening and item.ref_id is not None]
            for day in itinerary.days]


def swap_meal(result: dict[str, Any], day: int, item_index: int, restaurant_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    started = time.time()
    itinerary, profile = result["itinerary"], result["profile"]
    if not 1 <= day <= len(itinerary.days):
        raise ReplanError(422, "这一天不在行程里")
    plan_day = itinerary.days[day - 1]
    if not 0 <= item_index < len(plan_day.items):
        raise ReplanError(422, "找不到要更换的这一餐")
    item = plan_day.items[item_index]
    if item.kind != "meal" or item.meal is None:
        raise ReplanError(422, "只能更换正餐")
    if item.ref_id == restaurant_id:
        raise ReplanError(422, "已经是这家餐厅")
    restaurants = dict(result.get("restaurants") or {})
    target = restaurants.get(restaurant_id)
    if target is None:
        from .place_details import resolve_place
        from .schemas import PlaceReference
        target = resolve_place(PlaceReference(kind="meal", id=restaurant_id))
    if target is None:
        raise ReplanError(404, "找不到这家餐厅的资料，请重新生成行程后再试")
    restaurants[target.id] = target

    pins = {(d.day, i.meal): i.ref_id for d in itinerary.days for i in d.items
            if i.kind == "meal" and i.meal and i.ref_id is not None}
    pins[(day, item.meal)] = target.id
    user_meals = set(result.get("user_meals") or set()) | {(day, item.meal)}
    orders = _poi_orders(itinerary)
    source = result.get("travel_stats", {}).get("source")
    route = make_travel(source if source in {"amap", "estimate"} else None, profile.transport)
    catalog = RestaurantCatalog(route=route, initial=restaurants, online=False)
    scores = {candidate.poi_id: candidate.score for candidate in result.get("candidates", [])}
    must = {candidate.poi_id for candidate in result.get("candidates", []) if candidate.breakdown.get("must_visit")}
    pois = load_pois()

    old_transport = 0.0
    position = (profile.hotel.lat, profile.hotel.lon)
    with route.for_day(profile, day):
        for old_item in plan_day.items:
            place = pois.get(old_item.ref_id) if old_item.kind == "poi" else restaurants.get(old_item.ref_id)
            if place is None:
                continue
            destination = (place.lat, place.lon)
            old_transport += old_item.travel_evidence.cost if old_item.travel_evidence else route.leg(*position, *destination)[2]
            position = destination
        if plan_day.items:
            old_transport += plan_day.return_leg.cost if plan_day.return_leg else route.leg(*position, profile.hotel.lat, profile.hotel.lon)[2]

    def build(day_orders: list[list[int]]):
        partial = build_itinerary(day_orders, profile, result["selected"], result.get("meal_factor", 1.0), route,
                                  catalog=catalog, pins=pins, user_meals=user_meals, only_day=day)
        rebuilt = itinerary.model_copy(deep=True)
        rebuilt.days[day - 1] = partial.days[0]
        rebuilt.transport_cost = round(itinerary.transport_cost - old_transport + partial.transport_cost, 1)
        rebuilt, _ = add_evening_visits(rebuilt, profile, route, catalog, only_day=day)
        route.finalize(Itinerary(days=[rebuilt.days[day - 1]]), profile, catalog.restaurants)
        report = validate_itinerary(rebuilt, profile, travel_fn=route.leg, restaurants=catalog.restaurants)
        return rebuilt, report

    def conflicts(report) -> list[str]:
        return [v.message for v in report.violations if v.severity == "hard" and v.day in (day, None)]

    rebuilt, report = build(orders)
    removed: list[int] = []
    reordered = False
    if conflicts(report):  # ① 重排这一天的景点顺序
        solved = route_ortools(orders[day - 1], profile, scores, route, must=must, day=day)
        if solved is not None and solved[0] and solved[0] != orders[day - 1]:
            trial = [list(order) for order in orders]
            trial[day - 1] = solved[0]
            trial_it, trial_report = build(trial)
            if len(conflicts(trial_report)) < len(conflicts(report)):
                orders, rebuilt, report, reordered = trial, trial_it, trial_report, True
                removed += [pid for pid in solved[1] if pid in _poi_orders(itinerary)[day - 1]]
    for _ in range(MAX_REMOVALS):  # ② 移出得分最低的非必去景点
        if not conflicts(report):
            break
        removable = [pid for pid in orders[day - 1] if pid not in must]
        if len(removable) <= 1:
            break
        worst = min(removable, key=lambda pid: scores.get(pid, 0.0))
        orders = [list(order) for order in orders]
        orders[day - 1] = [pid for pid in orders[day - 1] if pid != worst]
        removed.append(worst)
        rebuilt, report = build(orders)

    # 让被换下的原餐厅出现在备选里，方便换回去
    new_day = rebuilt.days[day - 1]
    new_item = next((i for i in new_day.items if i.kind == "meal" and i.meal == item.meal), None)
    previous = restaurants.get(item.ref_id)
    if new_item is not None and previous is not None and all(a.ref_id != previous.id for a in new_item.alternatives):
        new_item.alternatives = [MealAlternative(ref_id=previous.id, name=previous.name, cuisine=previous.cuisine,
                                                 price=previous.price, rating=previous.rating, address=previous.address,
                                                 detour_min=0, travel_source="previous", provider=previous.provider,
                                                 fetched_at=previous.fetched_at, start=item.start, end=item.end),
                                 *new_item.alternatives][:2]

    old_end, new_end = day_end(plan_day), day_end(new_day)
    meal_name = "午餐" if item.meal == "lunch" else "晚餐"
    change = {"day": day, "meal": item.meal, "from": item.name, "to": target.name,
              "shift_min": None if old_end is None or new_end is None else new_end - old_end,
              "removed": [pois[pid].name for pid in removed if pid in pois], "reordered": reordered,
              "passed": report.passed, "conflicts": conflicts(report),
              "seconds": round(time.time() - started, 2), "live_calls": route.stats().get("live_calls", 0)}
    note = f"用户把第 {day} 天{meal_name}从「{item.name}」换成「{target.name}」，按真实路线重新核对当天安排"
    if reordered:
        note += "；重排了当天景点顺序"
    if change["removed"]:
        note += "；为保证时间可行移出 " + "、".join(change["removed"])
    final_evidence = {}
    for planned in rebuilt.days:
        position = (profile.hotel.lat, profile.hotel.lon)
        with route.for_day(profile, planned.day):
            for stop in planned.items:
                place = pois.get(stop.ref_id) if stop.kind == "poi" else catalog.restaurants.get(stop.ref_id)
                if place is None:
                    continue
                destination = (place.lat, place.lon)
                if stop.travel_evidence:
                    final_evidence[route.edge_key(*position, *destination)] = stop.travel_evidence
                position = destination
            if planned.return_leg:
                final_evidence[route.edge_key(*position, profile.hotel.lat, profile.hotel.lon)] = planned.return_leg
    travel_stats = route.stats()
    travel_stats.update(final_legs=len(final_evidence), approx_legs=sum(leg.source == "estimate" for leg in final_evidence.values()))
    updated = dict(result)
    updated.update({"itinerary": rebuilt, "report": report, "day_orders": orders,
                    "dropped": [*result.get("dropped", []), *removed], "restaurants": catalog.restaurants,
                    "user_meals": user_meals, "travel_stats": travel_stats, "log": [*result.get("log", []), note],
                    "seconds": change["seconds"], "operation": "meal_swap",
                    "usage": {"calls": 0, "cached_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                              "total_tokens": 0, "seconds": 0.0}})
    return updated, change
