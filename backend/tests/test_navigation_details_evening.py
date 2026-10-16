from datetime import date
from types import SimpleNamespace

import httpx
import pytest
from pydantic import ValidationError

from app import config
from app.amap import AmapRouter, pt
from app.evening import add_evening_visits
from app.llm import OpenAICompatLLM
from app.navigation import MapPoint, NavigationLeg, NavigationRequest, get_navigation, route_segments
from app.place_details import describe_place, enrich_place, match_place
from app.rules import activity_window
from app.schemas import DayPlan, Hotel, Item, Itinerary, POI, Restaurant, UserProfile


def make_router(handler):
    router = AmapRouter(key="test", qps=10000)
    router._client = httpx.Client(transport=httpx.MockTransport(handler))
    return router


def navigation_request(mode="步行"):
    return NavigationRequest(date=date(2026, 10, 20), legs=[NavigationLeg(
        origin=MapPoint(lon=120.16, lat=30.25), destination=MapPoint(lon=120.17, lat=30.26),
        mode=mode, departure=1150)])


@pytest.mark.parametrize("mode,endpoint", [("步行", "walking"), ("打车", "driving")])
def test_navigation_keeps_service_geometry_and_caches(mode, endpoint):
    def handle(request):
        assert request.url.path.endswith(endpoint)
        return httpx.Response(200, json={"status":"1", "route":{"paths":[{"duration":"600", "distance":"1000", "steps":[
            {"polyline":"120.16,30.25;120.165,30.251;120.17,30.26", "instruction":"沿道路前行", "distance":"1000"}]}]}})
    router = make_router(handle)
    first = get_navigation(navigation_request(mode), router)
    assert first["queries"] == 1
    assert first["legs"][0]["segments"][0]["path"][1] == [120.165, 30.251]
    assert get_navigation(navigation_request(mode), router)["queries"] == 0


def test_transit_uses_departure_time_and_keeps_walk_bus_separate():
    def handle(request):
        assert request.url.params["time"] == "19:10"
        assert request.url.params["date"] == "2026-10-20"
        return httpx.Response(200, json={"status":"1", "route":{"transits":[{"duration":"1200", "segments":[
            {"walking":{"steps":[{"polyline":"120.16,30.25;120.161,30.251"}]},
             "bus":{"buslines":[{"name":"1号线", "polyline":"120.162,30.252;120.17,30.26", "departure_stop":{"name":"甲"}, "arrival_stop":{"name":"乙"}}]}}
        ]}]}})
    response = get_navigation(navigation_request("公交/地铁"), make_router(handle))
    segments = response["legs"][0]["segments"]
    assert [segment["mode"] for segment in segments] == ["步行", "公交/地铁"]
    assert segments[0]["path"][-1] != segments[1]["path"][0]
    assert "1号线" in segments[1]["instruction"]


def test_unavailable_navigation_never_draws_endpoint_line():
    router = make_router(lambda request: httpx.Response(200, json={"status":"1", "route":{"paths":[]}}))
    result = get_navigation(navigation_request(), router)["legs"][0]
    assert result["status"] == "unavailable" and result["segments"] == []


def test_missing_polyline_is_reported_as_partial():
    segments, missing = route_segments({"steps":[{"distance":"200", "polyline":""}]}, "步行")
    assert not segments and missing == 1


@pytest.mark.parametrize("coordinate", [float("nan"), float("inf"), 0, 180])
def test_navigation_rejects_bad_coordinates(coordinate):
    with pytest.raises(ValidationError):
        MapPoint(lat=coordinate, lon=120.16)


def test_navigation_failure_does_not_expose_key_or_make_geometry():
    router = make_router(lambda request: httpx.Response(403, text="test"))
    result = get_navigation(navigation_request(), router)
    assert result["legs"][0]["segments"] == []
    assert "test" not in str(result)


def sample_poi(**values):
    return POI(id=69, name="武林夜市", type="商业街", lat=30.254, lon=120.17,
               open_min=1020, close_min=values.pop("close_min", 1380), price=0,
               min_hours=1, max_hours=2, interests=["购物街区","夜景夜游"], audience=["情侣","朋友"], **values)


def test_place_rating_requires_identity_and_distance():
    place = sample_poi()
    assert match_place(place, [{"name":"武林夜市分店", "location":pt(place.lat,place.lon)}]) is None
    assert match_place(place, [{"name":"武林夜市", "location":"120.5,30.5"}]) is None
    assert match_place(place, [{"name":"武林夜市", "location":pt(place.lat,place.lon)}]) is not None


