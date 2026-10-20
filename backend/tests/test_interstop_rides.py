from contextlib import nullcontext

import pytest

from app.meal_ranking import rank_restaurants
from app.nodes.meals import build_itinerary
from app.nodes.schedule import _schedule_groups, _seed_days, route_greedy, route_ortools
from app.nodes.validate import validate_itinerary
from app.planning_quality import balance_days
from app.schemas import DayPlan, Item, Itinerary, LegEvidence, POI, Restaurant, UserProfile, ValidationReport


class FixtureRoute:
    def __init__(self, coordinates, edges=None, mode="公交/地铁", terminal=60):
        self.coordinates = coordinates
        self.indices = {location: identifier for identifier, location in coordinates.items()}
        self.edges = edges or {}
        self.mode = mode
        self.terminal = terminal

    def set_day(self, profile, day):
        pass

    def for_day(self, profile, day):
        return nullcontext()

    def leg(self, lat1, lon1, lat2, lon2, live=True):
        before, after = self.indices[(lat1, lon1)], self.indices[(lat2, lon2)]
        if before == after:
            return 0, "原地", 0
        minutes = self.edges.get((before, after), self.terminal if 0 in (before, after) else 20)
        return minutes, self.mode, 0 if self.mode == "步行" else 2

    def detail(self, *coordinates, live=True):
        minutes, mode, cost = self.leg(*coordinates, live=live)
        return LegEvidence(minutes=minutes, mode=mode, cost=cost, source="estimate",
                           walking_m=minutes * 75 if mode == "步行" else 0)

    def matrix(self, locations):
        return [[self.leg(*before, *after, live=False)[0] for after in locations] for before in locations]


@pytest.fixture
def scenario(monkeypatch):
    profile = UserProfile(days=1, day_start=480, day_end=1320, include_evening=False,
                          transport="transit", budget=5000, max_walking_m=30000)
    coordinates = {0: (profile.hotel.lat, profile.hotel.lon),
                   1: (30.26, 120.16), 2: (30.27, 120.17), 3: (30.28, 120.18),
                   4: (30.29, 120.19), 9: (30.265, 120.165)}
    places = {identifier: POI(id=identifier, name=f"地点{identifier}", type="公园",
                             lat=coordinates[identifier][0], lon=coordinates[identifier][1],
                             open_min=480, close_min=1380, price=0, min_hours=1, max_hours=1)
              for identifier in range(1, 5)}
    for module in ("schedule", "validate", "meals", "repair"):
        monkeypatch.setattr(f"app.nodes.{module}.load_pois", lambda: places)
    monkeypatch.setattr("app.planning_quality.load_pois", lambda: places)
    monkeypatch.setattr("app.nodes.meals.load_restaurants", lambda: {})
    restaurant = Restaurant(id=9, name="沿途餐厅", lat=coordinates[9][0], lon=coordinates[9][1],
                            price=60, cuisine="江浙菜", open_min=600, close_min=1380)
    return profile, coordinates, places, restaurant


def itinerary_for(identifiers, profile, places, route):
    previous = (profile.hotel.lat, profile.hotel.lon)
    clock = profile.day_start
    items = []
    for identifier in identifiers:
        place = places[identifier]
        destination = (place.lat, place.lon)
        minutes, _, _ = route.leg(*previous, *destination)
        clock += minutes
        items.append(Item(kind="poi", ref_id=identifier, name=place.name, start=clock, end=clock + 60,
                          travel_min=1, travel_mode="步行"))
        clock += 60
        previous = destination
    return Itinerary(days=[DayPlan(day=1, items=items)])


@pytest.mark.parametrize("minutes,mode,blocked,long", [(25, "公交/地铁", False, False), (26, "公交/地铁", False, True),
                                                        (45, "公交/地铁", False, True), (46, "公交/地铁", True, False),
                                                        (25, "打车", False, False), (46, "打车", True, False),
                                                        (52, "步行", False, False)])
def test_validator_recomputes_internal_rides_and_exempts_terminals(scenario, minutes, mode, blocked, long):
    # 25 分钟是舒适线（超过只提示），45 分钟封顶（超过拦截）
    profile, coordinates, places, _ = scenario
    route = FixtureRoute(coordinates, {(1, 2): minutes}, mode=mode)
    itinerary = itinerary_for([1, 2], profile, places, route)
    report = validate_itinerary(itinerary, profile, travel_fn=route.leg, restaurants={})
    violations = [violation for violation in report.hard if violation.code == "interstop_ride_limit"]
    warnings = [violation for violation in report.violations if violation.code == "interstop_ride_long"]
    assert len(violations) == int(blocked)
    assert [(warning.item, warning.severity) for warning in warnings] == ([("地点2", "soft")] if long else [])
    assert report.passed is not blocked
    if blocked:
        assert violations[0].item == "地点2"


