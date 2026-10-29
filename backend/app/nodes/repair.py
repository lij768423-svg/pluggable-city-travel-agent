"""修复：按违例类型改选点，然后回到调度（最多 3 轮）。

- 某天出现 营业时间/交通时间/超时 → 删掉该天得分最低的非必去景点
- 超预算 → 先把门票最贵的景点换成同兴趣、更便宜的未选候选；没有可换的就降低每餐预算（×0.7）
- 求解器舍弃的景点 → 从选点中移除；若舍弃的是必去景点，改为删掉得分最低的非必去景点
  （每天都有一处必去离它超过单段乘车封顶时不删：删了也排不进，交统一校验说明原因）
- 缺午餐/晚餐 → 删掉缺餐那天挡住这一餐的景点（跨过午餐时段的、或当天最后一个），没有才删全程得分最低的
- 当天步行超上限且允许打车 → 先把当天走路最多的几段改打车（哪怕更慢或更贵）；没有可改的路段才删景点
无法再改动时返回 changed=False，由图结束循环。
"""
from __future__ import annotations

from ..areas import area_fill_conflicts, area_index
from ..data import load_pois
from ..geo import haversine_km, travel
from ..rules import HARD_INTERSTOP_RIDE_MIN, LUNCH, transport_dominates, walking_limit
from ..schemas import Selection
from ..travel import Travel, depart_at
from .select import rule_reason
from .validate import far_must_partner

# 步行超限改打车时，短于此距离的纯步行段不值得打车；同一连续游览组内部的路段也不改
MIN_TAXI_WALK_M = 800
TIME_CODES = {"opening_hours", "travel_time", "day_overlong", "walking_limit", "transport_unavailable", "date_closed", "excessive_transport", "interstop_ride_limit", "anchor_mixed", "audience_mismatch"}
MAX_ROUNDS = 3


def meal_blocker(itinerary, report, sel_ids: set[int], must: set[int], pois: dict) -> int | None:
    """缺餐那天挡住这一餐的景点（2026-10-20 用户：缺餐“先修这个”）。

    午餐：跨过午餐时段末的景点，否则最后一个在午餐末之前开始的；晚餐：当天最后一个景点（离酒店远、拖到太晚）。
    当天只有一个景点、或是必去/整日锚点时不删，交给原有逻辑。
    """
    for violation in sorted((v for v in report.violations if v.code in {"missing_lunch", "missing_dinner"} and v.day),
                            key=lambda v: (v.day, v.code)):
        visits = [item for item in itinerary.days[violation.day - 1].items if item.kind == "poi" and item.ref_id in pois]
        if len(visits) < 2:
            continue
        if violation.code == "missing_lunch":
            spanning = [item for item in visits if item.start < LUNCH[1] < item.end]
            before = [item for item in visits if item.start < LUNCH[1]]
            ordered = spanning or before[-1:]
        else:
            ordered = visits[-1:]
        for item in ordered:
            if item.ref_id in sel_ids and item.ref_id not in must and not pois[item.ref_id].day_anchor:
                return item.ref_id
    return None


