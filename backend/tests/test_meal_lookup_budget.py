"""餐厅排序少发高德请求：步行距离批量查、公交模式下可步行的先查、连续超时就停。"""
from types import SimpleNamespace

from app.meal_ranking import RIDE_MISS_LIMIT, rank_restaurants
from app.schemas import Restaurant, UserProfile
from app.travel import Travel


def cluster(count: int) -> dict[int, Restaurant]:
    return {i: Restaurant(id=i, name=f"店{i}", cuisine="江浙菜", lat=30.25, lon=120.16 + i * 0.001,
                          price=60, open_min=600, close_min=1320) for i in range(1, count + 1)}


class FakeRoute:
    """walkable 里的餐厅两头都能步行；其余一律 40 分钟公交（超过 25 分钟站间上限）。"""

    def __init__(self, scenario: str, walkable: set[tuple[float, float]] = frozenset()):
        self.source, self.scenario, self.walkable, self.lookups = "amap", scenario, walkable, 0

    def prefetch_walks(self, center, points):
        pass

    def walk_known(self, lat1, lon1, lat2, lon2):
        return (lat1, lon1) in self.walkable or (lat2, lon2) in self.walkable

    def detail(self, lat1, lon1, lat2, lon2, live=True):
        self.lookups += live          # 只数会联网的查询；live=False 在真实代码里只读缓存
        if self.walk_known(lat1, lon1, lat2, lon2):
            return SimpleNamespace(available=True, minutes=12, mode="步行", source="amap")
        return SimpleNamespace(available=True, minutes=40, mode="公交/地铁", source="amap")

    def leg(self, lat1, lon1, lat2, lon2, live=True):
        found = self.detail(lat1, lon1, lat2, lon2, live)
        return found.minutes, found.mode, 0.0


def rank(route, places):
    return rank_restaurants((30.25, 120.15), (30.26, 120.15), 720, UserProfile(), set(), 100, 810, places, route=route)


def test_transit_stops_after_repeated_long_rides():
    route = FakeRoute("transit")
    assert rank(route, cluster(20)) == []
    assert route.lookups == RIDE_MISS_LIMIT          # 以前会把 20 家逐一查完


def test_transit_checks_walkable_restaurants_first():
    places = cluster(20)
    walkable = {(places[i].lat, places[i].lon) for i in (17, 18, 19)}   # 原本排在最后
    route = FakeRoute("transit", walkable)
    picked = {option["restaurant"].id for option in rank(route, places)}
    assert picked == {17, 18, 19}
    assert route.lookups == 6                          # 三家各查去程、续程，没有白查公交


def test_taxi_scenario_keeps_checking_every_candidate():
    route = FakeRoute("taxi")
    assert rank(route, cluster(10)) == []
    assert route.lookups == 10


def test_prefetch_walks_fills_both_directions_with_one_request():
    calls = []

    class Router:
        cache, records = {}, {}

        def has(self, key):
            return key in self.cache

        def _batch(self, kind, origins, dest):
            calls.append((kind, list(origins), dest))
            for origin in origins:
                self.cache[f"walk|{origin}|{dest}"] = {"m": 600, "min": 8}

        def _put(self, key, value):
            self.cache[key] = value

    route = Travel("estimate", "transit")
    route.source, route.router = "amap", Router()
    route._can_go_live = lambda: True
    centre, spots = (30.25, 120.15), [(30.251, 120.151), (30.252, 120.152), (30.253, 120.153)]
    route.prefetch_walks(centre, spots)
    assert len(calls) == 1 and len(calls[0][1]) == 3
    assert all(route.walk_known(*centre, *spot) and route.walk_known(*spot, *centre) for spot in spots)


def test_estimate_routes_keep_the_original_order():
    """估算路线不联网，没必要省请求，也不能因此改变选中的餐厅。"""
    route = FakeRoute("transit")
    route.source = "estimate"
    assert rank(route, cluster(10)) == []
    assert route.lookups == 10
