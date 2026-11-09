from copy import deepcopy

from fastapi.testclient import TestClient
import pytest

from app import api, replan
from app.graph import run_plan
from app.meal_edits import describe_change
from app.replan import MAX_PACE_TRIES, ReplanError, _addable, _edit_context, _rebuild_day, adjust_pace
from app.schemas import Item, Selection, Violation
from app.sessions import SessionStore
from tests.conftest import needs_data
from tests.test_poi_swap import _taxi_on_day_two, case  # noqa: F401


def pace(client, session, **overrides):
    return client.post(f"/api/plans/{session.plan_id}/pace-preview", json={"revision": 0, "day": 1, "direction": "less", **overrides})


def commit(client, session, body):
    return client.post(f"/api/plans/{session.plan_id}/pace-commit", json={"revision": 0, "preview_id": body["preview_id"]})


def stops(result, day=1):
    return [item.ref_id for item in result["itinerary"].days[day - 1].items if item.kind == "poi" and not item.evening]


def score(result, poi_id, value):
    next(c for c in result["candidates"] if c.poi_id == poi_id).score = value


def lengthen(places, *ids):
    for identifier in ids:
        places[identifier] = places[identifier].model_copy(update={"min_hours": 12, "max_hours": 12})


def overprice(places, *ids):
    for identifier in ids:
        places[identifier] = places[identifier].model_copy(update={"price": 5000})


# ---------- 少排一站 ----------

def test_less_removes_the_lowest_scoring_stop_and_preview_is_read_only(case):
    client, _, session, original, _ = case
    score(original, 102, 2)
    before = deepcopy(original)
    response = pace(client, session)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_commit"]
    change = body["change"]
    assert (change["from"], change["to"], change["action"], change["removed"]) == ("下午展馆", None, "pace_less", [])  # 用户要去掉的不算“被移出”
    assert body["plan"]["meta"]["operation"] == "pace_adjust"
    assert [item["name"] for item in body["plan"]["days"][0]["items"] if item["kind"] == "poi"] == ["上午公园"]
    assert session.current == 0 and session.result() == before


def test_less_keeps_must_visit_stops(case):
    _, _, _, original, _ = case
    score(original, 101, 1)
    next(c for c in original["candidates"] if c.poi_id == 101).breakdown["must_visit"] = 1
    updated, change = adjust_pace(deepcopy(original), 1, "less")
    assert stops(updated) == [101] and change["from"] == "下午展馆"


def test_less_takes_the_stop_the_user_swapped_in_last(case):
    _, _, _, original, _ = case
    score(original, 102, 1)
    original["selected"] = [Selection(poi_id=s.poi_id, reason=s.reason, source="user" if s.poi_id == 102 else "rule") for s in original["selected"]]
    updated, _ = adjust_pace(deepcopy(original), 1, "less")
    assert stops(updated) == [102]


def test_less_refuses_when_only_one_stop_is_left(case):
    _, _, _, original, _ = case
    original["itinerary"].days[0].items = [item for item in original["itinerary"].days[0].items if item.ref_id != 102]
    with pytest.raises(ReplanError, match="只有一个"):
        adjust_pace(original, 1, "less")


def test_less_refuses_when_every_stop_is_must_visit(case):
    _, _, _, original, _ = case
    for candidate in original["candidates"]:
        if candidate.poi_id in {101, 102}:
            candidate.breakdown["must_visit"] = 1
    with pytest.raises(ReplanError, match="点名"):
        adjust_pace(original, 1, "less")


def test_less_does_not_bring_the_stop_back_as_an_evening_visit(case, monkeypatch):
    _, _, _, original, _ = case
    score(original, 102, 1)
    seen = {}
    real = replan.add_evening_visits
    def spy(*args, **kwargs):
        seen["excluded"] = kwargs.get("excluded_ids")
        return real(*args, **kwargs)
    monkeypatch.setattr(replan, "add_evening_visits", spy)
    adjust_pace(deepcopy(original), 1, "less")
    assert seen["excluded"] == {102}


# ---------- 多排一站 ----------

