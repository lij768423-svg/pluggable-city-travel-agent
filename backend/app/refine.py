"""合理性精修：校验结束后，按合理性指标找出的问题逐条尝试修改，确有改善才采纳（默认关闭）。

问题 → 动作（都不调用大模型，时间仍由确定性排程与同一校验器决定）：
- 为吃饭多绕路          → 这一餐换成备选餐厅里绕路最少的几家之一，其余餐次固定
- 出门过久 / 体验过多   → 当天删一个非必去景点（优先绕路最大的）
- 乘车占比高 / 顺序折返 → 同上
- 人群或体力不符        → 删掉该景点
- 景点间空档 / 提前收工 → 先试调整当天顺序（如傍晚开门的夜市移到最后），再从候选里补同片区景点
每个动作重建时间线后复核：硬约束不新增、缺餐不增加、必去仍在，且合理性综合分（不含停留维度，
避免与排程时长同源）至少提高 MIN_GAIN 才采纳；试算只读路线缓存，采纳前用真实路线重建再复核。
"""
from __future__ import annotations

import itertools
import re

from .data import load_pois, load_restaurants
from .evening import add_evening_visits
from .experiences import experience_key, keep_walks_together, walk_boundary
from .geo import haversine_km
from .nodes.meals import build_itinerary
from .nodes.retrieve import max_stamina
from .nodes.validate import validate_itinerary
from .reasonableness import LABELS, score_itinerary
from .schemas import Itinerary, Selection

MIN_GAIN = 1.0      # 综合分至少提高的分数
MAX_STEPS = 6       # 最多采纳几次修改
MAX_TRIALS = 48     # 每轮最多试算几个动作
CONFIRM_TRIES = 3   # 每轮最多用真实路线复核几个候选


class _CacheOnly:
    """试算用：所有路线查询只读缓存，不联网（夜游补点等没有 live 参数的调用也走这里）。"""

    def __init__(self, route):
        self._route = route

    def leg(self, *coordinates, live: bool = False):
        return self._route.leg(*coordinates, live=False)

    def detail(self, *coordinates, live: bool = False):
        return self._route.detail(*coordinates, live=False)

    def __getattr__(self, name):
        return getattr(self._route, name)


def _meal_pins(itinerary: Itinerary) -> dict[tuple[int, str], int]:
    return {(day.day, item.meal): item.ref_id for day in itinerary.days for item in day.items
            if item.kind == "meal" and item.meal and item.ref_id is not None}


def _missing(report) -> set[tuple[int | None, str]]:
    return {(item.day, item.code) for item in report.violations if item.code.startswith("missing_")}


