from __future__ import annotations

from datetime import date as Date
import math
from .amap import AmapError, MissingSnapshotError, get_router, straight_km
from .amap_limits import RequestBudget
from .schemas import MapPoint, NavigationLeg, NavigationRequest
from .geo import SAME_PLACE_KM


def number(value, default=0.0) -> float:
    try:
        result = float(value)
        return result if math.isfinite(result) else default
    except (TypeError, ValueError):
        return default


def coordinates(value) -> list[list[float]]:
    result = []
    for point in str(value or "").split(";"):
        try:
            longitude, latitude = map(float, point.split(","))
            if not (118 <= longitude <= 121 and 29 <= latitude <= 31):
                return []
            result.append([longitude, latitude])
        except ValueError:
            return []
    return result if len(result) >= 2 else []


def route_segments(route: dict, mode: str) -> tuple[list[dict], int]:
    segments = []
    missing = 0

    def append(step, segment_mode, instruction=""):
        nonlocal missing
        path = coordinates(step.get("polyline"))
        distance = number(step.get("distance"))
        if not path:
            missing += int(distance > 20)
            return
        segments.append({"mode": segment_mode, "path": path,
                         "instruction": instruction or str(step.get("instruction") or "沿导航路线前行"),
                         "distance_m": round(distance), "minutes": round(number(step.get("duration")) / 60, 1)})

    if mode != "公交/地铁":
        for step in route.get("steps", []):
            append(step, mode)
    else:
        for segment in route.get("segments", []):
            for step in (segment.get("walking") or {}).get("steps", []):
                append(step, "步行")
            buslines = (segment.get("bus") or {}).get("buslines") or []
            if buslines:
                line = buslines[0]
                departure = (line.get("departure_stop") or {}).get("name", "")
                arrival = (line.get("arrival_stop") or {}).get("name", "")
                append(line, "公交/地铁", f"{line.get('name', '公共交通')}：{departure} → {arrival}")
            railway = segment.get("railway") or {}
            if railway.get("id"):
                append(railway, "铁路", str(railway.get("name") or "铁路区段"))
                if not railway.get("polyline"):
                    missing += 1
    return segments, missing


def navigation_leg(router, leg: NavigationLeg, service_date: Date | None) -> dict:
    origin, destination = leg.origin.coordinate(), leg.destination.coordinate()
    if origin == destination or straight_km(origin, destination) < SAME_PLACE_KM:
        return {"status": "same_place", "segments": [], "minutes": 0, "distance_m": 0, "source": "amap"}
    if leg.mode == "原地":
        return {"status": "unavailable", "segments": [], "message": "起终点不同，无法按原地显示"}
    time_text = f"{min(leg.departure, 1439) // 60:02d}:{min(leg.departure, 1439) % 60:02d}"
    cache_key = f"navigation|{leg.mode}|{origin}|{destination}|{service_date}|{time_text}"
    if router.has(cache_key):
        return router.get(cache_key)
    params = {"origin": origin, "destination": destination, "extensions": "all"}
    if leg.mode == "公交/地铁":
        endpoint = "v3/direction/transit/integrated"
        params.update(city="杭州", cityd="杭州", strategy=0, time=time_text)
        if service_date:
            params["date"] = service_date.isoformat()
    else:
        endpoint = "v3/direction/walking" if leg.mode == "步行" else "v3/direction/driving"
    response = router._get(endpoint, params).get("route") or {}
    options = response.get("transits" if leg.mode == "公交/地铁" else "paths") or []
    if not options:
        return {"status": "unavailable", "segments": [], "message": "没有查到该出行方式的导航路线"}
    selected = min(options, key=lambda option: number(option.get("duration"), float("inf")))
    segments, missing = route_segments(selected, leg.mode)
    result = {"status": "partial" if missing else "ready" if segments else "unavailable", "segments": segments,
              "minutes": round(number(selected.get("duration")) / 60), "distance_m": round(number(selected.get("distance"))),
              "source": "amap", "departure": time_text, "date": service_date.isoformat() if service_date else None,
              "message": "部分区段没有路径数据，未连线代替" if missing else ""}
    router._put(cache_key, result)
    result["fetched_at"] = router.records.get(cache_key, {}).get("fetched_at")
    return result


def get_navigation(request: NavigationRequest, router=None) -> dict:
    budget = RequestBudget(40)
    router = (router or get_router()).fork(budget)
    results = []
    for leg in request.legs:
        try:
            results.append(navigation_leg(router, leg, request.date))
        except (AmapError, MissingSnapshotError):
            results.append({"status": "unavailable", "segments": [], "message": "导航路线暂不可用，请稍后重试"})
    return {"legs": results, "queries": budget.used, "coordinate_system": "GCJ-02"}
