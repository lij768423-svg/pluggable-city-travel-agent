from __future__ import annotations
from contextlib import nullcontext

from .geo import travel
from .progress import tick_track
from .restaurants import DIET_HINT, STRICT_DIET, RestaurantCatalog, brand_name, dietary_status, meal_start, valid_location
from .rules import MEAL_MIN, activity_window, interstop_ride_long
from .schemas import Restaurant, UserProfile


RIDE_MISS_LIMIT = 4


def rank_restaurants(position: tuple[float, float], following: tuple[float, float] | None, start: int,
                     profile: UserProfile, used: set[int], cap: float, latest_start: int,
                     restaurants: dict[int, Restaurant], route=None, live: bool = True,
                     catalog: RestaurantCatalog | None = None, day: int = 1,
                     need_arrive_by: int | None = None, is_departure: bool = False) -> list[dict]:
    with route.planning_limits() if hasattr(route, "planning_limits") else nullcontext():
        return _rank_restaurants(position, following, start, profile, used, cap, latest_start, restaurants,
                                 route, live, catalog, day, need_arrive_by, is_departure)


def _rank_restaurants(position: tuple[float, float], following: tuple[float, float] | None, start: int,
                      profile: UserProfile, used: set[int], cap: float, latest_start: int,
                      restaurants: dict[int, Restaurant], route=None, live: bool = True,
                      catalog: RestaurantCatalog | None = None, day: int = 1,
                      need_arrive_by: int | None = None, is_departure: bool = False) -> list[dict]:
    from .nodes.meals import meal_suitability
    if catalog:
        restaurants = catalog.nearby(following if is_departure and following is not None else position, live=live)
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
        if not valid_location(restaurant):
            continue
        if not restaurant.price_known or restaurant.price > cap or restaurant.price <= 0 or restaurant.price < (profile.meal_budget_min or 0):
            continue
        suitability = meal_suitability(restaurant, profile)
        diet = dietary_status(restaurant, profile)
        if suitability is None or diet == "conflict":
            continue
        coarse_go, coarse_mode, _ = (travel(restaurant.lat, restaurant.lon, *following)
                                     if is_departure and following is not None
                                     else travel(*position, restaurant.lat, restaurant.lon))
        if route is None and not is_departure and interstop_ride_long(coarse_go, coarse_mode):
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
        shortlist = pool[:30]
        # 只有真去查高德的公交规划才需要省请求；估算路线不联网，保持原有候选顺序
        live_transit = bool(route) and live and getattr(route, "source", None) == "amap" and getattr(route, "scenario", None) == "transit"
        if route and live and hasattr(route, "prefetch_walks"):
            # 步行距离先批量查完：去程、续程各一次请求，代替逐家各查一次
            spots = [(restaurant.lat, restaurant.lon) for _, _, restaurant in shortlist]
            route.prefetch_walks(position, spots)
            route.prefetch_walks(following or (profile.hotel.lat, profile.hotel.lon), spots)
            if live_transit and hasattr(route, "walk_known"):
                # 公交模式：前后两段都能步行的餐厅排在前面先检查，不用查公交。
                # 2026-10-17 实测 93 段餐厅公交全部超过 25 分钟，逐家查公交基本是白查；凑不够 3 家时仍按原顺序查公交
                after = following or (profile.hotel.lat, profile.hotel.lon)
                def walkable(restaurant: Restaurant) -> bool:
                    here = (restaurant.lat, restaurant.lon)
                    return route.walk_known(*position, *here) and route.walk_known(*here, *after)
                near = [row for row in shortlist if walkable(row[2])]
                # 要坐车的按粗估路程由近到远，近的都超时，远的更不可能合格
                far = sorted((row for row in shortlist if not walkable(row[2])),
                             key=lambda row: travel(*position, row[2].lat, row[2].lon)[0] + travel(row[2].lat, row[2].lon, *after)[0])
                shortlist = near + far
        # 公交模式下连续 RIDE_MISS_LIMIT 家因单段乘车超过上限被淘汰，就不再逐家查公交
        stop_early = live_transit
        ride_misses = 0
        for coarse_score, _, restaurant in shortlist:
            if stop_early and ride_misses >= RIDE_MISS_LIMIT:
                break
            destination = (restaurant.lat, restaurant.lon)
            if route:
                go_detail = route.detail(*position, *destination, live=live)
                if not go_detail.available:
                    continue
                go = go_detail.minutes
                go_mode = go_detail.mode
                # 去程已经不合格就不再查续程，省一次请求
                if not is_departure and interstop_ride_long(go, go_mode):
                    ride_misses += 1
                    continue
                early_arrival = meal_start(restaurant, start + go)
                if early_arrival is None or early_arrival > latest_start:
                    continue
                onward_detail = route.detail(*destination, *(following or (profile.hotel.lat, profile.hotel.lon)), live=live)
                if not onward_detail.available:
                    continue
                onward = onward_detail.minutes
                onward_mode = onward_detail.mode
                direct = route.leg(*position, *(following or (profile.hotel.lat, profile.hotel.lon)), live=False)[0]
                back = onward if following in (None, (profile.hotel.lat, profile.hotel.lon)) else route.leg(*destination, profile.hotel.lat, profile.hotel.lon, live=False)[0]
                source = "amap" if go_detail.source in {"amap", "same_place"} and onward_detail.source in {"amap", "same_place"} else "estimate"
            else:
                go, go_mode, _ = travel(*position, *destination)
                onward, onward_mode, _ = travel(*destination, *(following or (profile.hotel.lat, profile.hotel.lon)))
                direct = travel(*position, *(following or (profile.hotel.lat, profile.hotel.lon)))[0]
                back = travel(*destination, profile.hotel.lat, profile.hotel.lon)[0]
                source = "estimate"
            arrival = meal_start(restaurant, start + go)
            if not is_departure and interstop_ride_long(go, go_mode):
                continue
            if following is not None and interstop_ride_long(onward, onward_mode):
                ride_misses += 1
                continue
            if arrival is None or arrival > latest_start or arrival + MEAL_MIN + back > window["end"]:
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
