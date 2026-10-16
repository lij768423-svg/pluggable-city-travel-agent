from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
import json

import pytest
from pydantic import ValidationError

from app.amap import AmapBudgetExceeded, AmapRouter, MissingSnapshotError
from app.amap_limits import RequestBudget
from app.calendar import closed_on
from app.graph import _after_validate
from app.llm import OpenAICompatLLM
from app.meal_ranking import rank_restaurants
from app.nodes.meals import build_itinerary
from app.nodes.validate import validate_itinerary, validate_node
from app.restaurants import RestaurantCatalog, dietary_status, meal_start, normalize_restaurant, opening_periods
from app.rules import activity_window, required_meals
from app.schemas import DayPlan, Hotel, Item, Itinerary, LegEvidence, OpeningRules, POI, Restaurant, Selection, UserProfile, ValidationReport, Violation
from app.travel import Travel
from .test_amap import A, B, distance_handler, fake_router


@pytest.mark.parametrize("point", [{"lat":float("nan")}, {"lon":float("inf")}, {"lat":35}, {"lon":0}])
def test_origin_rejects_nonfinite_and_outside_city(point):
    with pytest.raises(ValidationError):
        Hotel(**point)


def test_api_nonfinite_input_returns_sanitized_422():
    from fastapi.testclient import TestClient
    from app.api import app
    response = TestClient(app).post("/api/plan",content='{"form":{"hotel":{"lat":NaN}}}',headers={"Content-Type":"application/json"})
    assert response.status_code == 422
    assert "input" not in response.json()["detail"][0]


def test_partial_form_does_not_reject_time_provided_in_text():
    from app.api import PlanRequest
    from app.llm import MockLLM
    from app.nodes.parse import parse_profile
    request = PlanRequest(query="八点出发十点返回",form={"day_end":600})
    profile = parse_profile(request.query,MockLLM({"parse":'{"day_start":480,"day_end":600}'}),request.form)
    assert activity_window(profile)["start"] == 480


@pytest.mark.parametrize("query,expected", [("从龙翔桥地铁站出发", "taxi"),("从龙翔桥地铁站出发，全程坐地铁", "transit")])
def test_origin_is_not_a_transport_choice(query, expected):
    from app.llm import MockLLM
    from app.nodes.parse import parse_profile
    profile = parse_profile(query,MockLLM({"parse":'{"transport":"transit"}'}))
    assert profile.transport == expected


@pytest.mark.parametrize("form", [
    {"day_start":1000,"day_end":900}, {"meal_budget_min":100,"meal_budget_max":50},
    {"days":1,"daily_windows":[{"day":2,"start":600,"end":900}]},
    {"daily_windows":[{"day":1,"start":600,"end":900},{"day":1,"start":700,"end":1000}]},
    {"max_walking_m":-1}, {"budget":float("nan")},
])
def test_rejects_conflicting_preferences(form):
    with pytest.raises(ValidationError):
        UserProfile(**form)


def test_half_days_only_require_meals_inside_the_window():
    profile = UserProfile(days=2, daily_windows=[{"day":1,"start":840,"end":1200},{"day":2,"start":540,"end":870}])
    assert required_meals(profile, 1) == ["dinner"]
    assert required_meals(profile, 2) == ["lunch"]
    assert activity_window(profile, 2)["end"] == 870


def test_retries_consume_request_budget_and_forks_are_independent(tmp_path, monkeypatch):
    monkeypatch.setattr("app.amap.time.sleep", lambda _: None)
    router = fake_router(tmp_path, lambda *_: {"status":"0","infocode":"10019"})
    first = router.fork(RequestBudget(2))
    second = router.fork(RequestBudget(1))
    with pytest.raises(AmapBudgetExceeded):
        first._get("test", {})
    with pytest.raises(AmapBudgetExceeded):
        second._get("test", {})
    assert first.budget.used == 2 and second.budget.used == 1
    assert len(router.log) == 3


def test_shared_request_budget_is_atomic_under_concurrency(tmp_path):
    router = fake_router(tmp_path, lambda *_: {"status":"1"}).fork(RequestBudget(7))
    def request(_):
        try:
            router._get("test", {})
            return True
        except AmapBudgetExceeded:
            return False
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert sum(pool.map(request, range(30))) == 7
    assert router.budget.used == 7


