"""20画像 F1–F4：首尾豁免、晚间时间窗、夜游绕行与跨轮排除。"""
from copy import deepcopy
import pytest

from app.schemas import Candidate, DayPlan, Hotel, Item, Itinerary, POI, Restaurant, Selection, UserProfile, ValidationReport, Violation


def place(identifier, name=None, lat=30.25, lon=120.15, **values):
    return POI(id=identifier, name=name or f"地点{identifier}", type="公园", lat=lat, lon=lon,
               **{"open_min": 480, "close_min": 1380, "price": 0, "min_hours": 1,
                  "max_hours": 1, "interests": ["人文历史"], **values})


@pytest.fixture
def patch_places(monkeypatch):
    def patch(places):
        for module in ("app.nodes.validate", "app.nodes.schedule", "app.nodes.repair", "app.nodes.select", "app.nodes.retrieve",
                       "app.nodes.meals", "app.planning_quality", "app.evening", "app.refine"):
            monkeypatch.setattr(f"{module}.load_pois", lambda: places)
        return places
    return patch


def test_long_commute_does_not_trigger_transport_repair(patch_places):
    from app.nodes.validate import validate_itinerary
    from app.nodes.repair import repair
    places = patch_places({1: place(1), 2: place(2, lat=30.251)})
    profile = UserProfile(days=1, budget=5000, include_evening=False,
                          hotel=Hotel(lat=30.36, lon=120.30), day_start=480, day_end=1260)
    home = (profile.hotel.lat, profile.hotel.lon)

    def leg(a, b, c, d):
        return (90 if (a, b) == home or (c, d) == home else 5), "公交/地铁", 2

    itinerary = Itinerary(days=[DayPlan(day=1, items=[
        Item(kind="poi", ref_id=1, name=places[1].name, start=570, end=630),
        Item(kind="poi", ref_id=2, name=places[2].name, start=635, end=695)])])
    report = validate_itinerary(itinerary, profile, travel_fn=leg, restaurants={})
    assert not any(v.code == "excessive_transport" for v in report.violations)
    state = {"profile": profile, "itinerary": itinerary, "report": report,
             "selected": [Selection(poi_id=i, reason="") for i in places],
             "candidates": [Candidate(poi_id=i, score=1) for i in places]}
    assert not any("减少跨区折返" in message for message in repair(state)[2])
    # 豁免时长不豁免费用和活动时段。
    assert report.totals["transport"] == 6
    profile.day_end = 750
    assert any(v.code == "day_overlong" for v in validate_itinerary(itinerary, profile, travel_fn=leg, restaurants={}).hard)


def test_internal_rides_still_trigger_comfort_warning(patch_places):
    from app.nodes.validate import validate_itinerary
    places = patch_places({i: place(i, lat=30.25 + i * .001) for i in range(1, 7)})
    itinerary = Itinerary(days=[DayPlan(day=1, items=[
        Item(kind="poi", ref_id=i, name=places[i].name, start=540 + (i - 1) * 95, end=600 + (i - 1) * 95)
        for i in places])])
    report = validate_itinerary(itinerary, UserProfile(days=1, budget=5000, day_start=480, day_end=1260),
                                travel_fn=lambda *args: (45, "公交/地铁", 2), restaurants={})
    warning = next(v for v in report.violations if v.code == "excessive_transport")
    assert "景点之间乘车约225分钟" in warning.message and warning.severity == "soft"


def test_balance_does_not_add_commuting_to_comfort_cost(patch_places):
    from app.planning_quality import balance_days
    places = patch_places({i: place(i, lat=30.25 + i * .001, min_hours=hours, max_hours=hours)
                           for i, hours in {1: 1, 2: 1, 3: .5, 4: 1}.items()})
    profile = UserProfile(days=2, day_start=480, day_end=1260, budget=5000, include_evening=False,
                          hotel=Hotel(lat=30.4, lon=120.3))
    times = [[0, 60, 90, 90, 75], [75, 0, 10, 10, 20], [75, 10, 0, 5, 20],
             [90, 20, 20, 0, 20], [90, 5, 20, 20, 0]]
    nodes = {(poi.lat, poi.lon): i for i, poi in places.items()}
    nodes[(profile.hotel.lat, profile.hotel.lon)] = 0

    class Route:
        def set_day(self, *args):
            pass

        def leg(self, a, b, c, d, live=False):
            return times[nodes[(a, b)]][nodes[(c, d)]], "公交/地铁", 2

    # 仍保留通勤的实际耗时成本；旧口径还把它当舒适度超标，错误地改变此分天结果。
    groups = balance_days([[1, 2], [3, 4]], profile, Route())
    assert [sorted(group) for group in groups] == [[1], [2, 3, 4]]


