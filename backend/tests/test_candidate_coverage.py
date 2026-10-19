from datetime import date

import pytest

from app.calendar import closed_on
from app.candidate_metrics import candidate_metrics
from app.data import load_pois
from app.nodes.retrieve import eligibility, retrieve_with_report, score_poi
from app.poi_semantics import experience_notices, safe_reason
from app.schemas import Candidate, DayPlan, ExperienceCondition, Item, Itinerary, OpeningRules, POI, Selection, UserProfile, ValidationReport
from tests.conftest import needs_data


def place(identifier, interests=None, **values):
    return POI(id=identifier, name=values.pop("name", f"地点{identifier}"), type="公园", lat=30.255, lon=120.17,
               open_min=540, close_min=1200, price=values.pop("price", 0), min_hours=1, max_hours=2,
               interests=interests or ["自然风光"], **values)


@pytest.fixture
def pool(monkeypatch):
    places = {}
    monkeypatch.setattr("app.nodes.retrieve.load_pois", lambda: places)
    monkeypatch.setattr("app.nodes.retrieve.travel", lambda *coordinates: (10, "步行", 0))
    return places


def test_rare_interest_survives_a_high_scoring_majority(pool):
    pool.update({identifier: place(identifier, ["自然风光"]) for identifier in range(40)})
    pool[40] = place(40, ["茶文化"], stamina=3)
    candidates, report = retrieve_with_report(UserProfile(interests=["自然风光", "茶文化"]), k=3)
    assert len(candidates) == 3
    assert 40 in {candidate.poi_id for candidate in candidates}
    assert all(entry["status"] == "covered" for entry in report["interest_coverage"])
    assert all(candidate.available_days == [1, 2] for candidate in candidates)


def test_capacity_and_missing_data_are_distinct(pool):
    pool.update({1: place(1, ["自然风光"]), 2: place(2, ["茶文化"])})
    _, report = retrieve_with_report(UserProfile(interests=["自然风光", "茶文化", "未收录兴趣"]), k=1)
    assert [entry["status"] for entry in report["interest_coverage"]] == ["covered", "capacity", "data_missing"]
    assert report["must_visit_recall"]["rate"] is None


def test_must_visit_is_prioritized_but_never_bypasses_closure(pool):
    pool.update({1: place(1, name="开放甲园"), 2: place(2, name="闭馆乙馆", opening_rules=OpeningRules(closed_weekdays=[5]))})
    candidates, report = retrieve_with_report(UserProfile(days=1, start_date=date(2026, 10, 17),
        must_visit=["开放甲园", "闭馆乙馆", "zz-absent-99"]), k=1)
    assert [candidate.poi_id for candidate in candidates] == [1]
    assert [entry["status"] for entry in report["must_visit"]] == ["covered", "conditions_unmatched", "data_missing"]
    assert report["must_visit"][1]["reasons"] == {"date_closed": 1}
    assert report["must_visit_recall"] == {"numerator": 1, "denominator": 3, "rate": 1 / 3}


def test_budget_and_user_avoid_are_not_overridden_by_coverage(pool):
    pool.update({1: place(1, ["茶文化"], price=500), 2: place(2, ["博物馆展览"], name="拒绝馆")})
    candidates, report = retrieve_with_report(UserProfile(budget=100, avoid=["拒绝馆"], interests=["茶文化", "博物馆展览"]))
    assert not candidates
    assert report["excluded_counts"] == {"ticket_over_budget": 1, "explicit_avoid": 1}
    assert all(entry["status"] == "conditions_unmatched" for entry in report["interest_coverage"])


def test_continuous_walk_keeps_complementary_stops_and_named_landmarks(pool):
    pool.update({1: place(1, ["自然风光"], visit_group="lake", visit_role="walk_segment"),
                 2: place(2, ["自然风光"], visit_group="lake", visit_role="viewpoint"),
                 3: place(3, ["人文历史"], visit_group="lake"), 4: place(4, ["茶文化"])})
    candidates, report = retrieve_with_report(UserProfile(interests=["自然风光", "茶文化"]))
    assert {candidate.poi_id for candidate in candidates} == {1, 3, 4}
    assert report["continuous_duplicates"] == [{"poi_id": 2, "representative": 1}]
    assert next(candidate for candidate in candidates if candidate.poi_id == 1).member_ids == [1, 2, 3]
    candidates, _ = retrieve_with_report(UserProfile(must_visit=["地点2"]))
    assert 2 in {candidate.poi_id for candidate in candidates}


