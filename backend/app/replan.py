"""换餐厅、换景点、调节奏后的局部重规划（设计原则"可编辑"：用户反馈后只重算受影响的那一天）。

流程：
1. 换餐：把所有正餐固定为当前餐厅（pins），被换的那一餐改为用户选的餐厅；所有景点顺序保持不变。
   换景点：当天顺序里换成用户从候选选的景点；用户亲自选的餐次仍固定，其余餐次按新路线重选。
   调节奏：当天少排或多排一站，餐次处理同换景点。多排不挤掉当天已有景点（含夜游），放不下就如实说。
2. 只重建目标日期；复制其他日期的完整结果，再校验整份行程。其他日期的路段沿用行程里的记录，不重新查询。
3. 若被换的那一天出现硬约束冲突，依次尝试：
   ① 当天步行超上限时，把走得最多的路段改打车；
   ② 用 OR-Tools 重排这一天的景点顺序（求解器可能舍弃放不下的点）；
   ③ 移出这一天得分最低的非必去景点，最多 2 个。
   仍不通过就如实返回冲突，由用户决定是否撤销。
不调用大模型；只为新增路段查询高德（受单次规划额度约束）。
"""
from __future__ import annotations

import time
from typing import Any

from .areas import area_index
from .bundles import umbrella_of
from .calendar import closed_on
from .experiences import experience_key, keep_walks_together
from .geo import haversine_km
from .walk_ends import stop_points
from .data import load_pois
from .evening import add_evening_visits
from .nodes.meals import build_itinerary
from .nodes.retrieve import max_stamina
from .nodes.schedule import route_ortools, service_min
from .nodes.validate import validate_itinerary
from .poi_semantics import evening_only
from .restaurants import RestaurantCatalog
from .rules import activity_window
from .schemas import Candidate, DayPlan, Itinerary, MealAlternative, POI, Selection
from .travel import FORCED_TAXI_REASON, depart_at, make_travel

MAX_REMOVALS = 2
MAX_POI_OPTIONS = 8
MAX_PACE_TRIES = 3  # 多排一站时最多试几个候选，每试一个都可能查路线


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


def _must_visit(result: dict[str, Any]) -> set[int]:
    return {candidate.poi_id for candidate in result.get("candidates", []) if candidate.breakdown.get("must_visit")}


def _route_for(result: dict[str, Any], restaurants: dict):
    """新的 Travel 先记下行程已有的路段：其他日期不重新查询，修复环节改的打车也不会被算回步行。"""
    profile = result["profile"]
    source = result.get("travel_stats", {}).get("source")
    route = make_travel(source if source in {"amap", "estimate"} else None, profile.transport)
    for planned in result["itinerary"].days:
        with route.for_day(profile, planned.day):
            for item, _, origin, destination, depart in route.day_legs(planned, profile, restaurants):
                evidence = planned.return_leg if item is None else item.travel_evidence
                if evidence is None:
                    continue
                if (evidence.fallback_reason or "").startswith(FORCED_TAXI_REASON):
                    route.taxi_overrides.add(route.pair(*origin, *destination))
                with depart_at(route, depart):
                    route.evidence[route.edge_key(*origin, *destination)] = evidence
    return route


