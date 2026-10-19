from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
import pytest

from app import api
from app.meal_edits import preserve_self_arranged
from app.nodes.validate import validate_itinerary
from app.schemas import Candidate, DayPlan, Item, Itinerary, MealAlternative, MealEditPreviewRequest, POI, ReservedMeal, Restaurant, Selection, UserProfile, ValidationReport, Violation
from app.sessions import MAX_PREVIEWS, PREVIEW_TTL_SECONDS, PreviewNotFound, SessionStore, StaleRevision
from app.travel import Travel


@pytest.fixture
def edit_case(monkeypatch):
    places = {101: POI(id=101, name="上午公园", type="公园", lat=30.25, lon=120.16, open_min=480, close_min=1200,
                     price=0, min_hours=2, max_hours=2, price_verified=True),
              102: POI(id=102, name="下午展馆", type="博物馆/纪念馆", lat=30.251, lon=120.161, open_min=480, close_min=1200,
                     price=0, min_hours=1, max_hours=1, price_verified=True)}
    restaurants = {201: Restaurant(id=201, name="原来的午餐", lat=30.2505, lon=120.1605, price=50,
                                  cuisine="杭帮菜", open_min=600, close_min=1200),
                   202: Restaurant(id=202, name="备选餐厅", lat=30.2507, lon=120.1607, price=70,
                                   cuisine="杭帮菜", open_min=600, close_min=1200)}
    for module in ("app.data", "app.api", "app.replan", "app.nodes.meals", "app.nodes.schedule", "app.nodes.validate", "app.candidate_metrics", "app.meal_edits"):
        monkeypatch.setattr(module + ".load_pois", lambda: places)
    for module in ("app.data", "app.api", "app.nodes.meals", "app.nodes.validate"):
        monkeypatch.setattr(module + ".load_restaurants", lambda: restaurants)
    profile = UserProfile(days=1, day_start=540, day_end=1020, include_evening=False,
                          hotel={"name": "公园门口", "lat": 30.25, "lon": 120.16}, budget=1500)
    itinerary = Itinerary(days=[DayPlan(day=1, items=[
        Item(kind="poi", ref_id=101, name=places[101].name, start=570, end=690),
        Item(kind="meal", ref_id=201, name=restaurants[201].name, start=700, end=760, cost=50, meal="lunch",
             alternatives=[MealAlternative(ref_id=202, name=restaurants[202].name, cuisine="杭帮菜", price=70,
                            detour_min=0, travel_source="estimate", provider="chinatravel", start=700, end=760)]),
        Item(kind="poi", ref_id=102, name=places[102].name, start=780, end=840)])])
    route = Travel("estimate")
    route.finalize(itinerary, profile, restaurants)
    result = {"profile": profile, "itinerary": itinerary, "restaurants": restaurants,
              "report": validate_itinerary(itinerary, profile, route.leg, restaurants),
              "selected": [Selection(poi_id=identifier, reason="测试") for identifier in places],
              "candidates": [Candidate(poi_id=identifier, score=1) for identifier in places],
              "day_orders": [[101, 102]], "travel_stats": {"source": "estimate"}, "usage": {}, "log": [], "seconds": 0}
    assert result["report"].passed
    store = SessionStore()
    monkeypatch.setattr(api, "SESSIONS", store)
    session = store.create(result, {"select_mode": "rule", "enable_repair": True, "guide_mode": "off"})
    client = TestClient(api.app)
    return client, store, session, result, places, restaurants


def preview(client, session, **overrides):
    return client.post(f"/api/plans/{session.plan_id}/meal-preview",
                       json={"revision": 0, "day": 1, "item_index": 1, "restaurant_id": 202, **overrides})


def commit(client, session, response, revision=0):
    return client.post(f"/api/plans/{session.plan_id}/meal-commit", json={"revision": revision, "preview_id": response.json()["preview_id"]})


def test_preview_is_read_only_and_shows_actual_changes(edit_case):
    client, store, session, original, _, _ = edit_case
    before = deepcopy(original)
    response = preview(client, session)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_commit"]
    assert body["change"]["budget_delta"] == 20
    assert body["change"]["affected_stops"]
    assert body["change"]["route_source"] == "estimate"
    assert session.current == 0 and len(session.revisions) == 1
    assert session.result() == before
    assert store.get_preview(session.plan_id, 0, body["preview_id"]).result["usage"]["total_tokens"] == 0


def test_confirm_commits_the_exact_preview_and_can_revert(edit_case):
    client, _, session, original, _, _ = edit_case
    response = preview(client, session)
    committed = commit(client, session, response)
    assert committed.status_code == 200, committed.text
    assert committed.json()["revision"] == 1
    assert committed.json()["days"] == response.json()["plan"]["days"]
    assert len(session.revisions) == 2
    assert session.result(0) == original
    assert commit(client, session, response).status_code == 409
    reverted = client.post(f"/api/plans/{session.plan_id}/revert", json={"revision": 0, "base_revision": 1})
    assert reverted.status_code == 200 and reverted.json()["revision"] == 2
    assert session.result()["itinerary"] == original["itinerary"]


