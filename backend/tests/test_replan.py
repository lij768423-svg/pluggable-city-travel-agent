import pytest
from fastapi.testclient import TestClient

from app.graph import run_plan
from app.replan import ReplanError, swap_meal
from app.sessions import SessionNotFound, SessionStore, StaleRevision
from tests.conftest import needs_data

pytestmark = needs_data
FORM = {"days": 2, "interests": ["人文历史"], "budget": 3000, "pace": "normal"}


@pytest.fixture
def plan_result(mock_llm):
    return run_plan("", FORM, llm=mock_llm, select_mode="rule")


def first_meal_with_alternatives(result, day_number=None):
    for day in result["itinerary"].days:
        if day_number is not None and day.day != day_number:
            continue
        for index, item in enumerate(day.items):
            if item.kind == "meal" and item.alternatives:
                return day.day, index, item
    pytest.skip("这份样例没有带备选的正餐")


def snapshot(day):
    return [(i.kind, i.name, i.start, i.end) for i in day.items]


@pytest.mark.parametrize("day_number", [1, 2])
def test_swap_changes_only_target_day(plan_result, day_number):
    day, index, item = first_meal_with_alternatives(plan_result, day_number)
    for other in plan_result["itinerary"].days:
        if other.day != day:
            other.notes.append("保留当天人工确认的安排")
            other.items[0].reason = "已确认的游览说明"
    original = plan_result["itinerary"].model_dump()
    target = item.alternatives[0]
    updated, change = swap_meal(plan_result, day, index, target.ref_id)
    new_meal = next(i for i in updated["itinerary"].days[day - 1].items if i.kind == "meal" and i.meal == item.meal)
    assert new_meal.ref_id == target.ref_id and "你选定的餐厅" in new_meal.reason
    for other in updated["itinerary"].days:
        if other.day != day:  # 其他日期完全不变
            assert other.model_dump() == original["days"][other.day - 1]
    assert change["from"] == item.name and change["to"] == target.name and change["day"] == day
    assert (day, item.meal) in updated["user_meals"] and "换成" in updated["log"][-1]
    assert updated["usage"]["total_tokens"] == 0
    assert updated["operation"] == "meal_swap"
    from app.api import serialize_plan
    payload = serialize_plan(updated, {"select_mode": "llm", "enable_repair": True, "guide_mode": "off"})
    assert payload["meta"]["operation"] == "meal_swap"
    # 原餐厅出现在新备选里，方便换回去
    assert item.ref_id in [a.ref_id for a in new_meal.alternatives]
    # 原结果没有被修改
    assert plan_result["itinerary"].days[day - 1].items[index].ref_id == item.ref_id
    assert plan_result["itinerary"].model_dump() == original
    expected_cost = sum(sum(stop.travel_evidence.cost for stop in planned.items) + planned.return_leg.cost
                        for planned in updated["itinerary"].days)
    assert updated["itinerary"].transport_cost == pytest.approx(expected_cost, abs=0.11)
    assert updated["travel_stats"]["final_legs"] > len(updated["itinerary"].days[day - 1].items) + 1


def test_swap_back_restores_original_choice(plan_result):
    day, index, item = first_meal_with_alternatives(plan_result)
    updated, _ = swap_meal(plan_result, day, index, item.alternatives[0].ref_id)
    meal_index = next(i for i, it in enumerate(updated["itinerary"].days[day - 1].items) if it.kind == "meal" and it.meal == item.meal)
    restored, change = swap_meal(updated, day, meal_index, item.ref_id)
    meal = restored["itinerary"].days[day - 1].items[meal_index]
    assert meal.ref_id == item.ref_id and change["to"] == item.name


