"""J2 客观路线指标（docs/评测协议v2.md 第 5 节）。

只读取行程本身、数据表（景点、餐厅）和路线快照，不 import 系统的规划节点、规则、片区、质量分与精修模块
（由 tests/test_objective_metrics.py 检查）。下面的常数按协议在本文件里自行定义，不从系统代码读取。

路程取 AmapRouter.leg_detail 的原始结果：步行可达（实际步行距离不超过步行上限）时用步行分钟；
否则打车 = 驾车分钟 + 5 分钟叫车，公交 = 公交方案分钟。公交用户若允许打车兜底（画像 allow_taxi_fallback），取公交与打车中较快者。
行程标明某段打车或公交/地铁时（2026-10-26 起），这段按标明的方式计分钟、车费和步行，步行可达也不改算成走路；
标明步行或没有标明（纯大模型、A′、R 的输出没有交通方式）的段仍按上面的规则。前一项是库外地点时不采用标明方式（起点不同）。
车费：公交用方案票价，打车用高德同起终点的打车估价。查不到的路段记为缺失，不用估算补。
出发时刻（2026-10-26 起）：公交按每段的出发时刻查（行程项记下的 travel_depart / return_depart；没有时取上一项结束时刻，
首段取当天活动时段开始），时刻取 30 分钟一档；日期取出游日期，没填时取评测冻结的参考日 TTA_REFERENCE_DATE。
驾车接口不支持出发时刻，开车分钟是快照里查询时的路况。
正餐：行程里的餐厅项之外，园内用餐的预留时段（ReservedMeal.inside_poi_id 指向当天游览的景点，且时段落在该景点的游览时间内）
也算这一餐，其预估花费计入预算。另报不计园内用餐的口径（A′、R 的输出格式表达不了园内用餐）。
步行上限有两种口径：主口径 J2 一律 1.5 km；敏感性口径 J2′ 按系统的步行规则（见 system_walk_limit_m）。

用户约束 U1–U5（2026-10-26 增补）：必去、回避、口味（不吃辣）、当日活动时段、每日步行。规则与关键词在本文件定义，
不调用系统的匹配或菜系判断；名称匹配为去掉通用后缀与括号分店名后的双向包含。
"客观可行"不变；"约束可行" = 客观可行且 U1–U5 全部通过。
"""
from __future__ import annotations

from collections.abc import Callable
import copy
from datetime import date, timedelta
from itertools import combinations
import math
import os
import re
import statistics
from typing import Any

from app.schemas import POI, Itinerary, Restaurant, UserProfile

TOL_MIN = 5            # 时间冲突容差
IDLE_FREE_MIN = 30     # 相邻两项之间不计为空档的等候分钟
TAXI_WAIT_MIN = 5      # 打车叫车等待
LUNCH = (11 * 60, 14 * 60)
DINNER = (17 * 60, 20 * 60 + 30)
MEAL_OVERLAP_MIN = 60  # 活动时段与正餐时段重叠不少于这么久，才要求这一餐
DEFAULT_WINDOW = (8 * 60 + 30, 21 * 60 + 30)  # 用户没填活动时段时的宽松默认
PROTOCOL_WALK_M = 1500                         # 主口径：单段实际步行不超过 1.5 km 才按步行算
SYSTEM_WALK_M = {"adult": 2500, "family_kids": 1500, "elderly": 1500}  # 敏感性口径，取值与系统 Travel.set_day 相同
# U5 每日步行上限（含公交换乘中的步行，不含景区内部）：用户自填时用用户的；否则取值与系统规则相同
DAILY_WALK_M = {"elderly": 3500, "family_kids": 4500}
DEFAULT_DAILY_WALK_M = 8000
# U1、U2 名称匹配前去掉的通用后缀
NAME_SUFFIXES = ("风景名胜区", "风景区", "旅游区", "景区", "遗址公园")
# U3 口味：不吃辣时，店名或菜系含下列关键词即算冲突（2026-10-26 起忌口改为辣度三档，只有"不吃辣"是硬约束）
SPICY_WORDS = ("辣", "川菜", "湘菜", "湖南菜", "火锅", "串串", "冒菜", "贵州菜", "江西菜", "重庆")
RIDES = ("打车", "公交/地铁")  # 行程标明这两种方式时按标明的算
DEPART_SLOT_MIN = 30  # 公交按出发时刻查询时取整到这么多分钟一档