def test_planning_leaves_budget_for_final_routes(tmp_path):
    router = fake_router(tmp_path, lambda *_: {"status":"1"})
    route = Travel("amap",router=router,live_budget=8)
    with route.planning_limits():
        for index in range(6):
            route.router._get("test", {})
        assert not route._can_go_live()
        with pytest.raises(AmapBudgetExceeded):
            route.router._get("test", {})
    assert route._can_go_live()
    route.router._get("test", {})
    assert route.live_calls == 7


def test_online_cache_expires_and_is_not_persisted_by_default():
    router = AmapRouter(key="test", qps=1000)
    router._put("example", {"value":1})
    assert router.has("example") and router.cache_path is None and not router.persist
    router.records["example"]["fetched_epoch"] = 0
    assert not router.has("example")


@pytest.mark.parametrize("live", [True, False])
def test_missing_route_snapshot_never_networks_or_silently_estimates(tmp_path, monkeypatch, live):
    monkeypatch.setenv("TTA_OFFLINE", "1")
    router = AmapRouter(key="test", qps=1000)
    monkeypatch.setattr(router._client, "get", lambda *_args, **_kwargs: pytest.fail("offline network"))
    with pytest.raises(MissingSnapshotError):
        Travel("amap", "taxi", router=router).leg(*A, *B, live=live)


def test_missing_llm_snapshot_never_calls_upstream(tmp_path, monkeypatch):
    monkeypatch.setenv("TTA_OFFLINE", "1")
    monkeypatch.setattr("app.config.LLM_CACHE_DIR", tmp_path)
    llm = OpenAICompatLLM(api_key="test", cache=False)
    monkeypatch.setattr(llm.client.chat.completions, "create", lambda **_: pytest.fail("offline network"))
    with pytest.raises(RuntimeError, match="离线评测缺少"):
        llm.complete([{"role":"user","content":"missing"}])


def test_transit_unavailable_does_not_implicitly_switch_to_taxi(tmp_path):
    def handler(kind, params):
        return {"status":"1","route":{"transits":[]}} if kind == "integrated" else distance_handler()(kind, params)
    router = fake_router(tmp_path, handler)
    route = Travel("amap", "transit", router=router)
    result = route.detail(*A, *B)
    assert not result.available and result.mode == "公交/地铁"
    allowed = Travel("amap", "transit", router=router)
    allowed.allow_taxi_fallback = True
    result = allowed.detail(*A, *B)
    assert result.available and result.mode == "打车" and result.fallback_reason


def raw_restaurant(**updates):
    return {"id":"B_TEST_1","name":"湖边菜馆","cityname":"杭州市","location":"120.16,30.25",
            "type":"餐饮服务;中餐厅;江浙菜","business":{"cost":"68","rating":"4.4","opentime_week":"周一至周日 11:00-14:00 17:00-22:00"}, **updates}


def test_normalization_preserves_missing_data_and_stable_identity():
    known = normalize_restaurant(raw_restaurant())
    missing = normalize_restaurant(raw_restaurant(business={}))
    assert known and missing and known.id == missing.id and known.id < 0
    assert known.price == 68 and known.rating == 4.4 and known.hours_known
    assert missing.price == 0 and not missing.price_known and not missing.hours_known and missing.rating is None
    assert normalize_restaurant(raw_restaurant(cityname="上海市")) is None
    assert normalize_restaurant(raw_restaurant(type="餐饮服务;咖啡店")) is None


def test_split_and_overnight_opening_does_not_cross_closed_interval():
    restaurant = normalize_restaurant(raw_restaurant())
    assert meal_start(restaurant, 810) == 1020
    assert opening_periods("18:00-02:00") == [(0,120),(1080,1440)]
    assert opening_periods("周一至周五 09:00-22:00") == []


