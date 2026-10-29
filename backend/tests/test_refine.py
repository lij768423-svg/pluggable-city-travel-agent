import sys
from pathlib import Path

from app.graph import _after_repair, _after_validate
from app.reasonableness import score_itinerary
from app.data import load_pois, load_restaurants
from tests.conftest import needs_data

pytestmark = needs_data
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "eval"))


class _Report:
    passed, experience_status = True, "complete"


def test_refine_runs_once_after_repair_only_when_enabled():
    done = {"report": _Report(), "repair_round": 0}
    assert _after_validate(done) == "__end__"
    assert _after_validate({**done, "enable_refine": True}) == "refine"
    assert _after_validate({**done, "enable_refine": True, "refined": True}) == "__end__"
    assert _after_repair({"repair_changed": False, "enable_refine": True}) == "refine"


def test_refine_fills_idle_day_without_new_hard_violations():
    from refine_eval import run
    from route_quality import cases
    # 2026-10-28 选餐计入车费后 west-lake-walk-normal 的精修改为调整顺序（综合分仍升 1 分），补点用例换成同组的 intense
    case = next(case for case in cases() if case["id"] == "west-lake-walk-intense")
    row = run(case)
    assert row["after"]["overall"] >= row["before"]["overall"] + 1
    assert row["after"]["hard"] <= row["before"]["hard"] and row["after"]["missing_meals"] <= row["before"]["missing_meals"]
    assert row["after"]["visits"] > row["before"]["visits"] and any("补入" in line for line in row["log"])


def test_refine_leaves_good_plan_alone():
    from refine_eval import run
    from route_quality import cases
    row = run(next(case for case in cases() if case["id"] == "park-day-normal"))
    assert row["after"] == row["before"] and "采纳 0 处修改" in row["log"][0]


def test_refine_moves_evening_market_out_of_the_morning():
    from app.data import load_restaurants
    from app.nodes.meals import build_itinerary
    from app.nodes.retrieve import retrieve
    from app.nodes.validate import validate_itinerary
    from app.refine import refine
    from app.schemas import Selection, UserProfile
    from app.travel import Travel
    pois = load_pois()
    market = next(identifier for identifier, place in pois.items() if place.name == "武林夜市")
    others = [next(identifier for identifier, place in pois.items() if place.name == name) for name in ("杭州孔庙", "钱王祠")]
    profile = UserProfile(days=1, pace="normal", transport="transit", include_evening=False)
    route = Travel("estimate", profile.transport)
    orders = [[market, *others]]
    selected = [Selection(poi_id=identifier, reason="测试", source="rule") for identifier in orders[0]]
    itinerary = build_itinerary(orders, profile, selected, 1.0, route, live=False)
    assert itinerary.days[0].items[0].kind == "meal" or itinerary.days[0].items[0].start >= pois[market].open_min
    report = validate_itinerary(itinerary, profile, travel_fn=route.leg, restaurants=load_restaurants())
    result = refine({"profile": profile, "travel": route, "catalog": None, "candidates": retrieve(profile), "day_orders": orders,
                     "selected": selected, "itinerary": itinerary, "report": report, "dropped": []})
    first = next(item for item in result["itinerary"].days[0].items if item.kind == "poi")
    assert first.ref_id != market and any("调整游览顺序" in line for line in result["log"])
    assert result["refine_scores"]["after"] >= result["refine_scores"]["before"] + 1
