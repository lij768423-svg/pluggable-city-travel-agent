from app.planning_quality import balance_days, choose_meal_aware_plan, meal_order_candidates, quality_key
from app.schemas import DayPlan, DayWindow, Item, Itinerary, OpeningRules, POI, UserProfile, ValidationReport, Violation
from app.travel import Travel


def place(identifier: int, hours: float, **extra) -> POI:
    return POI(id=identifier, name=f"景点{identifier}", type="公园", lat=30.25, lon=120.16,
               open_min=480, close_min=1320, price=0, min_hours=hours, max_hours=hours, **extra)


class TenMinuteRoute:
    def __init__(self):
        self.days = []

    def set_day(self, profile, day):
        self.days.append(day)

    def leg(self, *coordinates, live=True):
        assert not live
        return 10, "步行", 0


def test_short_departure_day_is_not_given_half_day_visit(monkeypatch):
    places = {1: place(1, 4), 2: place(2, 1)}
    monkeypatch.setattr("app.planning_quality.load_pois", lambda: places)
    profile = UserProfile(days=2, daily_windows=[DayWindow(day=2, start=540, end=720)])
    route = TenMinuteRoute()
    groups = balance_days([[2], [1]], profile, route)
    assert 1 in groups[0] and 1 not in groups[1]
    assert sorted(identifier for group in groups for identifier in group) == [1, 2]
    assert set(route.days) == {1, 2}


def test_balance_moves_whole_walk_and_preserves_anchor(monkeypatch):
    places = {1: place(1, 7, day_anchor=True, onsite_dining=True),
              2: place(2, 0.5, visit_group="lake"), 3: place(3, 0.5, visit_group="lake"), 4: place(4, 3)}
    monkeypatch.setattr("app.planning_quality.load_pois", lambda: places)
    groups = balance_days([[1], [2, 3, 4], []], UserProfile(days=3), TenMinuteRoute())
    assert [1] in groups
    assert any(2 in group and 3 in group for group in groups)
    assert sorted(identifier for group in groups for identifier in group) == [1, 2, 3, 4]


def test_closed_date_can_swap_anchor_to_another_day(monkeypatch):
    from datetime import date
    places = {1: place(1, 7, day_anchor=True, onsite_dining=True,
                       opening_rules=OpeningRules(closed_dates=[date(2026, 10, 20)])), 2: place(2, 2)}
    monkeypatch.setattr("app.planning_quality.load_pois", lambda: places)
    groups = balance_days([[1], [2]], UserProfile(days=2, start_date=date(2026, 10, 20)), TenMinuteRoute())
    assert groups == [[2], [1]]


def test_order_candidates_do_not_split_walks_or_change_park_days(monkeypatch):
    places = {1: place(1, 7, day_anchor=True), 2: place(2, 1, visit_group="lake"),
              3: place(3, 0.5, visit_group="lake"), 4: place(4, 2), 5: place(5, 1)}
    monkeypatch.setattr("app.planning_quality.load_pois", lambda: places)
    orders = [[1], [2, 3, 4, 5]]
    candidates = meal_order_candidates(orders)
    assert candidates
    for candidate in candidates:
        assert candidate[0] == [1]
        assert sorted(candidate[1]) == [2, 3, 4, 5]
        assert candidate[1].index(3) == candidate[1].index(2) + 1
    assert orders == [[1], [2, 3, 4, 5]]


def test_quality_never_trades_hard_constraints_or_meals_for_speed():
    empty = Itinerary(days=[])
    good = ValidationReport(passed=True, violations=[], totals={"idle_min": 0})
    hard = ValidationReport(passed=False, violations=[Violation(code="over_budget", severity="hard", message="超预算")], totals={})
    missing = ValidationReport(passed=True, violations=[Violation(code="missing_lunch", severity="soft", message="缺午餐")], totals={})
    long_route = Itinerary(days=[DayPlan(day=1, items=[], return_travel_min=200)])
    assert quality_key(long_route, good) < quality_key(empty, hard)
    assert quality_key(long_route, good) < quality_key(empty, missing)


def test_complete_plan_prefers_lower_transport_when_constraints_match():
    report = ValidationReport(passed=True, violations=[], totals={"idle_min": 0})
    short = Itinerary(days=[DayPlan(day=1, items=[], return_travel_min=20)])
    long = Itinerary(days=[DayPlan(day=1, items=[], return_travel_min=100)])
    assert quality_key(short, report) < quality_key(long, report)