def test_halal_is_not_inferred_from_northwest_cuisine():
    """清真不能靠菜系推断；附近只有未核验的店时照样排进去，但必须写明须到店确认。"""
    restaurant = Restaurant(id=1,name="西北菜馆",cuisine="西北菜",lat=30.25,lon=120.16,price=60,open_min=600,close_min=1320)
    assert dietary_status(restaurant, UserProfile(dietary=["清真"])) == "unknown"
    relaxed = rank_restaurants((30.25,120.16),None,720,UserProfile(dietary=["清真"]),set(),100,810,{1:restaurant})
    assert [option["restaurant"].id for option in relaxed] == [1]
    assert any("须到店确认" in notice for notice in relaxed[0]["notices"])
    # 有核验过的店时就不放宽，也不再出现这条提示
    verified = restaurant.model_copy(update={"id": 2, "dietary_verified": ["清真"]})
    both = rank_restaurants((30.25,120.16),None,720,UserProfile(dietary=["清真"]),set(),100,810,{1:restaurant,2:verified})
    assert [option["restaurant"].id for option in both] == [2]
    assert not any("须到店确认" in notice for notice in both[0]["notices"])
    restaurant.cuisine = "西餐"
    assert dietary_status(restaurant, UserProfile(dietary=["只吃中餐"])) == "conflict"


def test_ranking_uses_real_detour_and_avoids_used_brand():
    places = {place_id:Restaurant(id=place_id,name=name,cuisine="江浙菜",lat=30.25,lon=longitude,price=60,open_min=600,close_min=1320)
              for place_id,name,longitude in [(1,"连锁甲(一店)",120.16),(2,"连锁甲(二店)",120.161),(3,"独立乙",120.162),(4,"独立丙",120.163)]}
    places[1].brand = places[2].brand = "连锁甲"
    class Route:
        def detail(self, lat1, lon1, lat2, lon2, live=True):
            minutes = 35 if lon2 == 120.162 else 8
            return LegEvidence(minutes=minutes,mode="公交/地铁",cost=2,source="amap")
        def leg(self, *args, **kwargs):
            return (8,"公交/地铁",2)
    result = rank_restaurants((30.25,120.16),None,720,UserProfile(),{1},100,810,places,route=Route())
    assert result[0]["restaurant"].id == 4
    assert 3 not in {option["restaurant"].id for option in result}
    assert all(option["source"] == "amap" for option in result)


def test_online_catalog_is_bounded_and_carries_provenance(tmp_path):
    router = fake_router(tmp_path, lambda *_: {"status":"1","pois":[raw_restaurant()]})
    route = Travel("amap",router=router,live_budget=1)
    catalog = RestaurantCatalog(route, initial={}, online=True)
    catalog.nearby((30.25,120.16))
    catalog.nearby((30.3,120.2))
    assert route.live_calls == 1 and catalog.online_count == 1
    assert next(iter(catalog.restaurants.values())).provider == "amap"


def fixture_poi():
    return POI(id=9000,name="测试展馆",type="博物馆",lat=30.254433,lon=120.169771,price=0,
               open_min=540,close_min=1080,min_hours=1,max_hours=2,
               opening_rules=OpeningRules(closed_weekdays=[0], last_entry=990))


def test_calendar_date_exceptions_override_regular_closed_day():
    poi = fixture_poi()
    profile = UserProfile(start_date=date(2026,10,19))
    assert closed_on(poi, profile, 1)
    assert not closed_on(poi, profile, 2)
    poi.opening_rules.open_dates = [date(2026,10,19)]
    assert not closed_on(poi, profile, 1)


def test_missing_restaurant_reserves_time_without_claiming_a_meal(monkeypatch):
    poi = fixture_poi()
    monkeypatch.setattr("app.nodes.meals.load_pois", lambda: {poi.id:poi})
    monkeypatch.setattr("app.nodes.meals.load_restaurants", lambda: {})
    profile = UserProfile(days=1, day_start=660, day_end=1020)
    result = build_itinerary([[poi.id]],profile,[Selection(poi_id=poi.id,reason="test")])
    assert result.days[0].reserved_meals
    assert not any(item.kind == "meal" for item in result.days[0].items)
    slot = result.days[0].reserved_meals[0]
    assert slot.end - slot.start == 60


