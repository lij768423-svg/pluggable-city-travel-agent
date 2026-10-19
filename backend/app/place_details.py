from __future__ import annotations

from collections import OrderedDict
import re
import threading
from .amap import AmapError, MissingSnapshotError, get_router, pt, straight_km
from .amap_limits import RequestBudget
from .data import load_pois, load_restaurants
from .geo import hm
from .navigation import number
from .schemas import PlaceReference, PlaceDetailsRequest
from .place_guides import PLACE_GUIDES


_restaurants = OrderedDict()
_lock = threading.Lock()
HIGHLIGHTS = {
    "人文历史": "留意建筑、历史遗存和地方故事，按现场说明理解背景",
    "博物馆展览": "优先看与你兴趣相关的常设展，临展与预约另行确认",
    "自然风光": "沿开放的观景区域慢走，预留停留和拍照时间",
    "拍照出片": "选择不影响通行的拍摄点，现场光线与天气会影响效果",
    "茶文化": "结合茶文化介绍与茶园风景游览，体验项目以现场开放为准",
    "购物街区": "以沿街散步和地方小店为主，消费按自己的预算选择",
    "亲子游乐": "优先选择孩子感兴趣的区域，留出休息时间",
    "夜景夜游": "适合安排夜间散步；灯光、演出和船班不作保证",
    "园林古镇": "慢看街巷、院落与水边空间，避免把多个片区赶在一起",
    "宗教寺庙": "留意建筑与文化背景，遵守现场参观和拍摄规则",
    "艺术文创": "关注建筑、展陈与文创空间，具体展览以场馆公告为准",
    "户外徒步": "根据体力选择开放步道，天气不好时缩短路线",
    "小众安静": "以安静慢逛为主，是否清静仍受日期与客流影响",
    "美食探店": "比较沿途店铺和人均消费，排队情况到店前再确认",
}


def remember_restaurants(restaurants) -> None:
    with _lock:
        for restaurant in restaurants:
            _restaurants[restaurant.id] = restaurant
            _restaurants.move_to_end(restaurant.id)
        while len(_restaurants) > 2048:
            _restaurants.popitem(last=False)


def resolve_place(reference: PlaceReference):
    if reference.kind == "poi":
        return load_pois().get(reference.id)
    with _lock:
        return _restaurants.get(reference.id) or load_restaurants().get(reference.id)


def describe_place(place, kind: str) -> dict:
    if kind == "poi":
        guide = PLACE_GUIDES.get(place.name)
        summary = place.summary.strip()
        if not summary or summary == "信息不足":
            summary = f"{place.name}属于{place.type}。当前资料有限，建议结合现场介绍游览。"
        return {"kind": kind, "id": place.id, "name": place.name, "introduction": summary,
                "highlights": guide or [HIGHLIGHTS[tag] for tag in place.interests if tag in HIGHLIGHTS][:3] or ["结合现场介绍与个人兴趣安排游览"],
                "audiences": place.audience, "tags": place.interests,
                "duration": f"{round(place.min_hours * 60)}–{round(place.max_hours * 60)} 分钟", "opening": f"{hm(place.open_min)}–{hm(place.close_min)}",
                "tips": [text for text in (place.review_note, place.seasonal_note, place.duration_source, "适合人群和游览建议是项目推荐标签，非场馆认证") if text],
                "content_source": "项目自有补充资料：坐标、开放、票价逐字段注明来源（含用户提供与规则推断，核对者为AI），介绍与标签为项目整理，非场馆官方说明"
                    if place.catalog_source == "supplement" else
                    "ChinaTravel 地点资料与项目整理；介绍、标签和逛法含模型辅助生成，非场馆官方说明", "rating": None,
                "rating_source": None, "rating_status": "pending", "rating_at": None, "address": "",
                "source_url": place.opening_rules.source_url if place.opening_rules else None,
                "region_id": place.region_id, "region_label": place.region_label,
                "experience_kind": place.experience_kind, "merge_group": place.visit_group,
                "onsite_dining": place.onsite_dining,
                "booking_required": place.opening_rules.booking_required if place.opening_rules else None,
                "experience_conditions": [condition.model_dump(mode="json") for condition in place.experience_conditions],
                "field_evidence": {field: evidence.model_dump(mode="json") for field, evidence in place.field_evidence.items()}}
    return {"kind": kind, "id": place.id, "name": place.name,
            "introduction": f"{place.cuisine or '餐饮'}餐厅。" + (f"历史菜单参考：{'、'.join(dish.strip() for dish in re.split(r'[,，、]', place.dishes) if dish.strip())}。" if place.dishes else "具体菜品以商家当日菜单为准。"),
            "highlights": [f"参考人均 ¥{place.price:g}" if place.price_known else "人均消费暂无可靠数据", "在行程中比较到店时间、预算与绕路成本"],
            "audiences": [], "tags": [place.cuisine] if place.cuisine else [], "duration": "约 1 小时",
            "opening": place.opening_text or (f"{hm(place.open_min)}–{hm(place.close_min)}（历史记录）" if place.hours_known else "营业时间待确认"),
            "tips": ["口味、过敏原和特殊饮食要求请向商家确认"],
            "content_source": "高德地点资料" if place.provider == "amap" else "ChinaTravel 历史餐厅资料",
            "rating": place.rating, "rating_source": "高德" if place.rating is not None and place.provider == "amap" else None,
            "rating_status": "ready" if place.rating is not None else "pending", "rating_at": place.fetched_at,
            "address": place.address, "source_url": None}