def refine(state: dict) -> dict:
    profile, route, catalog = state["profile"], state.get("travel"), state.get("catalog")
    factor = state.get("meal_factor", 1.0)
    pois = load_pois()
    restaurants = catalog.restaurants if catalog else load_restaurants()
    must = {candidate.poi_id for candidate in state.get("candidates", []) if candidate.breakdown.get("must_visit")}
    scores = {candidate.poi_id: candidate.score for candidate in state.get("candidates", [])}
    hotel = (profile.hotel.lat, profile.hotel.lon)

    def build(orders, selected, pins, live: bool) -> Itinerary:
        itinerary = build_itinerary(orders, profile, selected, factor, route, live=live, catalog=catalog, pins=pins)
        return add_evening_visits(itinerary, profile, route if live or route is None else _CacheOnly(route), catalog)[0]

    def check(itinerary: Itinerary, live: bool):
        travel_fn = None if route is None else route.leg if live else _CacheOnly(route).leg
        return validate_itinerary(itinerary, profile, travel_fn=travel_fn, restaurants=restaurants)

    def grade(itinerary: Itinerary) -> dict:
        return score_itinerary(itinerary, profile, pois, restaurants)

    def acceptable(report, base_report) -> bool:
        return ({item.code for item in report.hard} <= {item.code for item in base_report.hard}
                and _missing(report) <= _missing(base_report)
                and not any(item.code == "excessive_transport" for item in report.violations
                            if item.day not in {old.day for old in base_report.violations if old.code == "excessive_transport"}))

    orders = [list(order) for order in state["day_orders"]]
    selected = list(state["selected"])
    itinerary, report = state["itinerary"], state["report"]
    log: list[str] = []
    notes: list[str] = []
    failed: set[str] = set()
    debug = state.get("refine_debug")
    before = grade(itinerary)
    current = before

    def proposals() -> list[tuple[str, list[list[int]], list[Selection], dict]]:
        found, seen = [], set()
        pins = _meal_pins(itinerary)
        chosen = {item.ref_id for day in itinerary.days for item in day.items if item.kind == "poi"}

        def remove(day: int, identifier: int, why: str) -> None:
            if identifier in must or identifier not in orders[day - 1] or ("remove", identifier) in seen:
                return
            seen.add(("remove", identifier))
            trial = [list(order) for order in orders]
            trial[day - 1].remove(identifier)
            found.append((f"第{day}天{why}：移除「{pois[identifier].name}」", trial,
                          [item for item in selected if item.poi_id != identifier], {}))

        def detour(day: int, identifier: int) -> float:
            order = orders[day - 1]
            index = order.index(identifier)
            origin = hotel if index == 0 else (pois[order[index - 1]].lat, pois[order[index - 1]].lon)
            target = hotel if index + 1 == len(order) else (pois[order[index + 1]].lat, pois[order[index + 1]].lon)
            place = pois[identifier]
            return haversine_km(*origin, place.lat, place.lon) + haversine_km(place.lat, place.lon, *target) - haversine_km(*origin, *target)

        for issue in current["issues"]:
            day, name, message = issue["day"], issue["item"], issue["message"]
            if day is None or day > len(orders):
                continue
            plan_day = itinerary.days[day - 1]
            if issue["dimension"] == "transport" and message.startswith("为吃饭多绕"):
                meal = next((item for item in plan_day.items if item.kind == "meal" and item.name == name), None)
                if meal is None or not meal.meal:
                    continue
                here = next((index for index, item in enumerate(plan_day.items) if item is meal), 0)
                around = [plan_day.items[index] for index in (here - 1, here + 1) if 0 <= index < len(plan_day.items)]
                points = [(pois[item.ref_id].lat, pois[item.ref_id].lon) for item in around if item.kind == "poi" and item.ref_id in pois]
                options = [option for option in meal.alternatives if option.ref_id in restaurants and option.ref_id != meal.ref_id]
                options.sort(key=lambda option: sum(haversine_km(restaurants[option.ref_id].lat, restaurants[option.ref_id].lon, *point) for point in points))
                for option in options[:3]:
                    found.append((f"第{day}天{'午' if meal.meal == 'lunch' else '晚'}餐绕路：改为「{option.name}」",
                                  [list(order) for order in orders], list(selected), {**pins, (day, meal.meal): option.ref_id}))
                continue
            if issue["dimension"] == "fit" and name:
                identifier = next((item.ref_id for item in plan_day.items if item.kind == "poi" and item.name == name), None)
                if identifier is not None:
                    remove(day, identifier, "人群或体力不符")
                continue
            underfilled = issue["dimension"] == "pace" and "节奏建议" in message and \
                int(re.search(r"当天 (\d+)", message).group(1)) < int(re.search(r"(\d+)–(\d+)", message).group(1))
            if issue["dimension"] in {"pace", "transport"} and not underfilled:
                removable = [identifier for identifier in orders[day - 1] if identifier not in must and not pois[identifier].day_anchor]
                for identifier in sorted(removable, key=lambda identifier: (-detour(day, identifier), scores.get(identifier, 0)))[:2]:
                    remove(day, identifier, "减轻负担" if issue["dimension"] == "pace" else "减少乘车与折返")
            elif issue["dimension"] in {"slack", "pace"}:
                order = orders[day - 1]
                if not order or any(pois[identifier].day_anchor for identifier in order):
                    continue
                if ("reorder", day) not in seen and len(order) > 1:
                    # 先试调整顺序：如傍晚才开门的夜市排在第一站，会让整个上午空等，补点也放不进去
                    seen.add(("reorder", day))
                    def path_km(variant: list[int]) -> float:
                        path = [hotel, *((pois[identifier].lat, pois[identifier].lon) for identifier in variant), hotel]
                        return sum(haversine_km(*a, *b) for a, b in zip(path, path[1:]))
                    variants = [sorted(order, key=lambda identifier: pois[identifier].open_min)]
                    if len(order) <= 5:
                        variants += sorted((list(variant) for variant in itertools.permutations(order)), key=path_km)
                    unique: list[list[int]] = []
                    for variant in variants:
                        variant = keep_walks_together(variant, pois)
                        if variant != order and variant not in unique:
                            unique.append(variant)
                    for variant in unique[:8]:
                        trial = [list(day_order) for day_order in orders]
                        trial[day - 1] = variant
                        found.append((f"第{day}天调整游览顺序", trial, list(selected), {}))
                groups = {experience_key(pois[identifier]) for identifier in chosen}
                near = lambda candidate: min(haversine_km(pois[identifier].lat, pois[identifier].lon, pois[candidate.poi_id].lat,
                                                          pois[candidate.poi_id].lon) for identifier in order)
                pool = [candidate for candidate in state.get("candidates", []) if candidate.poi_id not in chosen
                        and ("add", candidate.poi_id) not in seen and candidate.poi_id in pois
                        and pois[candidate.poi_id].visit_role != "viewpoint" and not pois[candidate.poi_id].day_anchor
                        and experience_key(pois[candidate.poi_id]) not in groups
                        and pois[candidate.poi_id].stamina <= max_stamina(profile)
                        and near(candidate) <= 5]
                pool.sort(key=lambda candidate: candidate.score - .05 * near(candidate), reverse=True)
                for candidate in pool[:3]:
                    seen.add(("add", candidate.poi_id))
                    place = pois[candidate.poi_id]
                    # 每个可插入位置都试：新地点放在上午还是午饭后，由重建后的时间线和综合分决定
                    for position in (index for index in range(len(order) + 1) if walk_boundary(order, index, pois)):
                        trial = [list(day_order) for day_order in orders]
                        trial[day - 1].insert(position, place.id)
                        found.append((f"第{day}天{LABELS['slack']}不足：补入「{place.name}」", trial,
                                      selected + [Selection(poi_id=place.id, reason="合理性精修：补充同片区地点以减少空档", source="repair")], {}))
        return found[:MAX_TRIALS]

    for _ in range(MAX_STEPS):
        ranked = []
        for label, trial_orders, trial_selected, pins in proposals():
            key = str((trial_orders, sorted(pins.items())))
            if key in failed:
                continue
            trial = build(trial_orders, trial_selected, pins or None, live=False)
            trial_report = check(trial, live=False)
            if not acceptable(trial_report, report):
                if debug is not None:
                    debug.append((label, "rejected", sorted({item.code for item in trial_report.hard} - {item.code for item in report.hard}),
                                  sorted(_missing(trial_report) - _missing(report))))
                continue
            graded = grade(trial)
            if debug is not None:
                debug.append((label, graded["overall"], current["overall"]))
            if graded["overall"] is not None and graded["overall"] >= current["overall"] + MIN_GAIN:
                ranked.append((graded["overall"], key, label, trial_orders, trial_selected, pins))
        accepted = False
        # 试算只读缓存，可能与真实路线不符：复核失败就换下一个候选，最多复核 CONFIRM_TRIES 个
        for _, key, label, trial_orders, trial_selected, pins in sorted(ranked, key=lambda row: row[0], reverse=True)[:CONFIRM_TRIES]:
            real = build(trial_orders, trial_selected, pins or None, live=True)
            real_report = check(real, live=True)
            graded = grade(real)
            if not acceptable(real_report, report) or graded["overall"] < current["overall"] + MIN_GAIN:
                failed.add(key)
                notes.append(f"合理性精修：「{label}」用真实路线复核后没有改善，未采纳")
                continue
            orders, selected, itinerary, report, current = trial_orders, trial_selected, real, real_report, graded
            log.append(f"合理性精修：{label}（综合分 {graded['overall']:.1f}）")
            accepted = True
            break
        if not accepted:
            break
    summary = f"合理性精修：综合分 {before['overall']:.1f} → {current['overall']:.1f}，采纳 {len(log)} 处修改"
    log.extend(notes)
    actual = {item.ref_id for day in itinerary.days for item in day.items if item.kind == "poi"}
    return {"itinerary": itinerary, "report": report, "selected": selected, "day_orders": orders,
            "dropped": [identifier for identifier in state.get("dropped", []) if identifier not in actual],
            "refine_scores": {"before": before["overall"], "after": current["overall"]}, "log": [summary, *log]}


def refine_node(state: dict) -> dict:
    """图中的精修节点：从修复循环留下的最佳方案出发（run_plan 原本也会换成它），精修后不再回退。"""
    best = state.get("best_candidate")
    base = {**state, **({field: best[field] for field in ("itinerary", "report", "selected", "day_orders", "dropped")} if best else {})}
    return {**refine(base), "best_candidate": None, "refined": True}