def test_swap_rejects_invalid_targets(plan_result):
    day, index, item = first_meal_with_alternatives(plan_result)
    poi_index = next(i for i, it in enumerate(plan_result["itinerary"].days[day - 1].items) if it.kind == "poi")
    with pytest.raises(ReplanError) as e:
        swap_meal(plan_result, day, poi_index, item.alternatives[0].ref_id)
    assert e.value.status == 422
    with pytest.raises(ReplanError) as e:
        swap_meal(plan_result, day, index, item.ref_id)
    assert e.value.status == 422
    with pytest.raises(ReplanError) as e:
        swap_meal(plan_result, day, index, 987654321)
    assert e.value.status == 404
    with pytest.raises(ReplanError):
        swap_meal(plan_result, 9, 0, item.alternatives[0].ref_id)


def test_session_store_revisions_and_conflicts():
    store = SessionStore(max_sessions=2, max_revisions=3, ttl=3600)
    session = store.create({"v": 0}, {"select_mode": "llm"})
    assert store.append(session.plan_id, 0, {"v": 1}) == 1
    with pytest.raises(StaleRevision):
        store.append(session.plan_id, 0, {"v": "late"})
    with pytest.raises(StaleRevision):
        store.revert(session.plan_id, 0, 0)
    assert store.revert(session.plan_id, 0, 1) == ({"v": 0}, 2)
    with pytest.raises(StaleRevision):
        store.append(session.plan_id, 0, {"v": "late after undo"})
    for v in range(3, 6):
        store.append(session.plan_id, v - 1, {"v": v})
    assert list(store.get(session.plan_id).revisions) == [3, 4, 5]  # 只保留最近 3 个版本
    with pytest.raises(SessionNotFound):
        store.revert(session.plan_id, 0, 5)
    store.create({}, {})
    store.create({}, {})
    with pytest.raises(SessionNotFound):  # 超过容量，最早的会话被淘汰
        store.get(session.plan_id)


def test_session_ttl_expiry(monkeypatch):
    store = SessionStore(ttl=10)
    session = store.create({}, {})
    import app.sessions as sessions
    now = sessions.time.time()
    monkeypatch.setattr(sessions.time, "time", lambda: now + 11)
    with pytest.raises(SessionNotFound):
        store.get(session.plan_id)
    with pytest.raises(SessionNotFound):
        store.append(session.plan_id, 0, {})


def test_api_swap_revert_and_errors(monkeypatch, mock_llm):
    import app.api as api
    real = api.run_plan
    monkeypatch.setattr(api, "run_plan", lambda *a, **k: real(*a, **{**k, "llm": mock_llm}))
    client = TestClient(api.app)
    plan = client.post("/api/plan", json={"query": "", "form": FORM, "select_mode": "rule"}).json()
    assert plan["plan_id"] and plan["revision"] == 0 and plan["change"] is None
    day, index, item = next((d["day"], i, it) for d in plan["days"] for i, it in enumerate(d["items"])
                            if it["kind"] == "meal" and it["alternatives"])
    url = f"/api/plans/{plan['plan_id']}"
    target = item["alternatives"][0]
    swapped = client.post(f"{url}/swap-meal", json={"revision": 0, "day": day, "item_index": index,
                                                    "restaurant_id": target["ref_id"]})
    assert swapped.status_code == 200, swapped.text
    body = swapped.json()
    assert body["revision"] == 1 and body["change"]["to"] == target["name"]
    stale = client.post(f"{url}/swap-meal", json={"revision": 0, "day": day, "item_index": index,
                                                  "restaurant_id": target["ref_id"]})
    assert stale.status_code == 409
    assert client.post(f"{url}/revert", json={"revision": 0, "base_revision": 0}).status_code == 409
    reverted = client.post(f"{url}/revert", json={"revision": 0, "base_revision": 1}).json()
    assert reverted["revision"] == 2 and reverted["days"] == plan["days"]
    assert client.post(f"{url}/revert", json={"revision": 0, "base_revision": 1}).status_code == 409
    assert client.post("/api/plans/nope/swap-meal", json={"revision": 0, "day": 1, "item_index": 0,
                                                          "restaurant_id": 1}).status_code == 410
    bad = client.post(f"{url}/swap-meal", json={"revision": 2, "day": day, "item_index": index, "restaurant_id": 987654321})
    assert bad.status_code == 404
