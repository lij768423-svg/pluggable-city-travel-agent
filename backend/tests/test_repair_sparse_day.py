"""补空档修复优先补最空的那天：按当天已排景点挑最近的候选，不补到别的片区。"""
from app.nodes.repair import repair
from app.schemas import Candidate, DayPlan, Item, Itinerary, POI, Selection, UserProfile, ValidationReport, Violation


def test_idle_fill_targets_the_sparse_day(monkeypatch):
    profile = UserProfile(days=2, transport="transit")
    coordinates = {1: (30.31, 120.34), 2: (30.25, 120.17), 3: (30.32, 120.35), 4: (30.24, 120.18)}
    places = {identifier: POI(id=identifier, name=f"地点{identifier}", type="公园", lat=lat, lon=lon,
                             open_min=0, close_min=1440, price=0, min_hours=1, max_hours=2)
              for identifier, (lat, lon) in coordinates.items()}
    monkeypatch.setattr("app.nodes.repair.load_pois", lambda: places)
    itinerary = Itinerary(days=[
        DayPlan(day=1, items=[Item(kind="poi", ref_id=1, name="地点1", start=540, end=660)]),
        DayPlan(day=2, items=[Item(kind="poi", ref_id=2, name="地点2", start=540, end=660),
                              Item(kind="poi", ref_id=4, name="地点4", start=700, end=900)]),
    ])
    state = {
        "profile": profile, "itinerary": itinerary, "dropped": [],
        "selected": [Selection(poi_id=identifier, reason="") for identifier in (1, 2, 4)],
        # 地点3紧挨第1天的地点1；下面再加一个得分更高、但在第2天片区的地点5
        "candidates": [Candidate(poi_id=1, score=0.6), Candidate(poi_id=2, score=0.6), Candidate(poi_id=4, score=0.6),
                       Candidate(poi_id=3, score=0.5)],
        "report": ValidationReport(passed=True, violations=[
            Violation(code="early_finish", severity="soft", message="", day=1),
            Violation(code="long_idle", severity="soft", message="", day=1, item="地点1")]),
    }
    far = POI(id=5, name="地点5", type="公园", lat=30.245, lon=120.175, open_min=0, close_min=1440, price=0, min_hours=1, max_hours=2)
    places[5] = far
    state["candidates"].append(Candidate(poi_id=5, score=0.7))
    selected, _, log, changed = repair(state)
    assert changed and selected[-1].poi_id == 3 and "地点3" in log[0]
