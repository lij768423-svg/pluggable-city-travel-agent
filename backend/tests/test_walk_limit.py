"""当天步行超上限：修复环节把走路最多的路段改打车，哪怕更慢或更贵（2026-10-22 用户决定）。"""
from app.nodes.repair import repair, repair_node
from app.schemas import (POI, Candidate, DayPlan, Hotel, Item, Itinerary, LegEvidence, Selection, UserProfile,
                         ValidationReport, Violation)
from app.travel import FORCED_TAXI_REASON, Travel

from .test_amap import A, B, distance_handler, fake_router

HOTEL = Hotel(name="测试起点", lat=30.25, lon=120.16)


def rush_hour(walk="2020", quote=""):
    # 北京 15 号同型：公交 45 分钟、接驳步行 2020 米；早高峰打车 69 分钟，比公交慢
    def handler(kind, params):
        if kind == "integrated":
            return {"status": "1", "route": {"taxi_cost": quote,
                    "transits": [{"duration": "2700", "cost": "3", "walking_distance": walk}]}}
        return distance_handler(drive=(7284, 4167))(kind, params)
    return handler


def test_forced_taxi_applies_even_when_slower_and_caches_separately(tmp_path):
    route = Travel("amap", "transit", router=fake_router(tmp_path, rush_hour()), live_budget=None)
    route.set_day(UserProfile(days=1, budget=300, transport="transit", companions="solo"), 1)
    assert route.leg(*A, *B) == (45, "公交/地铁", 3.0)  # 单段规则：打车更慢，不改
    route.taxi_overrides.add(route.pair(*A, *B))
    forced = route.detail(*A, *B)
    assert (forced.mode, forced.minutes, forced.walking_m, forced.fallback_reason) == ("打车", 74, 0, FORCED_TAXI_REASON)
    route.taxi_overrides.clear()
    assert route.leg(*A, *B) == (45, "公交/地铁", 3.0)  # 改前的结果另存一份，没被覆盖


def test_forced_taxi_ignores_fare_cap_but_needs_taxi_allowed(tmp_path):
    far = (30.40, 120.25)  # 直线约 21 公里，标定模型车费远超 60 元

    def handler(kind, params):
        if kind == "integrated":
            return {"status": "1", "route": {"transits": [{"duration": "4800", "cost": "6", "walking_distance": "2200"}]}}
        return distance_handler(drive=(24000, 2400))(kind, params)

    for allowed in (True, False):
        route = Travel("amap", "transit", router=fake_router(tmp_path / str(allowed), handler), live_budget=None)
        route.set_day(UserProfile(days=1, budget=1500, transport="transit", companions="family_kids",
                                  allow_taxi_fallback=allowed), 1)
        assert route.detail(*A, *far).mode == "公交/地铁"
        route.taxi_overrides.add(route.pair(*A, *far))
        result = route.detail(*A, *far)
        if allowed:
            assert result.mode == "打车" and result.cost > 60 and result.walking_m == 0
        else:
            assert result.mode == "公交/地铁"


def place(identifier, lat, lon, **changes):
    return POI(id=identifier, name=f"地点{identifier}", type="公园", lat=lat, lon=lon, open_min=540, close_min=1020,
               price=0, min_hours=1, max_hours=2, **changes)


def walking_day(monkeypatch, legs, profile, places, day_walking):
    """一天：住处 → 地点1 → 地点2 → 住处；legs 是三段的 (方式, 步行米数)，预先写进交通结果缓存。"""
    monkeypatch.setattr("app.nodes.repair.load_pois", lambda: places)
    monkeypatch.setattr("app.data.load_pois", lambda: places)
    route = Travel("estimate", "transit")
    points = [(HOTEL.lat, HOTEL.lon), (places[1].lat, places[1].lon), (places[2].lat, places[2].lon), (HOTEL.lat, HOTEL.lon)]
    with route.for_day(profile, 1):
        for (mode, walked), origin, destination in zip(legs, points, points[1:]):
            route.evidence[route.edge_key(*origin, *destination)] = LegEvidence(minutes=30, mode=mode, cost=0, walking_m=walked, source="amap")
    itinerary = Itinerary(days=[DayPlan(day=1, walking_m=day_walking, items=[
        Item(kind="poi", ref_id=1, name="地点1", start=600, end=700), Item(kind="poi", ref_id=2, name="地点2", start=720, end=820)])])
    return {"profile": profile, "itinerary": itinerary, "dropped": [], "travel": route,
            "selected": [Selection(poi_id=1, reason=""), Selection(poi_id=2, reason="")],
            "candidates": [Candidate(poi_id=1, score=0.7), Candidate(poi_id=2, score=0.5)],
            "report": ValidationReport(passed=False, violations=[
                Violation(code="walking_limit", severity="hard", message="", day=1)])}, route, points


