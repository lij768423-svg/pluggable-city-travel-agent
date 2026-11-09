"""住哪儿：起点不定的多日行程，按每天去第一站、从最后一站回来的路程挑住的片区，再查附近现在营业的住处。

只给建议，不改行程；网页与 MCP 共用 finish_result 里算好的结果。房价不写数字：高德住宿 POI 不给价格，
ChinaTravel 住宿表每家只有一个年份与入住日期都不明的价，只拿它的坐标当“哪里有酒店”（2026-10-30 用户决定）。
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from functools import lru_cache
import re
import time
from typing import Any

from .amap import AmapError, MissingSnapshotError
from .amap_limits import restaurant_requests
from .areas import area_index
from .cities import city_config
from .data import load_pois
from .geo import bd09_to_gcj02, haversine_km
from .places import normalize_place
from .travel import Travel, calibrated

MIN_DAYS = 2
NEARBY_RADIUS_M = 1500
LONG_COMMUTE_MIN = 60  # 当天往返超过这么久单独提醒
SIMILAR_MIN = 5  # 比默认起点每天省不到这么多，算“差不多”
PER_TIER = 2
# 高德住宿类型 → 档次；类型只写“宾馆酒店”“住宿服务相关”的看不出档次，归“其他”
TIERS = {"经济型连锁酒店": "经济型", "三星级宾馆": "三星", "四星级宾馆": "四星", "五星级宾馆": "五星/奢华",
         "奢华酒店": "五星/奢华", "旅馆招待所": "民宿/青旅", "青年旅舍": "民宿/青旅"}
TIER_ORDER = ("经济型", "三星", "四星", "五星/奢华", "民宿/青旅", "其他")
HOSTEL_NAMES = r"民宿|青年旅舍|青旅|客栈"
# 酒店里的餐厅也挂住宿类型（2026-10-30 预览：“法云安缦·蒸菜馆”排进五星）；“饭店”多是酒店，不算
NOT_LODGING_NAMES = r"菜馆|餐厅|餐馆|食府|面馆|咖啡|茶室|停车场"


def wanted(profile) -> bool:
    return profile.origin_known is False and profile.days >= MIN_DAYS and profile.familiarity != "local"


@lru_cache(maxsize=None)
def hotel_points(city: str) -> tuple[tuple[float, float], ...]:
    """住宿表里的酒店坐标（BD-09）；没有住宿表的城市不给建议。"""
    import pandas as pd
    path = city_config(city).data_dir / "accommodations.parquet"
    if not path.exists():
        return ()
    table = pd.read_parquet(path, columns=["lat", "lon"])
    return tuple(dict.fromkeys((round(float(lat), 6), round(float(lon), 6)) for lat, lon in zip(table.lat, table.lon)))


def day_ends(itinerary, restaurants: dict) -> list[tuple[int, tuple[float, float], tuple[float, float]]]:
    """每天第一站的到达处和最后一站的离开处（长堤按进出口）。"""
    places = load_pois()
    ends = []
    for day in itinerary.days:
        stops = []
        for item in day.items:
            place = places.get(item.ref_id) if item.kind == "poi" else restaurants.get(item.ref_id)
            if place is not None:
                stops.append((item.enter_point or (place.lat, place.lon), item.leave_point or (place.lat, place.lon)))
        if stops:
            ends.append((day.day, stops[0][0], stops[-1][1]))
    return ends


def commute(point: tuple[float, float], ends, transport: str) -> list[int]:
    return [calibrated(*point, *first, transport)[0] + calibrated(*last, *point, transport)[0] for _, first, last in ends]


def tier_of(raw: dict) -> str | None:
    """住宿类型那一段的最后一级；不是住宿的返回 None。"""
    segment = next((part for part in str(raw.get("type") or "").split("|") if part.startswith("住宿服务")), None)
    if segment is None or re.search(NOT_LODGING_NAMES, str(raw.get("name") or "")):
        return None
    tier = TIERS.get(segment.split(";")[-1], "其他")
    return "民宿/青旅" if tier == "其他" and re.search(HOSTEL_NAMES, str(raw.get("name") or "")) else tier


def nearby_hotels(router, spot: tuple[float, float], city: str) -> tuple[list[dict], str | None]:
    """附近住处按档次各取评分最高的几家，连同出现最多的商圈名；查一次高德。"""
    lat, lon = bd09_to_gcj02(*spot)
    with restaurant_requests():  # 与餐厅搜索一样可用到总额度，不占路线排程的余量
        data = router._get("v5/place/around", {"location": f"{lon:.6f},{lat:.6f}", "types": "100000",
                                               "radius": NEARBY_RADIUS_M, "sortrule": "distance",
                                               "page_size": 25, "page_num": 1, "show_fields": "business"})
    found, areas = [], Counter()
    for raw in data.get("pois", []):
        if not isinstance(raw, dict) or (tier := tier_of(raw)) is None:
            continue
        place = normalize_place(raw, city)
        if place is None:
            continue
        business = raw.get("business") if isinstance(raw.get("business"), dict) else {}
        if isinstance(business.get("business_area"), str) and business["business_area"].strip():
            areas[business["business_area"].strip()] += 1
        try:
            rating = float(business["rating"]) if business.get("rating") not in (None, "", []) else None
        except (TypeError, ValueError):
            rating = None
        found.append({"name": place["name"], "tier": tier, "amap_type": str(raw.get("type") or "").split("|")[0].split(";")[-1],
                      "rating": rating if rating is None or 0 <= rating <= 5 else None, "address": place["address"],
                      "external_id": place["id"], "lat": place["lat"], "lon": place["lon"],
                      "distance_m": round(haversine_km(*spot, place["lat"], place["lon"]) * 1000)})
    picked = []
    for tier in TIER_ORDER:
        group = sorted((hotel for hotel in found if hotel["tier"] == tier), key=lambda hotel: (-(hotel["rating"] or 0), hotel["distance_m"]))
        picked.extend(group[:PER_TIER])
    return picked, areas.most_common(1)[0][0] if areas else None


def nearest_area(spot: tuple[float, float]) -> str | None:
    index = area_index()
    if not index.positions:
        return None
    poi_id = min(index.positions, key=lambda identifier: haversine_km(*spot, *index.positions[identifier]))
    return index.label(index.area_of(poi_id))


def suggest_lodging(out: dict, route=None) -> dict[str, Any] | None:
    profile = out["profile"]
    if not wanted(profile):
        return None
    ends = day_ends(out["itinerary"], out.get("restaurants") or {})
    points = hotel_points(profile.city)
    if len(ends) < MIN_DAYS or not points:
        return None
    transport = profile.transport
    scored = min((sum(daily), max(daily), point, daily) for point in points for daily in [commute(point, ends, transport)])
    _, _, spot, daily = scored
    settings = city_config(profile.city)
    origin = settings.default_origin
    default = commute((origin["lat"], origin["lon"]), ends, transport)
    hotels, business_area, status = [], None, "unavailable"
    router = getattr(route, "router", None)
    if router is None or getattr(router, "offline", False):
        status = "offline"
    elif not hasattr(route, "_can_go_live") or route._can_go_live(restaurant=True):
        try:
            hotels, business_area = nearby_hotels(router, spot, profile.city)
            status = "ok"
        except (AmapError, MissingSnapshotError):
            pass
    average, default_average = round(sum(daily) / len(daily)), round(sum(default) / len(default))
    return {"area": business_area or nearest_area(spot) or "推荐位置", "area_source": "amap_business_area" if business_area else "area_index",
            "city": settings.name, "lat": spot[0], "lon": spot[1], "transport": transport,
            "daily": [{"day": day, "minutes": minutes} for (day, _, _), minutes in zip(ends, daily)],
            "average_minutes": average,
            "default_name": re.sub(r"[（(]默认[）)]", "", origin.get("name", "市中心")).strip() or "市中心",
            "default_average_minutes": default_average, "saving_minutes": default_average - average,
            "similar": default_average - average < SIMILAR_MIN,
            "long_days": [day for (day, _, _), minutes in zip(ends, daily) if minutes > LONG_COMMUTE_MIN],
            "hotels": hotels, "search_status": status, "radius_m": NEARBY_RADIUS_M,
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds") if status == "ok" else None,
            "method": "按出行方式的标定模型估算每天往返分钟，在住宿表坐标中取总和最小处；未查实时路况"}


MAX_REPLANS = 2  # 每份行程最多按住处重排几次（2026-10-30 用户同意的默认）


def replan_with_hotel(result: dict, external_id: str, route=None) -> dict:
    """景点沿用原行程，以选中的推荐住处为每天起点与终点重新分天排序、重新选餐；不调用大模型。

    不跑修复与精修：不为排下而删景点，排不下照实交给校验报告（2026-10-30 用户同意的默认）。
    """
    from .amap_limits import plan_http_limit
    from .graph import build_graph, finish_result
    from .poi_catalog import result_catalog
    from .replan import ReplanError
    from .restaurants import RestaurantCatalog
    from .schemas import Hotel

    lodging = result.get("lodging") or {}
    hotel = next((item for item in lodging.get("hotels", []) if item.get("external_id") == external_id), None)
    if hotel is None:
        raise ReplanError(404, "这家住处不在本行程的推荐里，请刷新后重试。")
    profile = result["profile"].model_copy(update={"hotel": Hotel(name=hotel["name"], lat=hotel["lat"], lon=hotel["lon"]),
                                                   "origin_known": True})
    kept = [item.ref_id for day in result["itinerary"].days for item in day.items if item.kind == "poi" and not item.evening]
    selected = [selection for selection in result["selected"] if selection.poi_id in kept]
    started = time.monotonic()
    with result_catalog(result):
        route = route or Travel("amap", profile.transport, live_budget=plan_http_limit(profile.days))
        route.allow_taxi_fallback = profile.allow_taxi_fallback
        out = build_graph(external_selection=True).invoke({
            "profile": profile, "candidates": result["candidates"], "selected": selected,
            "initial_selected_ids": [selection.poi_id for selection in selected],
            "retrieval_report": result.get("retrieval_report", {}), "travel": route, "catalog": RestaurantCatalog(route),
            "travel_source": "amap", "frozen_candidates": True, "enable_repair": False, "enable_refine": False,
            "use_solver": True, "meal_factor": 1.0, "repair_round": 0,
            "log": [f"按推荐住处「{hotel['name']}」重新规划：景点沿用原行程，未调用大模型"]}, {"recursion_limit": 50})
        out["usage"] = {"calls": 0, "cached_calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "seconds": 0}
        out["seconds"] = round(time.monotonic() - started, 2)
        out["poi_catalog"] = result.get("poi_catalog")
        out = finish_result(out)
    for field in ("guide_evidence", "selection_stats", "llm_timings"):
        if field in result:
            out[field] = result[field]
    out["operation"] = "lodging_replan"
    out["lodging"] = {**lodging, "chosen": {key: hotel.get(key) for key in ("name", "external_id", "tier")}}
    return out
