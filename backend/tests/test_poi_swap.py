from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from app import api, candidate_metrics, meal_edits  # noqa: F401  先导入：夹具打补丁时才首次导入的话，还原后会留下假数据
from app.graph import run_plan
from app.nodes.validate import validate_itinerary
from app.replan import ReplanError, poi_options, swap_meal, swap_poi
from app.schemas import Candidate, DayPlan, Item, Itinerary, POI, Restaurant, Selection, UserProfile
from app.sessions import SessionStore
from app.travel import Travel
from tests.conftest import needs_data


def poi(identifier, name, lat, lon, kind="公园", **extra):
    return POI(id=identifier, name=name, type=kind, lat=lat, lon=lon, open_min=480, close_min=1200,
               price=0, min_hours=1, max_hours=1, price_verified=True, **extra)


def meal(identifier, name, lat, lon, price=50):
    return Restaurant(id=identifier, name=name, lat=lat, lon=lon, price=price, cuisine="杭帮菜", open_min=600, close_min=1200)


@pytest.fixture
def case(monkeypatch):
    places = {101: poi(101, "上午公园", 30.25, 120.16), 102: poi(102, "下午展馆", 30.251, 120.161, "博物馆/纪念馆", indoor=True),
              103: poi(103, "附近公园", 30.252, 120.162), 104: poi(104, "附近美术馆", 30.2525, 120.1625, "美术馆/艺术馆", indoor=True),
              105: poi(105, "远处公园", 30.40, 120.40), 106: poi(106, "第二天公园", 30.26, 120.17),
              107: poi(107, "第二天展馆", 30.261, 120.171, "博物馆/纪念馆"),
              108: poi(108, "整日乐园", 30.253, 120.163, "游乐园/体育娱乐", day_anchor=True),
              109: poi(109, "只开周三", 30.2515, 120.1615, recommendable=False),
              110: poi(110, "只开上午", 30.2512, 120.1612).model_copy(update={"close_min": 720})}
    restaurants = {201: meal(201, "第一天午餐", 30.2505, 120.1605), 202: meal(202, "备选餐厅", 30.2507, 120.1607, 70),
                   203: meal(203, "第二天午餐", 30.2522, 120.1622)}  # 203 离 103 最近，但第二天已经吃过
    for module in ("app.data", "app.api", "app.replan", "app.nodes.meals", "app.nodes.schedule", "app.nodes.validate",
                   "app.candidate_metrics", "app.meal_edits", "app.bundles"):
        monkeypatch.setattr(module + ".load_pois", lambda: places)
    for module in ("app.data", "app.api", "app.nodes.meals", "app.nodes.validate"):
        monkeypatch.setattr(module + ".load_restaurants", lambda: restaurants)
    profile = UserProfile(days=2, day_start=540, day_end=1020, include_evening=False,
                          hotel={"name": "公园门口", "lat": 30.25, "lon": 120.16}, budget=1500)
    itinerary = Itinerary(days=[
        DayPlan(day=1, items=[Item(kind="poi", ref_id=101, name="上午公园", start=570, end=630),
                              Item(kind="meal", ref_id=201, name="第一天午餐", start=700, end=760, cost=50, meal="lunch"),
                              Item(kind="poi", ref_id=102, name="下午展馆", start=780, end=840)]),
        DayPlan(day=2, items=[Item(kind="poi", ref_id=106, name="第二天公园", start=600, end=660),
                              Item(kind="meal", ref_id=203, name="第二天午餐", start=720, end=780, cost=50, meal="lunch"),
                              Item(kind="poi", ref_id=107, name="第二天展馆", start=810, end=870)])])
    route = Travel("estimate")
    route.finalize(itinerary, profile, restaurants)
    candidates = [Candidate(poi_id=identifier, score=score) for identifier, score in
                  ((101, 5), (102, 5), (103, 3), (104, 4), (105, 9), (106, 5), (107, 5), (108, 8), (109, 8), (110, 1))]
    result = {"profile": profile, "itinerary": itinerary, "restaurants": restaurants,
              "report": validate_itinerary(itinerary, profile, route.leg, restaurants),
              "selected": [Selection(poi_id=identifier, reason="测试") for identifier in (101, 102, 106, 107)],
              "candidates": candidates, "day_orders": [[101, 102], [106, 107]],
              "travel_stats": {"source": "estimate"}, "usage": {}, "log": [], "seconds": 0, "dropped": []}
    assert result["report"].passed, result["report"].hard
    store = SessionStore()
    monkeypatch.setattr(api, "SESSIONS", store)
    session = store.create(result, {"select_mode": "rule", "enable_repair": True, "guide_mode": "off"})
    return TestClient(api.app), store, session, result, places


def preview(client, session, **overrides):
    return client.post(f"/api/plans/{session.plan_id}/poi-preview",
                       json={"revision": 0, "day": 1, "item_index": 0, "poi_id": 103, **overrides})


