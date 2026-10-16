import json

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.data import load_pois
from app.experiences import experience_count, keep_walks_together, walk_boundary
from app.graph import run_plan
from app.llm import MockLLM
from app.nodes.schedule import fit_service_min, service_min, split_days
from app.nodes.select import complete_experiences
from app.schemas import Candidate, Selection, UserProfile
from app.travel import Travel
from tests.conftest import needs_data
from tests.test_amap import A, distance_handler, fake_router
from tests.test_meal_quality import restaurant


@pytest.mark.parametrize("budget", [0, -1, "0"])
def test_invalid_total_budget_rejected_before_planning(monkeypatch, budget):
    def unexpected_plan(*args, **kwargs):
        pytest.fail("invalid budget reached the planner")
    monkeypatch.setattr("app.api.run_plan", unexpected_plan)
    response = TestClient(app).post("/api/plan", json={"query": "杭州一日游", "form": {"budget": budget}})
    assert response.status_code == 422


def test_unspecified_budget_has_positive_default():
    assert UserProfile().budget > 0


@needs_data
def test_walk_is_one_experience_with_separate_short_stops():
    places = load_pois()
    assert experience_count([36, 100, 136], places) == 1
    assert all(15 <= service_min(places[identifier], "relaxed") <= 30 for identifier in (36, 100, 136))
    assert all(service_min(places[identifier], "relaxed") <= 30 for identifier in (201, 204, 225, 283, 344, 345))
    assert experience_count([344, 283], places) == 0
    assert "雪后" in places[36].seasonal_note
    assert "未核实降雪" in places[36].seasonal_note
    assert places[36].recommendable
    assert keep_walks_together([36, 118, 100, 136], places) == [36, 100, 136, 118]
    assert not walk_boundary([36, 100, 136, 118], 2, places)
    assert walk_boundary([36, 100, 136, 118], 3, places)


@needs_data
def test_walk_stays_on_same_day_and_gains_another_experience():
    profile = UserProfile(days=2)
    groups = split_days([36, 100, 136, 118, 202, 20], profile)
    assert any({36, 100, 136} <= set(group) for group in groups)
    selected = [Selection(poi_id=identifier, reason="沿湖漫步", source="llm") for identifier in (36, 100, 136)]
    candidates = [Candidate(poi_id=identifier, score=score, breakdown={}) for identifier, score in [(36, 9), (100, 8), (136, 7), (225, 10), (118, 6)]]
    completed = complete_experiences(selected, candidates, UserProfile(days=1))
    assert [item.poi_id for item in completed] == [36, 100, 136, 118]
    assert complete_experiences(selected, candidates, UserProfile(days=1, day_start=540, day_end=780)) == selected


def test_adult_short_walk_avoids_driving_detour_without_forcing_families(tmp_path):
    destination = (A[0] + 0.016, A[1])
    router = fake_router(tmp_path, distance_handler(walk_m=2200, drive=(6500, 1200)))
    route = Travel("amap", "taxi", router=router)
    route.set_day(UserProfile(companions="couple"), 1)
    assert route.leg(*A, *destination)[1] == "步行"
    route.set_day(UserProfile(companions="elderly"), 1)
    assert route.leg(*A, *destination)[1] == "打车"
    route.set_day(UserProfile(companions="couple", max_walking_m=1000), 1)
    assert route.leg(*A, *destination)[1] == "打车"


def test_thirty_meter_transfer_is_not_reported_as_same_place(tmp_path):
    from app.geo import travel
    destination = (A[0] + 0.0003, A[1])
    route = Travel("amap", router=fake_router(tmp_path, distance_handler(walk_m=40)))
    assert route.leg(*A, *destination)[1] == "步行"
    assert travel(*A, *destination)[1] == "步行"


@needs_data
def test_closing_time_has_no_tolerance_and_stay_never_below_minimum():
    from app.nodes.validate import validate_itinerary
    from app.schemas import DayPlan, Item, Itinerary
    museum = load_pois()[92]
    start = museum.close_min - 70
    duration = fit_service_min(museum, "relaxed", start, 1230)
    assert duration == 60
    assert fit_service_min(museum, "relaxed", museum.close_min - 45, 1230) >= museum.min_hours * 60
    late = Itinerary(days=[DayPlan(day=1, items=[Item(kind="poi", ref_id=92, name=museum.name,
                                                   start=start, end=museum.close_min + 1)])])
    assert any(issue.code == "opening_hours" and issue.severity == "hard"
               for issue in validate_itinerary(late, UserProfile()).violations)


def test_meals_search_past_first_five_infeasible_candidates():
    from app.meal_ranking import rank_restaurants
    options = [restaurant(identifier, f"午餐菜馆{identifier}") for identifier in range(1, 7)]
    for place in options[:5]:
        place.open_min = 900
    result = rank_restaurants((30.25, 120.16), None, 720, UserProfile(), set(), 100, 810,
                              {place.id: place for place in options})
    assert result and result[0]["restaurant"].id == 6


def test_local_cuisine_is_not_penalized_against_vegetarian():
    from app.nodes.meals import meal_suitability
    assert meal_suitability(restaurant(1, "杭帮菜馆", "杭帮菜"), UserProfile()) < meal_suitability(restaurant(2, "牛排馆", "西餐"), UserProfile())
    assert meal_suitability(restaurant(3, "肯德基", "餐饮"), UserProfile()) > meal_suitability(restaurant(1, "杭帮菜馆", "杭帮菜"), UserProfile())
    assert meal_suitability(restaurant(3, "肯德基", "餐饮"), UserProfile(food_preference="quick")) == 0


def test_amap_local_cuisine_labels_are_preserved():
    from app.restaurants import normalize_restaurant
    raw = {"id": "B_TEST", "name": "江南忆·融合杭帮私房菜", "location": "120.16,30.26",
           "cityname": "杭州市", "type": "餐饮服务;中餐厅", "business": {"cost": "64"}}
    assert normalize_restaurant(raw).cuisine == "杭帮菜"


@needs_data
def test_reported_three_stop_case_includes_meals_and_different_afternoon():
    model = MockLLM({"select": json.dumps({"selected": [{"id": identifier, "reason": "雪后观景"} for identifier in (36, 100, 136)]})})
    result = run_plan("", {"days": 1, "budget": 800, "pace": "relaxed", "interests": ["自然风光", "人文历史"],
                           "start_date": "2026-10-16", "day_start": 560, "transport": "taxi",
                           "must_visit": ["断桥残雪", "白堤", "平湖秋月"],
                           "hotel": {"name": "龙翔桥(地铁站)", "lat": 30.260403442649167, "lon": 120.17062637077532}},
                      llm=model, travel_source="estimate")
    day = result["itinerary"].days[0]
    assert result["report"].passed, result["report"].model_dump()
    assert {item.meal for item in day.items if item.kind == "meal"} == {"lunch", "dinner"}
    sights = [item for item in day.items if item.kind == "poi"]
    assert {36, 100, 136} <= {item.ref_id for item in sights}
    positions = [index for index, item in enumerate(sights) if item.ref_id in {36, 100, 136}]
    assert max(positions) - min(positions) == 2
    walk_positions = [index for index, item in enumerate(day.items) if item.kind == "poi" and item.ref_id in {36, 100, 136}]
    assert max(walk_positions) - min(walk_positions) == 2
    assert experience_count([item.ref_id for item in sights], load_pois()) >= 2
    assert any(item.end >= 960 and item.ref_id not in {36, 100, 136} for item in sights)
    bridge = next(item for item in sights if item.ref_id == 36)
    assert "雪后" not in bridge.reason