def walking_taxi_overrides(state: dict) -> tuple[set[int], list[str]]:
    """当天步行超上限时，把走路最多的路段加入强制打车，直到预计步行不超过上限（2026-10-22 用户决定）。

    园内步行改不了，只计入预计值。返回这一轮新加了路段的天和日志；不允许打车或无路段可改时返回空集合。
    """
    route, profile, itinerary = state.get("travel"), state["profile"], state.get("itinerary")
    if route is None or itinerary is None or not profile.allow_taxi_fallback or not hasattr(route, "taxi_overrides"):
        return set(), []
    catalog = state.get("catalog")
    if catalog is not None:
        restaurants = catalog.restaurants
    else:
        from ..data import load_restaurants
        restaurants = load_restaurants()
    limit, days, log = walking_limit(profile), set(), []
    for d in sorted({v.day for v in state["report"].violations if v.code == "walking_limit" and v.day}):
        day = itinerary.days[d - 1]
        with route.for_day(profile, d):
            legs, previous = [], None
            for item, place, origin, destination, depart in route.day_legs(day, profile, restaurants):
                with depart_at(route, depart):
                    detail = route.detail(*origin, *destination, live=False)
                same_walk = bool(getattr(previous, "visit_group", "")) and getattr(previous, "visit_group", "") == getattr(place, "visit_group", "")
                short_walk = detail.mode == "步行" and detail.walking_m < MIN_TAXI_WALK_M
                if detail.walking_m > 0 and not same_walk and not short_walk and route.pair(*origin, *destination) not in route.taxi_overrides:
                    legs.append((detail.walking_m, item.name if item else "返回住处", route.pair(*origin, *destination)))
                previous = place
        projected, added = day.walking_m, []
        for walked, name, pair in sorted(legs, key=lambda leg: (-leg[0], leg[1])):
            if projected <= limit:
                break
            route.taxi_overrides.add(pair)
            projected -= walked
            added.append(f"前往{name}" if name != "返回住处" else name)
        if added:
            days.add(d)
            log.append(f"第 {d} 天步行约 {day.walking_m / 1000:.1f} 公里超上限：{len(added)} 段改打车（{'、'.join(added)}）")
    return days, log


