"""行程合理性评分（评测用，不参与排程）。

校验器 validate_itinerary 回答"能不能走通"；这里回答"像不像一个正常人会这样安排"：
按维度给 0–100 分，并逐条列出扣分依据。全部阈值是项目启发式，定义见
docs/计划合理性指标.md，尚未用人工评价标定。

停留参考区间默认取景点表 min/max_hours，与排程用的是同一份数据（同源偏差），
停留分只有在传入独立参考（references）时才有判别力；coverage 字段如实报告覆盖情况。
"""
from __future__ import annotations

import itertools
import csv
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from typing import Callable

from .experiences import experience_key
from .geo import haversine_km, hm, travel
from .rules import DINNER, LUNCH, activity_window
from .schemas import POI, DayPlan, Itinerary, Restaurant, StayReference, UserProfile

WEIGHTS = {"stay": .2, "transport": .2, "fit": .15, "pace": .15, "timing": .1, "slack": .1, "variety": .1}
LABELS = {"stay": "停留时长", "transport": "路程", "fit": "项目匹配", "pace": "节奏强度",
          "timing": "时段", "slack": "时间利用", "variety": "多样性"}
AUDIENCE = {"solo": "独行", "couple": "情侣", "friends": "朋友", "family_kids": "亲子", "elderly": "老人"}
EXPERIENCES = {"relaxed": (2, 3), "normal": (3, 4), "intense": (3, 5)}   # 每天体验数合理区间
ACTIVE_HOURS = {"relaxed": 9.0, "normal": 10.5, "intense": 12.0}        # 出门到回到起点的合理上限
PACE_NAMES = {"relaxed": "舒缓", "normal": "适中", "intense": "紧凑"}
SUNSET = 17 * 60 + 30                                                    # 秋季杭州日落约 17:30（启发式）

Reference = tuple[float, float, str]   # (下限分钟, 上限分钟, 来源)
TravelFn = Callable[[float, float, float, float], tuple[int, str, float]]
STAY_REFERENCE_PATH = Path(__file__).parent / "assets/stay_reference.json"
STAY_AUDIT_PATH = Path(__file__).parent.parent / "eval/stay_audit/top50.csv"


def _stay_interval(value: str) -> tuple[float, float]:
    matched = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*[-–—~至]\s*(\d+(?:\.\d+)?)\s*(?:分钟|min)?\s*", value)
    if not matched:
        raise ValueError("人工停留结论须为分钟区间，如30-60分钟，或采用LLM/采用数据集")
    low, high = map(float, matched.groups())
    if not 0 < low <= high <= 1440:
        raise ValueError("人工停留区间须满足0 < low <= high <= 1440")
    return low, high