def test_screenshot_two_long_afternoon_rides_are_both_hard_failures(scenario):
    profile, coordinates, places, _ = scenario
    profile.day_start = 723
    route = FixtureRoute(coordinates, {(1, 2): 52, (2, 3): 58}, terminal=0)
    itinerary = itinerary_for([1, 2, 3], profile, places, route)
    report = validate_itinerary(itinerary, profile, travel_fn=route.leg, restaurants={})
    violations = [violation for violation in report.hard if violation.code == "interstop_ride_limit"]
    assert [violation.item for violation in violations] == ["地点2", "地点3"]
    assert not report.passed


@pytest.mark.parametrize("day_start", [480, 720])
def test_long_first_and_return_legs_are_not_limited(scenario, day_start):
    # 用户决定：首段和回起点的尾段不限时长、不提示，上午或下午出发都一样
    profile, coordinates, places, _ = scenario
    profile.day_start = day_start
    route = FixtureRoute(coordinates, {(0, 1): 98, (1, 0): 98}, terminal=0)
    report = validate_itinerary(itinerary_for([1], profile, places, route), profile,
                                travel_fn=route.leg, restaurants={})
    assert report.passed
    assert not any(violation.code.startswith(("first_leg", "interstop")) for violation in report.violations)


def test_complete_executable_requires_all_three_statuses():
    report = ValidationReport(passed=True, constraint_status="passed", arrangement_status="complete", evidence_status="verified")
    assert report.complete_executable
    report = ValidationReport(passed=True, constraint_status="passed", arrangement_status="complete", evidence_status="partial")
    assert not report.complete_executable


def test_long_walks_do_not_count_as_excessive_riding(scenario):
    profile, coordinates, places, _ = scenario
    route = FixtureRoute(coordinates, {(1, 2): 70}, mode="步行", terminal=70)
    itinerary = itinerary_for([1, 2], profile, places, route)
    report = validate_itinerary(itinerary, profile, travel_fn=route.leg, restaurants={})
    assert not {"excessive_transport", "interstop_ride_limit"} & {violation.code for violation in report.violations}
    assert report.totals["walking_m"] == 210 * 75


def test_departure_exemption_resets_each_day(scenario):
    profile, coordinates, places, _ = scenario
    profile.days = 2
    route = FixtureRoute(coordinates, terminal=60)
    itinerary = itinerary_for([1], profile, places, route)
    second = itinerary_for([2], profile, places, route).days[0]
    second.day = 2
    itinerary.days.append(second)
    report = validate_itinerary(itinerary, profile, travel_fn=route.leg, restaurants={})
    assert report.passed
    assert not any(violation.code == "interstop_ride_limit" for violation in report.violations)


@pytest.mark.parametrize("solver", [True, False])
def test_routing_changes_order_instead_of_taking_long_rides(scenario, solver):
    profile, coordinates, _, _ = scenario
    route = FixtureRoute(coordinates, {(1, 2): 52, (2, 3): 58})
    if solver:
        result = route_ortools([1, 2, 3], profile, {1: 1, 2: 1, 3: 1}, route)
        assert result is not None
        order, dropped = result
        assert not dropped
    else:
        order = route_greedy([1, 2, 3], profile, route)
    assert set(order) == {1, 2, 3}
    assert all(route.leg(*coordinates[before], *coordinates[after])[0] <= 25
               for before, after in zip(order, order[1:]))


@pytest.mark.parametrize("solver", [True, False])
def test_afternoon_routing_keeps_a_far_first_stop(scenario, solver):
    # 首段不受限：下午出发、首站很远也照常排，不再为此丢点
    profile, coordinates, _, _ = scenario
    profile.day_start = 720
    edges = {(0, 1): 52, (0, 2): 52, (1, 2): 10, (2, 1): 10}
    route = FixtureRoute(coordinates, edges)
    if solver:
        result = route_ortools([1, 2], profile, {1: 1, 2: 1}, route)
        assert result is not None
        order, dropped = result
    else:
        order, dropped = route_greedy([1, 2], profile, route), []
    assert set(order) == {1, 2} and not dropped