def test_impossible_preview_cannot_replace_the_current_version(edit_case):
    client, _, session, original, _, restaurants = edit_case
    restaurants[202].price = 5000
    response = preview(client, session)
    assert response.status_code == 200, response.text
    assert not response.json()["can_commit"]
    assert commit(client, session, response).status_code == 422
    assert session.current == 0 and session.result() == original


def test_commit_revalidates_using_frozen_routes_and_current_constraints(edit_case):
    client, _, session, _, places, _ = edit_case
    response = preview(client, session)
    places[101].open_min = 1100
    result = commit(client, session, response)
    assert result.status_code == 422
    assert session.current == 0


def test_commit_does_not_query_navigation_again(edit_case, monkeypatch):
    client, _, session, _, _, _ = edit_case
    response = preview(client, session)
    monkeypatch.setattr("app.replan.make_travel", lambda *args: pytest.fail("commit queried a new route"))
    assert commit(client, session, response).status_code == 200


def test_changed_cost_requires_a_new_preview(edit_case):
    client, _, session, original, places, _ = edit_case
    response = preview(client, session)
    places[101].price = 20
    result = commit(client, session, response)
    assert result.status_code == 422
    assert "费用依据已变化" in result.json()["detail"]
    assert session.current == 0 and session.result() == original


@pytest.mark.parametrize("identifier", [101, 102])
def test_changed_coordinates_require_a_new_preview(edit_case, identifier):
    client, _, session, original, places, _ = edit_case
    response = preview(client, session)
    places[identifier].lat += 0.001
    result = commit(client, session, response)
    assert result.status_code == 422
    assert "地点位置已变化" in result.json()["detail"]
    assert session.current == 0 and session.result() == original


def test_missing_coordinate_snapshot_prevents_commit(edit_case):
    client, store, session, _, _, _ = edit_case
    response = preview(client, session)
    del store.get(session.plan_id).previews[response.json()["preview_id"]].result["meal_preview_locations"]
    assert commit(client, session, response).status_code == 422
    assert session.current == 0


def test_preview_does_not_save_account_history(edit_case, monkeypatch):
    client, _, session, _, _, _ = edit_case
    monkeypatch.setattr(api, "deliver_plan", lambda *args: pytest.fail("preview wrote account history"))
    assert preview(client, session).status_code == 200


def test_preview_storage_is_bounded(edit_case):
    _, store, session, original, _, _ = edit_case
    entries = [store.add_preview(session.plan_id, 0, original, {}) for _ in range(MAX_PREVIEWS + 1)]
    assert len(session.previews) == MAX_PREVIEWS
    with pytest.raises(PreviewNotFound):
        store.get_preview(session.plan_id, 0, entries[0].preview_id)


def test_rebuild_preserves_explicit_self_arranged_slots(edit_case):
    _, _, _, original, _, _ = edit_case
    original["itinerary"].days[0].reserved_meals = [ReservedMeal(meal="dinner", start=1020, end=1080,
        inside_poi_id=101, estimated_cost=65, reason="用户确认", status="self_arranged")]
    rebuilt = deepcopy(original["itinerary"])
    slot = rebuilt.days[0].reserved_meals[0]
    slot.status, slot.estimated_cost, slot.reason = "pending", 80, "待确认"
    preserve_self_arranged(original, rebuilt)
    assert (slot.status, slot.estimated_cost, slot.reason) == ("self_arranged", 65, "用户确认")


def test_stale_base_is_rejected_even_after_revert(edit_case):
    client, store, session, original, _, _ = edit_case
    response = preview(client, session)
    store.append(session.plan_id, 0, deepcopy(original))
    store.revert(session.plan_id, 0, 1)
    assert commit(client, session, response).status_code == 409
    assert session.current == 2


def test_expired_preview_preserves_the_plan(edit_case, monkeypatch):
    import app.sessions as sessions
    client, _, session, _, _, _ = edit_case
    response = preview(client, session)
    now = sessions.time.time()
    monkeypatch.setattr(sessions.time, "time", lambda: now + PREVIEW_TTL_SECONDS + 1)
    assert commit(client, session, response).status_code == 410
    assert session.current == 0


def test_preview_from_another_plan_cannot_be_used(edit_case):
    client, store, session, original, _, _ = edit_case
    response = preview(client, session)
    other = store.create(deepcopy(original), session.request)
    assert commit(client, other, response).status_code == 410
    assert other.current == 0