def test_preview_is_read_only_and_says_what_changes(case):
    client, _, session, original, _ = case
    before = deepcopy(original)
    response = preview(client, session)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_commit"]
    change = body["change"]
    assert (change["from"], change["to"], change["action"]) == ("上午公园", "附近公园", "replace_poi")
    assert change["removed"] == []  # 用户主动换掉的不算“被移出”
    assert all(stop["name"] != "上午公园" for stop in change["affected_stops"])
    assert body["plan"]["meta"]["operation"] == "poi_swap"
    assert session.current == 0 and len(session.revisions) == 1
    assert session.result() == before


def test_commit_then_revert_restores_the_original(case):
    client, _, session, original, _ = case
    response = preview(client, session)
    committed = client.post(f"/api/plans/{session.plan_id}/poi-commit", json={"revision": 0, "preview_id": response.json()["preview_id"]})
    assert committed.status_code == 200, committed.text
    assert committed.json()["revision"] == 1
    assert [item.ref_id for item in session.result().get("itinerary").days[0].items if item.kind == "poi"] == [103, 102]
    assert {s.poi_id: s.source for s in session.result()["selected"]}[103] == "user"
    assert 101 not in {s.poi_id for s in session.result()["selected"]}
    assert session.result()["usage"]["total_tokens"] == 0
    assert client.post(f"/api/plans/{session.plan_id}/poi-commit", json={"revision": 0, "preview_id": response.json()["preview_id"]}).status_code == 409
    reverted = client.post(f"/api/plans/{session.plan_id}/revert", json={"revision": 0, "base_revision": 1})
    assert reverted.status_code == 200, reverted.text
    assert session.result()["itinerary"] == original["itinerary"]


def test_other_days_stay_exactly_the_same(case):
    _, _, _, original, _ = case
    day_two = original["itinerary"].days[1].model_dump()
    updated, _ = swap_poi(deepcopy(original), 1, 0, 103)
    assert updated["itinerary"].days[1].model_dump() == day_two


@pytest.mark.parametrize(("overrides", "message"), [
    ({"poi_id": 999}, "候选"),
    ({"poi_id": 102}, "已经在行程"),
    ({"poi_id": 106}, "已经在行程"),
    ({"poi_id": 101}, "已经是"),
    ({"poi_id": 109}, "不推荐"),
    ({"poi_id": 108}, "一整天"),
    ({"item_index": 1}, "只能更换景点"),
    ({"item_index": 9}, "找不到"),
])
def test_rejected_swaps_leave_the_plan_alone(case, overrides, message):
    client, _, session, original, _ = case
    before = deepcopy(original)
    response = preview(client, session, **overrides)
    assert response.status_code == 422 and message in response.json()["detail"]
    assert session.result() == before


def test_must_visit_cannot_be_swapped_away(case):
    _, _, _, original, _ = case
    original["candidates"][0].breakdown["must_visit"] = 1
    with pytest.raises(ReplanError, match="点名"):
        swap_poi(original, 1, 0, 103)


def test_closed_day_is_rejected_and_not_offered(case):
    _, _, _, original, _ = case
    next(c for c in original["candidates"] if c.poi_id == 103).available_days = [2]
    with pytest.raises(ReplanError, match="不开放"):
        swap_poi(original, 1, 0, 103)
    assert 103 not in [option["poi_id"] for option in poi_options(original, 1, 0)]


def test_evening_stop_cannot_be_swapped(case):
    _, _, _, original, _ = case
    original["itinerary"].days[0].items[0].evening = True
    with pytest.raises(ReplanError, match="夜游"):
        swap_poi(original, 1, 0, 103)


def test_options_prefer_same_area_and_type_and_skip_planned(case):
    client, _, session, original, _ = case
    response = client.get(f"/api/plans/{session.plan_id}/poi-options", params={"day": 1, "item_index": 0})
    assert response.status_code == 200, response.text
    options = response.json()["options"]
    ids = [option["poi_id"] for option in options]
    assert not {101, 102, 106, 107, 108, 109} & set(ids)
    assert ids.index(103) < ids.index(104) < ids.index(105)  # 同片区同类型 > 同片区 > 其他，得分再高也靠后
    assert next(option for option in options if option["poi_id"] == 104)["indoor"] is True
    assert client.get(f"/api/plans/{session.plan_id}/poi-options", params={"day": 1, "item_index": 1}).status_code == 422


def test_meals_follow_the_new_route_but_keep_user_choices(case):
    _, _, _, original, _ = case
    updated, _ = swap_poi(deepcopy(original), 1, 0, 103)
    lunch = next(item for item in updated["itinerary"].days[0].items if item.meal == "lunch")
    assert lunch.ref_id != 203  # 第二天吃过的不再选，即使离新景点最近
    original["user_meals"] = {(1, "lunch")}
    original["itinerary"].days[0].items[1].ref_id = 202
    original["itinerary"].days[0].items[1].name = "备选餐厅"
    pinned, _ = swap_poi(deepcopy(original), 1, 0, 103)
    assert next(item for item in pinned["itinerary"].days[0].items if item.meal == "lunch").ref_id == 202
    assert (1, "lunch") in pinned.get("user_meals", set())