def test_solver_allows_long_walking_connections(scenario):
    profile, coordinates, _, _ = scenario
    route = FixtureRoute(coordinates, {(1, 2): 52, (2, 1): 52}, mode="步行", terminal=10)
    result = route_ortools([1, 2], profile, {1: 1, 2: 1}, route)
    assert result is not None and set(result[0]) == {1, 2} and not result[1]


@pytest.mark.parametrize("solver", [True, False])
def test_rides_between_soft_and_hard_limit_are_kept_when_no_closer_order(scenario, solver):
    # 公交稀疏片区（如下沙）两处只能坐 35 分钟车相连：超过舒适线但未到封顶，不应丢点
    profile, coordinates, _, _ = scenario
    route = FixtureRoute(coordinates, {(1, 2): 35, (2, 1): 35}, terminal=10)
    if solver:
        result = route_ortools([1, 2], profile, {1: 1, 2: 1}, route)
        assert result is not None
        order, dropped = result
    else:
        order, dropped = route_greedy([1, 2], profile, route), []
    assert set(order) == {1, 2} and not dropped


def test_greedy_fallback_reports_unreachable_stops_and_preserves_required(scenario):
    profile, coordinates, _, _ = scenario
    route = FixtureRoute(coordinates, {(before, after): 52 for before in (1, 2, 3)
                                       for after in (1, 2, 3) if before != after})
    orders, dropped, _ = _schedule_groups([[1, 2, 3]], [1, 2, 3], profile, {}, route, False, {2})
    assert orders == [[2]]
    assert set(dropped) == {1, 3}


def test_incompatible_required_stops_remain_an_explicit_failure(scenario):
    profile, coordinates, places, _ = scenario
    profile.must_visit = [places[1].name, places[2].name]
    route = FixtureRoute(coordinates, {(1, 2): 52, (2, 1): 58})
    result = route_ortools([1, 2], profile, {1: 1, 2: 1}, route, must={1, 2})
    assert result is not None and len(result[0]) == 1 and len(result[1]) == 1
    itinerary = build_itinerary([result[0]], profile, [], route=route, live=False)
    report = validate_itinerary(itinerary, profile, travel_fn=route.leg, restaurants={})
    assert not report.passed
    assert any(violation.code == "must_visit_missing" for violation in report.hard)


def test_balancing_separates_incompatible_districts(scenario):
    profile, coordinates, _, _ = scenario
    profile.days = 2
    near_pairs = {(1, 3), (3, 1), (2, 4), (4, 2)}
    edges = {(before, after): 10 if (before, after) in near_pairs else 52
             for before in (1, 2, 3, 4) for after in (1, 2, 3, 4) if before != after}
    groups = balance_days([[1, 2], [3, 4]], profile, FixtureRoute(coordinates, edges, terminal=10))
    assert {frozenset(group) for group in groups} == {frozenset({1, 3}), frozenset({2, 4})}


def test_seed_days_uses_locality_units_without_making_them_hard(monkeypatch):
    places = {
        identifier: POI(id=identifier, name=f"地点{identifier}", type="公园",
                        lat=lat, lon=lon, open_min=480, close_min=1320,
                        price=0, min_hours=1, max_hours=1)
        for identifier, lat, lon in (
            (1, 30.1000, 120.1000), (2, 30.1050, 120.1050),
            (3, 30.3000, 120.3000), (4, 30.3050, 120.3050),
            (5, 30.5000, 120.5000), (6, 30.5050, 120.5050),
        )
    }
    monkeypatch.setattr("app.nodes.schedule.load_pois", lambda: places)
    groups = _seed_days([1, 2, 3, 4, 5, 6], UserProfile(days=2))
    assert all(any(set(pair) <= set(group) for group in groups)
               for pair in ({1, 2}, {3, 4}, {5, 6}))


@pytest.mark.parametrize("incoming,onward,mode,departure,returning,accepted", [
    (25, 25, "公交/地铁", False, False, True),
    (26, 20, "公交/地铁", False, False, False),
    (20, 26, "公交/地铁", False, False, False),
    (52, 58, "步行", False, False, True),
    (20, 60, "公交/地铁", False, True, True),
    (60, 20, "公交/地铁", True, False, True),
    (60, 26, "公交/地铁", True, False, False),
])
def test_restaurants_check_both_sides_with_only_actual_terminal_exemptions(
        scenario, incoming, onward, mode, departure, returning, accepted):
    profile, coordinates, _, restaurant = scenario
    before, after = (0 if departure else 1), (0 if returning else 2)
    route = FixtureRoute(coordinates, {(before, 9): incoming, (9, after): onward}, mode=mode)
    results = rank_restaurants(coordinates[before], None if returning else coordinates[after], 720,
                               profile, set(), 100, 810, {9: restaurant}, route=route, is_departure=departure)
    assert bool(results) is accepted