def test_requested_region_survives_continuous_experience_dedup(pool):
    pool.update({1: place(1, visit_group="lake", region_id="west"),
                 2: place(2, visit_group="lake", region_id="east", visit_role="viewpoint")})
    candidates, report = retrieve_with_report(UserProfile(preferred_regions=["east"]), k=1)
    assert [candidate.poi_id for candidate in candidates] == [2]
    assert report["region_coverage"][0]["status"] == "covered"


def test_missing_region_is_reported_without_guessing(pool):
    pool[1] = place(1, region_id="known")
    _, report = retrieve_with_report(UserProfile(preferred_regions=["未收录片区"]))
    assert report["region_coverage"][0]["status"] == "data_missing"


def test_real_aliases_removed_but_same_name_distant_places_survive(pool):
    pool.update({1: place(1, name="甲园"), 2: place(2, name="杭州甲园"), 3: place(3, name="甲园")})
    pool[3].lat += 0.01
    candidates, report = retrieve_with_report(UserProfile())
    assert {candidate.poi_id for candidate in candidates} == {1, 3}
    assert len(report["alias_duplicates"]) == 1


def test_trip_spanning_a_closed_day_retains_the_open_day(pool):
    pool[1] = place(1, opening_rules=OpeningRules(closed_weekdays=[0]))
    candidates, _ = retrieve_with_report(UserProfile(days=2, start_date="2026-10-19"))
    assert candidates[0].available_days == [2]


def test_seasonal_experience_lowers_score_without_closing_the_place(pool):
    pool[1] = place(1, experience_conditions=[ExperienceCondition(label="雪景", months=[12, 1, 2], weather_required=True, reference="project assumption")])
    summer = UserProfile(start_date="2026-08-01")
    candidates, _ = retrieve_with_report(summer)
    assert candidates[0].breakdown["seasonal_penalty"] > 0
    ordinary = pool[1].model_copy(update={"experience_conditions": []})
    assert score_poi(pool[1], summer).score < score_poi(ordinary, summer).score
    assert "普通游览" in safe_reason("欣赏残雪", pool[1], summer)
    assert "不承诺" in experience_notices(pool[1], UserProfile(start_date="2026-01-01"))[0]


def test_time_specific_experience_is_checked_against_final_slot():
    venue = place(1, experience_conditions=[ExperienceCondition(label="夜游", start_min=1080, reference="project assumption")])
    profile = UserProfile(day_start=540, day_end=1200)
    assert not experience_notices(venue, profile)
    assert experience_notices(venue, profile, 1, 600, 660)
    assert not experience_notices(venue, profile, 1, 1080, 1140)
    assert "普通游览" in safe_reason("夜景", venue, profile, 1, 600, 660)


def test_schedule_trace_counts_every_missing_selected_point(monkeypatch):
    from app.nodes.schedule import schedule_node
    monkeypatch.setattr("app.nodes.schedule.schedule", lambda *args, **kwargs: ([[1]], [], []))
    state = {"profile": UserProfile(days=1), "travel": None,
             "candidates": [Candidate(poi_id=identifier, score=1) for identifier in (1, 2)],
             "selected": [Selection(poi_id=identifier, reason="test") for identifier in (1, 2)]}
    attempt = schedule_node(state)["schedule_attempts"][0]
    assert attempt["dropped_ids"] == [2]
    assert attempt["drop_rate"] == 0.5


def test_opening_months_are_hard_but_explicit_open_date_wins(pool):
    pool[1] = place(1, opening_rules=OpeningRules(open_months=[6, 7, 8]))
    profile = UserProfile(days=1, start_date="2026-10-17")
    assert closed_on(pool[1], profile, 1)
    assert eligibility(pool[1], profile) == ([], "date_closed")
    pool[1].opening_rules.open_dates = [date(2026, 10, 17)]
    assert not closed_on(pool[1], profile, 1)