def test_more_adds_a_nearby_candidate_and_keeps_every_stop(case):
    client, _, session, original, _ = case
    before = deepcopy(original)
    response = pace(client, session, direction="more")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_commit"], body["change"]["conflicts"]
    change = body["change"]
    assert (change["from"], change["to"], change["action"], change["removed"]) == (None, "附近美术馆", "pace_more", [])  # 同片区得分最高，远处公园得分再高也靠后
    names = [item["name"] for item in body["plan"]["days"][0]["items"] if item["kind"] == "poi"]
    assert {"上午公园", "下午展馆", "附近美术馆"} == set(names)
    assert session.current == 0 and session.result() == before


def test_more_tries_the_next_candidate_when_the_first_does_not_fit(case):
    _, _, _, original, places = case
    overprice(places, 104)
    updated, change = adjust_pace(deepcopy(original), 1, "more")
    assert change["to"] == "附近公园" and 103 in stops(updated)
    selected = next(s for s in updated["selected"] if s.poi_id == 103)
    assert selected.source == "rule"  # 系统选的，不算用户亲自选的


def test_more_refuses_honestly_when_nothing_fits(case):
    client, _, session, original, places = case
    overprice(places, 103, 104, 105, 110)
    before = deepcopy(original)
    response = pace(client, session, direction="more")
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "排不下" in detail and "原行程保持不变" in detail and "试过附近美术馆" in detail
    assert session.result() == before and session.current == 0


def test_more_queries_no_route_when_the_day_has_no_room(case, monkeypatch):
    _, _, _, original, places = case
    lengthen(places, 103, 104, 105, 110)
    monkeypatch.setattr(replan, "_rebuild_day", lambda *args, **kwargs: pytest.fail("明显放不下时不该查路线"))
    with pytest.raises(ReplanError, match="分钟空闲"):
        adjust_pace(deepcopy(original), 1, "more")


def test_more_does_not_squeeze_out_the_evening_stop(case):
    _, _, _, original, _ = case
    original["itinerary"].days[0].items.append(Item(kind="poi", ref_id=110, name="今晚的夜游", start=1000, end=1010, evening=True))
    with pytest.raises(ReplanError, match="挤掉今晚的夜游"):  # 夹具关了夜游，重建后夜游没了；白天加一站换夜游不算多排
        adjust_pace(original, 1, "more")


def test_more_gives_up_after_a_few_tries(case, monkeypatch):
    _, _, _, original, places = case
    overprice(places, 103, 104, 105, 110)
    calls = []
    real = replan._rebuild_day
    def counted(*args, **kwargs):
        calls.append(args[2][0])
        return real(*args, **kwargs)
    monkeypatch.setattr(replan, "_rebuild_day", counted)
    with pytest.raises(ReplanError):
        adjust_pace(deepcopy(original), 1, "more")
    assert len(calls) == MAX_PACE_TRIES


def test_more_refuses_on_a_full_day_anchor(case):
    _, _, _, original, places = case
    places[101] = places[101].model_copy(update={"day_anchor": True})
    with pytest.raises(ReplanError, match="一整天"):
        adjust_pace(original, 1, "more")


def test_more_refuses_when_no_candidate_is_left(case):
    _, _, _, original, _ = case
    original["candidates"] = [c for c in original["candidates"] if c.poi_id in {101, 102, 106, 107}]
    with pytest.raises(ReplanError, match="没有能加"):
        adjust_pace(original, 1, "more")


def test_more_ranks_same_area_by_score_then_others_by_distance(case):
    _, _, _, original, places = case
    assert [place.id for place in _addable(original, 1, [101, 102], places)] == [104, 103, 110, 105]


@pytest.mark.parametrize("update", [{"stamina": 3}, {"visit_role": "viewpoint"}, {"open_min": 18 * 60}, {"name": "运河夜游"}, {"recommendable": False}, {"day_anchor": True}])
def test_more_skips_candidates_the_selector_would_skip(case, update):
    _, _, _, original, places = case
    original["profile"].companions = "elderly"
    places[104] = places[104].model_copy(update=update)
    assert 104 not in [place.id for place in _addable(original, 1, [101, 102], places)]
    assert adjust_pace(deepcopy(original), 1, "more")[1]["to"] == "附近公园"