# leg(lat1, lon1, lat2, lon2, ride=False[, when=(日期, 出发分钟)])：ride 为真时步行可达也要给出驾车与公交数据；
# 带 timed 属性的查找函数（snapshot_leg）才接受 when
LegLookup = Callable[..., dict | None]


def depart_slot(minute: int) -> str:
    value = max(0, int(minute)) // DEPART_SLOT_MIN * DEPART_SLOT_MIN % (24 * 60)
    return f"{value // 60:02d}:{value % 60:02d}"


def route_day(profile: UserProfile, number: int) -> date | None:
    """查公交用的日期：出游日期；没填时为评测冻结的参考日（TTA_REFERENCE_DATE）；都没有则不带日期查。"""
    if profile.start_date:
        return profile.start_date + timedelta(days=number - 1)
    pinned = os.environ.get("TTA_REFERENCE_DATE")
    return date.fromisoformat(pinned) + timedelta(days=number - 1) if pinned else None


def system_walk_limit_m(profile: UserProfile) -> int:
    """敏感性口径 J2′ 的步行上限：成人 2.5 km、亲子与老人 1.5 km，用户自填步行上限时取更小值。

    数值与系统路程策略一致，但在本模块自行定义，不 import 系统代码。
    """
    base = SYSTEM_WALK_M.get(profile.companions, SYSTEM_WALK_M["adult"])
    return min(base, profile.max_walking_m or base)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(a))


def leg_choice(detail: dict | None, transport: str, allow_taxi: bool = False,
               stated: str = "") -> tuple[float | None, float | None, float]:
    """（分钟, 车费, 步行米）；分钟或车费无法判定时为 None。

    stated 是行程标明的方式：打车、公交/地铁且快照里有对应数据时照用；否则步行可达用步行，
    不可达时打车，公交用户按公交（允许打车兜底时取公交与打车中较快者）。步行米：步行段取实际步行距离，
    公交段取方案内的步行距离，打车为 0。
    """
    if not detail:
        return None, None, 0.0
    walk = detail.get("walk") or {}
    drive, transit = detail.get("drive") or None, detail.get("transit")
    transit = transit if isinstance(transit, dict) else None
    taxi = ((float(drive["min"]) + TAXI_WAIT_MIN, transit.get("taxi_cost") if transit else None, 0.0)
            if drive and drive.get("min") is not None else None)
    bus = (float(transit["min"]), transit.get("cost"), float(transit.get("walk_m") or 0)) if transit else None
    if stated == "打车" and taxi:
        return taxi
    if stated == "公交/地铁" and bus:
        return bus
    if walk.get("min") is not None:
        return float(walk["min"]), 0.0, float(walk.get("m") or 0)
    if transport == "transit" and bus:
        return taxi if allow_taxi and taxi and taxi[0] < bus[0] else bus
    return taxi or (None, None, 0.0)


def _norm(name: str) -> str:
    name = re.sub(r"[（(][^）)]*[）)]", "", name or "").strip()
    for suffix in NAME_SUFFIXES:
        if name.endswith(suffix) and len(name) > len(suffix) + 1:
            name = name[: -len(suffix)]
    return name


def name_matches(target: str, name: str) -> bool:
    """U1、U2 的名称匹配：去掉通用后缀和括号分店名后，两者至少两个字且一方包含另一方。"""
    t, n = _norm(target), _norm(name)
    return len(t) >= 2 and len(n) >= 2 and (t in n or n in t)


def no_spicy(profile: UserProfile) -> bool:
    """用户是否不吃辣。旧画像（2026-10-26 以前）没有 spice 字段，从 dietary 里读，以便重算旧运行。"""
    spice = getattr(profile, "spice", None)
    return spice == "no" if spice is not None else "不吃辣" in (getattr(profile, "dietary", None) or [])


def spicy_word(restaurant: Restaurant) -> str:
    """店名或菜系里命中的辣味关键词；没有则为空串。"""
    text = f"{restaurant.name} {restaurant.cuisine}"
    return next((word for word in SPICY_WORDS if word in text), "")


