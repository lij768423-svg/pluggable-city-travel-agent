from app.data import load_pois, load_restaurants
from app.nodes.validate import validate_itinerary
from app.schemas import DayPlan, Item, Itinerary, UserProfile
from tests.conftest import needs_data

pytestmark = needs_data


def _poi(name):
    return next(p for p in load_pois().values() if p.name == name)


def test_bad_itinerary_reports_all_violations():
    lingyin = _poi("灵隐寺")  # 06:30–17:45
    leifeng = _poi("雷峰塔")
    spicy = next(r for r in load_restaurants().values() if r.cuisine == "火锅")
    prof = UserProfile(days=1, budget=50, pace="normal", spice="no", must_visit=["西溪国家湿地公园"])
    it = Itinerary(days=[DayPlan(day=1, items=[
        Item(kind="poi", ref_id=lingyin.id, name=lingyin.name, start=17 * 60 + 30, end=18 * 60 + 30),  # 超过关门
        Item(kind="poi", ref_id=leifeng.id, name=leifeng.name, start=18 * 60 + 35, end=19 * 60),  # 路上来不及
        Item(kind="meal", ref_id=spicy.id, name=spicy.name, start=19 * 60 + 10, end=20 * 60),  # 饮食冲突
        Item(kind="poi", ref_id=None, name="不存在的乐园", start=20 * 60 + 30, end=21 * 60 + 30, cost=100),
        Item(kind="poi", ref_id=lingyin.id, name=lingyin.name, start=21 * 60 + 40, end=21 * 60 + 50),  # 重复
    ])])
    rep = validate_itinerary(it, prof)
    codes = {v.code for v in rep.violations}
    assert not rep.passed and rep.totals["feasible"] == 0
    for c in ["opening_hours", "travel_time", "diet_conflict", "unknown_place", "duplicate_poi",
              "over_budget", "day_overlong", "must_visit_missing", "missing_lunch"]:
        assert c in codes, c
    assert rep.totals["unknown_items"] == 1


def test_validator_recomputes_travel_not_trusting_itinerary():
    a, b = _poi("灵隐寺"), _poi("六和塔")
    prof = UserProfile(days=1, budget=5000)
    it = Itinerary(days=[DayPlan(day=1, items=[
        Item(kind="poi", ref_id=a.id, name=a.name, start=9 * 60, end=10 * 60, travel_min=0),
        Item(kind="poi", ref_id=b.id, name=b.name, start=10 * 60 + 1, end=11 * 60, travel_min=1),  # 自报 1 分钟
    ])])
    assert "travel_time" in {v.code for v in validate_itinerary(it, prof).violations}
