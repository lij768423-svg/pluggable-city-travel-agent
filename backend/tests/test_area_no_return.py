"""片区优先：允许空档，自动补点不得制造 A→B→A 或三个片区。"""
import pytest

from app.areas import area_fill_conflicts
from app.schemas import Candidate, DayPlan, Hotel, Item, Itinerary, POI, Selection, UserProfile, ValidationReport, Violation


@pytest.fixture
def places(monkeypatch):
    # A={1,3,4,20}，B={2}，C={5}；A、B相邻，防止相邻规则提前挡住测试。
    points = {1: 120.15, 3: 120.151, 4: 120.152, 20: 120.153, 2: 120.185, 5: 120.23}
    found = {i: POI(id=i, name=f"地点{i}", type="公园", lat=30.25, lon=lon,
                    open_min=480, close_min=1440, price=0, min_hours=1, max_hours=1)
             for i, lon in points.items()}
    for module in ("app.nodes.meals", "app.nodes.repair", "app.nodes.retrieve", "app.planning_quality", "app.evening", "app.refine"):
        monkeypatch.setattr(f"{module}.load_pois", lambda: found)
    return found


def itinerary(ids, end=900):
    return Itinerary(days=[DayPlan(day=1, items=[Item(kind="poi", ref_id=i, name=f"地点{i}",
        start=end - 60 * (len(ids) - index), end=end - 60 * (len(ids) - index - 1)) for index, i in enumerate(ids)])])


@pytest.mark.parametrize("ids,conflict", [([1, 3, 2], False), ([1, 2, 3], True),
    ([1, 2, 5], True), ([1, 3, 4], False), ([], False)])
def test_actual_area_sequence_keeps_returns_and_ignores_meals(places, ids, conflict):
    plan = itinerary(ids)
    plan.days[0].items.insert(1, Item(kind="meal", ref_id=5, name="餐厅", start=600, end=660, meal="lunch"))
    before = {i: p.model_dump() for i, p in places.items()}
    assert bool(area_fill_conflicts(plan, places)) is conflict
    assert {i: p.model_dump() for i, p in places.items()} == before


def test_best_plan_prefers_idle_over_area_return(places):
    from app.planning_quality import quality_key
    idle = ValidationReport(passed=True, totals={"idle_min": 180}, violations=[
        Violation(code=code, severity="soft", message="", day=1) for code in ("early_finish", "long_idle")])
    scattered = ValidationReport(passed=True, totals={"idle_min": 0}, violations=[
        Violation(code="area_scattered", severity="soft", message="", day=1)])
    assert quality_key(itinerary([1, 3]), idle) < quality_key(itinerary([1, 2, 3]), scattered)


@pytest.mark.parametrize("must", [False, True])
def test_initial_area_return_is_repaired_before_adding_idle_fill(places, must):
    from app.nodes.repair import repair_node
    candidates = [Candidate(poi_id=i, score=score, breakdown={"must_visit": int(must and i == 2)})
                  for i, score in ((1, .8), (2, .7), (3, .9), (4, 1))]
    state = {"profile": UserProfile(days=1), "itinerary": itinerary([1, 2, 3]),
        "report": ValidationReport(passed=True, violations=[Violation(code=code, severity="soft", message="", day=1)
            for code in ("area_scattered", "early_finish")]),
        "selected": [Selection(poi_id=i, reason="") for i in (1, 2, 3)], "candidates": candidates}
    result = repair_node(state)
    ids = [s.poi_id for s in result["selected"]]
    assert result["repair_changed"] and 4 not in ids and not result["repair_pending_additions"]
    assert not area_fill_conflicts(itinerary(ids), places)
    assert (2 in ids) is must


@pytest.mark.parametrize("bad_stage", ["cached", "live"])
def test_meal_idle_fill_rejects_area_return_at_both_checks(places, monkeypatch, bad_stage):
    from app.nodes.meals import meals_node
    profile = UserProfile(days=1, include_evening=False, hotel=Hotel(lat=30.25, lon=120.15))
    base = itinerary([1, 3], end=660)
    selected = [Selection(poi_id=i, reason="") for i in (1, 3)]
    monkeypatch.setattr("app.planning_quality.choose_meal_aware_plan", lambda *a, **kw: ([[1, 3]], base, []))
    monkeypatch.setattr("app.nodes.retrieve.retrieve", lambda *a, **kw: [])
    monkeypatch.setattr("app.nodes.validate.validate_itinerary", lambda *a, **kw: ValidationReport(passed=True))
    calls = []

    def build(orders, *args, live=True, **kw):
        calls.append(live)
        return itinerary([1, 2, 3] if bad_stage == "cached" or live else [1, 3, 2])

    monkeypatch.setattr("app.nodes.meals.build_itinerary", build)
    result = meals_node({"profile": profile, "day_orders": [[1, 3]], "selected": selected,
                         "candidates": [Candidate(poi_id=2, score=1)], "dropped": []})
    assert calls and (bad_stage != "live" or True in calls)
    assert [s.poi_id for s in result["selected"]] == [1, 3]
    assert result["itinerary"] == base