@pytest.mark.parametrize("name,opening,expected", [("钱塘江夜游", 540, True), ("武林夜市", 480, True),
    ("晚场展馆", 960, True), ("展馆", 959, False), ("夜景平台", 480, False)])
def test_evening_classification_is_derived(name, opening, expected):
    from app.poi_semantics import evening_only, visit_open_min
    poi = place(1, name, open_min=opening)
    original = poi.model_dump()
    assert evening_only(poi) is expected
    assert visit_open_min(poi) == (max(opening, 1020) if expected else opening)
    assert poi.model_dump() == original


@pytest.mark.parametrize("use_solver", [True, False])
def test_evening_time_window_survives_solver_and_greedy(patch_places, monkeypatch, use_solver):
    from app.nodes.schedule import schedule
    from app.nodes.meals import build_itinerary
    from app.nodes.validate import validate_itinerary
    from app.travel import Travel
    places = patch_places({1: place(1, "钱塘江夜游", open_min=540, close_min=1160)})
    monkeypatch.setattr("app.nodes.meals.rank_restaurants", lambda *a, **kw: [])
    profile = UserProfile(days=1, budget=5000, hotel=Hotel(lat=30.25, lon=120.15))
    route = Travel("estimate")
    orders, _, _ = schedule([1], profile, {1: 1}, route, use_solver=use_solver)
    assert orders == [[1]]
    itinerary = build_itinerary(orders, profile, [], route=route)
    visit = next(i for i in itinerary.days[0].items if i.kind == "poi")
    assert visit.start >= 1020
    assert not any(v.code == "opening_hours" for v in validate_itinerary(itinerary, profile, restaurants={}).hard)
    visit.start, visit.end = 600, 660
    assert any(v.code == "opening_hours" for v in validate_itinerary(itinerary, profile, restaurants={}).hard)


def test_evening_only_place_never_seeds_a_whole_day(patch_places):
    from app.nodes.schedule import _seed_days, split_days
    from app.travel import Travel
    places = patch_places({1: place(1), 2: place(2, lat=30.251),
        3: place(3, lat=30.30, lon=120.22), 4: place(4, lat=30.301, lon=120.221),
        5: place(5, "武林夜市", open_min=1020, lat=30.28, lon=120.17),
        6: place(6, "钱塘江夜游", lat=30.28, lon=120.171)})
    profile = UserProfile(days=2, budget=5000, hotel=Hotel(lat=30.36, lon=120.30))
    for groups in (_seed_days(list(places), profile), split_days(list(places), profile, Travel("estimate"))):
        assert all(set(group) & {1, 2, 3, 4} for group in groups)
        assert sorted(i for group in groups for i in group) == list(places)


def test_greedy_does_not_wait_for_night_market_before_daytime_visits(patch_places):
    from app.nodes.schedule import route_greedy
    from app.travel import Travel
    patch_places({1: place(1, "武林夜市", open_min=1020),
                  2: place(2, lat=30.26, close_min=1020), 3: place(3, lat=30.261, close_min=1020)})
    profile = UserProfile(days=1, hotel=Hotel(lat=30.25, lon=120.15))
    order = route_greedy([1, 2, 3], profile, Travel("estimate"))
    assert order[-1] == 1 and set(order[:2]) == {2, 3}


def test_optional_evening_does_not_interrupt_anchor_day_but_required_is_preserved(patch_places):
    from app.nodes.schedule import _seed_days
    patch_places({1: place(1, "整日园区", min_hours=6, max_hours=6, day_anchor=True),
                  2: place(2, "武林夜市", open_min=1020)})
    assert _seed_days([1, 2], UserProfile(days=1)) == [[1]]
    assert _seed_days([1, 2], UserProfile(days=1, must_visit=["武林夜市"])) == [[1, 2]]


def test_remote_optional_evening_is_not_attached_to_daytime_area(patch_places):
    from app.nodes.schedule import _seed_days
    patch_places({1: place(1), 2: place(2, "遥远夜市", open_min=1020, lat=30.40, lon=120.35)})
    profile = UserProfile(days=1, hotel=Hotel(lat=30.55, lon=120.50))
    assert _seed_days([1, 2], profile) == [[1]]
    assert _seed_days([1, 2], profile.model_copy(update={"must_visit": ["遥远夜市"]})) == [[1, 2]]