def _repair(state: dict) -> tuple[list[Selection], float, list[str], bool, set[int]]:
    pois = load_pois()
    areas = area_index(pois)
    p, report = state["profile"], state["report"]
    home = areas.home_areas(p.hotel.lat, p.hotel.lon)
    sel: list[Selection] = list(state["selected"])
    scores = {c.poi_id: c.score for c in state["candidates"]}
    must = {c.poi_id for c in state["candidates"] if c.breakdown.get("must_visit")}
    removed = set(state.get("repair_removed_ids", [])) - must
    meal_factor = state.get("meal_factor", 1.0)
    taxi_days, log = walking_taxi_overrides(state)
    changed = bool(taxi_days)

    quality_codes = {violation.code for violation in report.violations}
    # 缺餐优先于补点：缺一餐计 20 分，远重于偏空（4＋3）；能找到挡住这一餐的景点就先删它
    blocker = None
    if report.passed and quality_codes & {"missing_lunch", "missing_dinner"} and state.get("itinerary") and len(sel) > p.days:
        blocker = meal_blocker(state["itinerary"], report, {selection.poi_id for selection in sel}, must, pois)
    if blocker is not None:
        sel = [selection for selection in sel if selection.poi_id != blocker]
        removed.add(blocker)
        log.append(f"为用餐腾出时间：移除缺餐当天的{pois[blocker].name}")
        changed = True
    if report.passed and not changed and not quality_codes & {"excessive_transport", "anchor_mixed", "audience_mismatch", "area_scattered"} and quality_codes & {"early_finish", "long_idle", "empty_day"}:
        chosen = {selection.poi_id for selection in sel}
        alternatives = [candidate for candidate in state["candidates"] if candidate.poi_id not in chosen | removed
                        and pois[candidate.poi_id].stamina <= (2 if p.companions in {"family_kids", "elderly"} else 3)]
        if alternatives and len(sel) < p.days * 5 and sum(pois[item.poi_id].day_anchor for item in sel) < p.days:
            # 优先补最空的那天：按离当天已排景点（没有则按起点）的距离挑，避免补到别的片区去
            sparse_days = {violation.day for violation in report.violations
                           if violation.code in {"early_finish", "long_idle", "empty_day"} and violation.day}
            itinerary = state.get("itinerary")
            anchors = [(pois[selection.poi_id].lat, pois[selection.poi_id].lon) for selection in sel]
            if sparse_days and itinerary is not None:
                def visits(day: int) -> list[int]:
                    return [item.ref_id for item in itinerary.days[day - 1].items if item.kind == "poi" and item.ref_id in pois]
                target = min(sparse_days, key=lambda day: (len(visits(day)), day))
                anchors = [(pois[identifier].lat, pois[identifier].lon) for identifier in visits(target)] or [(p.hotel.lat, p.hotel.lon)]
                # 片区为主：先补当天片区内的，其次相邻片区；不相邻的不补
                zones = {areas.area_of(identifier) for identifier in visits(target)}
                if zones:
                    same = [candidate for candidate in alternatives if areas.area_of(candidate.poi_id) in zones]
                    alternatives = same or [candidate for candidate in alternatives
                                            if any(areas.compatible(areas.area_of(candidate.poi_id), zone, home) for zone in zones)]
            if alternatives:
                addition = max(alternatives, key=lambda candidate: candidate.score - min(
                    (haversine_km(pois[candidate.poi_id].lat, pois[candidate.poi_id].lon, *anchor) for anchor in anchors), default=0) * 0.05)
                # 这里只提出试排；repair_node 标记新增点，meals 按实际时序拒绝折返补点。
                sel.append(Selection(poi_id=addition.poi_id, reason="根据可用时段补充同片区候选，重新校验时间与交通", source="repair"))
                log.append(f"体验修复：尝试补入{pois[addition.poi_id].name}")
                changed = True
    if report.passed and quality_codes & {"missing_lunch", "missing_dinner"} and not changed:
        removable = [selection for selection in sel if selection.poi_id not in must]
        if removable and len(sel) > p.days:
            worst = min(removable, key=lambda selection: scores.get(selection.poi_id, 0))
            sel.remove(worst)
            removed.add(worst.poi_id)
            log.append(f"为用餐与交通腾出时间：移除{pois[worst.poi_id].name}")
            changed = True

    dropped = set(state.get("dropped", []))
    if dropped - must:
        removed.update(dropped - must)
        sel = [s for s in sel if s.poi_id not in dropped - must]
        log.append("移除求解器舍弃的：" + "、".join(pois[x].name for x in dropped - must))
        changed = True
    route = state.get("travel")
    quiet_leg = (lambda *points: route.leg(*points, live=False)) if isinstance(route, Travel) else getattr(route, "leg", travel)
    for m in dropped & must:  # 必去被舍弃：改为腾出时间——删掉得分最低的非必去景点
        partner = far_must_partner(m, state.get("day_orders", []), must, quiet_leg)
        if partner is not None:  # 卡在两处必去之间的单段乘车封顶，不是时间不够：删别的景点也排不进，交统一校验说明
            log.append(f"必去「{pois[m].name}」和「{pois[partner[0]].name}」之间{partner[2]}约{partner[1]}分钟，"
                       f"超过单段{HARD_INTERSTOP_RIDE_MIN}分钟上限，删别的景点也排不进同一天，不再为它删点")
            continue
        removable = [s.poi_id for s in sel if s.poi_id not in must]
        if removable:
            worst = min(removable, key=lambda x: scores.get(x, 0))
            sel = [s for s in sel if s.poi_id != worst]
            removed.add(worst)
            log.append(f"必去「{pois[m].name}」放不下：删去得分最低的「{pois[worst].name}」腾出时间")
            changed = True

    # 已为步行超限改了打车的天，这一轮不再为步行删景点；同一天其他时间类违例照旧处理
    bad_days = sorted({v.day for v in report.violations if v.code in TIME_CODES and v.day
                       and not (v.code == "walking_limit" and v.day in taxi_days)})
    for d in bad_days:
        day_ids = [i.ref_id for i in state["itinerary"].days[d - 1].items if i.kind == "poi"]
        removable = [x for x in day_ids if x not in must and any(s.poi_id == x for s in sel)]
        blocked = {violation.item for violation in report.violations if violation.day == d and violation.code == "interstop_ride_limit"}
        if blocked:
            day_items = state["itinerary"].days[d - 1].items
            endpoints = set()
            for index, item in enumerate(day_items):
                if item.name in blocked:
                    before = next((previous.ref_id for previous in reversed(day_items[:index]) if previous.kind == "poi"), None)
                    after = next((following.ref_id for following in day_items[index:] if following.kind == "poi"), None)
                    endpoints.update((before, after))
            nearby = [identifier for identifier in removable if identifier in endpoints]
            removable = nearby or removable
        quality_day = any(v.day == d and v.code in {"excessive_transport", "interstop_ride_limit", "anchor_mixed", "audience_mismatch"} for v in report.violations)
        if quality_day:
            removable = [identifier for identifier in removable if not pois[identifier].day_anchor]
        if removable:
            if quality_day:
                hotel = (p.hotel.lat, p.hotel.lon)
                def detour(identifier: int) -> float:
                    index = day_ids.index(identifier)
                    before = pois[day_ids[index - 1]] if index else None
                    after = pois[day_ids[index + 1]] if index + 1 < len(day_ids) else None
                    origin = (before.lat, before.lon) if before else hotel
                    destination = (after.lat, after.lon) if after else hotel
                    place = pois[identifier]
                    return haversine_km(*origin, place.lat, place.lon) + haversine_km(place.lat, place.lon, *destination) - haversine_km(*origin, *destination)
                worst = max(removable, key=lambda identifier: (detour(identifier), -scores.get(identifier, 0)))
            else:
                worst = min(removable, key=lambda x: scores.get(x, 0))
            sel = [s for s in sel if s.poi_id != worst]
            removed.add(worst)
            log.append(f"第 {d} 天{'减少跨区折返' if quality_day else '时间冲突修复'}：移除「{pois[worst].name}」")
            changed = True
            if not any(identifier != worst and any(s.poi_id == identifier for s in sel) for identifier in day_ids):
                # 底线：不把一天删空。换上离起点比被删地点更近、且之前没删过的候选
                hotel = (p.hotel.lat, p.hotel.lon)
                limit = haversine_km(*hotel, pois[worst].lat, pois[worst].lon)
                chosen = {s.poi_id for s in sel}
                nearer = [c for c in state["candidates"] if c.poi_id not in chosen | removed and not pois[c.poi_id].day_anchor
                          and haversine_km(*hotel, pois[c.poi_id].lat, pois[c.poi_id].lon) < limit]
                if nearer:
                    replacement = max(nearer, key=lambda c: (c.score - haversine_km(*hotel, pois[c.poi_id].lat, pois[c.poi_id].lon) * 0.05, -c.poi_id))
                    sel.append(Selection(poi_id=replacement.poi_id, reason="原地点时间冲突，换成离起点更近的候选，避免当天空白", source="repair"))
                    log.append(f"第 {d} 天不留空：换上离起点更近的「{pois[replacement.poi_id].name}」")

    itinerary = state.get("itinerary")
    for d in sorted({v.day for v in report.violations if v.code == "area_scattered" and v.day}):
        if itinerary is None or d > len(itinerary.days):
            continue
        from .schedule import service_min
        visits = [item.ref_id for item in itinerary.days[d - 1].items if item.kind == "poi" and item.ref_id in pois]
        weight: dict[str, int] = {}
        for identifier in visits:
            zone = areas.area_of(identifier)
            weight[zone] = weight.get(zone, 0) + service_min(pois[identifier], p.pace)
        main = max(weight, key=lambda zone: (weight[zone], -visits.index(next(i for i in visits if areas.area_of(i) == zone))))
        chosen = {selection.poi_id for selection in sel}
        removable = [identifier for identifier in visits if identifier in chosen and identifier not in must
                     and not areas.compatible(areas.area_of(identifier), main, home)]
        if not removable and len(weight) > 2:
            minor = min((zone for zone in weight if zone != main), key=lambda zone: (weight[zone], zone))
            removable = [identifier for identifier in visits if identifier in chosen and identifier not in must and areas.area_of(identifier) == minor]
        # 两个相邻片区也可能因营业时间排成 A→B→A。先修复折返，不能继续往空档加点。
        if not removable and d in area_fill_conflicts(itinerary, pois):
            optional = [identifier for identifier in visits if identifier in chosen and identifier not in must]
            def resolves(identifier: int) -> bool:
                trial = itinerary.model_copy(deep=True)
                trial.days[d - 1].items = [item for item in trial.days[d - 1].items
                                           if item.kind != "poi" or item.ref_id != identifier]
                return d not in area_fill_conflicts(trial, pois)
            removable = [identifier for identifier in optional if resolves(identifier)]
            if not removable:
                # 多个点组成支线时逐轮收缩；不能为消掉提示而删除必去。
                removable = [identifier for identifier in optional if areas.area_of(identifier) != main]
        if removable:
            worst = min(removable, key=lambda identifier: (scores.get(identifier, 0), identifier))
            sel = [selection for selection in sel if selection.poi_id != worst]
            removed.add(worst)
            log.append(f"第 {d} 天片区集中：移除离主片区「{areas.label(main)}」不顺路或造成折返的「{pois[worst].name}」")
            changed = True

    if any(v.code == "over_budget" for v in report.violations):
        chosen = {s.poi_id for s in sel}
        paid = sorted((s for s in sel if pois[s.poi_id].price > 0 and s.poi_id not in must),
                      key=lambda s: -pois[s.poi_id].price)
        swapped = False
        for s in paid:
            old = pois[s.poi_id]
            alts = [c for c in state["candidates"] if c.poi_id not in chosen | removed
                    and pois[c.poi_id].price < old.price and set(pois[c.poi_id].interests) & set(old.interests)]
            if alts:
                new = max(alts, key=lambda c: c.score)
                sel = [Selection(poi_id=new.poi_id, reason=rule_reason(new, p) + "（因预算替换）", source="repair")
                       if x.poi_id == s.poi_id else x for x in sel]
                removed.add(s.poi_id)
                log.append(f"超预算：用「{pois[new.poi_id].name}」替换门票更贵的「{old.name}」")
                swapped = changed = True
                break
        totals = report.totals or {}
        transport = totals.get("transport", 0.0)
        if not swapped and transport_dominates(transport, totals.get("tickets", 0.0) + totals.get("meals", 0.0)):
            # 2026-10-28 用户：预算紧时打车去远处吃便宜饭。钱主要花在路上时，压低餐标只会把饭推得更远，留给用户决定
            log.append(f"超预算主要来自交通（{transport:.0f} 元），不再压低每餐预算")
        elif not swapped and meal_factor > 0.3:
            meal_factor = round(meal_factor * 0.7, 3)
            log.append(f"超预算：每餐预算下调到原来的 {meal_factor:.0%}")
            changed = True
    return sel, meal_factor, log, changed, removed