def test_final_validator_enforces_the_same_opening_months(monkeypatch):
    from app.nodes.validate import validate_itinerary
    venue = place(1, opening_rules=OpeningRules(open_months=[6, 7, 8]))
    monkeypatch.setattr("app.nodes.validate.load_pois", lambda: {1: venue})
    itinerary = Itinerary(days=[DayPlan(day=1, items=[Item(kind="poi", ref_id=1, name=venue.name, start=600, end=660)])])
    report = validate_itinerary(itinerary, UserProfile(days=1, start_date="2026-10-17"),
                                travel_fn=lambda *coordinates: (0, "原地", 0), restaurants={})
    assert any(issue.code == "date_closed" and issue.severity == "hard" for issue in report.violations)


def test_last_admission_is_checked_during_recall(pool):
    pool[1] = place(1, opening_rules=OpeningRules(last_entry=600))
    candidates, report = retrieve_with_report(UserProfile(days=1, day_start=840))
    assert not candidates
    assert report["excluded_counts"] == {"last_entry": 1}


def test_route_impossibility_is_distinguished_from_closed_hours(pool, monkeypatch):
    pool[1] = place(1)
    monkeypatch.setattr("app.nodes.retrieve.travel", lambda *coordinates: (70, "打车", 50))
    candidates, report = retrieve_with_report(UserProfile(days=1, day_start=840, day_end=960))
    assert not candidates
    assert report["excluded_counts"] == {"route_infeasible_estimate": 1}


def test_expansion_does_not_rewrite_initial_must_visit_recall(monkeypatch):
    places = {1: place(1)}
    monkeypatch.setattr("app.candidate_metrics.load_pois", lambda: places)
    state = {"profile": UserProfile(must_visit=["地点1"]), "candidates": [Candidate(poi_id=1, score=1)],
             "retrieval_report": {"candidate_ids": []}, "selected": [], "day_orders": [],
             "itinerary": Itinerary(), "report": ValidationReport(passed=False)}
    metrics = candidate_metrics(state)
    assert metrics["must_visit_recall"]["rate"] == 0
    assert metrics["expanded_candidate_ids"] == [1]


@pytest.mark.parametrize("months", [[0], [13]])
def test_invalid_open_months_are_rejected(months):
    with pytest.raises(ValueError):
        OpeningRules(open_months=months)


def test_metrics_distinguish_not_selected_scheduler_drop_and_delivery_drop(monkeypatch):
    places = {identifier: place(identifier) for identifier in range(1, 5)}
    monkeypatch.setattr("app.candidate_metrics.load_pois", lambda: places)
    state = {"profile": UserProfile(must_visit=["地点1", "地点3"], interests=["自然风光"]),
             "candidates": [Candidate(poi_id=identifier, score=1) for identifier in places],
             "selected": [Selection(poi_id=identifier, reason="test") for identifier in (1, 2, 3)],
             "day_orders": [[1, 2]], "itinerary": Itinerary(days=[DayPlan(day=1, items=[Item(kind="poi", ref_id=1, name="地点1", start=600, end=660)])]),
             "report": ValidationReport(passed=True)}
    metrics = candidate_metrics(state)
    assert metrics["selected_to_scheduler_drop"]["rate"] == 1 / 3
    assert metrics["selected_to_delivery_drop"]["rate"] == 2 / 3
    assert metrics["candidate_to_delivery_unused"]["rate"] == 3 / 4
    assert metrics["must_visit_delivered"]["rate"] == 0.5


@needs_data
def test_loaded_semantics_retain_field_sources_and_whole_experience_duration():
    places = load_pois()
    assert all(place.field_evidence.get("audience") and place.field_evidence.get("duration") and place.region_id for place in places.values())
    assert places[1].experience_kind == "full_day" and places[1].onsite_dining and places[1].min_hours >= 6
    assert places[192].experience_kind == "session" and places[192].min_hours == 0.5
    assert places[36].experience_conditions and places[36].recommendable
    assert places[36].field_evidence["duration"].basis == "curator_inference"
    assert places[0].field_evidence["opening_rules"].basis == "observed"