def test_more_skips_a_candidate_closed_that_day(case):
    _, _, _, original, _ = case
    next(c for c in original["candidates"] if c.poi_id == 104).available_days = [2]
    _, change = adjust_pace(deepcopy(original), 1, "more")
    assert change["to"] == "附近公园"


def test_more_skips_another_part_of_a_walk_already_planned(case):
    _, _, _, original, places = case
    places[106] = places[106].model_copy(update={"visit_group": "同一段步行"})
    places[104] = places[104].model_copy(update={"visit_group": "同一段步行"})
    _, change = adjust_pace(deepcopy(original), 1, "more")
    assert change["to"] == "附近公园"


def test_more_on_a_day_without_stops(case):
    _, _, _, original, _ = case
    original["itinerary"].days[0].items = []
    updated, change = adjust_pace(deepcopy(original), 1, "more")
    assert change["to"] and len(stops(updated)) == 1


def test_a_failed_try_leaves_no_forced_taxi_behind(case, monkeypatch):
    _, _, _, original, _ = case
    seen = []
    real = replan._rebuild_day
    def fails_once(*args, **kwargs):
        route = args[6]
        seen.append(set(route.taxi_overrides))
        if len(seen) == 1:
            route.taxi_overrides.add(("试过的路段",))
            outcome = real(*args, **kwargs)
            return (*outcome[:5], ["放不下"])
        return real(*args, **kwargs)
    monkeypatch.setattr(replan, "_rebuild_day", fails_once)
    adjust_pace(deepcopy(original), 1, "more")
    assert len(seen) == 2 and ("试过的路段",) not in seen[1]


def test_preview_lists_stops_that_appear_on_their_own(case):
    _, _, _, original, _ = case
    updated, change = adjust_pace(deepcopy(original), 1, "more")
    described = describe_change(original, updated, 1, dict(change), "pace_more", change["poi_id"])
    assert described["added"] == []  # 用户要加的那一站不重复列
    updated["itinerary"].days[0].items.append(Item(kind="poi", ref_id=105, name="换来的夜游", start=1100, end=1110, evening=True))
    assert describe_change(original, updated, 1, dict(change), "pace_more", change["poi_id"])["added"] == ["换来的夜游"]


def test_preview_lists_new_soft_notes_only(case):
    _, _, _, original, _ = case
    original["report"].violations = [Violation(code="long_idle", severity="soft", day=1, message="原来就有的空档")]
    updated, change = adjust_pace(deepcopy(original), 1, "less")
    updated["report"].violations = [Violation(code="long_idle", severity="soft", day=1, message="空档变了数字"),
                                    Violation(code="early_finish", severity="soft", day=1, message="结束偏早"),
                                    Violation(code="early_finish", severity="soft", day=2, message="别的日期")]
    assert describe_change(original, updated, 1, dict(change), "pace_less", change["poi_id"])["new_notes"] == ["结束偏早"]


def test_meal_that_moves_to_another_restaurant_is_not_called_removed(case):
    client, _, session, _, _ = case
    response = client.post(f"/api/plans/{session.plan_id}/meal-preview", json={"revision": 0, "day": 1, "item_index": 1, "restaurant_id": 202})
    assert response.status_code == 200, response.text
    lunch = next(stop for stop in response.json()["change"]["affected_stops"] if stop["name"] == "第一天午餐")
    assert lunch["after_name"] == "备选餐厅" and lunch["after_start"] is not None


def test_stop_that_really_leaves_is_still_called_removed(case):
    _, _, _, original, _ = case
    updated, change = adjust_pace(deepcopy(original), 1, "less")
    updated["itinerary"].days[0].items = [item for item in updated["itinerary"].days[0].items if item.kind == "meal"]
    gone = next(stop for stop in describe_change(original, updated, 1, dict(change), "pace_less")["affected_stops"] if stop["name"] == "上午公园")
    assert gone["after_start"] is None and gone["after_name"] is None