def repair(state: dict) -> tuple[list[Selection], float, list[str], bool]:
    """保留直接调用者使用的四元组；跨轮排除集合由 graph 节点维护。"""
    selected, factor, log, changed, _ = _repair(state)
    return selected, factor, log, changed


def repair_node(state: dict) -> dict:
    sel, mf, log, changed, removed = _repair(state)
    route = state.get("travel")
    fingerprint = str((sorted(selection.poi_id for selection in sel), mf, sorted(getattr(route, "taxi_overrides", ()))))
    fingerprints = list(state.get("repair_fingerprints", []))
    if fingerprint in fingerprints:
        changed = False
        log.append("修改已重复，停止修复并保留当前结果")
    fingerprints.append(fingerprint)
    rnd = state.get("repair_round", 0) + 1
    return {"selected": sel if changed else state["selected"], "meal_factor": mf, "repair_round": rnd, "repair_changed": changed,
            "repair_fingerprints": fingerprints,
            "repair_pending_additions": sorted({item.poi_id for item in sel} - {item.poi_id for item in state["selected"]}) if changed else [],
            "repair_removed_ids": sorted(removed) if changed else state.get("repair_removed_ids", []),
            "log": [f"修复第 {rnd} 轮：" + ("；".join(log) if log else "无可行修改")]}
