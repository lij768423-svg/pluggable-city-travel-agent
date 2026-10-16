"""调度求解（确定性，LLM 不参与排时间——AGENTS.md 规则 5）。

1. 按天分组：KMeans 提供初始方案，再按时长、交通和餐饮比较完整分天方案。
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
from sklearn.cluster import KMeans

from ..data import load_pois
from ..experiences import experience_key, keep_walks_together
from ..rules import DINNER, LUNCH, MEAL_MIN, PACE, activity_window, required_meals
from ..calendar import closed_on
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
    units: dict[str, list[int]] = {}
    for identifier in ids:
        units.setdefault(experience_key(pois[identifier]), []).append(identifier)
    members = list(units.values())
    lat0 = math.radians(p.hotel.lat)
    X = np.array([[np.mean([pois[identifier].lat for identifier in group]),
                   np.mean([pois[identifier].lon for identifier in group]) * math.cos(lat0)] for group in members])
    k = min(D, len(members))
    km = KMeans(n_clusters=k, n_init=10, random_state=0).fit(X)
    dist = km.transform(X)
    cap = math.ceil(len(members) / D)
    assigned: dict[int, int] = {}
    counts = [0] * k
    for d, i, c in sorted((dist[i, c], i, c) for i in range(len(members)) for c in range(k)):
        if i in assigned or counts[c] >= cap:
            continue
        assigned[i] = c
        counts[c] += 1
    group_indices = [[index for index in range(len(members)) if assigned[index] == cluster] for cluster in range(k)]
    hotel = np.array([p.hotel.lat, p.hotel.lon * math.cos(lat0)])
    group_indices.sort(key=lambda group: float(np.linalg.norm(X[group].mean(axis=0) - hotel)))
    groups = [[identifier for index in group for identifier in members[index]] for group in group_indices]
    return groups + [[] for _ in range(D - k)]


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
    return pois, T, svc


def route_ortools(day_ids: list[int], p: UserProfile, scores: dict[int, float], travel,
                  time_limit_s: int = 1, must: set[int] | None = None, day: int = 1) -> tuple[list[int], list[int]] | None:
    """返回 (访问顺序, 被舍弃的景点)；无解返回 None。"""
    if not day_ids:
        return [], []
    pois, T, svc = _nodes(day_ids, p, travel)
    c = activity_window(p, day)
    n = len(T)
    manager = pywrapcp.RoutingIndexManager(n, 1, 0)
    routing = pywrapcp.RoutingModel(manager)

    def transit(fi: int, ti: int) -> int:
        i, j = manager.IndexToNode(fi), manager.IndexToNode(ti)
        return T[i][j] + svc[i]

    cb = routing.RegisterTransitCallback(transit)
    comfort_weight = 3 if p.companions in {"elderly", "family_kids"} or p.pace == "relaxed" else 2
    cost_callback = routing.RegisterTransitCallback(
        lambda before, after: T[manager.IndexToNode(before)][manager.IndexToNode(after)] * comfort_weight)
    routing.SetArcCostEvaluatorOfAllVehicles(cost_callback)
    routing.AddDimension(cb, 24 * 60, 24 * 60, False, "Time")
    tdim = routing.GetDimensionOrDie("Time")
    tdim.CumulVar(routing.Start(0)).SetRange(c["start"], c["start"] + 120)
    tdim.CumulVar(routing.End(0)).SetRange(c["start"], c["end"])
    tdim.SetSpanCostCoefficientForVehicle(1, 0)
    core = max(day_ids, key=lambda identifier: (scores.get(identifier, 0.0),
                                               service_min(pois[identifier], p.pace), -identifier))
    for k, pid in enumerate(day_ids, start=1):
        idx = manager.NodeToIndex(k)
        poi = pois[pid]
        lo, hi = max(poi.open_min, c["start"]), min(poi.close_min, c["end"]) - svc[k]
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


def route_greedy(day_ids: list[int], p: UserProfile, travel) -> list[int]:
    """兜底：最近邻 + 2-opt（只优化路程，不保证时间窗，交给校验/修复）。"""
    if len(day_ids) <= 1:
        return list(day_ids)
    _, T, _ = _nodes(day_ids, p, travel)
    rest, route, cur = list(range(1, len(T))), [], 0
    while rest:
        nxt = min(rest, key=lambda j: T[cur][j])
        route.append(nxt)
        rest.remove(nxt)
        cur = nxt

    def length(r: list[int]) -> int:
        path = [0] + r + [0]
        return sum(T[a][b] for a, b in zip(path, path[1:]))

    improved = True
    while improved:
        improved = False
        for i in range(len(route) - 1):
            for j in range(i + 1, len(route)):
                cand = route[:i] + route[i:j + 1][::-1] + route[j + 1:]
                if length(cand) < length(route):
                    route, improved = cand, True
    return keep_walks_together([day_ids[k - 1] for k in route], load_pois())


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
        log.append("大型园区单独安排一天，取消跨区拼接：" + "、".join(load_pois()[identifier].name for identifier in dropped))
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
            orders.append(route_greedy(g, p, travel))
            if g:
                log.append(f"第 {d} 天：OR-Tools 无解或未启用，退回贪心 + 2-opt")
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
    return {"day_orders": orders, "dropped": dropped,
            "log": [f"调度：{len(ids)} 个景点按时长、交通与用餐余量分到 {p.days} 天，日内用 OR-Tools VRPTW 排序"] + log}