def test_daytime_selection_coverage_does_not_count_night_markets(patch_places):
    from app.nodes.select import complete_experiences
    patch_places({1: place(1, "武林夜市"), 2: place(2, "钱塘江夜游"), 3: place(3), 4: place(4)})
    selected = complete_experiences([Selection(poi_id=i, reason="") for i in (1, 2)],
        [Candidate(poi_id=i, score=1, breakdown={"distance_km": 0}) for i in (1, 2, 3, 4)], UserProfile(days=1))
    assert {3, 4} <= {s.poi_id for s in selected}


@pytest.fixture
def night_case(patch_places, monkeypatch):
    places = patch_places({1: place(1), 69: place(69, "武林夜市", lat=30.251, open_min=1020),
                           20: place(20, "清河坊街", lat=30.281)})
    dinner = Restaurant(id=9, name="晚餐", lat=30.25, lon=120.15, cuisine="江浙菜", price=30,
                        open_min=480, close_min=1380)
    monkeypatch.setattr("app.evening.load_restaurants", lambda: {9: dinner})
    monkeypatch.setattr("app.nodes.validate.validate_itinerary", lambda *a, **kw: ValidationReport(passed=True))
    itinerary = Itinerary(days=[DayPlan(day=1, items=[
        Item(kind="poi", ref_id=1, name=places[1].name, start=900, end=990),
        Item(kind="meal", ref_id=9, name=dinner.name, start=1050, end=1110, meal="dinner")])])
    profile = UserProfile(days=1, budget=5000, hotel=Hotel(lat=30.36, lon=120.30))

    class Route:
        def __init__(self, same_go=20, same_back=20, cross_go=10, cross_back=15):
            self.minutes = {(30.251, 120.15): same_go, (30.281, 120.15): cross_go}
            self.returning = {(30.251, 120.15): same_back, (30.281, 120.15): cross_back}

        def set_day(self, *args):
            pass

        def leg(self, a, b, c, d):
            if (c, d) == (profile.hotel.lat, profile.hotel.lon):
                return self.returning.get((a, b), 10), "打车", 3
            return self.minutes.get((c, d), 0), "打车", 3

    return places, itinerary, profile, Route


def test_evening_prefers_same_area_even_if_cross_area_is_faster(night_case):
    from app.evening import add_evening_visits
    _, itinerary, profile, Route = night_case
    result, added = add_evening_visits(itinerary, profile, Route(cross_go=1, cross_back=1))
    assert added and result.days[0].items[-1].ref_id == 69


@pytest.mark.parametrize("extra,accepted", [(14, True), (15, True), (16, False)])
def test_cross_area_evening_uses_extra_round_trip_boundary(night_case, extra, accepted):
    from app.evening import add_evening_visits
    _, itinerary, profile, Route = night_case
    # 去程10 + 返程extra - 直返10 = extra；同片区已被修复排除。
    result, added = add_evening_visits(itinerary, profile, Route(cross_back=extra), excluded_ids={69})
    assert bool(added) is accepted
    assert len(result.days[0].items) == (3 if accepted else 2)
    if accepted:
        assert result.days[0].items[-1].ref_id == 20


def test_evening_respects_removed_ids(night_case):
    from app.evening import add_evening_visits
    _, itinerary, profile, Route = night_case
    assert not add_evening_visits(itinerary, profile, Route(), excluded_ids={20, 69})[1]


def test_same_round_and_later_budget_repairs_do_not_restore_removed_places(patch_places):
    from app.nodes.repair import repair_node
    places = patch_places({1: place(1), 2: place(2, lat=30.251), 3: place(3, lat=30.252, price=100),
                           4: place(4, lat=30.32, lon=120.25), 5: place(5, lat=30.253)})
    state = {"profile": UserProfile(days=1, budget=50, hotel=Hotel(lat=30.40, lon=120.35)),
        "selected": [Selection(poi_id=i, reason="") for i in (1, 2, 3, 4)],
        "candidates": [Candidate(poi_id=i, score=1 if i == 4 else .5, breakdown={"distance_km": 0}) for i in places],
        "itinerary": Itinerary(days=[DayPlan(day=1, items=[Item(kind="poi", ref_id=i, name=places[i].name,
                start=540 + 90 * index, end=600 + 90 * index) for index, i in enumerate((1, 2, 3, 4))])]),
        "report": ValidationReport(passed=False, violations=[
            Violation(code="area_scattered", severity="soft", message="", day=1),
            Violation(code="over_budget", severity="hard", message="")])}
    original = deepcopy(state)
    update = repair_node(state)
    assert state == original
    assert {3, 4} <= set(update["repair_removed_ids"])
    assert {i.poi_id for i in update["selected"]} == {1, 2, 5}
    later = {**state, **update, "report": ValidationReport(passed=True, violations=[
        Violation(code="early_finish", severity="soft", message="", day=1)])}
    assert {i.poi_id for i in repair_node(later)["selected"]} == {1, 2, 5}


