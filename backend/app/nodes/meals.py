"""插入餐厅并生成精确时间线（仿真）。

按调度给出的景点顺序逐站推进时间：到达 = 出发 + 交通；早到就等开门。
- 午餐：下一站实际开始时间（含等开门）已近午餐，或下一站会拖过 13:30 时就先吃；当天景点结束时还没吃且不晚于 13:45 也补上。
- 晚餐：各节奏均安排；到了用餐时段先吃，或最后一站后补上。
- 选餐厅：正餐适配、饮食限制、预算、额外绕行和用餐时间窗共同筛选。
- 校验通过后，尝试用候选补足下午空档，每次补点必须再次通过同一校验器。
"""
from __future__ import annotations

import re
from ..areas import area_fill_conflicts, area_index
from ..data import load_pois, load_restaurants
from ..geo import haversine_km, travel  # 仅用于餐厅候选的粗筛与排序（快）；定稿路段用 Travel
from ..rules import DINNER, LUNCH, MEAL_MIN, PACE, activity_window, required_meals
from ..schemas import DayPlan, Item, Itinerary, MealAlternative, ReservedMeal, Restaurant, Selection, UserProfile
from ..calendar import closed_on, date_notices, trip_date
from ..meal_ranking import rank_restaurants
from ..restaurants import dietary_status, meal_start
from ..experiences import experience_count, experience_key, walk_boundary
from ..poi_semantics import safe_reason, experience_notices, visit_open_min
from .schedule import fit_service_min, service_min


def scheduled_reason(reason: str, start: int, end: int) -> str:
    windows = [(r"清晨|早晨|早上|上午", 0, 12 * 60), (r"中午", 11 * 60, 14 * 60),
               (r"下午", 12 * 60, 18 * 60), (r"傍晚|黄昏|日落", 16 * 60, 20 * 60),
               (r"晚上|夜晚|夜游|夜景|夜间|晚饭后|晚餐后|入夜|夜幕", 18 * 60, 24 * 60)]
    clauses = re.split(r"[，。；,;]", reason)
    compatible = [clause for clause in clauses if clause.strip() and not any(
        re.search(pattern, clause) and (end <= lower or start >= upper) for pattern, lower, upper in windows)]
    return "，".join(compatible) or "从候选景点中选入；请结合实际安排时段判断是否符合你的游玩期待。"


def per_meal_budget(p: UserProfile, factor: float = 1.0) -> float:
    count = max(1, sum(len(required_meals(p, day)) for day in range(1, p.days + 1)))
    default = min(150.0, max(20.0, p.budget * 0.5 / count))
    return min(p.meal_budget_max or default, max(0, p.budget / count)) * factor


def meal_suitability(restaurant: Restaurant, profile: UserProfile) -> int | None:
    name = restaurant.name
    if re.search(r"酒场|酒馆|酒吧|居酒屋|清吧|夜店|酒肆", name):
        return None
    if not restaurant.cuisine.strip() or restaurant.price <= 0:
        return None
    if re.search(r"肯德基|麦当劳|汉堡王|必胜客|塔斯汀|德克士|华莱士", name) or restaurant.cuisine in {"快餐", "快餐厅"}:
        return 0 if profile.food_preference == "quick" else 35
    if restaurant.cuisine == "小吃":
        if not re.search(r"面馆|馄饨|饺子|米线|米粉|饭|拌川|汤面|片儿川", name + restaurant.dishes):
            return None
        return 24
    if profile.companions in {"family_kids", "elderly"} and restaurant.cuisine in {"烧烤", "火锅"}:
        return 32
    return 0 if restaurant.cuisine in {"杭帮菜", "江浙菜", "本帮菜", "农家菜"} else 12


def menu_suggestions(restaurant: Restaurant, profile: UserProfile) -> list[str]:
    dishes = [dish.strip() for dish in re.split(r"[,，、]", restaurant.dishes) if dish.strip()]
    excluded = []
    if "不吃辣" in profile.dietary:
        excluded.extend(["辣", "椒", "麻婆", "水煮", "剁椒"])
    if "不吃海鲜" in profile.dietary:
        excluded.extend(["虾", "蟹", "贝", "蛤", "蚝", "海", "鲍", "鱿", "鳗", "鱼", "螺"])
    return [dish for dish in dishes if not any(word in dish for word in excluded)][:3]


