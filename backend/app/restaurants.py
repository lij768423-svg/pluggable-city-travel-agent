from __future__ import annotations


import hashlib
import math
import re
from datetime import datetime, timezone

from .cities import current_city
from . import config
from .amap import AmapError
from .amap_limits import RESTAURANT_RESERVE, restaurant_requests
from .data import load_restaurants, spice_ok
from .geo import bd09_to_gcj02, haversine_km
from .matching import norm
from .places import normalize_place
from .schemas import Restaurant, UserProfile



def valid_location(restaurant: Restaurant) -> bool:
    return current_city().contains(restaurant.lat, restaurant.lon)


def brand_name(restaurant: Restaurant) -> str:
    if restaurant.brand:
        return norm(restaurant.brand)
    known = ("外婆家", "新白鹿", "知味观", "新丰小吃", "老头儿油爆虾", "绿茶餐厅", "楼外楼", "奎元馆", "弄堂里", "咬不得")
    for brand in known:
        if restaurant.name.startswith(brand):
            return norm(brand)
    return norm(restaurant.name)


def spice_conflict(restaurant: Restaurant, profile: UserProfile) -> bool:
    """不吃辣却是辣味菜系。"""
    return not spice_ok(restaurant.cuisine, profile.spice)


def opening_periods(text: str) -> list[tuple[int, int]]:
    if not text or (re.search(r"周|星期|节假日|工作日", text) and not re.search(r"周一[至到-]周日|周一[至到-]周天", text)):
        return []
    if "24小时" in text:
        return [(0, 1440)]
    periods = []
    for start_hour, start_min, end_hour, end_min in re.findall(r"(\d{1,2}):(\d{2})\s*[-~—至]\s*(\d{1,2}):(\d{2})", text):
        if max(int(start_hour), int(end_hour)) > 24 or max(int(start_min), int(end_min)) > 59 or (int(start_hour) == 24 and int(start_min)) or (int(end_hour) == 24 and int(end_min)):
            continue
        start = int(start_hour) * 60 + int(start_min)
        end = int(end_hour) * 60 + int(end_min)
        if end <= start:
            periods.extend([(start, 1440), (0, end)])
        else:
            periods.append((start, end))
    return sorted(set(periods))


def meal_start(restaurant: Restaurant, arrival: int, duration: int = 60) -> int | None:
    periods = restaurant.opening_periods or [(restaurant.open_min, restaurant.close_min)]
    possible = [max(arrival, start) for start, end in periods if max(arrival, start) + duration <= end]
    return min(possible) if possible else None


def normalize_restaurant(raw: dict) -> Restaurant | None:
    place = normalize_place(raw)
    if place is None or not place["id"]:
        return None
    categories = str(raw.get("type") or "")
    if re.search(r"咖啡|饮品|甜品|糕点|面包|酒吧|茶艺馆|冷饮|茶饮|奶茶|冰淇淋|茶吧|汉服体验", categories + place["name"]):
        return None
    # 一家店可挂多个分类（“|”分隔）：带购物分类的是商店，不当正餐；菜系只从餐饮那一段取
    segments = categories.split("|")
    if any(part.startswith("购物服务") for part in segments):
        return None
    categories = next((part for part in segments if part.startswith("餐饮服务")), categories)
    business = raw.get("business") if isinstance(raw.get("business"), dict) else {}
    try:
        price = float(business.get("cost") or 0)
        rating = float(business["rating"]) if business.get("rating") not in (None, "", []) else None
    except (ValueError, TypeError):
        return None
    if not 0 <= price <= 10000 or rating is not None and not 0 <= rating <= 5:
        return None
    opening = business.get("opentime_week") or business.get("opentime_today") or ""
    opening = opening if isinstance(opening, str) else ""
    periods = opening_periods(opening)
    cuisine = next((name for name in ("素食", "江浙菜", "杭帮菜", "川菜", "湘菜", "火锅", "西餐", "日本料理", "烧烤", "海鲜")
                    if name in categories + place["name"]), "中餐" if "中餐" in categories else categories.split(";")[-1] or "餐厅")
    local = re.search(current_city().local_cuisine, categories + place["name"])
    if cuisine not in {"素食", "清真"} and local:
        cuisine = next((label for token, label in current_city().local_cuisine_labels.items()
                        if token in categories + place["name"]), local.group())
    return Restaurant(id=-int(hashlib.sha256(place["id"].encode()).hexdigest()[:12], 16),
                      external_id=place["id"], provider="amap", name=place["name"], lat=place["lat"], lon=place["lon"],
                      address=place["address"], price=price, price_known=price > 0, cuisine=cuisine, rating=rating,
                      open_min=min((period[0] for period in periods), default=0),
                      close_min=max((period[1] for period in periods), default=1440),
                      opening_periods=periods, opening_text=opening, hours_known=bool(periods),
                      fetched_at=datetime.now(timezone.utc).isoformat())


class RestaurantCatalog:
    def __init__(self, route=None, initial: dict[int, Restaurant] | None = None, online: bool | None = None):
        self.restaurants = dict(initial if initial is not None else load_restaurants())
        self.route = route
        self.online = config.ONLINE_RESTAURANTS if online is None else online
        self.queries: set[tuple[float, float]] = set()
        self.notices: list[str] = []
        self.online_count = 0

    def nearby(self, position: tuple[float, float], live: bool = True) -> dict[int, Restaurant]:
        router = self.route.router if self.route else None
        if not self.online or not live or router is None or router.offline:
            return self.restaurants
        location = bd09_to_gcj02(*position)
        anchor = (round(location[0], 2), round(location[1], 2))
        if anchor in self.queries or len(self.queries) >= RESTAURANT_RESERVE or not self.route._can_go_live(restaurant=True):
            return self.restaurants
        self.queries.add(anchor)
        try:
            with restaurant_requests():  # 用餐厅预留额度，路线查询用不到这部分
                data = router._get("v5/place/around", {"location":f"{anchor[1]},{anchor[0]}", "types":"050000", "radius":2000,
                                                      "sortrule":"distance", "page_size":20, "page_num":1, "show_fields":"business"})
            for raw in data.get("pois", []):
                restaurant = normalize_restaurant(raw) if isinstance(raw, dict) else None
                if restaurant is None:
                    continue
                match = next((existing for existing in self.restaurants.values()
                              if norm(existing.name) == norm(restaurant.name)
                              and haversine_km(existing.lat, existing.lon, restaurant.lat, restaurant.lon) < 0.15), None)
                if match:
                    restaurant = restaurant.model_copy(update={"id":match.id, "dishes":match.dishes,
                                                               "cuisine":match.cuisine, "dietary_verified":match.dietary_verified})
                existing = self.restaurants.get(restaurant.id)
                if existing and existing.external_id and existing.external_id != restaurant.external_id:
                    continue
                self.restaurants[restaurant.id] = restaurant
                self.online_count += 1
        except AmapError:
            self.notices.append("餐饮在线查询暂不可用，本次保留历史候选并标明来源。")
        return self.restaurants