def load_stay_references(path: Path = STAY_REFERENCE_PATH, audit_path: Path | None = STAY_AUDIT_PATH,
                         pois: dict[int, POI] | None = None) -> dict[int, Reference]:
    artifact = json.loads(Path(path).read_text(encoding="utf-8"))
    references: dict[int, Reference] = {}
    names: dict[int, str] = {}
    for record in artifact["places"]:
        place = StayReference.model_validate(record)
        if place.poi_id in references:
            raise ValueError(f"独立参考POI重复：{place.poi_id}")
        if pois is not None and (place.poi_id not in pois or pois[place.poi_id].name != place.name):
            raise ValueError(f"独立参考POI身份不匹配：{place.poi_id} {place.name}")
        references[place.poi_id] = (place.low_min, place.high_min, place.source)
        names[place.poi_id] = place.name
    if audit_path is not None and Path(audit_path).exists():
        reviewed: set[int] = set()
        with Path(audit_path).open(encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                conclusion = row.get("人工结论", "").strip()
                if not conclusion or conclusion in {"待核", "不确定"}:
                    continue
                identifier = int(row["poi_id"])
                if identifier in reviewed or names.get(identifier) != row["name"]:
                    raise ValueError(f"人工核对POI身份不匹配或重复：{identifier}")
                required = ("来源 URL", "原文摘录", "核对人", "核对日期")
                if any(not row.get(key, "").strip() for key in required):
                    raise ValueError(f"人工核对缺来源、原文、核对人或日期：{identifier}")
                date.fromisoformat(row["核对日期"].strip())
                if not row["来源 URL"].strip().startswith(("https://", "http://")):
                    raise ValueError(f"人工核对来源须为公开URL：{identifier}")
                if conclusion == "采用LLM":
                    interval = references[identifier][:2]
                elif conclusion == "采用数据集":
                    interval = _stay_interval(row["数据集区间"])
                else:
                    interval = _stay_interval(conclusion)
                references[identifier] = (*interval, "manual")
                reviewed.add(identifier)
    return references


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def ramp(value: float, good: float, bad: float) -> float:
    """value 在 good 及更好时得 1，到 bad 时得 0，中间线性。"""
    return clamp((bad - value) / (bad - good)) if bad != good else float(value <= good)


def default_reference(place: POI) -> Reference:
    source = "dataset" if place.duration_source.startswith("ChinaTravel") else "project_review"
    return place.min_hours * 60, place.max_hours * 60, source


def must_named(place: POI, profile: UserProfile) -> bool:
    return any(name and (name in place.name or place.name in name) for name in profile.must_visit)


def score_itinerary(itinerary: Itinerary, profile: UserProfile, pois: dict[int, POI],
                    restaurants: dict[int, Restaurant] | None = None,
                    references: dict[int, Reference] | None = None,
                    travel_fn: TravelFn = travel) -> dict:
    restaurants = restaurants or {}
    references = references or {}
    issues: list[dict] = []
    stay_details: list[dict] = []
    parts: dict[str, list[float]] = {key: [] for key in WEIGHTS}
    coverage: Counter = Counter()

    def flag(dimension: str, message: str, day: int | None = None, item: str | None = None, severe: bool = False) -> None:
        issues.append({"dimension": dimension, "day": day, "item": item, "message": message, "severe": severe})

    hotel = (profile.hotel.lat, profile.hotel.lon)
    day_places: list[list[POI]] = []
    for day in itinerary.days:
        visits = [(item, pois[item.ref_id]) for item in day.items if item.kind == "poi" and item.ref_id in pois]
        day_places.append([place for _, place in visits])
        window = activity_window(profile, day.day)
        stay_minutes = sum(item.end - item.start for item, _ in visits)
        meal_minutes = sum(item.end - item.start for item in day.items if item.kind == "meal")

        # 1 停留时长：实际停留偏离参考区间的相对幅度
        for item, place in visits:
            low, high, source = references.get(place.id) or default_reference(place)
            coverage[f"stay:{source}"] += 1
            stay = item.end - item.start
            deviation = (low - stay) / low if stay < low else (stay - high) / high if stay > high else 0.0
            parts["stay"].append(clamp(1 - deviation))
            stay_details.append({"poi_id": place.id, "name": place.name, "day": day.day,
                                 "actual_min": stay, "low_min": low, "high_min": high, "source": source,
                                 "score": round(100 * clamp(1 - deviation), 3),
                                 "penalty": round(100 * (1 - clamp(1 - deviation)), 3)})
            if deviation > .25:
                flag("stay", f"停留 {stay} 分钟，参考 {low:.0f}–{high:.0f} 分钟（{source}）", day.day, item.name, deviation > .6)
            if place.visit_role in {"viewpoint", "walk_segment"} and stay > 60:
                flag("stay", f"观景/步行段停留 {stay} 分钟偏长", day.day, item.name)

        # 2 路程：坐车占比、最长单程、日内绕路、用餐绕路
        ride = longest = 0
        position = hotel
        sequence = [item for item in day.items if item.ref_id is not None]
        for item in sequence:
            if item.travel_mode not in {"步行", "原地", ""}:
                ride += item.travel_min
                longest = max(longest, item.travel_min)
                if item.travel_min > 60:
                    flag("transport", f"单程乘车约 {item.travel_min} 分钟", day.day, item.name)
            place = pois.get(item.ref_id) if item.kind == "poi" else restaurants.get(item.ref_id)
            if place:
                position = (place.lat, place.lon)
        if day.return_travel_min and travel_fn(*position, *hotel)[1] not in {"步行", "原地"}:
            ride += day.return_travel_min
        ratio = ride / (ride + stay_minutes + meal_minutes) if ride + stay_minutes + meal_minutes else 0
        ride_score = ramp(ratio, .2, .5)
        if ratio > .35:
            flag("transport", f"乘车占出行时间 {ratio:.0%}（乘车 {ride} 分钟）", day.day, severe=ratio > .5)
        detour_score = 1.0
        points = [(place.lat, place.lon) for place in day_places[-1]]
        if 3 <= len(points) <= 8:
            def length(order) -> float:
                path = [hotel, *order, hotel]
                return sum(haversine_km(*a, *b) for a, b in zip(path, path[1:]))
            actual = length(points)
            best = min(length(order) for order in itertools.permutations(points))
            detour = actual / best if best > .5 else 1.0
            detour_score = ramp(detour, 1.15, 1.6)
            if detour > 1.3:
                flag("transport", f"游览顺序比最短顺路多走 {detour - 1:.0%} 直线距离，存在折返", day.day)
        meal_penalty = 0.0
        for index, item in enumerate(day.items):
            if item.kind != "meal" or item.ref_id not in restaurants or index == 0 or index + 1 >= len(day.items):
                continue
            before, after = day.items[index - 1], day.items[index + 1]
            a = pois.get(before.ref_id) if before.kind == "poi" else restaurants.get(before.ref_id)
            b = pois.get(after.ref_id) if after.kind == "poi" else restaurants.get(after.ref_id)
            meal = restaurants[item.ref_id]
            if a and b:
                extra = (travel_fn(a.lat, a.lon, meal.lat, meal.lon)[0] + travel_fn(meal.lat, meal.lon, b.lat, b.lon)[0]
                         - travel_fn(a.lat, a.lon, b.lat, b.lon)[0])
                if extra > 25:
                    meal_penalty += .15
                    flag("transport", f"为吃饭多绕约 {extra} 分钟", day.day, item.name)
        parts["transport"].append(clamp(.7 * ride_score + .3 * detour_score - meal_penalty))

        # 3 项目匹配：人群、体力、兴趣（必去地点豁免）
        wanted = AUDIENCE[profile.companions]
        for item, place in visits:
            if must_named(place, profile):
                continue
            kids_only = place.family_only or place.audience == ["亲子"]
            if kids_only and profile.companions != "family_kids":
                parts["fit"].append(0.0)
                flag("fit", "儿童场所安排给非亲子出行", day.day, item.name, True)
                continue
            value = 1.0
            if place.audience:
                coverage["fit:labeled"] += 1
                # 成人缺"独行/情侣/朋友"标签不算不合适；只对老人、亲子这类需要照顾的人群扣分
                if wanted in {"老人", "亲子"} and wanted not in place.audience:
                    value -= .4
                    flag("fit", f"适合人群标签不含「{wanted}」", day.day, item.name)
            else:
                coverage["fit:unlabeled"] += 1
            if place.stamina >= 3 and (profile.companions == "elderly" or profile.pace == "relaxed"):
                value -= .5
                flag("fit", "高体力项目与老人/舒缓节奏不符", day.day, item.name)
            if profile.interests:
                value -= 0 if set(place.interests) & set(profile.interests) else .2
            parts["fit"].append(clamp(value))

        # 4 节奏强度：体验数、出门时长、连续高体力
        anchor = any(place.day_anchor for place in day_places[-1])
        count = len({experience_key(place) for place in day_places[-1] if place.visit_role != "viewpoint"})
        low, high = (1, 1) if anchor else EXPERIENCES[profile.pace]
        if count < low and stay_minutes >= 240:   # 一两个长时段场馆也能撑满一天
            low = count
        pace_score = 1 - .25 * (max(0, low - count) + max(0, count - high))
        if day.items:
            first = day.items[0].start - day.items[0].travel_min
            active = (max(item.end for item in day.items) + day.return_travel_min - first) / 60
            over = active - ACTIVE_HOURS[profile.pace]
            if over > 0:
                pace_score -= .15 * over
                flag("pace", f"出门约 {active:.1f} 小时，超过{PACE_NAMES[profile.pace]}节奏上限 {ACTIVE_HOURS[profile.pace]:.1f} 小时", day.day)
        if count < low or count > high:
            flag("pace", f"当天 {count} 个体验，节奏建议 {low}–{high} 个", day.day)
        if profile.companions in {"elderly", "family_kids"}:
            streak = 0
            for item in day.items:
                place = pois.get(item.ref_id) if item.kind == "poi" else None
                streak = streak + item.end - item.start if place and place.stamina >= 2 and not place.day_anchor else 0
                if streak > 180:
                    pace_score -= .2
                    flag("pace", "连续 3 小时以上中高体力游览，中间没有用餐休息", day.day, item.name)
                    streak = 0
        parts["pace"].append(clamp(pace_score))

        # 5 时段：用餐时刻、临近闭园入场、傍晚进山、夜游白天去
        timing = 1.0
        meal_starts = {}
        for item in day.items:
            if item.kind == "meal" and item.meal:
                ideal = (11 * 60, 12 * 60 + 45) if item.meal == "lunch" else (17 * 60 + 30, 19 * 60)
                off = max(0, ideal[0] - item.start, item.start - ideal[1])
                meal_starts[item.meal] = item.start
                if off:
                    timing -= .3 * clamp(off / 60)
                    flag("timing", f"{'午' if item.meal == 'lunch' else '晚'}餐 {hm(item.start)} 开始，偏离常见用餐时段", day.day, item.name)
        if {"lunch", "dinner"} <= meal_starts.keys() and meal_starts["dinner"] - meal_starts["lunch"] < 4 * 60:
            timing -= .2
            flag("timing", "午餐与晚餐间隔不足 4 小时", day.day)
        for item, place in visits:
            if place.close_min - item.start < 45 and item.end - item.start >= 30:
                timing -= .2
                flag("timing", f"闭园前 {place.close_min - item.start} 分钟才入场", day.day, item.name)
            if "户外徒步" in place.interests and place.stamina >= 3 and item.end > SUNSET:
                timing -= .2
                flag("timing", f"户外徒步 {hm(item.end)} 结束，晚于日落", day.day, item.name)
            if "夜景夜游" in place.interests and set(place.interests) <= {"夜景夜游", "拍照出片"} and item.end <= 16 * 60:
                timing -= .2
                flag("timing", "以夜景为主的地点安排在白天", day.day, item.name)
        parts["timing"].append(clamp(timing))

        # 6 时间利用：扣除交通和预留餐后的空档、提前收工
        idle = 0
        previous = None
        for item in day.items:
            if previous is not None:
                reserved = sum(slot.end - slot.start for slot in day.reserved_meals if slot.start >= previous and slot.end <= item.start)
                idle += max(0, item.start - previous - item.travel_min - reserved)
            previous = item.end
        span = window["end"] - window["start"]
        visit_end = max((item.end for item, _ in visits), default=window["start"])
        early = max(0, window["end"] - 120 - visit_end) if span >= 6 * 60 else 0
        slack = ramp((idle + early) / span if span else 0, .1, .4)
        if idle > 60:
            flag("slack", f"景点之间累计空档 {idle} 分钟", day.day)
        if early > 60:
            flag("slack", f"游览 {hm(visit_end)} 结束，可用时段还剩约 {(window['end'] - visit_end) / 60:.1f} 小时", day.day)
        parts["slack"].append(slack)

        # 7 多样性：同类扎堆、餐厅重复
        variety = 1.0
        for kind, number in Counter(place.type for place in day_places[-1] if place.visit_role == "attraction").items():
            if number >= 3:
                variety -= .25
                flag("variety", f"当天 {number} 个「{kind}」", day.day)
        parts["variety"].append(clamp(variety))

    # 跨天：同一片区拆在不同天（折返），餐厅菜系重复
    for (first, a), (second, b) in itertools.combinations(enumerate(day_places), 2):
        near = [(x.name, y.name) for x in a for y in b if haversine_km(x.lat, x.lon, y.lat, y.lon) < .6]
        if near:
            parts["transport"].append(.6)
            flag("transport", f"第{first + 1}天「{near[0][0]}」与第{second + 1}天「{near[0][1]}」相距不到 600 米却分两天去", second + 1)
    meals = [restaurants[item.ref_id] for day in itinerary.days for item in day.items
             if item.kind == "meal" and item.ref_id in restaurants]
    for brand, number in Counter(meal.brand or meal.name for meal in meals).items():
        if number > 1:
            parts["variety"].append(.4)
            flag("variety", f"「{brand}」重复用餐 {number} 次")
    cuisines = Counter(meal.cuisine for meal in meals if meal.cuisine)
    if len(meals) >= 4 and cuisines and cuisines.most_common(1)[0][1] / len(meals) > .6:
        parts["variety"].append(.6)
        flag("variety", f"{len(meals)} 顿饭里 {cuisines.most_common(1)[0][1]} 顿是「{cuisines.most_common(1)[0][0]}」")

    scores = {key: round(100 * sum(values) / len(values), 1) if values else None for key, values in parts.items()}
    # 停留参考与排程同源时自己比自己，必然高分：只报告、不计入综合分
    independent_stay = any(key.startswith("stay:") and key[5:] not in {"dataset", "project_review"} for key in coverage)
    present = {key: value for key, value in scores.items() if value is not None and (key != "stay" or independent_stay)}
    total = sum(WEIGHTS[key] * value for key, value in present.items()) / sum(WEIGHTS[key] for key in present) if present else None
    return {"overall": round(total, 1) if total is not None else None, "scores": scores, "counted": sorted(present),
            "severe": sum(issue["severe"] for issue in issues), "issues": issues, "coverage": dict(coverage),
            "stay_details": stay_details}
