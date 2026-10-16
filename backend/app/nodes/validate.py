from __future__ import annotations

from collections.abc import Callable
from contextlib import nullcontext
from copy import deepcopy

from ..calendar import closed_on, date_notices
from ..data import load_pois, load_restaurants
from ..geo import hm, travel
from ..matching import norm
from ..progress import tick_add
from ..restaurants import STRICT_DIET, dietary_status, meal_start
from ..rules import DINNER, LUNCH, TOL, activity_window, required_meals, walking_limit
from ..schemas import Itinerary, Restaurant, UserProfile, ValidationReport, Violation

TravelFn = Callable[[float, float, float, float], tuple[int, str, float]]
FEASIBILITY = {"unknown_place", "opening_hours", "date_closed", "travel_time", "time_order", "duplicate_poi", "transport_unavailable"}
CONSTRAINT = {"over_budget", "diet_conflict", "day_overlong", "avoid_visited", "must_visit_missing", "walking_limit"}
QUALITY_CODES = {"missing_lunch", "missing_dinner", "early_finish", "long_idle", "short_visit", "empty_day", "excessive_transport", "anchor_mixed", "audience_mismatch", "onsite_meal_unconfirmed"}


def validate_itinerary(it: Itinerary, profile: UserProfile, travel_fn: TravelFn | None = None,
                       restaurants: dict[int, Restaurant] | None = None) -> ValidationReport:
    travel_function = travel_fn or travel
    route = getattr(travel_fn, "__self__", None)
    pois = load_pois()
    restaurants = restaurants if restaurants is not None else load_restaurants()
    violations: list[Violation] = []
    unknowns: set[str] = set()
    seen: set[int] = set()
    visited_names: list[str] = []
    tickets = meals = transport = walking = idle_total = 0.0
    unknown_items = expected_meals = covered_meals = 0

    def add(code: str, message: str, day: int | None = None, item: str | None = None, hard: bool = False) -> None:
        violations.append(Violation(code=code, severity="hard" if hard else "soft", message=message, day=day, item=item))

    if profile.start_date is None:
        unknowns.add("未填写出游日期，具体日期开放情况未核验")
    for day in it.days:
        tick_add("days", 1, f"已核对 {{n}} / {len(it.days)} 天的安排", only={"validate", "repair"})
        window = activity_window(profile, day.day)
        wanted = required_meals(profile, day.day)
        expected_meals += len(wanted)
        matched_meals: set[str] = set()
        position = (profile.hotel.lat, profile.hotel.lon)
        previous_end = None
        first_start = None
        day_walking = 0
        visits = [item for item in day.items if item.kind == "poi"]
        transit_minutes = 0
        transit_limit = min(180, max(120, round((window["end"] - window["start"]) * 0.3)))
        if len(visits) > 1 and any(pois.get(item.ref_id) and pois[item.ref_id].day_anchor for item in visits):
            add("anchor_mixed", "大型园区应作为当天主项目；当前仍与其他景点混排，建议分天或减少必去地点", day.day)
        target = min(window["end"] - 90, 18 * 60 if "夜景夜游" in profile.interests else 16 * 60 + 30)
        if not visits:
            add("empty_day", "当天没有可安排的景点，需调整时段、地点或偏好", day.day)
        elif window["end"] - window["start"] >= 6 * 60 and visits[-1].end < target:
            add("early_finish", f"游览在 {hm(visits[-1].end)} 结束，与所选活动时段相比偏早", day.day)
        context = route.for_day(profile, day.day) if hasattr(route, "for_day") else nullcontext()
        with context:
            for item in day.items:
                place = pois.get(item.ref_id) if item.kind == "poi" else restaurants.get(item.ref_id)
                if item.end < item.start:
                    add("time_order", "结束早于开始", day.day, item.name, True)
                if place is None:
                    unknown_items += 1
                    add("unknown_place", f"「{item.name}」尚未匹配数据，无法核验其位置与开放情况", day.day, item.name, True)
                    if item.kind == "poi":
                        tickets += item.cost
                    else:
                        meals += item.cost
                    previous_end = item.end
                    continue
                needed, mode, cost = travel_function(*position, place.lat, place.lon)
                transit_minutes += needed
                transport += cost
                detail = route.detail(*position, place.lat, place.lon) if hasattr(route, "detail") else item.travel_evidence
                if detail:
                    day_walking += detail.walking_m
                    if not detail.available:
                        add("transport_unavailable", detail.fallback_reason or "没有符合出行方式的路线", day.day, item.name, True)
                    if detail.source == "estimate":
                        unknowns.add("部分交通使用估算，不能作为已核验路线")
                else:
                    day_walking += round(needed * 75) if mode == "步行" else 0
                    unknowns.add("交通含估算或缺少逐段来源")
                if previous_end is not None:
                    gap = item.start - previous_end - needed
                    gap -= sum(slot.end - slot.start for slot in day.reserved_meals
                               if slot.start >= previous_end and slot.end <= item.start)
                    idle_total += max(0, gap)
                    if gap > 90:
                        add("long_idle", f"扣除交通后仍有 {gap} 分钟空档", day.day, item.name)
                    if item.start + TOL < previous_end + needed:
                        add("travel_time", f"上一站 {hm(previous_end)} 结束，约需 {needed} 分钟交通，无法及时到达", day.day, item.name, True)
                if first_start is None:
                    first_start = item.start - needed
                if item.kind == "poi":
                    if place.family_only and profile.companions != "family_kids" and not any(norm(name) in norm(place.name) for name in profile.must_visit if norm(name)):
                        add("audience_mismatch", "儿童游乐场所与当前同行人不匹配", day.day, item.name)
                    tickets += place.price
                    if not place.price_verified:
                        unknowns.add("部分门票仅有历史或规划参考价，0元记录不代表已确认免费")
                    visited_names.append(place.name)
                    if place.id in seen:
                        add("duplicate_poi", "景点重复", day.day, item.name, True)
                    seen.add(place.id)
                    if item.start < place.open_min or item.end > place.close_min:
                        add("opening_hours", f"安排超出已知开放时段 {hm(place.open_min)}–{hm(place.close_min)}", day.day, item.name, True)
                    if closed_on(place, profile, day.day):
                        add("date_closed", "根据已核验的常规规则，该日期不开放", day.day, item.name, True)
                    if place.opening_rules and place.opening_rules.last_entry is not None and item.start > place.opening_rules.last_entry:
                        add("opening_hours", "已超过最后入场时间", day.day, item.name, True)
                    if item.end - item.start + TOL < place.min_hours * 60:
                        add("short_visit", "停留短于已记录建议区间，需核对游览范围", day.day, item.name)
                    if any(norm(name) and (norm(name) in norm(place.name) or norm(place.name) in norm(name)) for name in profile.avoid):
                        add("avoid_visited", "用户明确不去", day.day, item.name, True)
                    if place.walking_m is None:
                        unknowns.add("场所内部步行未完整计入；展示的步行量是已知路段合计")
                    else:
                        day_walking += place.walking_m
                    unknowns.update(date_notices(place, profile, day.day))
                else:
                    meals += place.price
                    if place.hours_known and meal_start(place, item.start, item.end - item.start) != item.start:
                        add("opening_hours", "餐厅时段不覆盖本次用餐", day.day, item.name, True)
                    if not place.hours_known:
                        unknowns.add("部分餐厅营业时段待确认")
                    if place.provider == "chinatravel":
                        unknowns.add("部分餐厅来自历史快照，营业与价格需出发前确认")
                    diet = dietary_status(place, profile)
                    if diet == "conflict":
                        add("diet_conflict", "已知菜系与饮食要求冲突", day.day, item.name, True)
                    elif diet == "unknown" and profile.dietary:
                        unknowns.add("饮食要求缺少商家确认，不能视为认证")
                        strict = STRICT_DIET & set(profile.dietary)
                        if strict:
                            requirement = "、".join(sorted(strict))
                            # 附近没有核验过的餐厅时排程会放宽，这里只提示待确认，不判为行不通
                            add("diet_unverified", f"没有可核验的{requirement}餐厅，这一餐按菜系挑选，须到店确认", day.day, item.name)
                            unknowns.add(f"{requirement}要求没有可核验依据，相关餐厅须到店确认")
                    for kind, bounds in (("lunch", LUNCH), ("dinner", DINNER)):
                        if bounds[0] - 30 <= item.start <= bounds[1] + 30 and item.end - item.start >= 30:
                            matched_meals.add(kind)
                position = (place.lat, place.lon)
                previous_end = item.end
            if previous_end is not None:
                previous_end = max(previous_end, max((slot.end for slot in day.reserved_meals), default=0))
                back, mode, cost = travel_function(*position, profile.hotel.lat, profile.hotel.lon)
                transit_minutes += back
                transport += cost
                detail = route.detail(*position, profile.hotel.lat, profile.hotel.lon) if hasattr(route, "detail") else day.return_leg
                day_walking += detail.walking_m if detail else round(back * 75) if mode == "步行" else 0
                if detail and not detail.available:
                    add("transport_unavailable", "返程没有符合出行方式的方案", day.day, hard=True)
                if detail and detail.source == "estimate":
                    unknowns.add("部分交通使用估算，不能作为已核验路线")
                if previous_end + back > window["end"] + TOL or first_start is not None and first_start < window["start"] - TOL:
                    add("day_overlong", f"行程超出所选时段 {hm(window['start'])}–{hm(window['end'])}", day.day, hard=True)
        if transit_minutes > transit_limit:
            add("excessive_transport", f"当天交通约{transit_minutes}分钟，超过本项目的{transit_limit}分钟舒适度阈值，建议减少跨区地点或调整起点", day.day)
        walking += day_walking
        if day_walking > walking_limit(profile):
            add("walking_limit", f"已知步行 {day_walking / 1000:.1f} 公里，超过每日上限 {walking_limit(profile) / 1000:.1f} 公里", day.day, hard=True)
        for kind in wanted:
            if kind not in matched_meals:
                onsite = next((slot for slot in day.reserved_meals if slot.meal == kind and slot.inside_poi_id is not None
                    and any(visit.ref_id == slot.inside_poi_id and visit.start <= slot.start < slot.end <= visit.end
                            and pois.get(visit.ref_id) and pois[visit.ref_id].onsite_dining for visit in visits)
                    and (LUNCH if kind == "lunch" else DINNER)[0] <= slot.start <= (LUNCH if kind == "lunch" else DINNER)[1]
                    and slot.end - slot.start >= 30), None)
                if onsite:
                    meals += onsite.estimated_cost
                    add("onsite_meal_unconfirmed", f"{'午' if kind == 'lunch' else '晚'}餐已预留园内时间与预算，具体商户和饮食要求尚待确认", day.day)
                    unknowns.add("园内餐费为预算预留，不是已核验商户报价；未计作已落实餐厅")
                else:
                    add(f"missing_{kind}", f"{'午' if kind == 'lunch' else '晚'}餐尚未落实餐厅，预留时段不计作已推荐", day.day)
                    unknowns.add("未落实的餐费尚未计入预算合计")
            else:
                covered_meals += 1
    for required in profile.must_visit:
        if not any(norm(required) in norm(name) or norm(name) in norm(required) for name in visited_names):
            add("must_visit_missing", f"必去景点「{required}」未安排", hard=True)
    total = tickets + meals + transport
    if total > profile.budget + 1e-6:
        add("over_budget", f"预计 {total:.0f} 元，超出预算 {profile.budget:.0f} 元", hard=True)
    passed = not any(item.severity == "hard" for item in violations)
    return ValidationReport(passed=passed, violations=violations,
        experience_status="complete" if passed and not any(item.code in QUALITY_CODES for item in violations) else "needs_attention",
        data_status="partial" if unknowns else "verified", unknowns=sorted(unknowns), totals={
            "tickets":round(tickets, 1), "meals":round(meals, 1), "transport":round(transport, 1), "total":round(total, 1),
            "items":sum(len(day.items) for day in it.days), "unknown_items":unknown_items,
            "feasible":float(not any(item.code in FEASIBILITY for item in violations)), "walking_m":walking,
            "idle_min":idle_total, "expected_meals":expected_meals, "covered_meals":covered_meals,
            "meal_coverage":covered_meals / expected_meals if expected_meals else 1.0})


def validate_node(state: dict) -> dict:
    route = state.get("travel")
    catalog = state.get("catalog")
    restaurants = catalog.restaurants if catalog else load_restaurants()
    if route:
        route.finalize(state["itinerary"], state["profile"], restaurants)
    report = validate_itinerary(state["itinerary"], state["profile"], travel_fn=route.leg if route else None, restaurants=restaurants)
    from ..planning_quality import quality_key
    score = quality_key(state["itinerary"], report)
    best = state.get("best_candidate")
    if best is None or score < best["score"]:
        best = {"score":score, "report":report.model_copy(deep=True),
                **{field:deepcopy(state.get(field, [])) for field in ("itinerary", "selected", "day_orders", "dropped")}}
    return {"report":report, "best_candidate":best,
            "log":[f"校验：硬约束{'通过' if report.passed else '未通过'}；体验{'完整' if report.experience_status == 'complete' else '有待完善'}；仍有 {len(report.unknowns)} 类数据待确认"]}
