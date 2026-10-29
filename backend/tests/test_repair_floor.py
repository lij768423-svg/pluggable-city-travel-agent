"""修复不把一天删空；只选主题乐园且附近乐园太少时，亲子游乐地点补位。"""
from app.nodes.repair import repair
from app.schemas import (POI, Candidate, DayPlan, Hotel, Item, Itinerary, Selection, UserProfile, ValidationReport,
                         Violation)

HOTEL = Hotel(name="测试起点", lat=30.25, lon=120.16)


def place(identifier, lat, lon, interests=("主题乐园",), **changes):
    return POI(id=identifier, name=f"地点{identifier}", type="游乐园/体育娱乐", lat=lat, lon=lon, open_min=540,
               close_min=1020, price=0, min_hours=1, max_hours=2, interests=list(interests), **changes)


def test_time_conflict_repair_never_empties_a_day(monkeypatch):
    # 第 2 天只有远处的地点2且时间冲突：删掉后换上比它近的地点3，地点4更远不入选
    places = {1: place(1, 30.26, 120.17), 2: place(2, 30.45, 120.40), 3: place(3, 30.28, 120.20), 4: place(4, 30.60, 120.50)}
    monkeypatch.setattr("app.nodes.repair.load_pois", lambda: places)
    itinerary = Itinerary(days=[DayPlan(day=1, items=[Item(kind="poi", ref_id=1, name="地点1", start=540, end=600)]),
                                DayPlan(day=2, items=[Item(kind="poi", ref_id=2, name="地点2", start=600, end=700)])])
    state = {"profile": UserProfile(days=2, hotel=HOTEL), "itinerary": itinerary, "dropped": [],
             "selected": [Selection(poi_id=1, reason=""), Selection(poi_id=2, reason="")],
             "candidates": [Candidate(poi_id=i, score=0.6) for i in (1, 2, 3, 4)],
             "report": ValidationReport(passed=False, violations=[
                 Violation(code="opening_hours", severity="hard", message="", day=2, item="地点2")])}
    selected, _, log, changed = repair(state)
    assert changed and [s.poi_id for s in selected] == [1, 3]
    assert any("不留空" in line and "地点3" in line for line in log)


def test_repair_still_removes_when_other_places_remain(monkeypatch):
    places = {1: place(1, 30.26, 120.17), 2: place(2, 30.45, 120.40), 3: place(3, 30.28, 120.20)}
    monkeypatch.setattr("app.nodes.repair.load_pois", lambda: places)
    itinerary = Itinerary(days=[DayPlan(day=1, items=[Item(kind="poi", ref_id=1, name="地点1", start=540, end=600),
                                                      Item(kind="poi", ref_id=2, name="地点2", start=660, end=760)])])
    state = {"profile": UserProfile(days=1, hotel=HOTEL), "itinerary": itinerary, "dropped": [],
             "selected": [Selection(poi_id=1, reason=""), Selection(poi_id=2, reason="")],
             "candidates": [Candidate(poi_id=1, score=0.7), Candidate(poi_id=2, score=0.5), Candidate(poi_id=3, score=0.6)],
             "report": ValidationReport(passed=False, violations=[
                 Violation(code="opening_hours", severity="hard", message="", day=1, item="地点2")])}
    selected, _, log, _ = repair(state)
    assert [s.poi_id for s in selected] == [1] and not any("不留空" in line for line in log)


def test_theme_only_profile_adds_family_places_when_few_parks_nearby(monkeypatch):
    from app.nodes import retrieve
    kids = place(9, 30.255, 120.165, interests=("亲子游乐", "自然风光"))
    far_parks = {i: place(i, 30.45 + i * 0.01, 120.45) for i in (1, 2)}   # 起点 15 公里外
    near_parks = {i: place(i, 30.26 + i * 0.001, 120.17) for i in range(1, 8)}  # 起点附近 7 个
    profile = UserProfile(days=2, interests=["主题乐园"], hotel=HOTEL, budget=5000)
    for parks, expected in ((far_parks, True), (near_parks, False)):
        places = {**parks, 9: kids}
        monkeypatch.setattr(retrieve, "load_pois", lambda places=places: places)
        candidates, report = retrieve.retrieve_with_report(profile)
        assert report["theme_relaxed"] is expected
        assert (9 in {c.poi_id for c in candidates}) is expected



def test_missing_meal_removes_the_visit_blocking_that_meal():
    from app.nodes.repair import meal_blocker
    from app.schemas import DayPlan, Item, Itinerary, POI, ValidationReport, Violation
    pois = {i: POI(id=i, name=f"景点{i}", type="公园", lat=31.3, lon=120.6, open_min=480, close_min=1260, price=0,
                   min_hours=1, max_hours=2, interests=["自然风光"], day_anchor=(i == 4)) for i in range(1, 6)}
    def visit(i, start, end):
        return Item(kind="poi", ref_id=i, name=f"景点{i}", start=start, end=end)
    itinerary = Itinerary(days=[DayPlan(day=1, items=[visit(1, 540, 660), visit(2, 700, 840), visit(3, 900, 1000)]),
                                DayPlan(day=2, items=[visit(5, 600, 900)])])
    lunch = ValidationReport(passed=True, violations=[Violation(code="missing_lunch", severity="soft", message="", day=1)])
    dinner = ValidationReport(passed=True, violations=[Violation(code="missing_dinner", severity="soft", message="", day=1)])
    alone = ValidationReport(passed=True, violations=[Violation(code="missing_lunch", severity="soft", message="", day=2)])
    ids = {1, 2, 3, 5}
    assert meal_blocker(itinerary, lunch, ids, set(), pois) == 2  # 11:40–14:00 跨过午餐末
    assert meal_blocker(itinerary, dinner, ids, set(), pois) == 3  # 当天最后一个
    assert meal_blocker(itinerary, lunch, ids, {2}, pois) is None  # 必去不删
    assert meal_blocker(itinerary, alone, ids, set(), pois) is None  # 当天只剩一个不删
