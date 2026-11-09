from app.planning_quality import balance_days, choose_meal_aware_plan, meal_order_candidates, quality_key, repair_choice_key
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


def test_day_routing_does_not_depend_on_search_time(monkeypatch):
    # 2026-10-31：不再用限时的引导局部搜索（r2 c03 重算对不上录制）；几种起点各做一次贪心下降，结果与时限、机器快慢无关
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    from app.nodes import schedule
    places = {identifier: place(identifier, 1) for identifier in range(1, 5)}
    monkeypatch.setattr("app.nodes.schedule.load_pois", lambda: places)

    class MatrixRoute:
        def matrix(self, locations):
            return [[0, 20, 35, 15, 30], [20, 0, 10, 25, 40], [35, 10, 0, 30, 12],
                    [15, 25, 30, 0, 18], [30, 40, 12, 18, 0]][:len(locations)]

        def leg(self, *coordinates, live=False):
            return 20, "打车", 0

    seen = []
    original = pywrapcp.RoutingModel.SolveWithParameters

    def spy(self, params):
        seen.append((params.local_search_metaheuristic, params.first_solution_strategy))
        return original(self, params)

    monkeypatch.setattr(pywrapcp.RoutingModel, "SolveWithParameters", spy)
    profile = UserProfile(days=1, include_evening=False, day_start=480, day_end=1200)
    scores = {1: 1, 2: 1, 3: 1, 4: 1}
    first = schedule.route_ortools([1, 2, 3, 4], profile, scores, MatrixRoute())
    second = schedule.route_ortools([1, 2, 3, 4], profile, scores, MatrixRoute(), time_limit_s=1)
    assert first is not None and first == second and not first[1]
    assert {meta for meta, _ in seen} == {routing_enums_pb2.LocalSearchMetaheuristic.GREEDY_DESCENT}
    assert [strategy for _, strategy in seen] == list(schedule.FIRST_SOLUTIONS) * 2


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


def _budget_report(total, idle, *codes):
    violations = [Violation(code="over_budget", severity="hard", message="超预算")] if total > 600 else []
    violations += [Violation(code=code, severity="soft", message="", day=2) for code in codes]
    return ValidationReport(passed=total <= 600, totals={"total": total, "idle_min": idle}, violations=violations)


def test_repair_choice_keeps_the_round_closest_to_budget():
    # e1-dev12-v3 r2 c03：原版超 32%、排得满；修复后的方案只超 2.5%，空档多。原规则（只比软扣分）留下原版
    empty = Itinerary(days=[])
    original = _budget_report(790, 31, "area_scattered", "interstop_ride_long")
    repaired = _budget_report(615, 431, "early_finish", "long_idle")
    assert quality_key(empty, original, count_idle=True) < quality_key(empty, repaired, count_idle=True)
    assert repair_choice_key(empty, repaired, 600) < repair_choice_key(empty, original, 600)


def test_repair_choice_does_not_skip_meals_to_save_money():
    empty = Itinerary(days=[])
    full = _budget_report(700, 0)
    skipped = _budget_report(650, 0, "missing_dinner")
    assert repair_choice_key(empty, full, 600) < repair_choice_key(empty, skipped, 600)


def test_repair_choice_ignores_small_overrun_differences_and_plans_within_budget():
    empty = Itinerary(days=[])
    # 同在 5%–10% 一档：几元之差不压过片区折返
    tidy, scattered = _budget_report(645, 0), _budget_report(640, 0, "area_scattered")
    assert repair_choice_key(empty, tidy, 600) < repair_choice_key(empty, scattered, 600)
    assert repair_choice_key(empty, _budget_report(630, 0), 600)[2] == 1  # 正好超 5% 仍是第一档
    # 都在预算内：与原规则一致（南京 12 号的取舍不变）
    soft = lambda *codes: [Violation(code=code, severity="soft", message="", day=1) for code in codes]
    lunch_missing = ValidationReport(passed=True, totals={"total": 500, "idle_min": 160}, violations=soft("early_finish", "long_idle", "missing_lunch"))
    sparse = ValidationReport(passed=True, totals={"total": 400, "idle_min": 552}, violations=soft("early_finish", "long_idle", "early_finish", "long_idle"))
    assert repair_choice_key(empty, lunch_missing, 600) < repair_choice_key(empty, sparse, 600)


def test_validate_node_replaces_best_with_cheaper_repair_round(monkeypatch):
    from app.nodes.validate import validate_node
    reports = iter([_budget_report(790, 31, "area_scattered"), _budget_report(615, 431, "early_finish", "long_idle")])
    monkeypatch.setattr("app.nodes.validate.validate_itinerary", lambda *a, **kw: next(reports))
    state = {"profile": UserProfile(days=2, budget=600), "itinerary": Itinerary(days=[]), "selected": ["original"]}
    first = validate_node(state)["best_candidate"]
    second = validate_node({**state, "selected": ["repaired"], "best_candidate": first})["best_candidate"]
    assert second["selected"] == ["repaired"]
