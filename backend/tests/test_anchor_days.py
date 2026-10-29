import pytest

from app.data import load_pois
from app.evening import add_evening_visits
from app.nodes.meals import build_itinerary
from app.nodes.repair import repair
from app.nodes.retrieve import retrieve
from app.nodes.schedule import fit_service_min, schedule, service_min, split_days
from app.nodes.select import complete_experiences
from app.nodes.validate import validate_itinerary
from app.schemas import Candidate, DayPlan, Item, Itinerary, ReservedMeal, Selection, UserProfile
from app.travel import Travel
from tests.conftest import needs_data


pytestmark = needs_data


def test_songcheng_duration_is_whole_park_not_one_show():
    place = load_pois()[1]
    for pace in ("relaxed", "normal", "intense"):
        assert service_min(place, pace) >= 360
        assert fit_service_min(place, pace, 900, 1080) >= 360
    assert "非" in load_pois()[28].duration_source


def test_major_park_gets_its_own_day_without_balancing_stop_counts():
    groups = split_days([1, 117, 20, 91, 36], UserProfile(days=2))
    assert [1] in groups
    assert set(identifier for group in groups for identifier in group) == {1, 117, 20, 91, 36}
    assert split_days([1, 28, 20, 91], UserProfile(days=2)) == [[1], [28]]


def test_single_day_drops_optional_detours_but_preserves_explicit_must_visit():
    profile = UserProfile(days=1, pace="intense", must_visit=["宋城"])
    orders, dropped, _ = schedule([1, 117, 20], profile, {}, Travel("estimate", "transit"))
    assert orders == [[1]]
    assert set(dropped) == {117, 20}
    profile.must_visit.append("清河坊街")
    assert split_days([1, 20, 117], profile) == [[1, 20]]


def test_childrens_park_is_not_an_adult_theme_park_default():
    profile = UserProfile(days=2, interests=["主题乐园"])
    assert 117 not in {candidate.poi_id for candidate in retrieve(profile, k=500)}
    profile.companions = "family_kids"
    profile.interests = ["亲子游乐"]
    assert 117 in {candidate.poi_id for candidate in retrieve(profile, k=500)}
    profile.companions = "solo"
    profile.must_visit = ["少年儿童公园"]
    assert 117 in {candidate.poi_id for candidate in retrieve(profile, k=500)}


def test_theme_park_request_does_not_fall_back_to_temples_or_stadiums():
    candidates = retrieve(UserProfile(days=2, interests=["主题乐园"]), k=500)
    assert candidates
    assert not {146, 253, 117} & {candidate.poi_id for candidate in candidates}
    assert all("主题乐园" in load_pois()[candidate.poi_id].interests for candidate in candidates)


def test_stop_completion_does_not_fill_a_park_day_with_extra_attractions():
    selected = [Selection(poi_id=1, source="rule", reason="主项目")]
    candidates = [Candidate(poi_id=identifier, score=1, breakdown={}) for identifier in (1, 20, 91)]
    assert complete_experiences(selected, candidates, UserProfile(days=1, pace="intense")) == selected


def test_onsite_lunch_stays_within_visit_and_counts_only_as_budget_reservation(monkeypatch):
    monkeypatch.setattr("app.nodes.meals.load_restaurants", lambda: {})
    profile = UserProfile(days=1, pace="intense", day_start=540, day_end=1050)
    itinerary = build_itinerary([[1]], profile, [], live=False)
    day = itinerary.days[0]
    visit = day.items[0]
    lunch = next(slot for slot in day.reserved_meals if slot.meal == "lunch")
    assert len(day.items) == 1 and visit.ref_id == 1
    assert lunch.inside_poi_id == 1
    assert visit.start <= lunch.start < lunch.end <= visit.end
    assert visit.end - visit.start == 420
    report = validate_itinerary(itinerary, profile, restaurants={})
    codes = {violation.code for violation in report.violations}
    assert "missing_lunch" not in codes
    assert "onsite_meal_unconfirmed" in codes
    assert report.totals["meals"] == lunch.estimated_cost > 0
    assert report.totals["covered_meals"] == 0
    assert report.experience_status == "needs_attention"


def test_fake_onsite_slot_cannot_hide_missing_meal():
    itinerary = Itinerary(days=[DayPlan(day=1, items=[Item(kind="poi", ref_id=1, name="杭州宋城", start=570, end=930)],
        reserved_meals=[ReservedMeal(meal="lunch", start=950, end=1010, inside_poi_id=1, estimated_cost=80, reason="无效时段")])])
    report = validate_itinerary(itinerary, UserProfile(days=1), restaurants={})
    assert "missing_lunch" in {violation.code for violation in report.violations}
    assert report.totals["meals"] == 0


def test_park_day_does_not_get_external_night_stop():
    itinerary = Itinerary(days=[DayPlan(day=1, items=[Item(kind="poi", ref_id=1, name="杭州宋城", start=570, end=990)])])
    updated, additions = add_evening_visits(itinerary, UserProfile(days=1, include_evening=True))
    assert not additions
    assert len(updated.days[0].items) == 1


def test_transport_quality_uses_recomputed_legs_and_repairs_optional_detour():
    profile = UserProfile(days=1, day_start=480, day_end=1380, must_visit=["清河坊街"])
    itinerary = Itinerary(days=[DayPlan(day=1, items=[
        Item(kind="poi", ref_id=20, name="清河坊街", start=600, end=720),
        Item(kind="poi", ref_id=117, name="少年儿童公园", start=800, end=920),
    ])])
    report = validate_itinerary(itinerary, profile, travel_fn=lambda *coords: (80, "公交/地铁", 2), restaurants={})
    assert "interstop_ride_limit" in {violation.code for violation in report.violations}
    assert "excessive_transport" not in {violation.code for violation in report.violations}
    assert report.experience_status == "needs_attention"
    state = {"profile":profile, "report":report, "itinerary":itinerary,
             "selected":[Selection(poi_id=identifier, source="rule", reason="") for identifier in (20, 117)],
             "candidates":[Candidate(poi_id=20, score=1, breakdown={"must_visit":1}), Candidate(poi_id=117, score=0.5, breakdown={})]}
    selected, _, _, changed = repair(state)
    assert changed and {selection.poi_id for selection in selected} == {20}


def test_explicit_far_must_visits_are_not_silently_removed():
    profile = UserProfile(days=1, must_visit=["宋城", "清河坊街"])
    itinerary = Itinerary(days=[DayPlan(day=1, items=[Item(kind="poi", ref_id=1, name="杭州宋城", start=570, end=930),
        Item(kind="poi", ref_id=20, name="清河坊街", start=1030, end=1120)])])
    report = validate_itinerary(itinerary, profile, travel_fn=lambda *coords: (80, "公交/地铁", 2), restaurants={})
    state = {"profile":profile, "report":report, "itinerary":itinerary,
        "selected":[Selection(poi_id=identifier, source="rule", reason="必去") for identifier in (1, 20)],
        "candidates":[Candidate(poi_id=identifier, score=1, breakdown={"must_visit":1}) for identifier in (1, 20)]}
    selected, _, _, _ = repair(state)
    assert {selection.poi_id for selection in selected} == {1, 20}
    assert {violation.code for violation in report.violations} >= {"anchor_mixed", "interstop_ride_limit"}