def test_placeholder_summary_is_not_shown(case):
    client, _, session, original, places = case
    places[104] = places[104].model_copy(update={"summary": "信息不足"})
    options = client.get(f"/api/plans/{session.plan_id}/poi-options", params={"day": 1, "item_index": 0}).json()["options"]
    assert next(option for option in options if option["poi_id"] == 104)["summary"] == ""
    updated, _ = adjust_pace(deepcopy(original), 1, "more")
    assert next(s for s in updated["selected"] if s.poi_id == 104).reason == "你让这一天多排一站，按片区和候选得分选入。"


def test_options_say_whether_the_price_is_verified(case):
    client, _, session, _, _ = case
    options = client.get(f"/api/plans/{session.plan_id}/poi-options", params={"day": 1, "item_index": 0}).json()["options"]
    assert all(option["price_verified"] is True for option in options)


# ---------- 两个方向共同的 ----------

def _break_day_two(original, places):
    """第二天原本就有硬冲突：把第二天的景点改到打烊之后。"""
    places[106] = places[106].model_copy(update={"close_min": 500})
    from app.nodes.validate import validate_itinerary
    from app.travel import Travel
    route = Travel("estimate")
    route.finalize(original["itinerary"], original["profile"], original["restaurants"])
    original["report"] = validate_itinerary(original["itinerary"], original["profile"], route.leg, original["restaurants"])
    assert any(v.day == 2 for v in original["report"].hard)


@pytest.mark.parametrize("direction", ["less", "more"])
def test_preview_says_why_it_cannot_commit_when_another_day_has_a_conflict(case, direction):
    client, _, session, original, places = case
    _break_day_two(original, places)
    response = pace(client, session, direction=direction)
    assert response.status_code == 200, response.text
    body = response.json()
    assert not body["can_commit"]
    assert any(conflict.startswith("第 2 天：") for conflict in body["change"]["conflicts"])
    assert commit(client, session, body).status_code == 422


def test_meal_preview_also_says_why(case):
    client, _, session, original, places = case
    _break_day_two(original, places)
    response = client.post(f"/api/plans/{session.plan_id}/meal-preview", json={"revision": 0, "day": 1, "item_index": 1, "restaurant_id": 202})
    assert response.status_code == 200, response.text
    assert any(conflict.startswith("第 2 天：") for conflict in response.json()["change"]["conflicts"])


@pytest.mark.parametrize("direction", ["less", "more"])
def test_commit_then_revert_restores_the_original(case, direction):
    client, _, session, original, _ = case
    body = pace(client, session, direction=direction).json()
    committed = commit(client, session, body)
    assert committed.status_code == 200, committed.text
    assert committed.json()["revision"] == 1
    assert len(stops(session.result())) == (1 if direction == "less" else 3)
    assert session.result()["usage"]["total_tokens"] == 0
    assert commit(client, session, body).status_code == 409
    reverted = client.post(f"/api/plans/{session.plan_id}/revert", json={"revision": 0, "base_revision": 1})
    assert reverted.status_code == 200, reverted.text
    assert session.result()["itinerary"] == original["itinerary"]


@pytest.mark.parametrize("direction", ["less", "more"])
def test_other_days_stay_exactly_the_same(case, direction):
    _, _, _, original, _ = case
    day_two = original["itinerary"].days[1].model_dump()
    updated, _ = adjust_pace(deepcopy(original), 1, direction)
    assert updated["itinerary"].days[1].model_dump() == day_two


@pytest.mark.parametrize("direction", ["less", "more"])
def test_meals_follow_the_new_route_but_keep_user_choices(case, direction):
    _, _, _, original, _ = case
    updated, _ = adjust_pace(deepcopy(original), 1, direction)
    lunch = next(item for item in updated["itinerary"].days[0].items if item.meal == "lunch")
    assert lunch.ref_id != 203  # 第二天吃过的不再选
    original["user_meals"] = {(1, "lunch")}
    original["itinerary"].days[0].items[1].ref_id = 202
    original["itinerary"].days[0].items[1].name = "备选餐厅"
    pinned, _ = adjust_pace(deepcopy(original), 1, direction)
    assert next(item for item in pinned["itinerary"].days[0].items if item.meal == "lunch").ref_id == 202