def _rebuild_day(result: dict[str, Any], day: int, orders: list[list[int]], selected: list[Selection], pins: dict, user_meals: set,
                 route, catalog, keep: set[int] | None = None, excluded: set[int] | None = None, used: set[int] | None = None):
    """只重建 day 这一天并校验，硬冲突时按模块说明依次尝试。返回 (行程, 校验报告, 各天顺序, 移出的景点, 是否重排)。"""
    itinerary, profile = result["itinerary"], result["profile"]
    plan_day = itinerary.days[day - 1]
    scores = {candidate.poi_id: candidate.score for candidate in result.get("candidates", [])}
    must = _must_visit(result) | (keep or set()) | {s.poi_id for s in selected if s.source == "user"}  # 用户亲自换入的不自动移出
    pois = load_pois()

    old_transport = 0.0
    position = (profile.hotel.lat, profile.hotel.lon)
    with route.for_day(profile, day):
        for old_item in plan_day.items:
            place = pois.get(old_item.ref_id) if old_item.kind == "poi" else catalog.restaurants.get(old_item.ref_id)
            if place is None:
                continue
            destination, leave = stop_points(old_item, place)
            old_transport += old_item.travel_evidence.cost if old_item.travel_evidence else route.leg(*position, *destination)[2]
            position = leave
        if plan_day.items:
            old_transport += plan_day.return_leg.cost if plan_day.return_leg else route.leg(*position, profile.hotel.lat, profile.hotel.lon)[2]

    def build(day_orders: list[list[int]]):
        partial = build_itinerary(day_orders, profile, selected, result.get("meal_factor", 1.0), route,
                                  catalog=catalog, pins=pins, user_meals=user_meals, only_day=day, used_restaurants=used)
        rebuilt = itinerary.model_copy(deep=True)
        rebuilt.days[day - 1] = partial.days[0]
        from .meal_edits import preserve_self_arranged
        preserve_self_arranged(result, rebuilt)
        rebuilt.transport_cost = round(itinerary.transport_cost - old_transport + partial.transport_cost, 1)
        rebuilt, _ = add_evening_visits(rebuilt, profile, route, catalog, only_day=day, excluded_ids=excluded)
        route.finalize(Itinerary(days=[rebuilt.days[day - 1]]), profile, catalog.restaurants)
        report = validate_itinerary(rebuilt, profile, travel_fn=route.leg, restaurants=catalog.restaurants)
        return rebuilt, report

    def conflicts(report) -> list[str]:
        return [v.message for v in report.violations if v.severity == "hard" and v.day in (day, None)]

    rebuilt, report = build(orders)
    removed: list[int] = []
    reordered = False
    if any(v.code == "walking_limit" and v.severity == "hard" and v.day == day for v in report.violations):  # ① 步行超上限改打车
        from .nodes.repair import walking_taxi_overrides
        today = report.model_copy(update={"violations": [v for v in report.violations if v.day == day]})
        if walking_taxi_overrides({"travel": route, "profile": profile, "itinerary": rebuilt, "report": today, "catalog": catalog})[0]:
            rebuilt, report = build(orders)

    def reorder() -> None:  # ② 重排这一天的景点顺序；求解器舍弃了用户刚换入的景点就不采用
        nonlocal orders, rebuilt, report, reordered, removed
        solved = route_ortools(orders[day - 1], profile, scores, route, must=must, day=day)
        if solved is None or not solved[0] or solved[0] == orders[day - 1] or not (keep or set()) <= set(solved[0]):
            return
        trial = [list(order) for order in orders]
        trial[day - 1] = solved[0]
        trial_it, trial_report = build(trial)
        if len(conflicts(trial_report)) < len(conflicts(report)):
            orders, rebuilt, report, reordered = trial, trial_it, trial_report, True
            removed += [pid for pid in solved[1] if pid in _poi_orders(itinerary)[day - 1]]

    if conflicts(report):
        reorder()
    for _ in range(MAX_REMOVALS):  # ③ 移出得分最低的非必去景点
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
        if keep and conflicts(report):  # 换入的景点可能只有上午开，少一站后要再排一次才放得下
            reorder()
    return rebuilt, report, orders, removed, reordered, conflicts(report)