def pick_restaurant(pos: tuple[float, float], nxt: tuple[float, float] | None, start: int, p: UserProfile,
                    used: set[int], cap: float, latest_start: int = 24 * 60) -> tuple[Restaurant, int] | None:
    options = rank_restaurants(pos, nxt, start, p, used, cap, latest_start, load_restaurants())
    return (options[0]["restaurant"], options[0]["go"]) if options else None


def build_itinerary(day_orders: list[list[int]], p: UserProfile, selected: list[Selection],
                    meal_factor: float = 1.0, route=None, live: bool = True, catalog=None,
                    pins: dict[tuple[int, str], int] | None = None,
                    user_meals: set[tuple[int, str]] | None = None,
                    only_day: int | None = None) -> Itinerary:
    """route: app.travel.Travel；None 时用估算模型。live=False 时只读缓存（大量试探时用）。

    pins：{(第几天, "lunch"/"dinner"): 餐厅 id}，把这一餐固定为指定餐厅（局部重规划用，见 app/replan.py）；
    user_meals：其中由用户亲自选定的餐次，推荐理由会写明"你选定的餐厅"。未出现在 pins 里的餐次照常排序挑选。
    """
    def leg(a: tuple[float, float], b: tuple[float, float]) -> tuple[int, str, float]:
        return route.leg(a[0], a[1], b[0], b[1], live=live) if route is not None else travel(a[0], a[1], b[0], b[1])

    pois = load_pois()
    reason = {s.poi_id: s.reason for s in selected}
    restaurants = catalog.restaurants if catalog else load_restaurants()
    cap = per_meal_budget(p, meal_factor)
    used: set[int] = set()
    days, transport = [], 0.0
    hotel = (p.hotel.lat, p.hotel.lon)

    for d, order in enumerate(day_orders, start=1):
        if only_day is not None and d != only_day:
            continue
        if route:
            route.set_day(p, d)
        c = activity_window(p, d)
        t, pos = c["start"], hotel
        items: list[Item] = []
        notes: list[str] = []
        reserved: list[ReservedMeal] = []
        wanted = required_meals(p, d)
        lunch, dinner = "lunch" not in wanted, "dinner" not in wanted
        want_dinner = "dinner" in wanted

        def eat(kind: str, nxt: tuple[float, float] | None, deadline: int | None = None) -> None:
            nonlocal t, pos, transport, lunch, dinner
            window = LUNCH if kind == "lunch" else DINNER
            latest = min(window[1], c["end"] - MEAL_MIN)
            pinned = pins.get((d, kind)) if pins else None
            if pinned is not None and pinned in restaurants:
                eat_pinned(kind, restaurants[pinned], nxt, deadline, window, latest)
                return
            options = rank_restaurants(pos, nxt, max(t, window[0]), p, used, cap, latest, restaurants,
                                       route=route, live=live, catalog=catalog, day=d, need_arrive_by=deadline, is_departure=not items)
            if not options:
                message = f"{'午' if kind == 'lunch' else '晚'}餐尚无符合饮食、预算与路线的候选，餐厅需自行确认"
                reserve_start = max(t, window[0])
                if reserve_start + MEAL_MIN + leg(pos, hotel)[0] <= c["end"]:
                    reserved.append(ReservedMeal(meal=kind, start=reserve_start, end=reserve_start + MEAL_MIN, reason=message))
                    t = reserve_start + MEAL_MIN
                else:
                    notes.append(message + "；剩余时段不足，需调整行程")
                if kind == "lunch":
                    lunch = True
                else:
                    dinner = True
                return
            chosen = options[0]
            r = chosen["restaurant"]
            mins, mode, cost = leg(pos, (r.lat, r.lon))
            go = mins
            transport += cost
            start = chosen["start"]
            near_park = bool(items and items[-1].kind == "poi" and pois[items[-1].ref_id].onsite_dining
                             and haversine_km(*pos, r.lat, r.lon) <= 2)
            if near_park:
                chosen["notices"].append("景区周边用餐，具体出口与步行接驳需确认")
            items.append(Item(kind="meal", ref_id=r.id, name=r.name, start=start, end=start + MEAL_MIN,
                              meal_detour_min=chosen["detour"], meal_context="park_exit" if near_park else "restaurant",
                              travel_min=mins, travel_mode=mode, cost=r.price, meal=kind,
                              alternatives=[MealAlternative(ref_id=option["restaurant"].id, name=option["restaurant"].name,
                                  cuisine=option["restaurant"].cuisine, price=option["restaurant"].price, rating=option["restaurant"].rating,
                                  address=option["restaurant"].address, detour_min=option["detour"], travel_source=option["source"],
                                  provider=option["restaurant"].provider, fetched_at=option["restaurant"].fetched_at,
                                  start=option["start"], end=option["start"] + MEAL_MIN) for option in options[1:]],
                              notices=chosen["notices"],
                              reason=f"{r.cuisine} · 从上一站约 {go} 分钟 · 额外绕路约 {chosen['detour']} 分钟 · 参考人均 ¥{r.price:.0f}；"
                              + (f"菜单参考：{'、'.join(menu_suggestions(r, p))}。" if menu_suggestions(r, p) else "")
                              + "营业、价格及饮食要求请到店前确认。"))
            used.add(r.id)
            t, pos = start + MEAL_MIN, (r.lat, r.lon)
            if kind == "lunch":
                lunch = True
            else:
                dinner = True

        def eat_pinned(kind: str, r: Restaurant, nxt: tuple[float, float] | None, deadline: int | None,
                       window: tuple[int, int], latest: int) -> None:
            """固定餐厅：时间按同样规则推算（到店后等开门），备选仍按常规排序给出。"""
            nonlocal t, pos, transport, lunch, dinner
            mins, mode, cost = leg(pos, (r.lat, r.lon))
            arrival = meal_start(r, max(t, window[0]) + mins)
            notices = []
            if arrival is None:
                arrival = max(t, window[0]) + mins
                notices.append("按已知营业时间，这个时段可能未营业，出发前请确认")
            if p.dietary and dietary_status(r, p) != "confirmed":
                notices.append("这家餐厅与饮食要求的匹配尚未确认，请向商家确认")
            following = nxt or hotel
            detour = max(0, mins + leg((r.lat, r.lon), following)[0] - leg(pos, following)[0])
            near_park = bool(items and items[-1].kind == "poi" and pois[items[-1].ref_id].onsite_dining
                             and haversine_km(*pos, r.lat, r.lon) <= 2)
            if near_park:
                notices.append("景区周边用餐，具体出口与步行接驳需确认")
            options = rank_restaurants(pos, nxt, max(t, window[0]), p, used | {r.id}, cap, latest, restaurants,
                                       route=route, live=live, catalog=catalog, day=d, need_arrive_by=deadline, is_departure=not items)
            alternatives = [MealAlternative(ref_id=option["restaurant"].id, name=option["restaurant"].name,
                                            cuisine=option["restaurant"].cuisine, price=option["restaurant"].price,
                                            rating=option["restaurant"].rating, address=option["restaurant"].address,
                                            detour_min=option["detour"], travel_source=option["source"],
                                            provider=option["restaurant"].provider, fetched_at=option["restaurant"].fetched_at,
                                            start=option["start"], end=option["start"] + MEAL_MIN)
                            for option in options if option["restaurant"].id != r.id][:2]
            chosen_by_user = bool(user_meals and (d, kind) in user_meals)
            menu = menu_suggestions(r, p)
            transport += cost
            items.append(Item(kind="meal", ref_id=r.id, name=r.name, start=arrival, end=arrival + MEAL_MIN,
                              meal_detour_min=detour, meal_context="park_exit" if near_park else "restaurant",
                              travel_min=mins, travel_mode=mode, cost=r.price, meal=kind, alternatives=alternatives,
                              notices=notices,
                              reason=f"{r.cuisine} · {'你选定的餐厅 · ' if chosen_by_user else ''}从上一站约 {mins} 分钟 · "
                              f"额外绕路约 {detour} 分钟 · 参考人均 ¥{r.price:.0f}；"
                              + (f"菜单参考：{'、'.join(menu)}。" if menu else "") + "营业、价格及饮食要求请到店前确认。"))
            used.add(r.id)
            t, pos = arrival + MEAL_MIN, (r.lat, r.lon)
            if kind == "lunch":
                lunch = True
            else:
                dinner = True

        for visit_index, pid in enumerate(order):
            poi = pois[pid]
            if closed_on(poi, p, d):
                notes.append(f"{poi.name}在所选日期按已知规则不开放，未安排")
                continue
            loc = (poi.lat, poi.lon)
            svc = service_min(poi, p.pace)
            go, mode, cost = leg(pos, loc)
            start = max(t + go, visit_open_min(poi))
            onsite_lunch = (poi.onsite_dining and not (pins and (d, "lunch") in pins)
                            and max(start, LUNCH[0]) <= LUNCH[1] and max(start, LUNCH[0]) + MEAL_MIN <= start + svc)
            onsite_dinner = (poi.onsite_dining and not (pins and (d, "dinner") in pins)
                             and max(start, DINNER[0]) <= DINNER[1] and max(start, DINNER[0]) + MEAL_MIN <= start + svc)
            inside_walk = bool(poi.visit_group and visit_index > 0 and pois[order[visit_index - 1]].visit_group == poi.visit_group)
            walk_finish, walk_position = start + svc, loc
            if inside_walk:
                for remaining_id in order[visit_index + 1:]:
                    remaining = pois[remaining_id]
                    if remaining.visit_group != poi.visit_group:
                        break
                    destination = (remaining.lat, remaining.lon)
                    duration = route.leg(*walk_position, *destination, live=False)[0] if route else travel(*walk_position, *destination)[0]
                    walk_finish = max(walk_finish + duration, visit_open_min(remaining)) + service_min(remaining, p.pace)
                    walk_position = destination
            finish_before_lunch = inside_walk and walk_finish <= LUNCH[1] - 25
            # 用"等开门后的实际开始时间"判断：开门前的空档可以拿来吃饭；下一站会拖过午餐窗口就先吃
            if not lunch and not onsite_lunch and not finish_before_lunch and (start >= LUNCH[0] - 15 or (t >= LUNCH[0] - 60 and start + svc > LUNCH[1])):
                eat("lunch", loc, poi.close_min - svc)
            elif want_dinner and not dinner and not onsite_dinner and t >= DINNER[0] - 15:
                eat("dinner", loc, poi.close_min - svc)
            go, mode, cost = leg(pos, loc)
            start = max(t + go, visit_open_min(poi))
            transport += cost
            duration = fit_service_min(poi, p.pace, start, c["end"])
            duration_note = [f"临近活动时段结束，本次安排停留{duration}分钟，仍在现有建议时长范围内"] if duration < svc else []
            items.append(Item(kind="poi", ref_id=pid, name=poi.name, start=start, end=start + duration,
                              travel_min=go, travel_mode=mode, cost=poi.price,
                              notices=list(dict.fromkeys(date_notices(poi, p, d) + duration_note + experience_notices(poi, p, d, start, start + duration))),
                              reason=safe_reason(poi.summary if poi.seasonal_note else scheduled_reason(reason.get(pid, ""), start, start + duration), poi, p, d, start, start + duration)))
            t, pos = start + duration, loc
            if poi.onsite_dining:
                for kind, bounds, needed in (("lunch", LUNCH, not lunch), ("dinner", DINNER, not dinner)):
                    meal_at = max(start, bounds[0])
                    if needed and kind in wanted and meal_at <= bounds[1] and meal_at + MEAL_MIN <= t:
                        reserved.append(ReservedMeal(meal=kind, start=meal_at, end=meal_at + MEAL_MIN,
                            inside_poi_id=poi.id, estimated_cost=round(min(cap, 80), 1),
                            reason=f"在{poi.name}园内用餐，不出园绕路；这1小时已包含在园区停留中。具体商户、价格与饮食要求需现场确认"))
                        if kind == "lunch":
                            lunch = True
                        else:
                            dinner = True
                notes.append(f"{poi.name}作为当天主项目，停留包含园内用餐与等候余量；演出场次及开放情况需另行确认")
        if order:
            if not lunch and t <= LUNCH[1] + 15:
                eat("lunch", None)
            if want_dinner and not dinner and t <= DINNER[1]:
                eat("dinner", None)
        back, _, cost = leg(pos, hotel)
        transport += cost if order else 0
        zones = area_index(load_pois())
        for item in items:
            if item.kind == "poi":
                item.area = zones.label(zones.area_of(item.ref_id))
        days.append(DayPlan(day=d, date=trip_date(p, d), items=items, return_travel_min=back if order else 0, notes=notes, reserved_meals=reserved,
                            areas=[zones.label(zone) for zone in zones.areas_of(order)]))
    return Itinerary(days=days, transport_cost=round(transport, 1))


