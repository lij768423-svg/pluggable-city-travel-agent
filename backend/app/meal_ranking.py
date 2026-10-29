from __future__ import annotations
from contextlib import nullcontext

from .geo import travel
from .progress import tick_track
from .data import SPICY_CUISINES
from .restaurants import RestaurantCatalog, brand_name, meal_start, spice_conflict, valid_location
from .rules import HARD_INTERSTOP_RIDE_MIN, MEAL_MIN, activity_window, interstop_ride_long
from .schemas import Restaurant, UserProfile
from .travel import depart_at


RIDE_MISS_LIMIT = 4
# 每餐上限由总预算推出（用户没给每餐范围）时只是参考：近店可以略超，超出部分按每元扣分。
# 2026-10-28 用户：预算紧时系统打车去很远的地方吃一顿很便宜的饭，不合理。
# 起因：上限 20 元把附近 25、30 元的店全部排除，只剩远处的便宜店；而去吃饭多出来的车费从来不算。
MEAL_CAP_SLACK = 10.0  # 只放宽固定的 10 元：预算宽的情况几乎不受影响，预算紧时附近 25、30 元的店能进来
OVER_CAP_WEIGHT = 0.5
# 为去吃这顿多出来的交通费（去程 + 续程 − 直达），每元扣分；比餐价偏离目标（每元 0.18）重，多花的车费是纯开销
EXTRA_FARE_WEIGHT = 0.5


def flexible_cap(cap: float, profile: UserProfile) -> float:
    """候选餐价的放宽上限；用户明说了每餐预算就按用户的，不放宽。"""
    if profile.meal_budget_max is not None:
        return cap
    return cap + MEAL_CAP_SLACK