def test_meals_and_refine_never_try_removed_candidates(patch_places, monkeypatch):
    from app.nodes.meals import meals_node
    from app.refine import refine
    places = patch_places({i: place(i, lat=30.25 + i * .001) for i in range(1, 5)})
    itinerary = Itinerary(days=[DayPlan(day=1, items=[
        Item(kind="poi", ref_id=1, name=places[1].name, start=540, end=600),
        Item(kind="poi", ref_id=2, name=places[2].name, start=900, end=960)])])
    built, exclusions = [], []

    def build(orders, *a, **kw):
        built.append({i for order in orders for i in order})
        return itinerary.model_copy(deep=True)

    def evening(it, *a, excluded_ids=None, **kw):
        exclusions.append(excluded_ids)
        return it, []

    monkeypatch.setattr("app.nodes.meals.build_itinerary", build)
    monkeypatch.setattr("app.nodes.retrieve.retrieve", lambda *a, **kw: [])
    monkeypatch.setattr("app.nodes.validate.validate_itinerary", lambda *a, **kw: ValidationReport(passed=True))
    monkeypatch.setattr("app.planning_quality.choose_meal_aware_plan", lambda orders, *a, **kw: (orders, itinerary, []))
    monkeypatch.setattr("app.evening.add_evening_visits", evening)
    monkeypatch.setattr("app.refine.add_evening_visits", evening)
    monkeypatch.setattr("app.refine.build_itinerary", build)
    monkeypatch.setattr("app.refine.validate_itinerary", lambda *a, **kw: ValidationReport(passed=True))
    monkeypatch.setattr("app.refine.score_itinerary", lambda *a, **kw: {"overall": 50, "issues": [
        {"dimension": "slack", "day": 1, "item": "", "message": "空档"}]})
    state = {"profile": UserProfile(days=1, budget=5000), "day_orders": [[1, 2]], "itinerary": itinerary,
             "selected": [Selection(poi_id=i, reason="") for i in (1, 2)],
             "candidates": [Candidate(poi_id=i, score=1) for i in places],
             "report": ValidationReport(passed=True), "repair_removed_ids": [3]}
    meals_node(state)
    assert built and any(4 in group for group in built) and all(3 not in group for group in built)
    built.clear()
    refine(state)
    assert built and any(4 in group for group in built) and all(3 not in group for group in built)
    assert exclusions and all(3 in excluded for excluded in exclusions)


def test_refine_excludes_a_newly_added_place_when_later_trying_to_remove_it(patch_places, monkeypatch):
    from app.refine import refine
    places = patch_places({1: place(1), 69: place(69, "武林夜市", lat=30.251)})
    exclusion_checks = []

    def build(orders, *args, **kwargs):
        return Itinerary(days=[DayPlan(day=1, items=[
            Item(kind="poi", ref_id=i, name=places[i].name, start=540 + 90 * n, end=600 + 90 * n)
            for n, i in enumerate(orders[0])])])

    def evening(itinerary, *args, excluded_ids=None, **kwargs):
        if 69 not in {i.ref_id for i in itinerary.days[0].items}:
            exclusion_checks.append(set(excluded_ids or []))
            if 69 not in (excluded_ids or set()):
                itinerary.days[0].items.append(Item(kind="poi", ref_id=69, name=places[69].name, start=1110, end=1170))
        return itinerary, []

    def score(itinerary, *args):
        added = any(i.ref_id == 69 for i in itinerary.days[0].items)
        return {"overall": 60 if added else 50, "issues": [
            {"dimension": "pace" if added else "slack", "day": 1, "item": "", "message": "体验过多" if added else "空档"}]}

    monkeypatch.setattr("app.refine.build_itinerary", build)
    monkeypatch.setattr("app.refine.add_evening_visits", evening)
    monkeypatch.setattr("app.refine.score_itinerary", score)
    monkeypatch.setattr("app.refine.validate_itinerary", lambda *a, **kw: ValidationReport(passed=True))
    state = {"profile": UserProfile(days=1), "day_orders": [[1]], "itinerary": build([[1]]),
             "selected": [Selection(poi_id=1, reason="")], "report": ValidationReport(passed=True),
             "candidates": [Candidate(poi_id=1, score=1, breakdown={"must_visit": 1}), Candidate(poi_id=69, score=.7)]}
    result = refine(state)
    assert result["refine_scores"]["after"] == 60
    # 第一步补入的夜市在第二步尝试移除时也必须传入排除集合，不能被夜游模块当场补回。
    assert exclusion_checks and all(69 in excluded for excluded in exclusion_checks)
