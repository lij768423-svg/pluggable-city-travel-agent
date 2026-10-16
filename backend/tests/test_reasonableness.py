from app.reasonableness import score_itinerary
from app.schemas import POI, DayPlan, Item, Itinerary, UserProfile


def _poi(identifier, name, lat, lon, low=1.0, high=2.0, **extra):
    return POI(id=identifier, name=name, type=extra.pop("type", "历史古迹"), lat=lat, lon=lon, open_min=480, close_min=1080,
               price=0, min_hours=low, max_hours=high, audience=extra.pop("audience", ["朋友", "老人", "亲子"]), **extra)


def _visit(place, start, end, travel=0, mode="步行"):
    return Item(kind="poi", ref_id=place.id, name=place.name, start=start, end=end, travel_min=travel, travel_mode=mode)


def _issues(result, dimension):
    return [issue["message"] for issue in result["issues"] if issue["dimension"] == dimension]


NEAR = _poi(1, "小祠堂", 30.2550, 120.1700, .5, 1.0)
NEXT = _poi(2, "湖边公园", 30.2560, 120.1710)
FAR = _poi(3, "远郊景区", 30.3600, 120.0200)


def test_stay_needs_independent_reference_to_count():
    plan = Itinerary(days=[DayPlan(day=1, items=[_visit(NEAR, 600, 720), _visit(NEXT, 730, 820)])])
    profile = UserProfile(days=1)
    same_source = score_itinerary(plan, profile, {1: NEAR, 2: NEXT})
    assert "stay" not in same_source["counted"] and same_source["coverage"]["stay:dataset"] == 2
    independent = score_itinerary(plan, profile, {1: NEAR, 2: NEXT}, references={1: (20, 40, "manual")})
    assert "stay" in independent["counted"] and independent["scores"]["stay"] < 60
    assert any("参考 20–40" in message for message in _issues(independent, "stay"))


def test_kids_only_place_for_adults_is_severe():
    park = _poi(4, "儿童乐园", 30.2560, 120.1710, type="游乐园/体育娱乐", audience=["亲子"])
    plan = Itinerary(days=[DayPlan(day=1, items=[_visit(park, 600, 720)])])
    adults = score_itinerary(plan, UserProfile(days=1, companions="couple"), {4: park})
    kids = score_itinerary(plan, UserProfile(days=1, companions="family_kids"), {4: park})
    assert adults["severe"] == 1 and adults["scores"]["fit"] == 0
    assert kids["severe"] == 0 and kids["scores"]["fit"] == 100
    named = score_itinerary(plan, UserProfile(days=1, companions="couple", must_visit=["儿童乐园"]), {4: park})
    assert named["severe"] == 0


def test_long_rides_lower_transport_score():
    short = Itinerary(days=[DayPlan(day=1, items=[_visit(NEAR, 600, 720), _visit(NEXT, 730, 840)], return_travel_min=5)])
    long = Itinerary(days=[DayPlan(day=1, items=[_visit(NEAR, 600, 660), _visit(FAR, 750, 810, 90, "公交/打车")],
                                   return_travel_min=90)])
    pois = {1: NEAR, 2: NEXT, 3: FAR}
    assert score_itinerary(short, UserProfile(days=1), pois)["scores"]["transport"] == 100
    result = score_itinerary(long, UserProfile(days=1), pois)
    assert result["scores"]["transport"] < 50
    assert any("单程乘车约 90 分钟" in message for message in _issues(result, "transport"))


def test_late_lunch_and_idle_gap_are_reported():
    plan = Itinerary(days=[DayPlan(day=1, items=[
        _visit(NEAR, 570, 630), _visit(NEXT, 780, 840),
        Item(kind="meal", ref_id=None, name="餐厅", start=870, end=930, meal="lunch")])])
    result = score_itinerary(plan, UserProfile(days=1, include_evening=False), {1: NEAR, 2: NEXT})
    assert any("午餐 14:30" in message for message in _issues(result, "timing"))
    assert any("累计空档" in message for message in _issues(result, "slack"))