def test_experience_repair_withdraws_addition_after_actual_schedule(places, monkeypatch):
    from app.nodes.repair import repair_node
    from app.nodes.meals import meals_node
    profile = UserProfile(days=1, include_evening=False, hotel=Hotel(lat=30.25, lon=120.15))
    report = ValidationReport(passed=True, violations=[Violation(code="early_finish", severity="soft", message="", day=1)])
    state = {"profile": profile, "itinerary": itinerary([1, 3], end=660), "report": report,
             "selected": [Selection(poi_id=i, reason="") for i in (1, 3)],
             "candidates": [Candidate(poi_id=2, score=1)], "dropped": []}
    proposed = repair_node(state)
    assert proposed["repair_pending_additions"] == [2]

    def choose(orders, *a, **kw):
        return orders, itinerary([i for order in orders for i in order]), []

    monkeypatch.setattr("app.planning_quality.choose_meal_aware_plan", choose)
    monkeypatch.setattr("app.nodes.retrieve.retrieve", lambda *a, **kw: [])
    monkeypatch.setattr("app.nodes.validate.validate_itinerary", lambda *a, **kw: ValidationReport(passed=True))
    actual = meals_node({**state, **proposed, "day_orders": [[1, 2, 3]], "enable_repair": False})
    assert [s.poi_id for s in actual["selected"]] == [1, 3]
    assert actual["day_orders"] == [[1, 3]]
    assert actual["repair_removed_ids"] == [2] and not actual["repair_pending_additions"]
    assert any("体验修复未采纳" in line for line in actual["log"])
    # 后续轮次可尝试同片区候选，不再试被拒绝的地点2。
    next_state = {**state, **actual, "candidates": state["candidates"] + [Candidate(poi_id=4, score=.5)]}
    assert repair_node(next_state)["repair_pending_additions"] == [4]


def test_evening_fill_cannot_return_to_departed_area(places, monkeypatch):
    from app.evening import add_evening_visits
    plan = itinerary([1, 2])
    plan.days[0].items.append(Item(kind="meal", ref_id=9, name="晚餐", start=1050, end=1110, meal="dinner"))
    monkeypatch.setattr("app.evening.NIGHT_STREETS", {20: "同A片区夜游"})
    monkeypatch.setattr("app.evening.load_restaurants", lambda: {})
    monkeypatch.setattr("app.nodes.validate.validate_itinerary", lambda *a, **kw: ValidationReport(passed=True))
    profile = UserProfile(days=1, budget=5000, hotel=Hotel(lat=30.25, lon=120.20))

    class Route:
        def set_day(self, *args): pass
        def leg(self, *args, **kw): return 5, "步行", 0

    result, added = add_evening_visits(plan, profile, Route())
    assert not added and not area_fill_conflicts(result, places)


def test_refine_does_not_accept_higher_score_with_area_return(places, monkeypatch):
    from app.refine import refine
    base = itinerary([1, 3])
    monkeypatch.setattr("app.refine.load_restaurants", lambda: {})
    monkeypatch.setattr("app.refine.add_evening_visits", lambda it, *a, **kw: (it, []))
    monkeypatch.setattr("app.refine.validate_itinerary", lambda *a, **kw: ValidationReport(passed=True))
    builds = []

    def build(orders, *a, **kw):
        builds.append(orders)
        return itinerary([1, 2, 3] if any(2 in order for order in orders) else [1, 3])

    monkeypatch.setattr("app.refine.build_itinerary", build)
    monkeypatch.setattr("app.refine.score_itinerary", lambda it, *a: {"overall": 95 if len(it.days[0].items) > 2 else 80,
        "issues": [{"dimension": "slack", "day": 1, "item": None, "message": "下午空档"}]})
    result = refine({"profile": UserProfile(days=1, include_evening=False), "day_orders": [[1, 3]],
        "selected": [Selection(poi_id=i, reason="") for i in (1, 3)], "itinerary": base,
        "report": ValidationReport(passed=True), "candidates": [Candidate(poi_id=2, score=1)]})
    assert builds and result["itinerary"] == base
    assert "采纳 0 处修改" in result["log"][0]
