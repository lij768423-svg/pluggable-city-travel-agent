"""调度求解（确定性，LLM 不参与排时间——AGENTS.md 规则 5）。

1. 按天分组：以片区为单位给每天定一个主片区、至多挂一个相邻片区（app/areas.py），再按时长、交通和餐饮比较完整分天方案。
2. 日内排序：OR-Tools VRPTW（单车辆，起终点为酒店）。
   - 时间窗：景点开放时间（须在关门前游玩完）；整天在该节奏的活动时间窗内。
   - 用餐：各节奏用 break interval 预留午餐与晚餐各 75 分钟（就餐 60 + 绕行 15）。
   - 普通候选允许因交通或时段舍弃；必去与每日核心点优先保护。
3. 求解失败时退回"最近邻 + 2-opt"。
精确时间线（含具体餐厅）由 meals 节点仿真生成，再交给 validate 校验。
"""
from __future__ import annotations

import math

import numpy as np
from ortools.constraint_solver import pywrapcp, routing_enums_pb2

from ..areas import area_index, area_transition_cost
from ..data import load_pois
from ..experiences import experience_key, keep_walks_together
from ..rules import (
    DINNER,
    LUNCH,
    LOCALITY_PREFERENCE_MIN,
    MAX_INTERSTOP_RIDE_MIN,
    MEAL_MIN,
    PACE,
    activity_window,
    interstop_ride_allowed,
    locality_transition_penalty,
    required_meals,
)
from ..calendar import closed_on
from ..poi_semantics import evening_only, visit_open_min
from ..schemas import POI, UserProfile

MEAL_BLOCK = MEAL_MIN + 15


def service_min(poi: POI, pace: str) -> int:
    lo, hi = poi.min_hours * 60, poi.max_hours * 60
    # 轻松：建议时长的中点；适中：下限 + 25%；紧凑：下限（数据集建议时长普遍偏长，如鼓楼 2–3.5 h）
    v = {"relaxed": (lo + hi) / 2, "normal": lo + 0.25 * (hi - lo), "intense": lo}[pace]
    if poi.day_anchor:
        v = (lo + hi) / 2
    minimum = 15 if poi.visit_role in {"viewpoint", "walk_segment"} else 30
    return max(minimum, int(round(v / 15) * 15))