def test_repair_switches_the_longest_walks_to_taxi_instead_of_removing_places(monkeypatch):
    # 亲子上限 4500 米：去程接驳 2400、园间步行 900、返程接驳 2300，共 5600；改掉去程（2400）即降到 3200，不再改其他段
    places = {1: place(1, 30.30, 120.20), 2: place(2, 30.305, 120.205)}
    profile = UserProfile(days=1, budget=1500, transport="transit", companions="family_kids", hotel=HOTEL)
    state, route, points = walking_day(monkeypatch, [("公交/地铁", 2400), ("步行", 900), ("公交/地铁", 2300)], profile, places, 5600)
    state["repair_fingerprints"] = [str(([1, 2], 1.0, []))]  # 上一轮同样的选点，但还没改交通
    out = repair_node(state)
    assert out["repair_changed"] and [s.poi_id for s in out["selected"]] == [1, 2]
    assert route.taxi_overrides == {route.pair(*points[0], *points[1])}
    assert "1 段改打车" in out["log"][0] and "前往地点1" in out["log"][0]


def test_repair_falls_back_to_removing_when_walking_is_inside_the_parks(monkeypatch):
    # 超限来自园内步行，路段只有短步行：没有可改的路段，退回删得分最低的景点
    places = {1: place(1, 30.30, 120.20, walking_m=3000), 2: place(2, 30.302, 120.202, walking_m=2500)}
    profile = UserProfile(days=1, budget=1500, transport="transit", companions="family_kids", hotel=HOTEL)
    state, route, _ = walking_day(monkeypatch, [("步行", 500), ("步行", 300), ("步行", 400)], profile, places, 6700)
    selected, _, log, changed = repair(state)
    assert changed and [s.poi_id for s in selected] == [1] and not route.taxi_overrides
    assert not any("改打车" in line for line in log)


def test_repair_does_not_force_taxi_when_not_allowed(monkeypatch):
    places = {1: place(1, 30.30, 120.20), 2: place(2, 30.305, 120.205)}
    profile = UserProfile(days=1, budget=1500, transport="transit", companions="family_kids", hotel=HOTEL,
                          allow_taxi_fallback=False)
    state, route, _ = walking_day(monkeypatch, [("公交/地铁", 2400), ("步行", 900), ("公交/地铁", 2300)], profile, places, 5600)
    selected, _, _, changed = repair(state)
    assert changed and len(selected) == 1 and not route.taxi_overrides


def test_finish_result_restores_the_best_rounds_taxi_overrides(monkeypatch):
    from app import graph
    monkeypatch.setattr("app.candidate_metrics.candidate_metrics", lambda out: {})
    monkeypatch.setattr("app.meal_edits.meal_metrics", lambda out: {})

    class Route:
        taxi_overrides = {("a",), ("b",)}
        seen = None

        def finalize(self, *args):
            Route.seen = set(self.taxi_overrides)

        def stats(self):
            return {}

    best = {"taxi_overrides": {("a",)}, "itinerary": Itinerary(days=[]), "report": ValidationReport(passed=True),
            "selected": [], "day_orders": [], "dropped": []}
    graph.finish_result({"best_candidate": best, "travel": Route(), "profile": UserProfile(days=1), "log": []})
    assert Route.seen == {("a",)}