def test_restaurant_coarse_estimate_does_not_override_actual_short_connection(scenario, monkeypatch):
    profile, coordinates, _, restaurant = scenario
    monkeypatch.setattr("app.meal_ranking.travel", lambda *coordinates: (80, "公交/地铁", 2))
    results = rank_restaurants(coordinates[1], coordinates[2], 720, profile, set(), 100, 810,
                               {9: restaurant}, route=FixtureRoute(coordinates))
    assert results and results[0]["go"] == 20


def test_first_meal_searches_near_first_attraction_instead_of_departure(scenario):
    profile, coordinates, _, restaurant = scenario
    restaurants = {9: restaurant}
    edges = {(0, 9): 60, (9, 2): 5}
    for identifier in range(100, 131):
        location = (profile.hotel.lat + (identifier - 100) * 0.00001, profile.hotel.lon)
        coordinates[identifier] = location
        restaurants[identifier] = restaurant.model_copy(update={"id": identifier, "name": f"起点旁餐厅{identifier}",
                                                                 "lat": location[0], "lon": location[1]})
        edges[(0, identifier)] = 1
        edges[(identifier, 2)] = 52
    results = rank_restaurants(coordinates[0], coordinates[2], 720, profile, set(), 100, 810,
                               restaurants, route=FixtureRoute(coordinates, edges), is_departure=True)
    assert results and results[0]["restaurant"].id == 9


def test_attraction_at_origin_coordinates_is_not_a_return_trip(scenario):
    profile, coordinates, _, restaurant = scenario
    route = FixtureRoute(coordinates, {(9, 0): 60})
    results = rank_restaurants(coordinates[1], coordinates[0], 720, profile, set(), 100, 810,
                               {9: restaurant}, route=route)
    assert not results


def test_meal_to_next_attraction_is_validated_as_internal(scenario):
    profile, coordinates, places, restaurant = scenario
    route = FixtureRoute(coordinates, {(9, 2): 52})
    itinerary = Itinerary(days=[DayPlan(day=1, items=[
        Item(kind="meal", ref_id=9, name=restaurant.name, meal="lunch", start=720, end=780),
        Item(kind="poi", ref_id=2, name=places[2].name, start=832, end=892),
    ])])
    report = validate_itinerary(itinerary, profile, travel_fn=route.leg, restaurants={9: restaurant})
    assert [violation.item for violation in report.hard if violation.code == "interstop_ride_limit"] == [places[2].name]


@pytest.mark.parametrize("minutes,mode,accepted", [(25, "公交/地铁", True), (26, "公交/地铁", False), (52, "步行", True)])
def test_evening_uses_same_ride_limit_and_allows_walks(scenario, monkeypatch, minutes, mode, accepted):
    from app.evening import add_evening_visits

    profile, coordinates, places, restaurant = scenario
    profile.include_evening = True
    monkeypatch.setattr("app.evening.load_pois", lambda: places)
    monkeypatch.setattr("app.evening.load_restaurants", lambda: {9: restaurant})
    from dataclasses import replace
    from app.cities import current_city
    monkeypatch.setattr("app.evening.current_city", lambda: replace(current_city(), evening_streets={3: places[3].name}))
    # 本测试只隔离单段25分钟/步行规则；跨片区额外15分钟另有专门边界回归。
    coordinates[3] = (places[1].lat + .0001, places[1].lon)
    places[3] = places[3].model_copy(update={"lat": coordinates[3][0], "lon": coordinates[3][1]})
    route = FixtureRoute(coordinates, {(9, 3): minutes}, mode=mode)
    itinerary = Itinerary(days=[DayPlan(day=1, items=[
        Item(kind="poi", ref_id=1, name=places[1].name, start=900, end=960),
        Item(kind="meal", ref_id=9, name=restaurant.name, meal="dinner", start=1050, end=1110),
    ])])
    updated, additions = add_evening_visits(itinerary, profile, route)
    assert bool(additions) is accepted
    if accepted:
        assert updated.days[0].items[-1].ref_id == 3
        assert updated.days[0].items[-1].travel_min == minutes