def daily_walk_limit_m(profile: UserProfile) -> int:
    return profile.max_walking_m or DAILY_WALK_M.get(profile.companions, DEFAULT_DAILY_WALK_M)


def _hm(minutes: float) -> str:
    return f"{int(minutes) // 60:02d}:{int(minutes) % 60:02d}"


def _window(profile: UserProfile) -> tuple[int, int]:
    return (profile.day_start if profile.day_start is not None else DEFAULT_WINDOW[0],
            profile.day_end if profile.day_end is not None else DEFAULT_WINDOW[1])


def _day_window(profile: UserProfile, number: int) -> tuple[int, int, bool]:
    """当天活动时段与"用户是否给了结束时间"（给了才要求返程在结束前到家）。"""
    for window in profile.daily_windows:
        if window.day == number:
            return window.start, window.end, True
    start, end = _window(profile)
    return start, end, profile.day_end is not None


def _expected_meals(profile: UserProfile) -> list[tuple[int, int]]:
    start, end = _window(profile)
    return [meal for meal in (LUNCH, DINNER) if min(end, meal[1]) - max(start, meal[0]) >= MEAL_OVERLAP_MIN]


def _open_interval(poi: POI) -> tuple[int, int]:
    close = poi.close_min if poi.close_min > poi.open_min else poi.close_min + 1440
    return poi.open_min, close


