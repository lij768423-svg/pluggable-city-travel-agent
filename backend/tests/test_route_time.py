"""路程按出发时刻查、定稿不估算（2026-10-26 用户：裁判用当时的高德快照，不要估计，路程要对应计划出发时刻）。"""
from datetime import date

from app.amap import AmapError
from app.amap_limits import plan_http_limit
from app.calendar import reference_date
from app.schemas import DayPlan, Hotel, Item, Itinerary, Selection, UserProfile
from app.travel import Travel, depart_at, make_travel
from tests.conftest import needs_data


class TimedRouter:
    """记下每次查公交时的日期与出发时刻；步行一律不可达，公交 30 分钟。"""
    offline = False
    calls = 0

    def __init__(self):
        self.records, self.cache, self.seen = {}, {}, []
        self.service_date, self.departure_time, self.walk_limit_m = None, "09:00", 1500

    def has(self, key):
        return key in self.cache

    def _walk_m(self, o, d, live=True):
        return None

    def transit_key(self, o, d):
        return f"transit|{o}|{d}|{self.service_date}|{self.departure_time}"

    def transit(self, o, d, live=True):
        self.seen.append((self.service_date, self.departure_time))
        return {"min": 30, "cost": 2.0, "walk_m": 300, "taxi_cost": 20.0}

    def drive(self, o, d, live=True):
        return {"m": 3000, "min": 12}


class BrokenRouter(TimedRouter):
    def transit(self, o, d, live=True):
        raise AmapError("额度用完")

    def drive(self, o, d, live=True):
        raise AmapError("额度用完")


def profile(**values):
    return UserProfile(**{"days": 1, "budget": 1000, "transport": "transit", "hotel": Hotel(lat=30.24, lon=120.14), **values})


def test_reference_date_is_next_working_day_unless_pinned(monkeypatch):
    monkeypatch.delenv("TTA_REFERENCE_DATE", raising=False)
    assert reference_date(date(2026, 10, 26)) == date(2026, 10, 27)   # 周一 → 周二
    assert reference_date(date(2026, 10, 30)) == date(2026, 11, 2)    # 周五 → 下周一
    assert reference_date(date(2026, 10, 31)) == date(2026, 11, 2)    # 周六 → 周一
    monkeypatch.setenv("TTA_REFERENCE_DATE", "2026-10-29")
    assert reference_date(date(2026, 10, 31)) == date(2026, 10, 29)   # 评测冻结的参考日优先


def test_slots_and_quota_by_trip_length():
    assert Travel.slot(9 * 60 + 44) == "09:30" and Travel.slot(15 * 60) == "15:00" and Travel.slot(0) == "00:00"
    assert [plan_http_limit(days) for days in (1, 2, 3, 5)] == [200, 200, 260, 380]
    assert make_travel("estimate", "taxi", 5).live_budget == 380


def test_transit_is_queried_at_each_departure_on_the_route_date(monkeypatch):
    monkeypatch.setenv("TTA_REFERENCE_DATE", "2026-10-29")
    router = TimedRouter()
    route = Travel("amap", "transit", router=router)
    p = profile(allow_taxi_fallback=False)  # 不让慢公交改打车，只看公交按什么时刻查
    with route.for_day(p, 1):
        morning = route.leg(30.25, 120.15, 30.27, 120.16, depart=9 * 60 + 40)
        with depart_at(route, 15 * 60 + 20):
            afternoon = route.leg(30.25, 120.15, 30.27, 120.16)
    assert morning[1] == afternoon[1] == "公交/地铁"
    assert router.seen == [(date(2026, 10, 29), "09:30"), (date(2026, 10, 29), "15:00")]
    assert route.departure_time == "09:00"  # 出了 depart_at 恢复当天起点时刻
    dated = profile(start_date=date(2026, 11, 3), days=2, allow_taxi_fallback=False)
    with route.for_day(dated, 2):
        route.leg(30.25, 120.15, 30.27, 120.16, depart=10 * 60)
    assert router.seen[-1] == (date(2026, 11, 4), "10:00")  # 填了出游日期就按出游日期


@needs_data
def test_timeline_records_each_departure_and_finalize_reuses_it(monkeypatch):
    from app.nodes.meals import build_itinerary
    monkeypatch.setenv("TTA_REFERENCE_DATE", "2026-10-29")
    p = profile(days=1, transport="taxi")
    route = Travel("estimate", "taxi")
    selected = [Selection(poi_id=92, reason="测试"), Selection(poi_id=20, reason="测试")]
    it = build_itinerary([[92, 20]], p, selected, route=route)
    day = it.days[0]
    assert all(item.travel_depart is not None and item.travel_depart <= item.start for item in day.items)
    assert day.return_depart == day.items[-1].end or day.return_depart >= day.items[-1].end
    legs = route.day_legs(day, p, {})
    assert [leg[4] for leg in legs[:-1] if leg[0] is not None] == [item.travel_depart for item in day.items if item.kind == "poi"]


@needs_data
def test_estimated_final_leg_is_a_hard_violation_only_in_final_validation(monkeypatch):
    from app.nodes.validate import validate_itinerary
    monkeypatch.setenv("TTA_REFERENCE_DATE", "2026-10-29")
    p = profile(transport="transit")
    route = Travel("amap", "transit", router=BrokenRouter())
    items = [Item(kind="poi", ref_id=92, name="浙江省博物馆孤山馆区", start=600, end=690, travel_depart=570),
             Item(kind="poi", ref_id=20, name="清河坊街", start=760, end=840, travel_depart=690)]
    it = Itinerary(days=[DayPlan(day=1, items=items, return_depart=840)])
    strict = validate_itinerary(it, p, travel_fn=route.leg, strict_routes=True)
    assert any(v.code == "route_unverified" and v.severity == "hard" for v in strict.violations)
    assert not strict.passed
    trial = validate_itinerary(it, p, travel_fn=route.leg)
    assert not any(v.code == "route_unverified" for v in trial.violations)
    assert any("公交按 10月29日（周四）起的班次查询" in note for note in strict.unknowns)