def test_new_poi_is_kept_when_the_day_has_to_shrink(case):
    client, _, session, original, _ = case
    response = preview(client, session, poi_id=105)  # 远处公园，路上很久
    assert response.status_code == 200, response.text
    body = response.json()
    names = [item["name"] for item in body["plan"]["days"][0]["items"] if item["kind"] == "poi"]
    assert "远处公园" in names
    if not body["can_commit"]:
        assert body["change"]["conflicts"]
        refused = client.post(f"/api/plans/{session.plan_id}/poi-commit", json={"revision": 0, "preview_id": body["preview_id"]})
        assert refused.status_code == 422
        assert session.result()["itinerary"] == original["itinerary"]


def test_poi_open_only_in_the_morning_moves_earlier_instead_of_being_dropped(case):
    client, store, session, _, places = case
    places[101] = places[101].model_copy(update={"min_hours": 3, "max_hours": 3})  # 上午那站要到 12:30，后面放不下
    response = preview(client, session, item_index=2, poi_id=110)  # 下午那一站换成只开到 12:00 的
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_commit"], body["change"]["conflicts"]
    assert body["change"]["reordered"]
    stop = next(item for item in store.get_preview(session.plan_id, 0, body["preview_id"]).result["itinerary"].days[0].items if item.ref_id == 110)
    assert stop.end <= 720


def _taxi_on_day_two(original):
    """第二天第一段是修复环节改的打车，行程里记着；新的 Travel 不知道。"""
    profile, itinerary, restaurants = original["profile"], original["itinerary"], original["restaurants"]
    route = Travel("estimate")
    first = itinerary.days[1].items[0]
    from app.data import load_pois
    place = load_pois()[first.ref_id]
    route.taxi_overrides.add(route.pair(profile.hotel.lat, profile.hotel.lon, place.lat, place.lon))
    route.finalize(itinerary, profile, restaurants)
    first.travel_mode = first.travel_evidence.mode
    first.travel_min = first.travel_evidence.minutes
    assert first.travel_mode == "打车"
    original["report"] = validate_itinerary(itinerary, profile, route.leg, restaurants)
    assert original["report"].passed, original["report"].hard
    return first.travel_evidence


@pytest.mark.parametrize("swap", ["poi", "meal"])
def test_taxi_on_another_day_survives_and_can_commit(case, swap):
    client, store, session, original, _ = case
    evidence = _taxi_on_day_two(original)
    response = (preview(client, session) if swap == "poi" else
                client.post(f"/api/plans/{session.plan_id}/meal-preview", json={"revision": 0, "day": 1, "item_index": 1, "restaurant_id": 202}))
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_commit"], body["change"]["conflicts"]
    kept = store.get_preview(session.plan_id, 0, body["preview_id"]).result["itinerary"].days[1].items[0]
    assert kept.travel_evidence == evidence
    endpoint = "poi-commit" if swap == "poi" else "meal-commit"
    committed = client.post(f"/api/plans/{session.plan_id}/{endpoint}", json={"revision": 0, "preview_id": body["preview_id"]})
    assert committed.status_code == 200, committed.text


@needs_data
@pytest.mark.parametrize("day", [1, 2])
def test_real_plan_swaps_one_stop_end_to_end(mock_llm, monkeypatch, day):
    result = run_plan("", {"days": 2, "interests": ["人文历史"], "budget": 3000, "pace": "normal"}, llm=mock_llm, select_mode="rule")
    store = SessionStore()
    monkeypatch.setattr(api, "SESSIONS", store)
    session = store.create(result, {"select_mode": "rule", "enable_repair": True, "guide_mode": "off"})
    client = TestClient(api.app)
    index = next(i for i, item in enumerate(result["itinerary"].days[day - 1].items) if item.kind == "poi" and not item.evening)
    listed = client.get(f"/api/plans/{session.plan_id}/poi-options", params={"day": day, "item_index": index})
    assert listed.status_code == 200, listed.text
    options = listed.json()["options"]
    assert options
    other = result["itinerary"].days[2 - day].model_dump()
    body = client.post(f"/api/plans/{session.plan_id}/poi-preview",
                       json={"revision": 0, "day": day, "item_index": index, "poi_id": options[0]["poi_id"]}).json()
    assert body["change"]["to"] == options[0]["name"], body
    assert body["can_commit"], body["change"]["conflicts"]
    committed = client.post(f"/api/plans/{session.plan_id}/poi-commit", json={"revision": 0, "preview_id": body["preview_id"]})
    assert committed.status_code == 200, committed.text
    assert session.result()["itinerary"].days[2 - day].model_dump() == other
    assert options[0]["poi_id"] in [item.ref_id for item in session.result()["itinerary"].days[day - 1].items]


def test_swap_meal_still_rejects_non_meals(case):
    _, _, _, original, _ = case
    with pytest.raises(ReplanError, match="只能更换正餐"):
        swap_meal(original, 1, 0, 202)
