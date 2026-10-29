"""2026-10-29 杭州 4 日行程核对：打车费记成 0、灵隐没提预约。"""
from app.bundles import bundle_notice
from app.calendar import date_notices
from app.data import load_pois
from app.schemas import UserProfile
from app.travel import Travel

from .test_amap import A, B, distance_handler, fake_router


def by_name(name: str):
    return next(place for place in load_pois().values() if place.name == name)


def test_zero_taxi_quote_from_amap_is_not_a_free_ride(tmp_path):
    # 2026-10-29 起高德公交接口的 taxi_cost 一律是 "0"；原先当成车费，5 段打车里 4 段记成免费
    def handler(kind, params):
        if kind == "integrated":
            return {"status": "1", "route": {"taxi_cost": "0",
                    "transits": [{"duration": "3000", "cost": "2", "walking_distance": "800"}]}}
        return distance_handler(drive=(5000, 900))(kind, params)

    route = Travel("amap", "transit", router=fake_router(tmp_path, handler), live_budget=None)
    route.set_day(UserProfile(days=1, budget=300, transport="transit", companions="couple"), 1)
    leg = route.detail(*A, *B)
    assert leg.mode == "打车" and leg.cost > 0 and leg.cost_source == "estimate"


def test_lingyin_is_free_but_needs_booking_even_without_a_date():
    # 韬光寺同在免票范围内，但预约要求写进原库会改原库摘要，留待下一批原库修正
    place = by_name("灵隐飞来峰景区")
    assert place.price == 0 and place.opening_rules.booking_required is True
    assert "需要提前预约，本计划未代为预约" in date_notices(place, UserProfile(days=1), 1)
    notice = bundle_notice(by_name("灵隐飞来峰景区").id)
    assert "免票" in notice and "预约" in notice and "以现场为准" not in notice