def meals_node(state: dict) -> dict:
    from .validate import validate_itinerary
    from .retrieve import retrieve

    profile = state["profile"]
    hotel = (profile.hotel.lat, profile.hotel.lon)
    orders = [list(order) for order in state["day_orders"]]
    selected = list(state["selected"])
    factor = state.get("meal_factor", 1.0)
    route = state.get("travel")
    catalog = state.get("catalog")
    from ..planning_quality import choose_meal_aware_plan
    orders, it, quality_log = choose_meal_aware_plan(orders, profile, selected, factor, route, catalog)
    additions = []
    removed = set(state.get("repair_removed_ids", []))
    # 体验修复只提出新增点。经过营业时间、午晚餐和真实交通排序后再决定是否采纳。
    pending = set(state.get("repair_pending_additions", []))
    if pending and area_fill_conflicts(it, load_pois()):
        selected = [item for item in selected if item.poi_id not in pending]
        orders = [[identifier for identifier in order if identifier not in pending] for order in orders]
        removed.update(pending)
        orders, it, retry_log = choose_meal_aware_plan(orders, profile, selected, factor, route, catalog)
        quality_log += ["体验修复未采纳：试排后出现返回已离开片区或超过两个片区，保留空档；排除 "
                        + "、".join(load_pois()[identifier].name for identifier in sorted(pending))] + retry_log
    candidates = list(state["candidates"])
    candidate_ids = {candidate.poi_id for candidate in candidates}
    places = load_pois()
    anchors = [places[identifier] for order in orders for identifier in order]
    nearby_reviewed = [candidate for candidate in retrieve(profile, k=90)
                       if candidate.poi_id not in candidate_ids and places[candidate.poi_id].review_note
                       and places[candidate.poi_id].visit_role != "viewpoint"
                       and any(haversine_km(anchor.lat, anchor.lon, places[candidate.poi_id].lat,
                                            places[candidate.poi_id].lon) <= 3 for anchor in anchors)]
    if not state.get("frozen_candidates"):
        candidates.extend(nearby_reviewed[:8])

    def check(itinerary: Itinerary, live: bool):
        fn = (lambda a, b, c, d: route.leg(a, b, c, d, live=live)) if route is not None else None
        return validate_itinerary(itinerary, profile, travel_fn=fn, restaurants=catalog.restaurants if catalog else None)

    def idle_minutes(itinerary: Itinerary) -> int:
        total = 0
        for day in itinerary.days:
            visits = [item for item in day.items if item.kind == "poi"]
            if not visits:
                continue
            window = activity_window(profile, day.day)
            target = min(window["end"] - 60, 18 * 60 if "夜景夜游" in profile.interests else 16 * 60 + 30)
            total += max(0, target - visits[-1].end)
            total += sum(max(0, following.start - previous.end - following.travel_min - 60)
                         for previous, following in zip(day.items, day.items[1:]))
        return total

    zones = area_index(load_pois())
    home_zones = zones.home_areas(profile.hotel.lat, profile.hotel.lon)
    if state.get("enable_repair", True) and check(it, True).passed:
        for _ in range(profile.days * 2):
            current_idle = idle_minutes(it)
            if current_idle < 30:
                break
            current_travel = sum(day.return_travel_min + sum(item.travel_min for item in day.items) for day in it.days)
            current_meals = {(day.day, item.meal) for day in it.days for item in day.items if item.kind == "meal"}
            chosen = {item.ref_id for day in it.days for item in day.items if item.kind == "poi"}
            proposals = {}
            best_gain = 30.0
            for candidate in candidates:
                if candidate.poi_id in chosen | removed:
                    continue
                poi = load_pois()[candidate.poi_id]
                if poi.visit_role == "viewpoint" or (poi.visit_group and any(
                        experience_key(load_pois()[identifier]) == experience_key(poi) for identifier in chosen)):
                    continue
                if re.search(r"墓|碑|亭|雕像|造像", poi.name):
                    continue
                if poi.stamina > PACE[profile.pace]["max_stamina"]:
                    continue
                for day_index, order in enumerate(orders):
                    if poi.day_anchor or any(places[identifier].day_anchor for identifier in order):
                        continue
                    if order and not any(zones.compatible(zones.area_of(poi.id), zones.area_of(identifier), home_zones) for identifier in order):
                        continue  # 片区为主：只补当天片区或相邻片区
                    if experience_count(order, load_pois()) >= PACE[profile.pace]["per_day"] + 1 or len(order) >= 7 or not order:
                        continue
                    positions = sorted((index for index in range(len(order) + 1) if walk_boundary(order, index, load_pois())), key=lambda index: travel(
                        *(hotel if index == 0 else (load_pois()[order[index - 1]].lat, load_pois()[order[index - 1]].lon)),
                        poi.lat, poi.lon)[0])[:2]
                    for position in positions:
                        trial_orders = [list(day_order) for day_order in orders]
                        trial_orders[day_index].insert(position, poi.id)
                        trial_selection = selected + [Selection(poi_id=poi.id, reason="补充在当天可用时间内，结合兴趣与沿途位置安排", source="rule")]
                        # 试探只读缓存（不联网），否则每个候选 × 每个位置都会触发真实路线查询
                        trial = build_itinerary(trial_orders, profile, trial_selection, factor, route, live=False, catalog=catalog)
                        if area_fill_conflicts(trial, places):
                            continue
                        trial_meals = {(day.day, item.meal) for day in trial.days for item in day.items if item.kind == "meal"}
                        if not current_meals <= trial_meals:
                            continue
                        trial_travel = sum(day.return_travel_min + sum(item.travel_min for item in day.items) for day in trial.days)
                        if trial_travel > current_travel + 25:
                            continue
                        gain = current_idle - idle_minutes(trial) - max(0, trial_travel - current_travel)
                        trial_report = check(trial, False)
                        if gain <= best_gain or not trial_report.passed or any(v.code == "excessive_transport" for v in trial_report.violations):
                            continue
                        if poi.id not in proposals or gain > proposals[poi.id][0]:
                            proposals[poi.id] = (gain, trial_orders, trial_selection, poi.name)
            if not proposals:
                break
            # 试探只读缓存；采纳前必须用真实路线重建并通过校验，否则放弃该补点并停止
            accepted = False
            for _, trial_orders, trial_selection, name in sorted(proposals.values(), key=lambda proposal: proposal[0], reverse=True)[:3]:
                real = build_itinerary(trial_orders, profile, trial_selection, factor, route, catalog=catalog)
                if area_fill_conflicts(real, places):
                    continue
                real_meals = {(day.day, item.meal) for day in real.days for item in day.items if item.kind == "meal"}
                real_report = check(real, True)
                if not real_report.passed or any(v.code == "excessive_transport" for v in real_report.violations) or not current_meals <= real_meals or idle_minutes(real) >= current_idle:
                    continue
                orders, selected, it = trial_orders, trial_selection, real
                additions.append(name)
                accepted = True
                break
            if not accepted:
                break
    from ..evening import add_evening_visits
    it, evening_additions = add_evening_visits(it, profile, route, catalog, excluded_ids=removed)
    n_meal = sum(1 for d in it.days for i in d.items if i.kind == "meal")
    actual = {item.ref_id for day in it.days for item in day.items if item.kind == "poi"}
    log = quality_log + [f"用餐与时间线：插入 {n_meal} 餐，生成逐分钟时间线"]
    if additions:
        log.append("空档修复：通过同一校验器后补入 " + "、".join(additions))
    if evening_additions:
        log.append("夜间安排：通过时间、预算和步行校验后补入 " + "、".join(evening_additions))
    return {"itinerary": it, "selected": selected, "day_orders": orders, "candidates": candidates,
            "repair_removed_ids": sorted(removed), "repair_pending_additions": [],
            "dropped": [place_id for place_id in state.get("dropped", []) if place_id not in actual], "log": log}