def test_another_owner_cannot_preview_commit_or_read(edit_case):
    client, _, session, _, _, _ = edit_case
    response = preview(client, session)
    session.owner = "user:not-the-caller"
    assert preview(client, session).status_code == 404
    assert commit(client, session, response).status_code == 404
    assert client.get(f"/api/plans/{session.plan_id}").status_code == 404


def test_the_old_endpoint_cannot_bypass_confirmation(edit_case):
    client, _, session, _, _, _ = edit_case
    response = client.post(f"/api/plans/{session.plan_id}/swap-meal", json={"revision": 0, "day": 1, "item_index": 1, "restaurant_id": 202})
    assert response.status_code == 428 and session.current == 0


def test_client_cannot_submit_a_different_restaurant_or_plan(edit_case):
    client, _, session, _, _, _ = edit_case
    response = preview(client, session)
    result = client.post(f"/api/plans/{session.plan_id}/meal-commit", json={"revision": 0,
        "preview_id": response.json()["preview_id"], "restaurant_id": 999})
    assert result.status_code == 422 and session.current == 0


def test_only_one_concurrent_commit_can_win(edit_case):
    _, store, session, original, _, _ = edit_case
    report = ValidationReport(passed=True)
    entry = store.add_preview(session.plan_id, 0, original, {})
    def attempt():
        try:
            return store.commit_preview(session.plan_id, 0, entry.preview_id, report)[2]
        except StaleRevision:
            return "stale"
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(lambda _: attempt(), range(2)))
    assert sorted(outcomes, key=str) == [1, "stale"]
    assert session.current == 1 and len(session.revisions) == 2


def test_version_change_during_commit_validation_is_rejected(edit_case, monkeypatch):
    client, store, session, original, _, _ = edit_case
    response = preview(client, session)
    from app import meal_edits
    validate = meal_edits.validate_preview
    def interrupted(candidate):
        report = validate(candidate)
        store.append(session.plan_id, 0, deepcopy(original))
        return report
    monkeypatch.setattr(meal_edits, "validate_preview", interrupted)
    assert commit(client, session, response).status_code == 409
    assert session.current == 1 and len(session.revisions) == 2


def test_missing_route_evidence_prevents_commit(edit_case):
    client, store, session, _, _, _ = edit_case
    response = preview(client, session)
    store.get(session.plan_id).previews[response.json()["preview_id"]].result["itinerary"].days[0].return_leg = None
    assert commit(client, session, response).status_code == 422
    assert session.current == 0


def test_store_returns_independent_preview_copies(edit_case):
    _, store, session, original, _, _ = edit_case
    entry = store.add_preview(session.plan_id, 0, original, {})
    entry.result["itinerary"].days.clear()
    assert store.get_preview(session.plan_id, 0, entry.preview_id).result["itinerary"].days


@pytest.mark.parametrize("coordinates", [(float("nan"), 120.16), (30.25, float("inf")), (0, 0)])
def test_invalid_restaurant_coordinates_are_rejected(edit_case, coordinates):
    client, _, session, _, _, restaurants = edit_case
    restaurants[202].lat, restaurants[202].lon = coordinates
    assert preview(client, session).status_code == 422
    assert session.current == 0


def test_explicit_self_arranged_meal_is_not_counted_as_a_restaurant(edit_case):
    client, _, session, original, places, _ = edit_case
    day = original["itinerary"].days[0]
    day.items = [day.items[0]]
    day.items[0].end = 900
    places[101].onsite_dining = True
    day.reserved_meals = [ReservedMeal(meal="lunch", start=700, end=760, inside_poi_id=101, estimated_cost=80, reason="园内用餐待确认")]
    route = Travel("estimate")
    route.finalize(original["itinerary"], original["profile"], original["restaurants"])
    original["report"] = validate_itinerary(original["itinerary"], original["profile"], route.leg, original["restaurants"])
    response = client.post(f"/api/plans/{session.plan_id}/meal-preview", json={"revision": 0, "day": 1,
        "action": "self_arranged", "meal": "lunch", "estimated_cost": 65})
    assert response.status_code == 200, response.text
    assert response.json()["can_commit"]
    result = commit(client, session, response)
    assert result.status_code == 200
    body = result.json()
    assert body["report"]["arrangement_status"] == "complete"
    assert body["report"]["totals"]["covered_meals"] == 0
    assert body["report"]["totals"]["self_arranged_meals"] == 1
    assert body["meta"]["meals"]["restaurant_meals"] == 0
    assert body["days"][0]["reserved_meals"][0]["estimated_cost"] == 65
    assert original["itinerary"].days[0].reserved_meals[0].status == "pending"


def test_self_arranged_confirmation_requires_an_explicit_budget():
    with pytest.raises(ValueError):
        MealEditPreviewRequest(revision=0, day=1, action="self_arranged", meal="lunch")
