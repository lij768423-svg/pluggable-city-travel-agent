from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy

from .walk_ends import stop_points
from .data import load_pois
from .nodes.validate import validate_itinerary
from .poi_catalog import result_catalog
from .replan import ReplanError, day_end, swap_meal
from .restaurants import spice_conflict
from .schemas import MealEditPreviewRequest


def freeze_preview_locations(result: dict) -> None:
    places = load_pois()
    restaurants = result.get("restaurants", {})
    profile = result["profile"]
    locations = {"hotel": [profile.hotel.lat, profile.hotel.lon]}
    for day in result["itinerary"].days:
        for item in day.items:
            place = places.get(item.ref_id) if item.kind == "poi" else restaurants.get(item.ref_id)
            if place is None:
                raise ReplanError(422, "预览缺少地点资料，请重新计算预览")
            locations[f"{item.kind}:{item.ref_id}"] = [place.lat, place.lon]
    result["meal_preview_locations"] = locations


class FrozenMealTravel:
    def __init__(self, result: dict):
        places = load_pois()
        restaurants = result.get("restaurants", {})
        profile = result["profile"]
        locations = result.get("meal_preview_locations", {})
        if locations.get("hotel") != [profile.hotel.lat, profile.hotel.lon]:
            raise ReplanError(422, "预览起点证据已变化或缺失，请重新计算预览")
        self.records = {}
        self.current_day = 1
        self.position = 0
        self.last = None
        for day in result["itinerary"].days:
            origin = (profile.hotel.lat, profile.hotel.lon)
            records = []
            for item in day.items:
                place = places.get(item.ref_id) if item.kind == "poi" else restaurants.get(item.ref_id)
                if place is None or item.travel_evidence is None:
                    raise ReplanError(422, "预览缺少完整路线证据，请重新计算预览")
                if locations.get(f"{item.kind}:{item.ref_id}") != [place.lat, place.lon]:
                    raise ReplanError(422, "地点位置已变化，请重新计算预览")
                destination, leave = stop_points(item, place)
                records.append((origin, destination, item.travel_evidence))
                origin = leave
            if day.items:
                if day.return_leg is None:
                    raise ReplanError(422, "预览缺少返程证据，请重新计算预览")
                records.append((origin, (profile.hotel.lat, profile.hotel.lon), day.return_leg))
            self.records[day.day] = records

    @contextmanager
    def for_day(self, profile, day: int):
        self.current_day, self.position, self.last = day, 0, None
        yield self

    def leg(self, origin_lat: float, origin_lon: float, destination_lat: float, destination_lon: float):
        records = self.records.get(self.current_day, [])
        if self.position >= len(records):
            raise ReplanError(422, "预览路线记录不完整，请重新预览")
        origin, destination, evidence = records[self.position]
        expected = (*origin, *destination)
        actual = (origin_lat, origin_lon, destination_lat, destination_lon)
        if any(abs(left - right) > 0.0000001 for left, right in zip(expected, actual)):
            raise ReplanError(422, "地点位置已变化，请重新计算预览")
        self.position += 1
        self.last = evidence
        return evidence.minutes, evidence.mode, evidence.cost

    def detail(self, *coordinates):
        return self.last


def validate_preview(result: dict):
    with result_catalog(result):
        route = FrozenMealTravel(result)
        return validate_itinerary(result["itinerary"], result["profile"], travel_fn=route.leg,
                                  restaurants=result.get("restaurants", {}))


def preserve_self_arranged(original: dict, itinerary) -> None:
    confirmed = {(day.day, slot.meal, slot.inside_poi_id): slot for day in original["itinerary"].days
                 for slot in day.reserved_meals if slot.status == "self_arranged"}
    for day in itinerary.days:
        for slot in day.reserved_meals:
            previous = confirmed.get((day.day, slot.meal, slot.inside_poi_id))
            if previous is not None:
                slot.status = "self_arranged"
                slot.estimated_cost = previous.estimated_cost
                slot.reason = previous.reason