def test_cached_candidate_checks_keep_each_service_date(monkeypatch):
    from datetime import date
    orders = [[20, 91], [36, 100, 109]]
    profile = UserProfile(days=2, start_date=date(2026, 10, 20))

    class OfflineOnly(Travel):
        def __init__(self):
            super().__init__("estimate")
            self.validated_dates = set()

        def detail(self, *coordinates, live=True):
            assert live is False
            self.validated_dates.add(self.service_date)
            return super().detail(*coordinates, live=False)

    route = OfflineOnly()
    chosen, itinerary, _ = choose_meal_aware_plan(orders, profile, [], route=route, live=False)
    assert len(itinerary.days) == 2
    assert {date(2026, 10, 20), date(2026, 10, 21)} <= route.validated_dates
    assert sorted(identifier for group in chosen for identifier in group) == [20, 36, 91, 100, 109]


def test_solver_can_drop_remote_optional_stop_but_retains_required(monkeypatch):
    from app.nodes.schedule import route_ortools
    places = {1: place(1, 1), 2: place(2, 1)}
    monkeypatch.setattr("app.nodes.schedule.load_pois", lambda: places)

    class MatrixRoute:
        def matrix(self, locations):
            return [[0, 5, 100], [5, 0, 100], [100, 100, 0]]

        def leg(self, *coordinates, live=False):
            return 100, "公交/地铁", 2

    profile = UserProfile(days=1, include_evening=False, day_start=480, day_end=1200)
    optional = route_ortools([1, 2], profile, {1: 1, 2: 0}, MatrixRoute())
    required = route_ortools([1, 2], profile, {1: 1, 2: 0}, MatrixRoute(), must={2})
    assert optional is not None and optional[0] == [1] and optional[1] == [2]
    assert required is not None and 2 in required[0]


def test_balance_does_not_empty_days_to_reduce_commuting(monkeypatch):
    places = {identifier: place(identifier, 1) for identifier in range(1, 5)}
    monkeypatch.setattr("app.planning_quality.load_pois", lambda: places)
    groups = balance_days([[1, 2], [3], [4]], UserProfile(days=3), TenMinuteRoute())
    assert all(groups)


def test_cached_improvement_rejected_if_final_recheck_breaks_budget(monkeypatch):
    orders = [[1, 2]]
    monkeypatch.setattr("app.planning_quality.meal_order_candidates", lambda _: [[[2, 1]]])

    def build(candidate, *args, live=True, **kwargs):
        name = "original" if candidate == orders else "failed" if live else "promising"
        return Itinerary(days=[DayPlan(day=1, items=[Item(kind="poi", ref_id=1, name=name,
                                                       start=600, end=660, travel_min=30 if name == "original" else 1)])])

    def validate(itinerary, *args, **kwargs):
        violations = [Violation(code="over_budget", severity="hard", message="超预算")] if itinerary.days[0].items[0].name == "failed" else []
        return ValidationReport(passed=not violations, violations=violations, totals={"idle_min": 0})

    monkeypatch.setattr("app.nodes.meals.build_itinerary", build)
    monkeypatch.setattr("app.nodes.validate.validate_itinerary", validate)
    chosen, itinerary, _ = choose_meal_aware_plan(orders, UserProfile(days=1), [], live=True)
    assert chosen == orders
    assert itinerary.days[0].items[0].name == "original"


def test_best_plan_does_not_trade_visits_for_hours_of_idle():
    # 南京 12 号：缺一顿午餐、空档 160 分的方案，应优于删掉景点、空档 552 分的方案
    empty = Itinerary(days=[])
    soft = lambda *codes: [Violation(code=code, severity="soft", message="", day=1) for code in codes]
    lunch_missing = ValidationReport(passed=True, totals={"idle_min": 160}, violations=soft("early_finish", "long_idle", "missing_lunch"))
    sparse = ValidationReport(passed=True, totals={"idle_min": 552}, violations=soft("early_finish", "long_idle", "early_finish", "long_idle"))
    assert quality_key(empty, lunch_missing, count_idle=True) < quality_key(empty, sparse, count_idle=True)
    assert quality_key(empty, sparse) < quality_key(empty, lunch_missing)  # 分天/餐序比较不计空档，行为不变
    within = ValidationReport(passed=True, totals={"idle_min": 239}, violations=[])
    assert quality_key(empty, within, count_idle=True)[1] == 0