@pytest.mark.parametrize("direction", ["less", "more"])
def test_taxi_on_another_day_survives_and_can_commit(case, direction):
    client, store, session, original, _ = case
    evidence = _taxi_on_day_two(original)
    response = pace(client, session, direction=direction)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["can_commit"], body["change"]["conflicts"]
    assert store.get_preview(session.plan_id, 0, body["preview_id"]).result["itinerary"].days[1].items[0].travel_evidence == evidence
    assert commit(client, session, body).status_code == 200


@pytest.mark.parametrize(("overrides", "status"), [
    ({"direction": "sideways"}, 422), ({"day": 3}, 422), ({"day": 0}, 422), ({"revision": 7}, 409), ({"extra": 1}, 422),
])
def test_bad_requests_leave_the_plan_alone(case, overrides, status):
    client, _, session, original, _ = case
    before = deepcopy(original)
    assert pace(client, session, **overrides).status_code == status
    assert session.result() == before


def test_stop_the_user_swapped_in_is_not_removed_automatically(case):
    """后面改别的地方时，当天放不下也不自动移出用户亲自换入的景点。"""
    _, _, _, original, places = case
    for identifier in (101, 102, 103):
        places[identifier] = places[identifier].model_copy(update={"min_hours": 3.5, "max_hours": 3.5})
    score(original, 103, 1)
    orders = [[101, 102, 103], [106, 107]]
    selected = [*original["selected"], Selection(poi_id=103, reason="你换入的景点。", source="user")]
    pins, user_meals, used, route, catalog = _edit_context(original, 1)
    _, _, new_orders, removed, _, _ = _rebuild_day(original, 1, orders, selected, pins, user_meals, route, catalog, used=used)
    assert removed and 103 not in removed and 103 in new_orders[0]


@needs_data
def test_plan_marks_must_visit_for_the_frontend(mock_llm):
    """前端靠 score.must_visit 决定不给必去景点显示“换一个”。"""
    result = run_plan("", {"days": 2, "interests": ["人文历史"], "budget": 3000, "pace": "normal", "must_visit": ["灵隐寺"]}, llm=mock_llm, select_mode="rule")
    from app.presentation import serialize_plan
    items = [item for day in serialize_plan(result, {"select_mode": "rule", "enable_repair": True, "guide_mode": "off"}, "p", 0)["days"] for item in day["items"] if item["kind"] == "poi"]
    assert any((item["score"] or {}).get("must_visit") for item in items)
    assert any(not (item["score"] or {}).get("must_visit") for item in items)


@needs_data
@pytest.mark.parametrize("direction", ["less", "more"])
def test_real_plan_adjusts_end_to_end(mock_llm, monkeypatch, direction):
    result = run_plan("", {"days": 2, "interests": ["人文历史"], "budget": 3000, "pace": "normal"}, llm=mock_llm, select_mode="rule")
    store = SessionStore()
    monkeypatch.setattr(api, "SESSIONS", store)
    session = store.create(result, {"select_mode": "rule", "enable_repair": True, "guide_mode": "off"})
    client = TestClient(api.app)
    responses = {day: pace(client, session, day=day, direction=direction) for day in (1, 2)}
    assert all(response.status_code == 200 or "排不下" in response.json()["detail"] for response in responses.values())
    day, response = next((day, response) for day, response in responses.items() if response.status_code == 200)
    before = stops(result, day)
    other = result["itinerary"].days[2 - day].model_dump()
    body = response.json()
    assert body["can_commit"], body["change"]["conflicts"]
    assert body["change"]["walking_before_m"] == result["itinerary"].days[day - 1].walking_m is not None
    assert commit(client, session, body).status_code == 200
    # 预览写的步行和提交后时间线上的“已知步行”是同一个数
    assert body["change"]["walking_after_m"] == session.result()["itinerary"].days[day - 1].walking_m is not None
    assert session.result()["itinerary"].days[2 - day].model_dump() == other
    assert len(stops(session.result(), day)) == len(before) + (1 if direction == "more" else -1)
