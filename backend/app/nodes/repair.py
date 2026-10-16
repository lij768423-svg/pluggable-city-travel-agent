"""修复：按违例类型改选点，然后回到调度（最多 3 轮）。

- 某天出现 营业时间/交通时间/超时 → 删掉该天得分最低的非必去景点
- 超预算 → 先把门票最贵的景点换成同兴趣、更便宜的未选候选；没有可换的就降低每餐预算（×0.7）
- 求解器舍弃的景点 → 从选点中移除；若舍弃的是必去景点，改为删掉得分最低的非必去景点
无法再改动时返回 changed=False，由图结束循环。
"""
from __future__ import annotations

from ..data import load_pois
from ..geo import haversine_km
from ..schemas import Selection
from .select import rule_reason

TIME_CODES = {"opening_hours", "travel_time", "day_overlong", "walking_limit", "transport_unavailable", "date_closed", "excessive_transport", "anchor_mixed", "audience_mismatch"}
MAX_ROUNDS = 3


def repair(state: dict) -> tuple[list[Selection], float, list[str], bool]:
    pois = load_pois()
    p, report = state["profile"], state["report"]
    sel: list[Selection] = list(state["selected"])
    scores = {c.poi_id: c.score for c in state["candidates"]}
    must = {c.poi_id for c in state["candidates"] if c.breakdown.get("must_visit")}
    meal_factor = state.get("meal_factor", 1.0)
    log: list[str] = []
    changed = False

    quality_codes = {violation.code for violation in report.violations}
    if report.passed and not quality_codes & {"excessive_transport", "anchor_mixed", "audience_mismatch"} and quality_codes & {"early_finish", "long_idle", "empty_day"}:
        chosen = {selection.poi_id for selection in sel}
        alternatives = [candidate for candidate in state["candidates"] if candidate.poi_id not in chosen
                        and pois[candidate.poi_id].stamina <= (2 if p.companions in {"family_kids", "elderly"} else 3)]
        if alternatives and len(sel) < p.days * 5 and sum(pois[item.poi_id].day_anchor for item in sel) < p.days:
            addition = max(alternatives, key=lambda candidate: candidate.score - min(
                (haversine_km(pois[candidate.poi_id].lat, pois[candidate.poi_id].lon, pois[selection.poi_id].lat, pois[selection.poi_id].lon) for selection in sel), default=0) * 0.05)
            sel.append(Selection(poi_id=addition.poi_id, reason="根据可用时段补充同片区候选，重新校验时间与交通", source="repair"))
            log.append(f"体验修复：尝试补入{pois[addition.poi_id].name}")
            changed = True
    if report.passed and quality_codes & {"missing_lunch", "missing_dinner"} and not changed:
        removable = [selection for selection in sel if selection.poi_id not in must]
        if removable and len(sel) > p.days:
            worst = min(removable, key=lambda selection: scores.get(selection.poi_id, 0))
            sel.remove(worst)
            log.append(f"为用餐与交通腾出时间：移除{pois[worst.poi_id].name}")
            changed = True

    dropped = set(state.get("dropped", []))
    if dropped - must:
        sel = [s for s in sel if s.poi_id not in dropped - must]
        log.append("移除求解器舍弃的：" + "、".join(pois[x].name for x in dropped - must))
        changed = True
    for m in dropped & must:  # 必去被舍弃：改为腾出时间——删掉得分最低的非必去景点
        removable = [s.poi_id for s in sel if s.poi_id not in must]
        if removable:
            worst = min(removable, key=lambda x: scores.get(x, 0))
            sel = [s for s in sel if s.poi_id != worst]
            log.append(f"必去「{pois[m].name}」放不下：删去得分最低的「{pois[worst].name}」腾出时间")
            changed = True

    bad_days = sorted({v.day for v in report.violations if v.code in TIME_CODES and v.day})
    for d in bad_days:
        day_ids = [i.ref_id for i in state["itinerary"].days[d - 1].items if i.kind == "poi"]
        removable = [x for x in day_ids if x not in must and any(s.poi_id == x for s in sel)]
        quality_day = any(v.day == d and v.code in {"excessive_transport", "anchor_mixed", "audience_mismatch"} for v in report.violations)
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
            log.append(f"第 {d} 天{'减少跨区折返' if quality_day else '时间冲突修复'}：移除「{pois[worst].name}」")
            changed = True

    if any(v.code == "over_budget" for v in report.violations):
        chosen = {s.poi_id for s in sel}
        paid = sorted((s for s in sel if pois[s.poi_id].price > 0 and s.poi_id not in must),
                      key=lambda s: -pois[s.poi_id].price)
        swapped = False
        for s in paid:
            old = pois[s.poi_id]
            alts = [c for c in state["candidates"] if c.poi_id not in chosen
                    and pois[c.poi_id].price < old.price and set(pois[c.poi_id].interests) & set(old.interests)]
            if alts:
                new = max(alts, key=lambda c: c.score)
                sel = [Selection(poi_id=new.poi_id, reason=rule_reason(new, p) + "（因预算替换）", source="repair")
                       if x.poi_id == s.poi_id else x for x in sel]
                log.append(f"超预算：用「{pois[new.poi_id].name}」替换门票更贵的「{old.name}」")
                swapped = changed = True
                break
        if not swapped and meal_factor > 0.3:
            meal_factor = round(meal_factor * 0.7, 3)
            log.append(f"超预算：每餐预算下调到原来的 {meal_factor:.0%}")
            changed = True
    return sel, meal_factor, log, changed


def repair_node(state: dict) -> dict:
    sel, mf, log, changed = repair(state)
    fingerprint = str((sorted(selection.poi_id for selection in sel), mf))
    fingerprints = list(state.get("repair_fingerprints", []))
    if fingerprint in fingerprints:
        changed = False
        log.append("修改已重复，停止修复并保留当前结果")
    fingerprints.append(fingerprint)
    rnd = state.get("repair_round", 0) + 1
    return {"selected": sel if changed else state["selected"], "meal_factor": mf, "repair_round": rnd, "repair_changed": changed,
            "repair_fingerprints": fingerprints,
            "log": [f"修复第 {rnd} 轮：" + ("；".join(log) if log else "无可行修改")]}