@pytest.mark.parametrize("rating,expected", [("4.5",4.5), ("",None), ("0",None), ("9",None)])
def test_rating_missing_is_not_fabricated(rating, expected):
    place = sample_poi()
    router = make_router(lambda request: httpx.Response(200, json={"status":"1", "pois":[
        {"name":place.name, "location":pt(place.lat,place.lon), "business":{"rating":rating}}]}))
    result = enrich_place(place, describe_place(place,"poi"), router)
    assert result["rating"] == expected
    assert result["rating_status"] == ("ready" if expected else "unrated")
    assert result["audiences"] == ["情侣","朋友"]


@pytest.fixture
def evening_data(monkeypatch):
    place = sample_poi()
    meal = Restaurant(id=7, name="晚餐", lat=30.254, lon=120.168, price=50, cuisine="江浙菜", open_min=600, close_min=1380)
    monkeypatch.setattr("app.evening.load_pois", lambda:{69:place})
    monkeypatch.setattr("app.nodes.validate.load_pois", lambda:{69:place})
    monkeypatch.setattr("app.evening.load_restaurants", lambda:{7:meal})
    itinerary = Itinerary(days=[DayPlan(day=1, items=[Item(kind="meal", ref_id=7, name=meal.name, start=1050, end=1110, meal="dinner", cost=50)])])
    profile = UserProfile(days=1, day_start=1050, day_end=1260, budget=200, hotel=Hotel(lat=30.254,lon=120.166))
    return place, itinerary, profile


def test_evening_adds_feasible_visit_after_dinner(evening_data):
    _, itinerary, profile = evening_data
    result, added = add_evening_visits(itinerary, profile)
    assert added and result.days[0].items[-1].evening
    assert result.days[0].items[-1].start >= 1110
    assert result.days[0].items[-1].end + result.days[0].return_travel_min <= 1260
    assert len(itinerary.days[0].items) == 1


@pytest.mark.parametrize("restriction", ["disabled", "early", "closed", "avoid", "walking"])
def test_evening_respects_user_and_feasibility(evening_data, restriction):
    place, itinerary, profile = evening_data
    if restriction == "disabled": profile.include_evening = False
    if restriction == "early": profile.day_end = 1140
    if restriction == "closed": place.close_min = 1100
    if restriction == "avoid": profile.avoid = ["武林"]
    if restriction == "walking": profile.max_walking_m = 500
    result, added = add_evening_visits(itinerary, profile)
    assert not added and len(result.days[0].items) == 1


def test_evening_window_defaults_and_explicit_override():
    assert activity_window(UserProfile())['end'] == 1260
    assert activity_window(UserProfile(companions="elderly"))['end'] == 1230
    assert activity_window(UserProfile(day_end=1140))['end'] == 1140
    assert activity_window(UserProfile(include_evening=False))['end'] == 1200


def test_thinking_request_is_enabled_without_exposing_reasoning(monkeypatch):
    monkeypatch.setattr(config,"LLM_THINKING","adaptive")
    llm = OpenAICompatLLM(api_key="test",model="MiniMax-M3",cache=False)
    captured = {}
    def complete(**payload):
        captured.update(payload)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}',reasoning_content="private reasoning"))],usage=None)
    llm.client.chat.completions.create = complete
    assert llm.complete([{"role":"user","content":"test"}],tag="test",max_tokens=100) == '{"ok":true}'
    assert captured["extra_body"] == {"thinking":{"type":"adaptive"},"reasoning_split":True}
    assert captured["max_tokens"] >= 12000


def test_retrieval_collapses_reviewed_same_site_aliases():
    from app.nodes.retrieve import retrieve
    candidates = retrieve(UserProfile(start_date=date(2026, 10, 20), budget=800), k=1000)
    assert len({candidate.poi_id for candidate in candidates} & {158, 190}) == 1


def test_same_site_matching_does_not_merge_distant_namesakes():
    from app.matching import same_attraction
    original = sample_poi().model_copy(update={"name": "德寿宫遗址"})
    alias = original.model_copy(update={"name": "南宋德寿宫遗址博物馆"})
    assert same_attraction(original, alias)
    assert not same_attraction(original, alias.model_copy(update={"lat": original.lat + 0.1}))


def test_daytime_recommendations_do_not_promise_after_dinner():
    from app.nodes.meals import scheduled_reason
    reason = "适合晚饭后沿街漫步，兼具人文历史与市井烟火气"
    assert scheduled_reason(reason, 750, 825) == "兼具人文历史与市井烟火气"
    assert "晚饭后" in scheduled_reason(reason, 1140, 1200)


def test_curated_guide_has_place_specific_content_and_provenance():
    place = sample_poi().model_copy(update={"name": "德寿宫遗址", "summary": "南宋皇家园林遗址博物馆"})
    detail = describe_place(place, "poi")
    assert "南宋" in detail["introduction"]
    assert any("宫苑遗址" in highlight for highlight in detail["highlights"])
    assert "模型辅助生成" in detail["content_source"]
    assert "非场馆官方说明" in detail["content_source"]
    assert detail["rating"] is None
