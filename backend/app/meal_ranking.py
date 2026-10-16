from __future__ import annotations
from contextlib import nullcontext

from .geo import travel
from .progress import tick_track
from .restaurants import DIET_HINT, STRICT_DIET, RestaurantCatalog, brand_name, dietary_status, meal_start
from .rules import MEAL_MIN, activity_window
from .schemas import Restaurant, UserProfile


def rank_restaurants(position: tuple[float, float], following: tuple[float, float] | None, start: int,
                     profile: UserProfile, used: set[int], cap: float, latest_start: int,
                     restaurants: dict[int, Restaurant], route=None, live: bool = True,
                     catalog: RestaurantCatalog | None = None, day: int = 1,
                     need_arrive_by: int | None = None) -> list[dict]:
    with route.planning_limits() if hasattr(route, "planning_limits") else nullcontext():
        return _rank_restaurants(position, following, start, profile, used, cap, latest_start, restaurants,
                                 route, live, catalog, day, need_arrive_by)


def _rank_restaurants(position: tuple[float, float], following: tuple[float, float] | None, start: int,
                      profile: UserProfile, used: set[int], cap: float, latest_start: int,
                      restaurants: dict[int, Restaurant], route=None, live: bool = True,
                      catalog: RestaurantCatalog | None = None, day: int = 1,
                      need_arrive_by: int | None = None) -> list[dict]:
    from .nodes.meals import meal_suitability
    if catalog:
        restaurants = catalog.nearby(position, live=live)
    window = activity_window(profile, day)
    used_brands = {brand_name(restaurants[place_id]) for place_id in used if place_id in restaurants}
    used_cuisines = {restaurants[place_id].cuisine for place_id in used if place_id in restaurants}
    # 清真这类要求只认已核验的商家；找不到就依次放宽到“菜系可能合规”和“任意餐厅”，两种都在提示里写明
    requirement = sorted(STRICT_DIET & set(profile.dietary))
    hints = set().union(*(DIET_HINT.get(name, set()) for name in requirement)) if requirement else set()
    strict: list[tuple[float, int, Restaurant]] = []
    likely: list[tuple[float, int, Restaurant]] = []
    fallback: list[tuple[float, int, Restaurant]] = []
    target = min(cap * 0.75, 100 if profile.food_preference == "quality" else 65)
    if profile.meal_budget_min is not None:
        target = min(cap, (profile.meal_budget_min + cap) / 2)
    for restaurant in restaurants.values():
        if not restaurant.price_known or restaurant.price > cap or restaurant.price <= 0 or restaurant.price < (profile.meal_budget_min or 0):
            continue
        suitability = meal_suitability(restaurant, profile)
        diet = dietary_status(restaurant, profile)
        if suitability is None or diet == "conflict":
            continue
        coarse_go = travel(*position, restaurant.lat, restaurant.lon)[0]
        if coarse_go > 25:
            continue
        score = suitability + coarse_go * 0.6 + abs(restaurant.price - target) * 0.18
        score += 120 if restaurant.id in used else 70 if brand_name(restaurant) in used_brands else 0
        score += 8 if restaurant.cuisine in used_cuisines else 0
        score += 8 if not restaurant.hours_known else 0
        score += 10 if restaurant.provider == "chinatravel" else 0
        if restaurant.rating is not None:
            score -= max(0, restaurant.rating - 3.0) * 5
        if profile.food_preference == "local" and restaurant.cuisine in {"江浙菜", "杭帮菜", "本帮菜", "农家菜"}:
            score -= 12
        if profile.food_preference == "quick" and restaurant.cuisine == "小吃":
            score -= 40
        pool = strict if diet != "unknown" or not requirement else likely if restaurant.cuisine in hints else fallback
        pool.append((score, restaurant.id, restaurant))

    def collect(pool: list[tuple[float, int, Restaurant]], unverified: str) -> list[dict]:
        pool.sort(key=lambda row: (row[0], row[1]))
        results: list[dict] = []
        for coarse_score, _, restaurant in pool[:30]:
            destination = (restaurant.lat, restaurant.lon)
            if route:
                go_detail = route.detail(*position, *destination, live=live)
                if not go_detail.available:
                    continue
                go = go_detail.minutes
                onward_detail = route.detail(*destination, *(following or (profile.hotel.lat, profile.hotel.lon)), live=live)
                if not onward_detail.available:
                    continue
                onward = onward_detail.minutes
                direct = route.leg(*position, *(following or (profile.hotel.lat, profile.hotel.lon)), live=False)[0]
                back = onward if following in (None, (profile.hotel.lat, profile.hotel.lon)) else route.leg(*destination, profile.hotel.lat, profile.hotel.lon, live=False)[0]
                source = "amap" if go_detail.source in {"amap", "same_place"} and onward_detail.source in {"amap", "same_place"} else "estimate"
            else:
                go = travel(*position, *destination)[0]
                onward = travel(*destination, *(following or (profile.hotel.lat, profile.hotel.lon)))[0]
                direct = travel(*position, *(following or (profile.hotel.lat, profile.hotel.lon)))[0]
                back = travel(*destination, profile.hotel.lat, profile.hotel.lon)[0]
                source = "estimate"
            arrival = meal_start(restaurant, start + go)
            if go > 25 or arrival is None or arrival > latest_start or arrival + MEAL_MIN + back > window["end"]:
                continue
            if need_arrive_by is not None and arrival + MEAL_MIN + onward > need_arrive_by:
                continue
            detour = max(0, go + onward - direct)
            notices = []
            if brand_name(restaurant) in used_brands:
                notices.append("可行候选较少，沿用了同一品牌")
            if not restaurant.hours_known:
                notices.append("营业时段未知，出发前请确认")
            if unverified:
                notices.append(unverified)
            elif dietary_status(restaurant, profile) == "unknown":
                notices.append("饮食要求需与商家确认，尚未获得认证")
            if source == "estimate":
                notices.append("到店或续程交通含估算")
            results.append({"restaurant":restaurant, "start":arrival, "go":go, "detour":detour,
                            "source":source, "notices":notices, "score":coarse_score + detour * 1.5 + go * 0.4})
            if len(results) >= 3:
                break
        return sorted(results, key=lambda option: (option["score"], option["restaurant"].id))

    tick_track("restaurants", (row[1] for row in (*strict, *likely, *fallback)), "已比较 {n} 家餐厅", only={"meals"})
    wanted = "、".join(requirement)
    return (collect(strict, "")
            or collect(likely, f"没有经过核验的{wanted}餐厅，这家按菜系看可能合规，仍须到店确认")
            or collect(fallback, f"附近没有{wanted}餐厅，这家只按价格和路线挑选，是否符合要求须到店确认"))