def meal_metrics(result: dict) -> dict:
    restaurants = result.get("restaurants", {})
    profile = result["profile"]
    meals = [item for day in result["itinerary"].days for item in day.items if item.kind == "meal"]
    slots = [slot for day in result["itinerary"].days for slot in day.reserved_meals]
    expected = int(result["report"].totals.get("expected_meals", 0))
    covered = int(result["report"].totals.get("covered_meals", 0))
    spicy = sum(item.ref_id in restaurants and spice_conflict(restaurants[item.ref_id], profile) for item in meals)
    return {"expected_meals": expected, "restaurant_meals": len(meals),
            "covered_restaurant_meals": covered,
            "restaurant_coverage": covered / expected if expected else None,
            "self_arranged_meals": sum(slot.status == "self_arranged" for slot in slots),
            "onsite_pending": sum(slot.inside_poi_id is not None and slot.status == "pending" for slot in slots),
            "park_exit_meals": sum(item.meal_context == "park_exit" for item in meals),
            "spice": profile.spice, "spice_conflicts": spicy,
            "detour_min": sum(item.meal_detour_min or 0 for item in meals),
            "detour_known": sum(item.meal_detour_min is not None for item in meals),
            "meal_budget": result["report"].totals.get("meals", 0)}


def build_preview(original: dict, request: MealEditPreviewRequest) -> tuple[dict, dict]:
    """换餐/自理确认沿用原行程的目录版本；全局新发布不改变该行程的地点身份和坐标。"""
    with result_catalog(original):
        return _build_preview(original, request)


def _build_preview(original: dict, request: MealEditPreviewRequest) -> tuple[dict, dict]:
    if request.action == "replace":
        updated, change = swap_meal(deepcopy(original), request.day, request.item_index, request.restaurant_id)
        meal = original["itinerary"].days[request.day - 1].items[request.item_index].meal
        if not any(item.kind == "meal" and item.meal == meal and item.ref_id == request.restaurant_id
                   for item in updated["itinerary"].days[request.day - 1].items):
            raise ReplanError(422, "新餐厅未能排入这一天，原行程保持不变")
    else:
        if not 1 <= request.day <= len(original["itinerary"].days):
            raise ReplanError(422, "这一天不在行程中")
        updated = deepcopy(original)
        slot = next((slot for slot in updated["itinerary"].days[request.day - 1].reserved_meals if slot.meal == request.meal), None)
        if slot is None:
            raise ReplanError(422, "找不到需要确认的预留餐段")
        slot.status = "self_arranged"
        slot.estimated_cost = request.estimated_cost
        slot.reason = "你已确认自行安排这一餐，商户和饮食要求由你到店确认；预留费用按你的填写计入"
        freeze_preview_locations(updated)
        updated["report"] = validate_preview(updated)
        updated["operation"] = "meal_self_arranged"
        updated["usage"] = {"calls": 0, "cached_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "seconds": 0}
        updated["seconds"] = 0
        meal = request.meal
        change = {"day": request.day, "meal": meal, "from": "待确认的预留用餐", "to": "自行安排用餐",
                  "shift_min": 0, "removed": [], "reordered": False, "passed": updated["report"].passed,
                  "conflicts": [issue.message for issue in updated["report"].hard], "seconds": 0, "live_calls": 0}
        updated["log"].append(f"第{request.day}天{meal}由用户确认自行安排，预留费用{request.estimated_cost:g}元")
    freeze_preview_locations(updated)
    before_day = original["itinerary"].days[request.day - 1]
    after_day = updated["itinerary"].days[request.day - 1]
    before = {(item.kind, item.ref_id, item.meal): item for item in before_day.items}
    after = {(item.kind, item.ref_id, item.meal): item for item in after_day.items}
    affected = [{"name": item.name, "before_start": item.start, "before_end": item.end,
                 "after_start": after[key].start if key in after else None, "after_end": after[key].end if key in after else None}
                for key, item in before.items() if key not in after or (item.start, item.end) != (after[key].start, after[key].end)]
    def minutes(day, rides: bool = False):
        return sum(item.travel_min for item in day.items if not rides or item.travel_mode not in {"步行", "原地"}) + (
            day.return_travel_min if not rides or day.return_leg and day.return_leg.mode not in {"步行", "原地"} else 0)
    old_budget = original["report"].totals.get("total", 0)
    new_budget = updated["report"].totals.get("total", 0)
    change.update(action=request.action, budget_before=old_budget, budget_after=new_budget,
                  budget_delta=round(new_budget - old_budget, 1), travel_delta_min=minutes(after_day) - minutes(before_day),
                  ride_delta_min=minutes(after_day, True) - minutes(before_day, True),
                  end_before=day_end(before_day), end_after=day_end(after_day), affected_stops=affected,
                  removed=[item.name for key, item in before.items() if item.kind == "poi" and key not in after],
                  metrics_before=meal_metrics(original), metrics_after=meal_metrics(updated),
                  route_source="estimate" if any(item.travel_evidence is None or item.travel_evidence.source == "estimate" for day in updated["itinerary"].days for item in day.items)
                               or any(day.return_leg is None or day.return_leg.source == "estimate" for day in updated["itinerary"].days if day.items) else "amap")
    return updated, change