def objective_metrics(itinerary: Itinerary, profile: UserProfile, leg: LegLookup,
                      pois: dict[int, POI], restaurants: dict[int, Restaurant]) -> dict[str, Any]:
    home = (profile.hotel.lat, profile.hotel.lon)
    days = {day.day: day for day in itinerary.days}
    per_day: list[dict[str, float]] = []
    conflicts = opening = unknown = missing_legs = missing_fares = short = visits = duplicates = onsite_meals = 0
    known_visits = landmark_visits = 0  # O13：去了几处知名景点、城市地标（描述性，不计入可行判定）
    covered = expected = 0
    max_leg = 0.0
    spend = 0.0
    seen: set[int] = set()
    meals_needed = _expected_meals(profile)
    # 用户约束 U1–U5
    must_seen = {target: False for target in profile.must_visit}
    avoided = spice_conflicts = window_violations = walk_days_over = covered_no_onsite = 0
    max_daily_walk = 0.0
    details: list[str] = []
    walk_cap = daily_walk_limit_m(profile)
    avoid_spicy = no_spicy(profile)
    timed = getattr(leg, "timed", False)
    for number in range(1, profile.days + 1):
        day = days.get(number)
        items = day.items if day else []
        stops: list[tuple[Any, tuple[float, float]]] = []
        stated: list[str] = []  # 到达每一站时行程标明的交通方式；紧跟库外地点的一站不采用
        departs: list[int | None] = []  # 每一段的出发时刻（分钟）
        after_unknown = False
        previous_end: int | None = None
        w_start, w_end, user_end = _day_window(profile, number)
        for item in items:
            depart = item.travel_depart if item.travel_depart is not None and not after_unknown else previous_end
            previous_end = item.end
            if item.start < w_start - TOL_MIN or item.end > w_end + TOL_MIN:
                window_violations += 1
                details.append(f"U4 第{number}天「{item.name}」{_hm(item.start)}–{_hm(item.end)} 超出活动时段 {_hm(w_start)}–{_hm(w_end)}")
            if item.kind == "poi":  # 库外地点也按名称参与必去、回避判断
                names = [item.name] + ([pois[item.ref_id].name] if item.ref_id in pois else [])
                for target in must_seen:
                    must_seen[target] = must_seen[target] or any(name_matches(target, name) for name in names)
                for target in profile.avoid:
                    if any(name_matches(target, name) for name in names):
                        avoided += 1
                        details.append(f"U2 第{number}天安排了回避地点「{target}」（{item.name}）")
            place = pois.get(item.ref_id) if item.kind == "poi" else restaurants.get(item.ref_id)
            if item.ref_id is None or place is None:
                unknown += 1
                after_unknown = True
                continue
            if item.kind != "poi" and avoid_spicy and (hit := spicy_word(place)):
                spice_conflicts += 1
                details.append(f"U3 第{number}天「{item.name}」（{place.cuisine}）与“不吃辣”冲突（含“{hit}”）")
            stops.append((item, (place.lat, place.lon)))
            stated.append("" if after_unknown else item.travel_mode)
            departs.append(depart)
            after_unknown = False
            if item.kind == "poi":
                visits += 1
                known_visits += getattr(place, "popularity", "") in {"landmark", "known"}
                landmark_visits += getattr(place, "popularity", "") == "landmark"
                duplicates += item.ref_id in seen
                seen.add(item.ref_id)
                spend += place.price
                start, close = _open_interval(place)
                if item.start < start - TOL_MIN or item.end > close + TOL_MIN:
                    opening += 1
                if item.end - item.start < place.min_hours * 60 - TOL_MIN:
                    short += 1
            else:
                spend += place.price
        onsite = [slot for slot in (day.reserved_meals if day else []) if slot.inside_poi_id is not None and any(
                  item.kind == "poi" and item.ref_id == slot.inside_poi_id
                  and item.start - TOL_MIN <= slot.start and slot.end <= item.end + TOL_MIN for item in items)]
        onsite_meals += len(onsite)
        spend += sum(slot.estimated_cost for slot in onsite)
        expected += len(meals_needed)
        covered += sum(any(item.kind == "meal" and low <= item.start <= high for item in items)
                       or any(low <= slot.start <= high for slot in onsite) for low, high in meals_needed)
        covered_no_onsite += sum(any(item.kind == "meal" and low <= item.start <= high for item in items)
                                 for low, high in meals_needed)
        points = [home] + [point for _, point in stops] + ([home] if stops else [])
        back = day.return_leg.mode if day and day.return_leg and not after_unknown else ""
        modes = stated + [back]
        departs.append(day.return_depart if day and day.return_depart is not None and not after_unknown else previous_end)
        when_day = route_day(profile, number)
        travel = idle = walked = 0.0
        for index, (a, b) in enumerate(zip(points, points[1:])):
            ride = modes[index] in RIDES
            timing = {"when": (when_day, w_start if departs[index] is None else departs[index])} if timed else {}
            detail = (leg(*a, *b, ride=True, **timing) if ride else leg(*a, *b, **timing)) if a != b else None
            minutes, fare, walk_m = (leg_choice(detail, profile.transport, profile.allow_taxi_fallback, modes[index])
                                     if a != b else (0.0, 0.0, 0.0))
            if minutes is None:
                missing_legs += 1
                continue
            walked += walk_m
            if fare is None:
                missing_fares += 1
            else:
                spend += fare
            travel += minutes
            max_leg = max(max_leg, minutes)
            if 0 < index < len(points) - 2:  # 两个行程项之间；首段出发和末段返程没有声称的时间可比
                before, after = stops[index - 1][0], stops[index][0]
                gap = after.start - before.end
                if gap < minutes - TOL_MIN:
                    conflicts += 1
                idle += max(0.0, gap - minutes - IDLE_FREE_MIN)
            elif index == len(points) - 2 and user_end and stops[-1][0].end + minutes > w_end + TOL_MIN:
                window_violations += 1  # 用户给了结束时间：返程须在结束前到家
                details.append(f"U4 第{number}天返程约 {_hm(stops[-1][0].end + minutes)} 到家，晚于 {_hm(w_end)}")
        max_daily_walk = max(max_daily_walk, walked)
        if walked > walk_cap:
            walk_days_over += 1
            details.append(f"U5 第{number}天已知步行 {walked / 1000:.1f} km，超过每日上限 {walk_cap / 1000:.1f} km")
        poi_points = [point for item, point in stops if item.kind == "poi"]
        per_day.append({"travel_min": travel, "idle_min": idle, "pois": len(poi_points),
                        "diameter_km": max((haversine_km(*a, *b) for a, b in combinations(poi_points, 2)), default=0.0),
                        "path_km": sum(haversine_km(*a, *b) for a, b in zip(points, points[1:]))})
    mean = lambda key: statistics.mean(day[key] for day in per_day) if per_day else 0.0  # noqa: E731
    coverage = covered / expected if expected else 1.0
    coverage_no_onsite = covered_no_onsite / expected if expected else 1.0
    deviation = spend / profile.budget - 1
    judgeable = missing_legs == 0  # 车费缺失只让预算偏差偏低（对被评方法有利），单独报告 missing_fares
    must_missing = [target for target, seen_it in must_seen.items() if not seen_it]
    details[:0] = [f"U1 必去「{target}」未安排" for target in must_missing]
    feasible = (conflicts == 0 and opening == 0 and duplicates == 0 and coverage == 1.0
                and deviation <= 0 and unknown == 0) if judgeable else None
    constraints_ok = not (must_missing or avoided or spice_conflicts or window_violations or walk_days_over)
    both = lambda flag, extra: None if flag is None else bool(flag and extra)  # noqa: E731
    return {
        "O1_travel_min_per_day": round(mean("travel_min"), 1),
        "O2_max_leg_min": round(max_leg, 1),
        "O3_time_conflicts": conflicts,
        "O4_opening_conflicts": opening,
        "O5_idle_min_per_day": round(mean("idle_min"), 1),
        "O6_diameter_km_per_day": round(mean("diameter_km"), 2),
        "O7_path_km_per_day": round(mean("path_km"), 2),
        "O8_short_visit_rate": round(short / visits, 3) if visits else 0.0,
        "O9_meal_coverage": round(coverage, 3),
        "O10_budget_deviation": round(deviation, 3),
        "O11_pois_per_day": round(mean("pois"), 2),
        "O12_duplicate_pois": duplicates,
        # O13 知名度（描述性）：景点中知名景点与城市地标的占比、城市地标处数；画像熟悉程度另行分组报告
        "O13_known_share": round(known_visits / visits, 3) if visits else 0.0, "O13_landmark_visits": landmark_visits,
        "unknown_items": unknown, "missing_legs": missing_legs, "missing_fares": missing_fares, "onsite_meals": onsite_meals,
        "objective_feasible": feasible,
        # 不计园内用餐（A′、R 表达不了）：缺的那一餐算未覆盖
        "O9_meal_coverage_no_onsite": round(coverage_no_onsite, 3),
        "objective_feasible_no_onsite": both(feasible, coverage_no_onsite == 1.0),
        # 用户约束 U1–U5；约束可行 = 客观可行且 U1–U5 全部通过
        "U1_must_missing": len(must_missing), "U2_avoid_visited": avoided,
        "U3_spice_conflicts": spice_conflicts,
        "U4_window_violations": window_violations, "U5_walk_days_over": walk_days_over,
        "max_daily_walk_m": round(max_daily_walk), "daily_walk_limit_m": walk_cap,
        "constraints_ok": constraints_ok,
        "constraint_feasible": both(feasible, constraints_ok),
        "constraint_feasible_no_onsite": both(feasible, constraints_ok and coverage_no_onsite == 1.0),
        "constraint_details": details,
    }