def ride_too_long(minutes: int, mode: str, estimated: bool) -> bool:
    """实测路段按舒适线淘汰；估算路段（额度紧或未缓存时的兜底，公交估算普遍偏长）只按硬上限淘汰，并在候选上提示。

    2026-10-20 用户：“先修这个”（缺餐）。苏州 17 号额度紧时到店路线全退回估算，连续几家“坐车超 25 分钟”就放弃，整天缺餐。
    """
    if estimated:
        return mode not in {"步行", "原地"} and minutes > HARD_INTERSTOP_RIDE_MIN
    return interstop_ride_long(minutes, mode)


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
    pool: list[tuple[float, int, Restaurant]] = []
    flex = flexible_cap(cap, profile)
    target = min(cap * 0.75, 100 if profile.food_preference == "quality" else 65)
    if profile.meal_budget_min is not None:
        target = min(cap, (profile.meal_budget_min + cap) / 2)
    for restaurant in restaurants.values():
        if not valid_location(restaurant):
            continue
        if not restaurant.price_known or restaurant.price > flex or restaurant.price <= 0 or restaurant.price < (profile.meal_budget_min or 0):
            continue
        suitability = meal_suitability(restaurant, profile)
        if suitability is None or spice_conflict(restaurant, profile):
            continue
        coarse_go, coarse_mode, _ = (travel(restaurant.lat, restaurant.lon, *following)
                                     if is_departure and following is not None
                                     else travel(*position, restaurant.lat, restaurant.lon))
        if route is None and not is_departure and interstop_ride_long(coarse_go, coarse_mode):
            continue
        score = suitability + coarse_go * 0.6 + abs(restaurant.price - target) * 0.18
        score += max(0.0, restaurant.price - cap) * OVER_CAP_WEIGHT
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
        if profile.spice == "love" and restaurant.cuisine in SPICY_CUISINES:
            score -= 15
        pool.append((score, restaurant.id, restaurant))

    def collect(pool: list[tuple[float, int, Restaurant]]) -> list[dict]:
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
                with depart_at(route, start):
                    go_detail = route.detail(*position, *destination, live=live)
                if not go_detail.available:
                    continue
                go = go_detail.minutes
                go_mode = go_detail.mode
                # 只放宽联网路线因额度/缓存缺失退回的估算；纯估算模式（离线评测）各段都是估算，仍按舒适线
                fallback = getattr(route, "source", None) == "amap"
                go_estimated = fallback and go_detail.source == "estimate"
                # 去程已经不合格就不再查续程，省一次请求；估算淘汰不算“连续坐车太久”
                if not is_departure and ride_too_long(go, go_mode, go_estimated):
                    ride_misses += not go_estimated
                    continue
                early_arrival = meal_start(restaurant, start + go)
                if early_arrival is None or early_arrival > latest_start:
                    continue
                leave = early_arrival + MEAL_MIN
                with depart_at(route, leave):
                    onward_detail = route.detail(*destination, *(following or (profile.hotel.lat, profile.hotel.lon)), live=live)
                if not onward_detail.available:
                    continue
                onward = onward_detail.minutes
                onward_mode = onward_detail.mode
                onward_estimated = fallback and onward_detail.source == "estimate"
                with depart_at(route, start):
                    direct, _, direct_cost = route.leg(*position, *(following or (profile.hotel.lat, profile.hotel.lon)), live=False)
                go_cost, onward_cost = getattr(go_detail, "cost", 0.0), getattr(onward_detail, "cost", 0.0)  # 测试替身可能不带车费
                with depart_at(route, leave):
                    back = onward if following in (None, (profile.hotel.lat, profile.hotel.lon)) else route.leg(*destination, profile.hotel.lat, profile.hotel.lon, live=False)[0]
                source = "amap" if go_detail.source in {"amap", "same_place"} and onward_detail.source in {"amap", "same_place"} else "estimate"
            else:
                go, go_mode, go_cost = travel(*position, *destination)
                onward, onward_mode, onward_cost = travel(*destination, *(following or (profile.hotel.lat, profile.hotel.lon)))
                direct, _, direct_cost = travel(*position, *(following or (profile.hotel.lat, profile.hotel.lon)))
                back = travel(*destination, profile.hotel.lat, profile.hotel.lon)[0]
                source = "estimate"
                go_estimated = onward_estimated = False  # 不查路线的粗排保持原口径
            arrival = meal_start(restaurant, start + go)
            if not is_departure and ride_too_long(go, go_mode, go_estimated):
                continue
            if following is not None and ride_too_long(onward, onward_mode, onward_estimated):
                ride_misses += not onward_estimated
                continue
            if arrival is None or arrival > latest_start or arrival + MEAL_MIN + back > window["end"]:
                continue
            if need_arrive_by is not None and arrival + MEAL_MIN + onward > need_arrive_by:
                continue
            detour = max(0, go + onward - direct)
            extra_fare = round(max(0.0, go_cost + onward_cost - direct_cost), 1)
            if restaurant.price > cap and extra_fare > 0:
                continue  # 超出每餐上限只换来“不用多花车费”才值得；还要多花车费的贵店不选
            notices = []
            if brand_name(restaurant) in used_brands:
                notices.append("可行候选较少，沿用了同一品牌")
            if not restaurant.hours_known:
                notices.append("营业时段未知，出发前请确认")
            if source == "estimate":
                notices.append("到店或续程交通含估算")
            results.append({"restaurant":restaurant, "start":arrival, "go":go, "detour":detour, "extra_fare":extra_fare,
                            "source":source, "notices":notices,
                            "score":coarse_score + detour * 1.5 + go * 0.4 + extra_fare * EXTRA_FARE_WEIGHT})
            if len(results) >= 3:
                break
        # 餐价加多出的车费超过放宽上限的不选；都超过时仍按分数给出，不让这一餐落空
        affordable = [option for option in results if option["restaurant"].price + option["extra_fare"] <= flex]
        return sorted(affordable or results, key=lambda option: (option["score"], option["restaurant"].id))

    tick_track("restaurants", (row[1] for row in pool), "已比较 {n} 家餐厅", only={"meals"})
    return collect(pool)