def fit_service_min(poi: POI, pace: str, start: int, latest_end: int) -> int:
    duration = service_min(poi, pace)
    available = min(poi.close_min, latest_end) - start
    minimum = max(15, math.ceil(poi.min_hours * 60))
    if minimum <= available < duration:
        return max(minimum, available // 15 * 15)
    return duration


def _seed_days(ids: list[int], p: UserProfile) -> list[list[int]]:
    pois = load_pois()
    D = p.days
    if not ids:
        return [[] for _ in range(D)]
    evening = [identifier for identifier in ids if evening_only(pois[identifier])]
    if evening:
        # 先给白天分主片区，再把晚间项目挂过去，不能让夜市占用一个完整白天。
        daytime = [identifier for identifier in ids if identifier not in evening]
        return _attach_evenings(_seed_days(daytime, p), evening, p)
    anchors = [identifier for identifier in ids if pois[identifier].day_anchor]
    if anchors:
        from .retrieve import must_visit_ids
        required = must_visit_ids(p)
        anchors.sort(key=lambda identifier: identifier not in required)
        groups = [[identifier] for identifier in anchors[:D]]
        remaining = [identifier for identifier in ids if identifier not in anchors[:D]]
        if len(groups) < D:
            groups.extend(_seed_days(remaining, p.model_copy(update={"days": D - len(groups)})))
        else:
            for identifier in remaining:
                if identifier in required:
                    groups[-1].append(identifier)
        return groups
    if D == 1 or len(ids) == 1:
        return [list(ids)] + [[] for _ in range(D - 1)]
    return _area_days(ids, p)


def _attach_evenings(groups: list[list[int]], identifiers: list[int], p: UserProfile) -> list[list[int]]:
    pois = load_pois()
    areas = area_index(pois)
    home = areas.home_areas(p.hotel.lat, p.hotel.lon)
    groups = [list(group) for group in groups]
    from .retrieve import must_visit_ids
    required = must_visit_ids(p)
    daytime_zones = [areas.areas_of(group) for group in groups]
    for identifier in identifiers:
        zone = areas.area_of(identifier)

        def rank(index: int) -> tuple:
            zones = daytime_zones[index]
            compatible = any(areas.compatible(zone, other, home) for other in zones)
            return (closed_on(pois[identifier], p, index + 1),
                    not bool(zones), zone not in zones, not compatible,
                    sum(service_min(pois[i], p.pace) for i in groups[index]), index)

        available = [index for index, group in enumerate(groups)
                     if not any(pois[i].day_anchor for i in group)
                     and (not daytime_zones[index] or any(areas.compatible(zone, other, home)
                                                         for other in daytime_zones[index]))]
        if not available:
            if identifier not in required:
                continue  # 不用可选夜游打断整日主项目，也不为夜游拼接不顺路片区。
            available = list(range(len(groups)))  # 必去冲突仍交统一校验，不能悄悄丢掉。
        groups[min(available, key=rank)].append(identifier)
    return groups


def day_capacity(p: UserProfile, day: int) -> int:
    """一天能用于游览的分钟数：活动时段扣掉正餐和约一小时路上时间。"""
    window = activity_window(p, day)
    return max(120, window["end"] - window["start"] - len(required_meals(p, day)) * MEAL_BLOCK - 60)


def _chunks(members: list[int], p: UserProfile, capacity: int, parts: int | None = None) -> list[list[int]]:
    """同一片区按体验单元就近串起来再切块：超过一天容量时切开，或指定切成 parts 份。"""
    pois = load_pois()
    blocks: dict[str, list[int]] = {}
    for identifier in members:
        blocks.setdefault(experience_key(pois[identifier]), []).append(identifier)
    units = list(blocks.values())

    def centre(block: list[int]) -> tuple[float, float]:
        return float(np.mean([pois[i].lat for i in block])), float(np.mean([pois[i].lon for i in block]))

    def load(block: list[int]) -> int:
        return sum(service_min(pois[i], p.pace) for i in block)

    path = [units.pop(0)]
    while units:
        last = centre(path[-1])
        units.sort(key=lambda block: (math.dist(last, centre(block)), min(block)))
        path.append(units.pop(0))
    limit = capacity if parts is None else sum(load(block) for block in path) / parts
    chunks: list[list[int]] = []
    current: list[int] = []
    used = 0
    for block in path:
        if current and used + load(block) > limit and (parts is None or len(chunks) < parts - 1):
            chunks.append(current)
            current, used = [], 0
        current.extend(block)
        used += load(block)
    chunks.append(current)
    return chunks


def _area_days(ids: list[int], p: UserProfile) -> list[list[int]]:
    """片区为主的初始分天（2026-10-19 用户决定）：每天一个主片区，至多再挂一个相邻片区。

    只决定搜索起点；之后的分天均衡、日内求解和统一校验照常运行，不因片区丢必去点。
    """
    pois = load_pois()
    areas = area_index(pois)
    home = areas.home_areas(p.hotel.lat, p.hotel.lon)
    D = p.days
    capacity = max(day_capacity(p, day) for day in range(1, D + 1))

    def load(unit: tuple[str, list[int]]) -> int:
        return sum(service_min(pois[i], p.pace) for i in unit[1])

    by_area: dict[str, list[int]] = {}
    for identifier in ids:
        by_area.setdefault(areas.area_of(identifier), []).append(identifier)
    units = [(area, chunk) for area, members in by_area.items() for chunk in _chunks(members, p, capacity)]
    # 片区比天数少时把最大的片区再切开，避免出现空白天
    while len(units) < D:
        splittable = [unit for unit in units if len({experience_key(pois[i]) for i in unit[1]}) > 1]
        if not splittable:
            break
        largest = max(splittable, key=lambda unit: (load(unit), -min(unit[1])))
        units.remove(largest)
        units.extend((largest[0], chunk) for chunk in _chunks(largest[1], p, capacity, parts=2))
    units.sort(key=lambda unit: (-load(unit), min(unit[1])))
    days = [[unit] for unit in units[:D]]
    for unit in units[D:]:
        loads = [sum(load(entry) for entry in day) for day in days]

        def rank(index: int) -> tuple:
            main = days[index][0][0]
            near = main != unit[0] and areas.compatible(main, unit[0], home)
            return (not near, len(days[index]) > 1, loads[index] + load(unit) > capacity, loads[index], index)
        days[min(range(len(days)), key=rank)].append(unit)
    hotel = (p.hotel.lat, p.hotel.lon)

    def distance(day: list[tuple[str, list[int]]]) -> float:
        members = [i for unit in day for i in unit[1]]
        return math.dist(hotel, (float(np.mean([pois[i].lat for i in members])), float(np.mean([pois[i].lon for i in members]))))
    days.sort(key=distance)
    groups = [[identifier for unit in day for identifier in unit[1]] for day in days]
    return groups + [[] for _ in range(D - len(groups))]


def split_days(ids: list[int], p: UserProfile, travel=None) -> list[list[int]]:
    from ..planning_quality import balance_days
    from ..travel import Travel

    groups = _seed_days(ids, p)
    if p.days < 2 or len(ids) < 2:
        return groups
    return balance_days(groups, p, travel or Travel("estimate", p.transport))


def _nodes(day_ids: list[int], p: UserProfile, travel):
    pois = load_pois()
    locs = [(p.hotel.lat, p.hotel.lon)] + [(pois[i].lat, pois[i].lon) for i in day_ids]
    T = travel.matrix(locs)  # 打车模式为高德真实矩阵；公交模式为缓存真实值 + 标定兜底
    svc = [0] + [service_min(pois[i], p.pace) for i in day_ids]
    allowed = [[True for _ in locs] for _ in locs]
    locality = [[0 for _ in locs] for _ in locs]
    for before in range(1, len(locs)):
        for after in range(1, len(locs)):
            if before != after and T[before][after] > LOCALITY_PREFERENCE_MIN:
                mode = travel.leg(*locs[before], *locs[after], live=False)[1]
                locality[before][after] = locality_transition_penalty(T[before][after], mode)
                if T[before][after] > MAX_INTERSTOP_RIDE_MIN:
                    allowed[before][after] = interstop_ride_allowed(T[before][after], mode)
    # 片区为主：景点之间跨片区再加成本，不相邻片区更高；与起点相连的首尾段不加
    areas = area_index(pois)
    home = areas.home_areas(p.hotel.lat, p.hotel.lon)
    zones = [areas.area_of(identifier) for identifier in day_ids]
    for before in range(1, len(locs)):
        for after in range(1, len(locs)):
            if before != after:
                locality[before][after] += area_transition_cost(zones[before - 1], zones[after - 1], areas, home)
    return pois, T, svc, allowed, locality


def route_ortools(day_ids: list[int], p: UserProfile, scores: dict[int, float], travel,
                  time_limit_s: int = 1, must: set[int] | None = None, day: int = 1) -> tuple[list[int], list[int]] | None:
    """返回 (访问顺序, 被舍弃的景点)；无解返回 None。"""
    if not day_ids:
        return [], []
    pois, T, svc, allowed, locality = _nodes(day_ids, p, travel)
    c = activity_window(p, day)
    n = len(T)
    manager = pywrapcp.RoutingIndexManager(n, 1, 0)
    routing = pywrapcp.RoutingModel(manager)
    for before in range(1, n):
        for after in range(1, n):
            if not allowed[before][after]:
                routing.NextVar(manager.NodeToIndex(before)).RemoveValue(manager.NodeToIndex(after))

    def transit(fi: int, ti: int) -> int:
        i, j = manager.IndexToNode(fi), manager.IndexToNode(ti)
        return T[i][j] + svc[i]

    cb = routing.RegisterTransitCallback(transit)
    comfort_weight = 3 if p.companions in {"elderly", "family_kids"} or p.pace == "relaxed" else 2
    cost_callback = routing.RegisterTransitCallback(
        lambda before, after: (
            T[manager.IndexToNode(before)][manager.IndexToNode(after)] * comfort_weight
            + locality[manager.IndexToNode(before)][manager.IndexToNode(after)]
        ))
    routing.SetArcCostEvaluatorOfAllVehicles(cost_callback)
    routing.AddDimension(cb, 24 * 60, 24 * 60, False, "Time")
    tdim = routing.GetDimensionOrDie("Time")
    tdim.CumulVar(routing.Start(0)).SetRange(c["start"], c["start"] + 120)
    tdim.CumulVar(routing.End(0)).SetRange(c["start"], c["end"])
    tdim.SetSpanCostCoefficientForVehicle(1, 0)
    core = max([identifier for identifier in day_ids if not evening_only(pois[identifier])] or day_ids, key=lambda identifier: (scores.get(identifier, 0.0),
                                               service_min(pois[identifier], p.pace), -identifier))
    for k, pid in enumerate(day_ids, start=1):
        idx = manager.NodeToIndex(k)
        poi = pois[pid]
        lo, hi = max(visit_open_min(poi), c["start"]), min(poi.close_min, c["end"]) - svc[k]
        if poi.opening_rules and poi.opening_rules.last_entry is not None:
            hi = min(hi, poi.opening_rules.last_entry)
        if hi < lo or closed_on(poi, p, day):
            routing.ActiveVar(idx).SetValue(0)
        tdim.CumulVar(idx).SetRange(lo, max(lo, hi))
        # 时间维度的跨度成本本意是压缩等待，但也把游玩时长本身算成了成本；把游玩时长加回丢弃惩罚，
        # 否则路程一长（如高德公交），越值得多玩的景点越容易被整段丢掉，留下大段空档
        penalty = 180 + 90 * min(3.0, max(0.0, scores.get(pid, 0.0))) + svc[k]
        if poi.day_anchor:
            penalty += 600
        if pid == core:
            penalty += 800
        routing.AddDisjunction([idx], 1_000_000 if must and pid in must else int(penalty))

    solver = routing.solver()
    walk_groups: dict[str, list[int]] = {}
    for node_index, identifier in enumerate(day_ids, start=1):
        if pois[identifier].visit_group:
            walk_groups.setdefault(pois[identifier].visit_group, []).append(manager.NodeToIndex(node_index))
    for indices in walk_groups.values():
        if len(indices) > 1:
            active = [routing.ActiveVar(index) for index in indices]
            internal = [solver.IsEqualCstVar(routing.NextVar(origin), destination)
                        for origin in indices for destination in indices if origin != destination]
            solver.Add(solver.Sum(internal) == solver.Sum(active) - solver.Max(active))
    breaks = []
    for name in ([] if any(pois[identifier].onsite_dining for identifier in day_ids) else required_meals(p, day)):
        bounds = LUNCH if name == "lunch" else DINNER
        earliest, latest = max(bounds[0], c["start"]), min(bounds[1], c["end"] - MEAL_BLOCK)
        if earliest <= latest:
            breaks.append(solver.FixedDurationIntervalVar(earliest, latest, MEAL_BLOCK, False, name))
    visit_transit = [svc[manager.IndexToNode(i)] for i in range(routing.Size())]
    tdim.SetBreakIntervalsOfVehicle(breaks, 0, visit_transit)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = routing_enums_pb2.FirstSolutionStrategy.PATH_CHEAPEST_ARC
    params.local_search_metaheuristic = routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.seconds = time_limit_s
    sol = routing.SolveWithParameters(params)
    if sol is None:
        return None
    order: list[int] = []
    idx = sol.Value(routing.NextVar(routing.Start(0)))
    while not routing.IsEnd(idx):
        order.append(day_ids[manager.IndexToNode(idx) - 1])
        idx = sol.Value(routing.NextVar(idx))
    dropped = [pid for pid in day_ids if pid not in order]
    return order, dropped


def route_greedy(day_ids: list[int], p: UserProfile, travel, must: set[int] | None = None, day: int = 1) -> list[int]:
    """兜底：最近邻 + 2-opt（只优化路程，不保证时间窗，交给校验/修复）。"""
    if len(day_ids) <= 1:
        return list(day_ids)
    pois, T, _, allowed, locality = _nodes(day_ids, p, travel)

    def nearest_route(first: int) -> list[int]:
        remaining = [node for node in range(1, len(T)) if node != first]
        ordered = [first]
        while remaining:
            reachable = [node for node in remaining if allowed[ordered[-1]][node]]
            if not reachable:
                break
            following = min(reachable, key=lambda node: (
                evening_only(pois[day_ids[node - 1]]), T[ordered[-1]][node] + locality[ordered[-1]][node], node))
            ordered.append(following)
            remaining.remove(following)
        return ordered

    def length(r: list[int]) -> int:
        path = [0] + r + [0]
        # 夜间型移到白天项目之后，避免兜底顺序先等到17:00、再把白天项目挤闭馆。
        late_first = sum(evening_only(pois[day_ids[a - 1]]) and not evening_only(pois[day_ids[b - 1]])
                         for a, b in zip(r, r[1:]))
        return sum(T[a][b] + locality[a][b] for a, b in zip(path, path[1:])) + late_first * 10000

    route = max((nearest_route(first) for first in range(1, len(T))),
                key=lambda candidate: (sum(day_ids[node - 1] in (must or set()) for node in candidate),
                                       len(candidate), -length(candidate)))
    improved = True
    while improved:
        improved = False
        for i in range(len(route) - 1):
            for j in range(i + 1, len(route)):
                cand = route[:i] + route[i:j + 1][::-1] + route[j + 1:]
                if all(allowed[before][after] for before, after in zip(cand, cand[1:])) and length(cand) < length(route):
                    route, improved = cand, True
    identifiers = [day_ids[node - 1] for node in route]
    grouped = keep_walks_together(identifiers, load_pois())
    indices = {identifier: index + 1 for index, identifier in enumerate(day_ids)}
    return grouped if all(allowed[indices[before]][indices[after]] for before, after in zip(grouped, grouped[1:])) else identifiers


def schedule(selected_ids: list[int], p: UserProfile, scores: dict[int, float], travel,
             use_solver: bool = True, must: set[int] | None = None) -> tuple[list[list[int]], list[int], list[str]]:
    from ..planning_quality import CachedRoute, quality_key
    from .meals import build_itinerary
    from .validate import validate_itinerary

    groups = split_days(selected_ids, p, travel)
    result = _schedule_groups(groups, selected_ids, p, scores, travel, use_solver, must)
    seed = _seed_days(selected_ids, p)
    if [sorted(group) for group in groups] == [sorted(group) for group in seed]:
        return result
    alternative = _schedule_groups(seed, selected_ids, p, scores, CachedRoute(travel), use_solver, must)

    def key(proposal):
        itinerary = build_itinerary(proposal[0], p, [], route=travel, live=False)
        report = validate_itinerary(itinerary, p, travel_fn=CachedRoute(travel).leg)
        return quality_key(itinerary, report)

    if key(alternative) < key(result):
        result = alternative
        result[2].append("分天比较：含餐饮的完整时间线更适合原片区分组，保留该方案")
    else:
        result[2].append("分天比较：通过含餐饮时间线比较，采用时长与交通均衡方案")
    return result


def _schedule_groups(groups: list[list[int]], selected_ids: list[int], p: UserProfile,
                     scores: dict[int, float], travel, use_solver: bool,
                     must: set[int] | None) -> tuple[list[list[int]], list[int], list[str]]:
    groups = [list(group) for group in groups]
    assigned = {identifier for group in groups for identifier in group}
    orders, dropped, log = [], [identifier for identifier in selected_ids if identifier not in assigned], []
    if dropped:
        log.append("按片区与主项目分天后，取消不顺路的可选地点：" + "、".join(load_pois()[identifier].name for identifier in dropped))
    unavailable = []
    for day_index, group in enumerate(groups):
        for place_id in list(group):
            if closed_on(load_pois()[place_id], p, day_index + 1):
                group.remove(place_id)
                unavailable.append(place_id)
    for place_id in unavailable:
        valid_days = [index for index in range(p.days) if not closed_on(load_pois()[place_id], p, index + 1)]
        if valid_days:
            groups[min(valid_days, key=lambda index: len(groups[index]))].append(place_id)
        else:
            dropped.append(place_id)
    for d, g in enumerate(groups, start=1):
        travel.set_day(p, d)
        res = route_ortools(g, p, scores, travel, must=must, day=d) if use_solver else None
        if res is None:
            order = route_greedy(g, p, travel, must=must, day=d + 1)
            orders.append(order)
            dropped.extend(identifier for identifier in g if identifier not in order)
            if g:
                log.append(f"第 {d} 天：OR-Tools 无解或未启用，退回遵守单段乘车上限的贪心 + 2-opt")
        else:
            order, drop = res
            orders.append(order)
            dropped.extend(drop)
            if drop:
                names = "、".join(load_pois()[x].name for x in drop)
                log.append(f"第 {d} 天：综合时段与交通负担，未安排 {names}")
    return orders, dropped, log


def schedule_node(state: dict) -> dict:
    p = state["profile"]
    scores = {c.poi_id: c.score for c in state["candidates"]}
    ids = [s.poi_id for s in state["selected"]]
    must = {c.poi_id for c in state["candidates"] if c.breakdown.get("must_visit")}
    orders, dropped, log = schedule(ids, p, scores, state["travel"], use_solver=state.get("use_solver", True), must=must)
    scheduled_ids = [identifier for order in orders for identifier in order]
    absent = sorted(set(ids) - set(scheduled_ids))
    return {"day_orders": orders, "dropped": dropped,
            "schedule_attempts": [{"repair_round": state.get("repair_round", 0), "selected_ids": ids,
                "scheduled_ids": scheduled_ids, "dropped_ids": absent,
                "drop_rate": len(absent) / len(set(ids)) if ids else None}],
            "log": [f"调度：{len(ids)} 个景点按时长、交通与用餐余量分到 {p.days} 天，日内用 OR-Tools VRPTW 排序"] + log}