def _replanned(result: dict[str, Any], rebuilt, report, orders, route, catalog, note: str, seconds: float, operation: str, **fields) -> dict[str, Any]:
    profile = result["profile"]
    pois = load_pois()
    final_evidence = {}
    for planned in rebuilt.days:
        position = (profile.hotel.lat, profile.hotel.lon)
        with route.for_day(profile, planned.day):
            for stop in planned.items:
                place = pois.get(stop.ref_id) if stop.kind == "poi" else catalog.restaurants.get(stop.ref_id)
                if place is None:
                    continue
                destination, leave = stop_points(stop, place)
                if stop.travel_evidence:
                    final_evidence[route.edge_key(*position, *destination)] = stop.travel_evidence
                position = leave
            if planned.return_leg:
                final_evidence[route.edge_key(*position, profile.hotel.lat, profile.hotel.lon)] = planned.return_leg
    travel_stats = route.stats()
    travel_stats.update(final_legs=len(final_evidence), approx_legs=sum(leg.source == "estimate" for leg in final_evidence.values()))
    updated = dict(result)
    updated.update({"itinerary": rebuilt, "report": report, "day_orders": orders, "restaurants": catalog.restaurants,
                    "travel_stats": travel_stats, "log": [*result.get("log", []), note],
                    "seconds": seconds, "operation": operation,
                    "usage": {"calls": 0, "cached_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                              "total_tokens": 0, "seconds": 0.0}, **fields})
    return updated


def _reported_conflicts(report, day: int, conflicts: list[str]) -> list[str]:
    """提交时校验整份行程，其他日期原有的硬冲突也要写明，不然预览不能提交却看不出原因。"""
    return [*conflicts, *(f"第 {v.day} 天：{v.message}" for v in report.hard if v.day not in (day, None))]


def _edit_context(result: dict[str, Any], day: int):
    """换景点、调节奏共用：用户亲自选的餐次固定，其余餐次按新路线重选，不重复其他日期吃过的店。"""
    itinerary = result["itinerary"]
    user_meals = set(result.get("user_meals") or set())
    pins = {(d.day, i.meal): i.ref_id for d in itinerary.days for i in d.items
            if i.kind == "meal" and i.meal and i.ref_id is not None and (d.day != day or (day, i.meal) in user_meals)}
    used = {i.ref_id for d in itinerary.days if d.day != day for i in d.items if i.kind == "meal" and i.ref_id is not None}
    restaurants = dict(result.get("restaurants") or {})
    route = _route_for(result, restaurants)
    return pins, user_meals, used, route, RestaurantCatalog(route=route, initial=restaurants)


def _finish_poi_edit(result: dict[str, Any], day: int, started: float, outcome, route, catalog, selected: list[Selection], note: str,
                     operation: str, before: str | None, after: str | None, poi_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """换景点、调节奏共用：写明这一天变了什么，生成新行程。poi_id 是用户这次操作针对的景点。"""
    rebuilt, report, orders, removed, reordered, conflicts = outcome
    pois = load_pois()
    old_end, new_end = day_end(result["itinerary"].days[day - 1]), day_end(rebuilt.days[day - 1])
    change = {"day": day, "meal": None, "from": before, "to": after, "poi_id": poi_id,
              "shift_min": None if old_end is None or new_end is None else new_end - old_end,
              "removed": [pois[pid].name for pid in removed if pid in pois], "reordered": reordered,
              "passed": report.passed, "conflicts": _reported_conflicts(report, day, conflicts),
              "seconds": round(time.time() - started, 2), "live_calls": route.stats().get("live_calls", 0)}
    if reordered:
        note += "；重排了当天景点顺序"
    if change["removed"]:
        note += "；为保证时间可行移出 " + "、".join(change["removed"])
    updated = _replanned(result, rebuilt, report, orders, route, catalog, note, change["seconds"], operation, selected=selected,
                         dropped=[pid for pid in [*result.get("dropped", []), *removed] if pid != poi_id])
    return updated, change


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
    from .restaurants import valid_location
    if not valid_location(target):
        raise ReplanError(422, "餐厅坐标无效，不能计算新路线")
    restaurants[target.id] = target

    pins = {(d.day, i.meal): i.ref_id for d in itinerary.days for i in d.items
            if i.kind == "meal" and i.meal and i.ref_id is not None}
    pins[(day, item.meal)] = target.id
    user_meals = set(result.get("user_meals") or set()) | {(day, item.meal)}
    route = _route_for(result, restaurants)
    catalog = RestaurantCatalog(route=route, initial=restaurants, online=False)
    pois = load_pois()
    rebuilt, report, orders, removed, reordered, conflicts = _rebuild_day(
        result, day, _poi_orders(itinerary), result["selected"], pins, user_meals, route, catalog)

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
              "passed": report.passed, "conflicts": _reported_conflicts(report, day, conflicts),
              "seconds": round(time.time() - started, 2), "live_calls": route.stats().get("live_calls", 0)}
    note = f"用户把第 {day} 天{meal_name}从「{item.name}」换成「{target.name}」，重新核对当天路线和时间"
    if reordered:
        note += "；重排了当天景点顺序"
    if change["removed"]:
        note += "；为保证时间可行移出 " + "、".join(change["removed"])
    updated = _replanned(result, rebuilt, report, orders, route, catalog, note, change["seconds"], "meal_swap",
                         dropped=[*result.get("dropped", []), *removed], user_meals=user_meals)
    return updated, change


def _summary(place: POI) -> str:
    return "" if place.summary == "信息不足" else place.summary


def _poi_item(itinerary, day: int, item_index: int):
    if not 1 <= day <= len(itinerary.days):
        raise ReplanError(422, "这一天不在行程里")
    plan_day = itinerary.days[day - 1]
    if not 0 <= item_index < len(plan_day.items):
        raise ReplanError(422, "找不到要更换的这个景点")
    item = plan_day.items[item_index]
    if item.kind != "poi" or item.ref_id is None:
        raise ReplanError(422, "只能更换景点")
    if item.evening:
        raise ReplanError(422, "夜游是按当天结束的位置自动安排的，不能单独更换")
    return item


def _unavailable(candidate: Candidate, place: POI, result: dict[str, Any], day: int, replaced: int) -> str | None:
    """这个候选不能换到这一天的原因；能换返回 None。"""
    itinerary, profile = result["itinerary"], result["profile"]
    planned = {item.ref_id for d in itinerary.days for item in d.items if item.kind == "poi" and item.ref_id != replaced}
    if place.id in planned:
        return f"「{place.name}」已经在行程里"
    if umbrella_of(place.id) in {umbrella_of(identifier) for identifier in planned}:
        return f"「{place.name}」所在的景区已经在行程里"
    if not place.recommendable:
        return f"「{place.name}」目前不推荐安排"
    if closed_on(place, profile, day) or (candidate.available_days and day not in candidate.available_days):
        return f"「{place.name}」这一天不开放"
    others = [item for item in itinerary.days[day - 1].items if item.kind == "poi" and not item.evening and item.ref_id != replaced]
    if place.day_anchor and others:
        return f"「{place.name}」要玩一整天，这一天还有其他景点，不能只换其中一站"
    return None


def poi_options(result: dict[str, Any], day: int, item_index: int) -> list[dict[str, Any]]:
    """能换到这一站的候选：同片区、同类型优先，再按候选得分；不查路线，具体影响看预览。"""
    item = _poi_item(result["itinerary"], day, item_index)
    pois = load_pois()
    current = pois.get(item.ref_id)
    if current is None:
        raise ReplanError(404, "找不到这个景点的资料，请重新生成行程后再试")
    zones = area_index(pois)
    area = zones.area_of(current.id)
    options = []
    for candidate in result.get("candidates", []):
        place = pois.get(candidate.poi_id)
        if place is None or place.id == current.id or _unavailable(candidate, place, result, day, current.id):
            continue
        options.append({"poi_id": place.id, "name": place.name, "type": place.type, "area": zones.label(zones.area_of(place.id)),
                        "same_area": zones.area_of(place.id) == area, "same_type": place.type == current.type,
                        "indoor": place.indoor, "price": place.price, "price_verified": place.price_verified, "min_hours": place.min_hours, "max_hours": place.max_hours,
                        "open_min": place.open_min, "close_min": place.close_min,
                        "score": candidate.score, "distance_km": round(haversine_km(current.lat, current.lon, place.lat, place.lon), 1),
                        "summary": _summary(place)})
    options.sort(key=lambda option: (not option["same_area"], not option["same_type"], -option["score"], option["poi_id"]))
    return options[:MAX_POI_OPTIONS]


def swap_poi(result: dict[str, Any], day: int, item_index: int, poi_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    """把一站景点换成这份行程的另一个候选。其他日期不变；这一天的非用户指定餐次按新路线重选。"""
    started = time.time()
    itinerary = result["itinerary"]
    item = _poi_item(itinerary, day, item_index)
    if item.ref_id == poi_id:
        raise ReplanError(422, "已经是这个景点")
    if item.ref_id in _must_visit(result):
        raise ReplanError(422, f"「{item.name}」是你点名要去的，要换掉请修改需求后重新规划")
    candidate = next((c for c in result.get("candidates", []) if c.poi_id == poi_id), None)
    pois = load_pois()
    target = pois.get(poi_id)
    if candidate is None or target is None:
        raise ReplanError(422, "只能换成这份行程的候选景点")
    if reason := _unavailable(candidate, target, result, day, item.ref_id):
        raise ReplanError(422, reason)

    orders = _poi_orders(itinerary)
    orders[day - 1] = keep_walks_together([poi_id if pid == item.ref_id else pid for pid in orders[day - 1]], pois)
    selected = [s for s in result["selected"] if s.poi_id not in {item.ref_id, poi_id}]
    selected.append(Selection(poi_id=poi_id, reason=f"你换入的景点。{_summary(target)}".rstrip(), source="user"))
    pins, user_meals, used, route, catalog = _edit_context(result, day)
    outcome = _rebuild_day(result, day, orders, selected, pins, user_meals, route, catalog, keep={poi_id}, excluded={item.ref_id}, used=used)
    note = f"用户把第 {day} 天的「{item.name}」换成「{target.name}」，重新核对当天路线和时间"
    return _finish_poi_edit(result, day, started, outcome, route, catalog, selected, note, "poi_swap", item.name, target.name, poi_id)


def _one_fewer(result: dict[str, Any], day: int, started: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """去掉当天得分最低的非必去景点，用户亲自换入的留到最后。其余顺序不动，有冲突才重排。"""
    orders = _poi_orders(result["itinerary"])
    stops = orders[day - 1]
    if len(stops) <= 1:
        raise ReplanError(422, "这一天白天只有一个景点，不能再少了")
    removable = [pid for pid in stops if pid not in _must_visit(result)]
    if not removable:
        raise ReplanError(422, "这一天的景点都是你点名要去的，要减少请修改需求后重新规划")
    scores = {candidate.poi_id: candidate.score for candidate in result.get("candidates", [])}
    chosen = {s.poi_id for s in result["selected"] if s.source == "user"}
    target = min(removable, key=lambda pid: (pid in chosen, scores.get(pid, 0.0), pid))
    name = next(item.name for item in result["itinerary"].days[day - 1].items if item.kind == "poi" and item.ref_id == target)
    orders[day - 1] = [pid for pid in stops if pid != target]
    selected = [s for s in result["selected"] if s.poi_id != target]
    pins, user_meals, used, route, catalog = _edit_context(result, day)
    outcome = _rebuild_day(result, day, orders, selected, pins, user_meals, route, catalog, excluded={target}, used=used)
    note = f"用户让第 {day} 天少排一站，去掉「{name}」，重新核对当天路线和时间"
    return _finish_poi_edit(result, day, started, outcome, route, catalog, selected, note, "pace_adjust", name, None, target)


def _addable(result: dict[str, Any], day: int, stops: list[int], pois: dict[int, POI]) -> list[POI]:
    """能加到这一天的候选：同片区按得分，其他片区按离当天景点的远近。和选点补位一样，不加观景点、只开夜间的和超出体力的。"""
    itinerary, profile = result["itinerary"], result["profile"]
    zones = area_index(pois)
    areas = {zones.area_of(pid) for pid in stops}
    planned = {experience_key(pois[item.ref_id]) for planned_day in itinerary.days for item in planned_day.items
               if item.kind == "poi" and item.ref_id in pois}
    points = [(pois[pid].lat, pois[pid].lon) for pid in stops] or [(profile.hotel.lat, profile.hotel.lon)]
    ranked = []
    for candidate in result.get("candidates", []):
        place = pois.get(candidate.poi_id)
        if (place is None or experience_key(place) in planned or place.visit_role == "viewpoint" or evening_only(place)
                or place.stamina > max_stamina(profile) or _unavailable(candidate, place, result, day, None)):
            continue
        same_area = zones.area_of(place.id) in areas
        rank = -candidate.score if same_area else min(haversine_km(place.lat, place.lon, *point) for point in points)
        ranked.append(((not same_area, rank, place.id), place))
    return [place for _, place in sorted(ranked, key=lambda pair: pair[0])]


def _insert_cheapest(stops: list[int], place: POI, pois: dict[int, POI], hotel) -> list[int]:
    """按直线距离插到绕路最少的位置，已有景点的先后不变；同一段步行游览不拆开。"""
    points = [(hotel.lat, hotel.lon), *((pois[pid].lat, pois[pid].lon) for pid in stops), (hotel.lat, hotel.lon)]
    def detour(position: int) -> float:
        before, after = points[position], points[position + 1]
        return haversine_km(*before, place.lat, place.lon) + haversine_km(place.lat, place.lon, *after) - haversine_km(*before, *after)
    position = min(range(len(points) - 1), key=detour)
    return keep_walks_together([*stops[:position], place.id, *stops[position:]], {**pois, place.id: place})


def _one_more(result: dict[str, Any], day: int, started: float) -> tuple[dict[str, Any], dict[str, Any]]:
    """从候选挑一个加到这一天。当天已有景点（含夜游）都不挤掉，放不下就试下一个，都不行如实拒绝。"""
    itinerary, profile = result["itinerary"], result["profile"]
    plan_day = itinerary.days[day - 1]
    pois = load_pois()
    orders = _poi_orders(itinerary)
    stops = orders[day - 1]
    anchor = next((pois[pid].name for pid in stops if pid in pois and pois[pid].day_anchor), None)
    if anchor:
        raise ReplanError(422, f"「{anchor}」要玩一整天，这一天不能再加景点")
    choices = _addable(result, day, stops, pois)
    if not choices:
        raise ReplanError(422, "候选里没有能加到这一天的景点了")
    window = activity_window(profile, day)  # 先按空闲时间筛一遍，明显放不下的不用查路线
    free = window["end"] - window["start"] - plan_day.return_travel_min - sum(item.end - item.start + item.travel_min for item in plan_day.items)
    choices = [place for place in choices if service_min(place, profile.pace) <= free]
    if not choices:
        raise ReplanError(422, f"这一天只剩约 {max(free, 0)} 分钟空闲，再加一站就排不下了，原行程保持不变")
    pins, user_meals, used, route, catalog = _edit_context(result, day)
    existing = {item.ref_id: item.name for item in plan_day.items if item.kind == "poi"}
    tried: list[str] = []
    first_conflict = ""
    for place in choices[:MAX_PACE_TRIES]:
        trial = [list(order) for order in orders]
        trial[day - 1] = _insert_cheapest(stops, place, pois, profile.hotel)
        reason = f"你让这一天多排一站，按片区和候选得分选入。{_summary(place)}".rstrip()
        selected = [s for s in result["selected"] if s.poi_id != place.id]
        selected.append(Selection(poi_id=place.id, reason=reason, source="rule"))
        overrides = set(route.taxi_overrides)  # 这次试的改打车不带到下一个候选
        outcome = _rebuild_day(result, day, trial, selected, pins, user_meals, route, catalog, keep={*stops, place.id}, used=used)
        kept = {item.ref_id for item in outcome[0].days[day - 1].items if item.kind == "poi"}
        conflicts = outcome[5]
        squeezed = [name for pid, name in existing.items() if pid not in kept]
        if not conflicts and not squeezed and place.id in kept:
            note = f"用户让第 {day} 天多排一站，加入「{place.name}」，重新核对当天路线和时间"
            return _finish_poi_edit(result, day, started, outcome, route, catalog, selected, note, "pace_adjust", None, place.name, place.id)
        route.taxi_overrides = overrides
        tried.append(place.name)
        first_conflict = first_conflict or (conflicts[0] if conflicts else f"会挤掉{'、'.join(squeezed)}" if squeezed else "")
    raise ReplanError(422, f"这一天再加一站就排不下了，原行程保持不变。试过{'、'.join(tried)}" + (f"：{first_conflict}" if first_conflict else ""))


def adjust_pace(result: dict[str, Any], day: int, direction: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """当天少排（less）或多排（more）一站，其他日期不变。"""
    started = time.time()
    if not 1 <= day <= len(result["itinerary"].days):
        raise ReplanError(422, "这一天不在行程里")
    return _one_fewer(result, day, started) if direction == "less" else _one_more(result, day, started)