def snapshot_leg(router, walk_limit_m: int = PROTOCOL_WALK_M) -> LegLookup:
    """把 AmapRouter.leg_detail 包成查不到时返回 None 的函数（离线回放缺快照、额度或接口错误都算缺失）。

    步行上限只设在浅拷贝上：与原路由器共用缓存和快照文件，但不改原路由器的设置。
    when=(日期, 出发分钟)：按这一天、这一档出发时刻查公交（快照键带日期与时刻）；日期为 None 时不带时刻查。
    """
    view = copy.copy(router)
    view.walk_limit_m = walk_limit_m

    def lookup(lat1: float, lon1: float, lat2: float, lon2: float, ride: bool = False,
               when: tuple[date | None, int] | None = None) -> dict | None:
        try:
            if when is not None:
                view.service_date, view.departure_time = when[0], depart_slot(when[1])
            detail = view.leg_detail(lat1, lon1, lat2, lon2)
            if ride and (detail.get("walk") or {}).get("min"):  # 标明乘车的段步行可达时，补上驾车与公交（原地不补）
                from app.amap import pt
                o, d = pt(lat1, lon1), pt(lat2, lon2)
                detail = {**detail, "drive": view.drive(o, d), "transit": view.transit(o, d)}
            return detail
        except Exception:  # noqa: BLE001 —— 缺失如实计数，由调用方报告
            return None
    lookup.timed = True
    return lookup
