from fastapi.testclient import TestClient

import app.graph as graph
from app.api import app
from tests.conftest import needs_data

pytestmark = needs_data
client = TestClient(app)


def test_health_and_meta():
    h = client.get("/api/health").json()
    assert h["ok"] and h["pois"] == 377
    m = client.get("/api/meta").json()
    assert "人文历史" in m["interests"] and m["hotels"]


def test_pois_are_gcj():
    p = client.get("/api/pois").json()
    assert len(p) == 377 and {"lat", "lon", "name"} <= p[0].keys()


def test_plan_endpoint_with_mock(monkeypatch, mock_llm):
    real = graph.run_plan
    monkeypatch.setattr("app.api.run_plan", lambda *a, **k: real(*a, llm=mock_llm, **k))
    r = client.post("/api/plan", json={"query": "", "form": {"days": 2, "interests": ["人文历史"]}, "select_mode": "rule"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["days"]) == 2 and body["report"]["passed"] in (True, False)
    poi = next(i for d in body["days"] for i in d["items"] if i["kind"] == "poi")
    assert poi["lat"] and poi["start_hm"] and poi["score"]["score"] is not None
    assert body["profile"]["hotel_gcj"]["lat"]
    assert body["meta"]["retrieval"]["strategy"] == "coverage_v1"
    assert body["meta"]["candidate_metrics"]["schedule_attempts"]
    assert body["candidates"][0]["available_days"]
    assert poi["detail"]["field_evidence"]["duration"]["basis"]


def test_meta_exposes_map_provider_without_security_code():
    m = client.get("/api/meta").json()
    assert m["map"]["provider"] in ("amap", "leaflet")
    import app.config as cfg
    if cfg.AMAP_JS_SECURITY:
        assert cfg.AMAP_JS_SECURITY not in str(m)  # 安全密钥绝不下发到前端


def test_custom_origin_survives_plan_and_map_conversion(monkeypatch, mock_llm):
    from app.places import normalize_place
    origin = normalize_place({"name": "龙翔桥地铁站A口", "cityname": "杭州市", "location": "120.164248,30.255323"})
    real = graph.run_plan
    monkeypatch.setattr("app.api.run_plan", lambda *args, **kwargs: real(*args, llm=mock_llm, **kwargs))
    response = client.post("/api/plan", json={"query": "", "form": {"days": 2, "hotel": origin}, "select_mode": "rule"})
    assert response.status_code == 200, response.text
    profile = response.json()["profile"]
    assert profile["hotel"]["name"] == origin["name"]
    assert abs(profile["hotel_gcj"]["lat"] - origin["gcj"]["lat"]) < 0.000002
    assert abs(profile["hotel_gcj"]["lon"] - origin["gcj"]["lon"]) < 0.000002


def test_online_meal_alternative_has_own_location_and_details(monkeypatch):
    from collections import OrderedDict
    import app.place_details as details
    from app.api import gcj
    from app.schemas import DayPlan, Item, Itinerary, MealAlternative, Restaurant, UserProfile, ValidationReport

    chosen = Restaurant(id=990001, name="原餐厅", lat=30.25, lon=120.16, price=80,
                        cuisine="杭帮菜", open_min=600, close_min=1260, provider="amap", rating=4.2)
    alternative = chosen.model_copy(update={"id": 990002, "name": "备选餐厅", "lat": 30.26, "lon": 120.17,
                                           "address": "备选路12号", "price": 55, "rating": 4.7})
    meal = Item(kind="meal", ref_id=chosen.id, name=chosen.name, start=720, end=780, cost=chosen.price, meal="lunch",
                alternatives=[MealAlternative(ref_id=alternative.id, name=alternative.name, cuisine=alternative.cuisine,
                              price=alternative.price, rating=alternative.rating, address=alternative.address,
                              detour_min=4, travel_source="amap", provider="amap", start=725, end=785)])
    result = {"profile": UserProfile(days=1), "itinerary": Itinerary(days=[DayPlan(day=1, items=[meal])]),
              "restaurants": {chosen.id: chosen, alternative.id: alternative}, "candidates": [], "selected": [],
              "report": ValidationReport(passed=True), "log": [], "usage": {}, "seconds": 0}
    monkeypatch.setattr("app.api.run_plan", lambda *args, **kwargs: result)
    monkeypatch.setattr(details, "_restaurants", OrderedDict())
    response = client.post("/api/plan", json={"query": "", "form": {"days": 1}})
    assert response.status_code == 200
    item = response.json()["days"][0]["items"][0]
    preview = item["alternatives"][0]
    assert {key: preview[key] for key in ("lat", "lon")} == gcj(alternative.lat, alternative.lon)
    assert preview["detail"]["id"] == alternative.id
    assert preview["detail"]["name"] == alternative.name
    assert preview["detail"]["rating"] == 4.7
    assert item["ref_id"] == chosen.id and item["start"] == 720
    detail_response = client.post("/api/place-details", json={"places": [{"kind": "meal", "id": alternative.id}]})
    assert detail_response.status_code == 200
    returned = detail_response.json()["places"][0]
    assert returned["name"] == alternative.name and returned["address"] == alternative.address
    assert returned["rating"] == 4.7