def test_walking_and_calendar_are_hard_but_missing_meal_is_quality(monkeypatch):
    poi = fixture_poi()
    monkeypatch.setattr("app.nodes.validate.load_pois", lambda: {poi.id:poi})
    profile = UserProfile(days=1,start_date=date(2026,10,19),max_walking_m=500)
    itinerary = Itinerary(days=[DayPlan(day=1,items=[Item(kind="poi",ref_id=poi.id,name=poi.name,start=600,end=720,
        travel_evidence=LegEvidence(minutes=10,mode="步行",walking_m=800,cost=0,source="amap"))])])
    report = validate_itinerary(itinerary,profile)
    assert {item.code for item in report.hard} >= {"date_closed","walking_limit"}
    assert report.experience_status == "needs_attention" and report.data_status == "partial"


def test_quality_repair_runs_after_hard_constraints_pass():
    report = ValidationReport(passed=True,experience_status="needs_attention",violations=[Violation(code="missing_dinner",severity="soft",message="missing")])
    assert _after_validate({"report":report}) == "repair"


def test_best_candidate_is_not_replaced_by_worse_repair(monkeypatch):
    profile = UserProfile(days=1)
    best = {"score":(0,0,0),"marker":"keep"}
    state = {"profile":profile,"itinerary":Itinerary(days=[DayPlan(day=1)]),"best_candidate":best}
    assert validate_node(state)["best_candidate"] is best


def test_curated_review_has_44_identity_checked_records():
    from app.data import load_pois
    from app import data
    review = json.loads((Path(data.__file__).parent / "assets" / "poi_reviews.json").read_text())
    pois = load_pois()
    assert len(review["places"]) == 44
    assert len({record["id"] for record in review["places"]}) == len(review["places"])
    assert not pois[106].recommendable and pois[9].price == 40
    assert pois[202].opening_rules.closed_weekdays == [1]
    assert pois[179].opening_rules.closed_weekdays == [0,1]
    assert pois[36].max_hours < 1 and "估计" in pois[36].duration_source
    assert pois[118].opening_rules is None and pois[9].price_verified
    assert pois[30].price_verified


def test_explicit_meal_price_floor_is_respected():
    place = normalize_restaurant(raw_restaurant())
    profile = UserProfile(meal_budget_min=80,meal_budget_max=100)
    assert rank_restaurants((place.lat,place.lon),None,720,profile,set(),100,810,{place.id:place}) == []


def test_offline_restaurant_snapshot_is_available_to_shared_validator(tmp_path, monkeypatch):
    from app.data import load_restaurants
    place = normalize_restaurant(raw_restaurant())
    snapshot = tmp_path / "restaurants.json"
    snapshot.write_text(json.dumps([place.model_dump()]))
    monkeypatch.setenv("TTA_OFFLINE","1")
    monkeypatch.setenv("TTA_RESTAURANT_SNAPSHOT",str(snapshot))
    load_restaurants.cache_clear()
    try:
        assert load_restaurants()[place.id].external_id == "B_TEST_1"
    finally:
        load_restaurants.cache_clear()


@pytest.mark.parametrize("anchor", [(30.25,120.16),(30.28,120.16),(30.21,120.2),(30.26,120.06)])
@pytest.mark.parametrize("preference,expected", [("local",1),("quick",2),("quality",3)])
@pytest.mark.parametrize("transport", ["taxi","transit"])
def test_24_meal_regression_scenarios(anchor, preference, expected, transport):
    places = {place_id:Restaurant(id=place_id,name=name,cuisine=cuisine,lat=anchor[0],lon=anchor[1] + place_id * 0.0001,
              price=price,rating=rating,open_min=600,close_min=1320,provider="amap")
              for place_id,name,cuisine,price,rating in [(1,"杭州菜馆","江浙菜",68,4.0),(2,"片儿川面馆","小吃",24,4.0),(3,"精致杭帮菜","江浙菜",100,4.7)]}
    class Route:
        def detail(self, *args, **kwargs):
            return LegEvidence(minutes=6,mode="打车" if transport == "taxi" else "公交/地铁",cost=5,source="amap")
        def leg(self, *args, **kwargs):
            return (6,"测试路线",5)
    profile = UserProfile(food_preference=preference,transport=transport,hotel=Hotel(lat=anchor[0],lon=anchor[1]))
    ranked = rank_restaurants(anchor,None,720,profile,set(),150,810,places,route=Route())
    assert ranked[0]["restaurant"].id == expected
    assert len(ranked) == 3 and all(option["start"] + 60 <= 1200 for option in ranked)