def normalized_name(value: str) -> str:
    return re.sub(r"[\W_]", "", value).removeprefix("杭州市").removeprefix("杭州")


def match_place(place, pois: list[dict]) -> dict | None:
    origin = pt(place.lat, place.lon)
    accepted = {normalized_name(place.name)}
    accepted.update({"断桥残雪": ["断桥"], "清河坊街": ["清河坊历史文化街区", "河坊街"]}.get(place.name, []))
    matches = []
    for candidate in pois:
        if normalized_name(str(candidate.get("name", ""))) not in accepted:
            continue
        location = str(candidate.get("location") or "")
        try:
            distance = straight_km(origin, location)
        except (ValueError, TypeError):
            continue
        if distance <= 0.5:
            matches.append((distance, candidate))
    return min(matches, key=lambda match: match[0])[1] if matches else None


def enrich_place(place, detail: dict, router) -> dict:
    if detail["rating_status"] == "ready":
        return detail
    key = f"place-details|{detail['kind']}|{place.id}|{place.name}|{pt(place.lat, place.lon)}"
    if router.has(key):
        return detail | router.get(key)
    try:
        response = router._get("v5/place/around", {"location": pt(place.lat, place.lon), "keywords": place.name,
                              "radius": 1000, "page_size": 10, "show_fields": "business", "sortrule": "distance"})
        candidate = match_place(place, response.get("pois") or [])
        business = (candidate or {}).get("business") or {}
        rating = number(business.get("rating"), -1)
        rating = rating if 0 < rating <= 5 else None
        enrichment = {"rating": rating, "rating_source": "高德" if rating is not None else None,
                      "rating_status": "ready" if rating is not None else "unrated",
                      "rating_at": None, "address": str((candidate or {}).get("address") or detail["address"])}
        router._put(key, enrichment)
        enrichment["rating_at"] = router.records.get(key, {}).get("fetched_at")
        return detail | enrichment
    except (AmapError, MissingSnapshotError):
        return detail | {"rating_status": "unavailable"}


def get_place_details(request: PlaceDetailsRequest, router=None) -> dict:
    budget = RequestBudget(24)
    try:
        router = (router or get_router()).fork(budget)
    except AmapError:
        router = None
    results = []
    for reference in request.places:
        place = resolve_place(reference)
        if place is None:
            continue
        detail = describe_place(place, reference.kind)
        results.append(enrich_place(place, detail, router) if router else detail | {"rating_status": "unavailable"})
    return {"places": results, "queries": budget.used}
