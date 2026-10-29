"""超预算时先看钱花在哪：交通占一半以上就不再压低每餐预算（压低只会把饭推得更远），报告写明构成。

2026-10-28 用户：预算很紧时，系统打车去很远的地方吃一顿很便宜的饭，不合理。
"""
from app.nodes.repair import repair
from app.schemas import DayPlan, Hotel, Itinerary, UserProfile, ValidationReport, Violation

HOTEL = Hotel(name="测试起点", lat=30.25, lon=120.16)


def state(totals: dict, transport: str = "taxi") -> dict:
    return {"profile": UserProfile(days=1, budget=300, transport=transport, hotel=HOTEL), "dropped": [], "selected": [],
            "candidates": [], "meal_factor": 1.0, "itinerary": Itinerary(days=[DayPlan(day=1, items=[])]),
            "report": ValidationReport(passed=False, totals=totals, violations=[
                Violation(code="over_budget", severity="hard", message="")])}


def test_transport_driven_overspend_keeps_meal_budget(monkeypatch):
    monkeypatch.setattr("app.nodes.repair.load_pois", lambda: {})
    _, factor, log, _ = repair(state({"tickets": 25.0, "meals": 134.0, "transport": 344.0}))
    assert factor == 1.0
    assert any("主要来自交通" in line for line in log)


def test_meal_driven_overspend_still_lowers_meal_budget(monkeypatch):
    monkeypatch.setattr("app.nodes.repair.load_pois", lambda: {})
    _, factor, log, changed = repair(state({"tickets": 25.0, "meals": 300.0, "transport": 60.0}))
    assert factor == 0.7 and changed
