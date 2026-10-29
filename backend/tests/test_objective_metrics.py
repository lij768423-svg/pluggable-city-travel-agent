"""J2 客观路线指标：独立性约束与各项计算口径（docs/评测协议v2.md 第 5 节）。"""
import ast
from pathlib import Path

from app.schemas import DayPlan, Hotel, Item, Itinerary, POI, Restaurant, UserProfile
from eval.objective_metrics import PROTOCOL_WALK_M, objective_metrics, snapshot_leg, system_walk_limit_m

FORBIDDEN = ("app.nodes", "app.rules", "app.areas", "app.planning_quality", "app.reasonableness", "app.refine")


def test_module_does_not_import_planning_logic():
    tree = ast.parse((Path(__file__).resolve().parents[1] / "eval" / "objective_metrics.py").read_text(encoding="utf-8"))
    imported = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module}
    imported |= {alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names}
    assert not [name for name in imported if name.startswith(FORBIDDEN)], imported


def place(identifier, lat=30.25, lon=120.15, **values):
    return POI(id=identifier, name=f"景点{identifier}", type="公园", lat=lat, lon=lon,
               **{"open_min": 480, "close_min": 1080, "price": 20, "min_hours": 1, "max_hours": 2, **values})


def restaurant(identifier, lat=30.252, lon=120.152):
    return Restaurant(id=identifier, name=f"餐厅{identifier}", lat=lat, lon=lon, price=50, cuisine="杭帮菜",
                      open_min=600, close_min=1260)


POIS = {1: place(1), 2: place(2, lat=30.27), 3: place(3, close_min=600)}
RESTAURANTS = {10: restaurant(10), 11: restaurant(11)}
PROFILE = UserProfile(days=1, budget=1000, transport="taxi", hotel=Hotel(lat=30.24, lon=120.14))


def day(*items):
    return Itinerary(days=[DayPlan(day=1, items=list(items))])


def poi(identifier, start, end):
    return Item(kind="poi", ref_id=identifier, name=f"景点{identifier}", start=start, end=end)


def meal(identifier, start, end):
    return Item(kind="meal", ref_id=identifier, name=f"餐厅{identifier}", start=start, end=end,
                meal="lunch" if start < 900 else "dinner")


def walking(minutes):
    return lambda *coordinates: {"walk": {"m": minutes * 75, "min": minutes}}


GOOD = day(poi(1, 570, 690), meal(10, 720, 780), poi(2, 800, 920), meal(11, 1080, 1140))


def test_feasible_day_has_no_conflicts():
    result = objective_metrics(GOOD, PROFILE, walking(10), POIS, RESTAURANTS)
    assert result["O3_time_conflicts"] == 0 and result["O4_opening_conflicts"] == 0
    assert result["O9_meal_coverage"] == 1.0 and result["O11_pois_per_day"] == 2
    assert result["O1_travel_min_per_day"] == 50  # 起点→景点1→餐厅→景点2→餐厅→起点，共 5 段
    assert result["O5_idle_min_per_day"] == 120  # 晚餐前 160 分钟间隔扣 10 分钟路程和 30 分钟免计，另外两段各 0
    assert result["O10_budget_deviation"] == round((20 + 50 + 20 + 50) / 1000 - 1, 3)
    assert result["objective_feasible"] is True


def test_time_conflict_uses_snapshot_minutes_not_claimed_times():
    tight = day(poi(1, 570, 690), meal(10, 695, 760), poi(2, 780, 900), meal(11, 1080, 1140))
    result = objective_metrics(tight, PROFILE, walking(20), POIS, RESTAURANTS)
    assert result["O3_time_conflicts"] == 1  # 5 分钟间隔小于 20 − 5；20 分钟间隔不算冲突
    assert result["objective_feasible"] is False


def test_opening_hours_short_visit_and_duplicates():
    bad = day(poi(3, 570, 640), meal(10, 720, 780), poi(1, 800, 830), poi(1, 840, 900), meal(11, 1080, 1140))
    result = objective_metrics(bad, PROFILE, walking(5), POIS, RESTAURANTS)
    assert result["O4_opening_conflicts"] == 1  # 景点 3 10:00 关门
    assert result["O8_short_visit_rate"] == round(1 / 3, 3)  # 景点 1 第一次只停 30 分钟
    assert result["O12_duplicate_pois"] == 1 and result["objective_feasible"] is False


def test_unknown_place_and_missing_meal():
    plan = day(Item(kind="poi", ref_id=None, name="库外地点", start=570, end=690), meal(10, 720, 780))
    result = objective_metrics(plan, PROFILE, walking(5), POIS, RESTAURANTS)
    assert result["unknown_items"] == 1 and result["O9_meal_coverage"] == 0.5
    assert result["objective_feasible"] is False


def test_missing_leg_makes_plan_unjudgeable():
    result = objective_metrics(GOOD, PROFILE, lambda *coordinates: None, POIS, RESTAURANTS)
    assert result["missing_legs"] == 5 and result["objective_feasible"] is None


def test_taxi_and_transit_follow_raw_amap_results():
    detail = {"walk": {"m": None, "min": None}, "drive": {"min": 15}, "transit": {"min": 40, "cost": 3, "taxi_cost": 28}}
    taxi = objective_metrics(GOOD, PROFILE, lambda *c: detail, POIS, RESTAURANTS)
    transit = objective_metrics(GOOD, PROFILE.model_copy(update={"transport": "transit", "allow_taxi_fallback": False}),
                                lambda *c: detail, POIS, RESTAURANTS)
    assert taxi["O2_max_leg_min"] == 20 and transit["O2_max_leg_min"] == 40
    assert taxi["O10_budget_deviation"] == round((140 + 5 * 28) / 1000 - 1, 3)
    assert transit["O10_budget_deviation"] == round((140 + 5 * 3) / 1000 - 1, 3)


def test_user_window_controls_expected_meals():
    lunch_only = PROFILE.model_copy(update={"day_start": 540, "day_end": 900})
    plan = day(poi(1, 570, 690), meal(10, 720, 780), poi(2, 800, 880))
    assert objective_metrics(plan, lunch_only, walking(5), POIS, RESTAURANTS)["O9_meal_coverage"] == 1.0


def test_system_walk_limit_follows_companions_and_user_cap():
    """J2′ 敏感性口径：成人 2.5 km、亲子与老人 1.5 km，用户自填上限时取更小值；主口径恒为 1.5 km。"""
    assert PROTOCOL_WALK_M == 1500
    assert system_walk_limit_m(PROFILE.model_copy(update={"companions": "couple"})) == 2500
    assert system_walk_limit_m(PROFILE.model_copy(update={"companions": "elderly"})) == 1500
    assert system_walk_limit_m(PROFILE.model_copy(update={"companions": "family_kids"})) == 1500
    assert system_walk_limit_m(PROFILE.model_copy(update={"companions": "friends", "max_walking_m": 1000})) == 1000
    assert system_walk_limit_m(PROFILE.model_copy(update={"companions": "solo", "max_walking_m": 4000})) == 2500


class TwoKmRouter:
    """每段实际步行 2 km：步行上限 1.5 km 时走驾车，2.5 km 时走步行（模拟 AmapRouter.leg_detail 对 walk_limit_m 的依赖）。"""
    walk_limit_m = 1500

    def leg_detail(self, lat1, lon1, lat2, lon2):
        if 2000 <= self.walk_limit_m:
            return {"walk": {"m": 2000, "min": 2000 / 75}}
        return {"walk": {"m": None, "min": None}, "drive": {"min": 8}, "transit": {"min": 30, "cost": 2, "taxi_cost": 15}}


def test_walk_limit_is_set_on_a_copy_only():
    router = TwoKmRouter()
    assert snapshot_leg(router)(0, 0, 1, 1)["drive"]["min"] == 8
    assert snapshot_leg(router, 2500)(0, 0, 1, 1)["walk"]["m"] == 2000
    assert router.walk_limit_m == 1500


def test_main_and_sensitivity_walk_limits_can_disagree():
    router = TwoKmRouter()
    adult = PROFILE.model_copy(update={"companions": "couple"})
    main = objective_metrics(GOOD, adult, snapshot_leg(router), POIS, RESTAURANTS)
    sensitivity = objective_metrics(GOOD, adult, snapshot_leg(router, system_walk_limit_m(adult)), POIS, RESTAURANTS)
    assert main["O3_time_conflicts"] == 0 and main["O2_max_leg_min"] == 13  # 打车 8 + 5
    assert sensitivity["O3_time_conflicts"] == 1  # 20 分钟间隔放不下约 27 分钟步行
    assert main["objective_feasible"] is True and sensitivity["objective_feasible"] is False


def test_transit_user_with_taxi_fallback_takes_the_faster_mode():
    """公交用户允许打车兜底时取公交与打车中较快者（dev-12 r1 中 B 把慢公交改打车被 J2 误记为冲突）。"""
    detail = {"walk": {"m": None, "min": None}, "drive": {"min": 15}, "transit": {"min": 40, "cost": 3, "taxi_cost": 28}}
    strict = PROFILE.model_copy(update={"transport": "transit", "allow_taxi_fallback": False})
    fallback = strict.model_copy(update={"allow_taxi_fallback": True})
    assert objective_metrics(GOOD, strict, lambda *c: detail, POIS, RESTAURANTS)["O2_max_leg_min"] == 40
    lenient = objective_metrics(GOOD, fallback, lambda *c: detail, POIS, RESTAURANTS)
    assert lenient["O2_max_leg_min"] == 20 and lenient["O10_budget_deviation"] == round((140 + 5 * 28) / 1000 - 1, 3)
    slow_taxi = {**detail, "drive": {"min": 50}}
    assert objective_metrics(GOOD, fallback, lambda *c: slow_taxi, POIS, RESTAURANTS)["O2_max_leg_min"] == 40  # 打车更慢时仍用公交


def test_onsite_reserved_meal_counts_only_inside_that_park_visit():
    from app.schemas import ReservedMeal

    def plan(slot, visit_end=900):
        return Itinerary(days=[DayPlan(day=1, items=[poi(1, 570, visit_end), meal(11, 1080, 1140)], reserved_meals=[slot])])

    inside = ReservedMeal(meal="lunch", start=690, end=750, reason="园内用餐", inside_poi_id=1, estimated_cost=40)
    counted = objective_metrics(plan(inside), PROFILE, walking(5), POIS, RESTAURANTS)
    assert counted["O9_meal_coverage"] == 1.0 and counted["onsite_meals"] == 1
    assert counted["O10_budget_deviation"] == round((20 + 40 + 50) / 1000 - 1, 3)
    other_park = inside.model_copy(update={"inside_poi_id": 2})  # 当天没去这个景点
    not_onsite = inside.model_copy(update={"inside_poi_id": None})  # 普通的待定餐位
    for slot in (other_park, not_onsite):
        assert objective_metrics(plan(slot), PROFILE, walking(5), POIS, RESTAURANTS)["O9_meal_coverage"] == 0.5
    left_park = objective_metrics(plan(inside, visit_end=680), PROFILE, walking(5), POIS, RESTAURANTS)  # 时段不在游览时间内
    assert left_park["O9_meal_coverage"] == 0.5 and left_park["onsite_meals"] == 0
    assert left_park["objective_feasible_no_onsite"] is False


def test_onsite_meal_has_two_reported_views():
    """园内用餐两种口径并列：计入时可行，不计入时那一餐算缺（A′、R 的输出格式表达不了园内用餐）。"""
    from app.schemas import ReservedMeal
    slot = ReservedMeal(meal="lunch", start=690, end=750, reason="园内用餐", inside_poi_id=1, estimated_cost=40)
    plan = Itinerary(days=[DayPlan(day=1, items=[poi(1, 570, 900), meal(11, 1080, 1140)], reserved_meals=[slot])])
    result = objective_metrics(plan, PROFILE, walking(5), POIS, RESTAURANTS)
    assert result["objective_feasible"] is True and result["O9_meal_coverage_no_onsite"] == 0.5
    assert result["objective_feasible_no_onsite"] is False
    assert result["constraint_feasible"] is True and result["constraint_feasible_no_onsite"] is False


def test_good_day_passes_every_user_constraint():
    result = objective_metrics(GOOD, PROFILE, walking(10), POIS, RESTAURANTS)
    assert result["constraints_ok"] is True and result["constraint_feasible"] is True
    assert result["constraint_details"] == [] and result["max_daily_walk_m"] == 5 * 10 * 75


def named_pois():
    return {1: place(1).model_copy(update={"name": "灵隐飞来峰景区"}), 2: place(2, lat=30.27).model_copy(update={"name": "宋城（杭州）"}),
            3: place(3).model_copy(update={"name": "宋城千古情剧院"})}


def test_must_visit_and_avoid_match_names_both_ways():
    pois = named_pois()
    plan = day(poi(1, 570, 690), meal(10, 720, 780), poi(2, 800, 920), meal(11, 1080, 1140))
    must = PROFILE.model_copy(update={"must_visit": ["灵隐飞来峰", "西湖"]})
    result = objective_metrics(plan, must, walking(10), pois, RESTAURANTS)
    assert result["U1_must_missing"] == 1 and "U1 必去「西湖」未安排" in result["constraint_details"]
    assert result["objective_feasible"] is True and result["constraint_feasible"] is False
    avoid = PROFILE.model_copy(update={"avoid": ["宋城"]})
    hit = objective_metrics(plan, avoid, walking(10), pois, RESTAURANTS)
    assert hit["U2_avoid_visited"] == 1 and hit["constraint_feasible"] is False
    # 库外地点也按名称判断：纯大模型写了"灵隐寺"但不在目录里，仍算排了必去
    unknown = day(Item(kind="poi", ref_id=None, name="灵隐寺", start=570, end=690), meal(10, 720, 780))
    assert objective_metrics(unknown, PROFILE.model_copy(update={"must_visit": ["灵隐寺"]}), walking(5), pois, RESTAURANTS)["U1_must_missing"] == 0


def cuisine(identifier, name, kind):
    return Restaurant(id=identifier, name=name, lat=30.252, lon=120.152, price=50, cuisine=kind, open_min=600, close_min=1260)


def test_only_no_spicy_is_a_hard_taste_constraint():
    from types import SimpleNamespace
    from eval.objective_metrics import no_spicy
    plan = day(poi(1, 570, 690), meal(20, 720, 780), poi(2, 800, 920), meal(21, 1080, 1140))
    spicy = {20: cuisine(20, "蜀地小馆", "川菜"), 21: cuisine(21, "外婆家", "杭帮菜")}
    result = objective_metrics(plan, PROFILE.model_copy(update={"spice": "no"}), walking(10), POIS, spicy)
    assert result["U3_spice_conflicts"] == 1 and result["constraint_feasible"] is False
    assert "川菜" in result["constraint_details"][0] and "不吃辣" in result["constraint_details"][0]
    for spice in ("any", "love"):  # 微辣都行、爱吃辣只是偏好，不算冲突
        relaxed = objective_metrics(plan, PROFILE.model_copy(update={"spice": spice}), walking(10), POIS, spicy)
        assert relaxed["U3_spice_conflicts"] == 0 and relaxed["constraint_feasible"] is True
    # 2026-10-26 以前的旧画像没有 spice、只有 dietary：重算旧运行时照样认出"不吃辣"
    assert no_spicy(SimpleNamespace(dietary=["不吃辣", "清真"])) and not no_spicy(SimpleNamespace(dietary=["清真"]))


def test_window_violation_and_return_home_when_user_gave_an_end():
    late = day(poi(1, 570, 690), meal(10, 720, 780), poi(2, 800, 920), meal(11, 1260, 1320))  # 晚餐 21:00–22:00
    result = objective_metrics(late, PROFILE, walking(10), POIS, RESTAURANTS)
    assert result["U4_window_violations"] == 1  # 默认时段到 21:30
    ends_at_seven = PROFILE.model_copy(update={"day_end": 1140})
    home = objective_metrics(GOOD, ends_at_seven, walking(10), POIS, RESTAURANTS)  # 19:00 吃完，返程 10 分钟
    assert home["U4_window_violations"] == 1 and any("返程" in line for line in home["constraint_details"])
    assert objective_metrics(GOOD, PROFILE, walking(10), POIS, RESTAURANTS)["U4_window_violations"] == 0  # 没填结束时间不查返程


def test_daily_walking_counts_walk_legs_and_transit_walking():
    elderly = PROFILE.model_copy(update={"companions": "elderly"})
    far = objective_metrics(GOOD, elderly, walking(12), POIS, RESTAURANTS)  # 5 段 × 900 m = 4.5 km > 3.5 km
    assert far["U5_walk_days_over"] == 1 and far["daily_walk_limit_m"] == 3500 and far["constraint_feasible"] is False
    bus = {"walk": {"m": None, "min": None}, "drive": {"min": 15}, "transit": {"min": 30, "cost": 2, "walk_m": 800, "taxi_cost": 25}}
    transit = elderly.model_copy(update={"transport": "transit", "allow_taxi_fallback": False})
    assert objective_metrics(GOOD, transit, lambda *c: bus, POIS, RESTAURANTS)["max_daily_walk_m"] == 5 * 800
    taxi = objective_metrics(GOOD, elderly, lambda *c: bus, POIS, RESTAURANTS)
    assert taxi["max_daily_walk_m"] == 0 and taxi["U5_walk_days_over"] == 0
    capped = PROFILE.model_copy(update={"max_walking_m": 2000})
    assert objective_metrics(GOOD, capped, walking(10), POIS, RESTAURANTS)["U5_walk_days_over"] == 1  # 用户自填上限优先


class ShortLegRouter:
    """每段实际步行 1 km（步行可达）；驾车 4 分钟，公交 12 分钟（含步行 300 m），打车估价 14 元。"""
    walk_limit_m = 1500

    def leg_detail(self, lat1, lon1, lat2, lon2):
        return {"walk": {"m": 1000, "min": 1000 / 75}}

    def drive(self, o, d):
        return {"m": 1200, "min": 4}

    def transit(self, o, d):
        return {"min": 12, "cost": 2, "walk_m": 300, "taxi_cost": 14}


def stated(plan, mode):
    """把行程每一段（含返程）都标成 mode。"""
    from app.schemas import LegEvidence
    days = [d.model_copy(update={"items": [i.model_copy(update={"travel_mode": mode}) for i in d.items],
                                 "return_leg": LegEvidence(minutes=5, mode=mode, cost=0, source="amap")}) for d in plan.days]
    return Itinerary(days=days)


def test_stated_taxi_legs_count_as_taxi_not_walking():
    """2026-10-26：修复环节把老人步行过多的短段改成打车，裁判不能再按步行可达算回走路（dev-12 c04、c12 的 U5 误报）。"""
    elderly = PROFILE.model_copy(update={"companions": "elderly"})
    router = ShortLegRouter()
    unstated = objective_metrics(GOOD, elderly, snapshot_leg(router), POIS, RESTAURANTS)
    assert unstated["max_daily_walk_m"] == 5000 and unstated["U5_walk_days_over"] == 1
    taxi = objective_metrics(stated(GOOD, "打车"), elderly, snapshot_leg(router), POIS, RESTAURANTS)
    assert taxi["max_daily_walk_m"] == 0 and taxi["U5_walk_days_over"] == 0 and taxi["constraint_feasible"] is True
    assert taxi["O2_max_leg_min"] == 9 and taxi["O1_travel_min_per_day"] == 45  # 驾车 4 + 叫车 5，共 5 段
    assert taxi["O10_budget_deviation"] == round((140 + 5 * 14) / 1000 - 1, 3)
    bus = objective_metrics(stated(GOOD, "公交/地铁"), elderly, snapshot_leg(router), POIS, RESTAURANTS)
    assert bus["max_daily_walk_m"] == 5 * 300 and bus["O2_max_leg_min"] == 12
    walk = objective_metrics(stated(GOOD, "步行"), elderly, snapshot_leg(router), POIS, RESTAURANTS)
    assert walk == unstated  # 标明步行与没标明一样，仍按协议规则


def test_stated_mode_is_ignored_right_after_an_unknown_place():
    """紧跟库外地点的一站，标明的方式说的是从库外地点出发的那段，不能套到从上一个已知地点出发的路段上。"""
    elderly = PROFILE.model_copy(update={"companions": "elderly"})
    unknown = Item(kind="poi", ref_id=None, name="库外地点", start=540, end=560)
    plan = stated(day(unknown, poi(1, 570, 690)), "打车")
    result = objective_metrics(plan, elderly, snapshot_leg(ShortLegRouter()), POIS, RESTAURANTS)
    assert result["max_daily_walk_m"] == 1000  # 起点→景点 1 按步行；返程标明打车照用


def test_stated_ride_without_route_data_is_a_missing_leg():
    class Offline(ShortLegRouter):
        def drive(self, o, d):
            raise RuntimeError("离线快照缺少驾车路段")
    result = objective_metrics(stated(GOOD, "打车"), PROFILE, snapshot_leg(Offline()), POIS, RESTAURANTS)
    assert result["missing_legs"] == 5 and result["objective_feasible"] is None


def test_transit_is_judged_at_each_leg_departure(monkeypatch):
    """J2 按行程项记下的出发时刻、冻结的参考日查公交，与系统查的是同一个快照键。"""
    from datetime import date

    class Timed(ShortLegRouter):
        def __init__(self):
            self.seen = []

        def leg_detail(self, lat1, lon1, lat2, lon2):
            self.seen.append((self.service_date, self.departure_time))
            return {"walk": {"m": None, "min": None}, "drive": {"min": 10}, "transit": {"min": 20, "cost": 2, "walk_m": 200}}

    monkeypatch.setenv("TTA_REFERENCE_DATE", "2026-10-29")
    router = Timed()
    plan = Itinerary(days=[DayPlan(day=1, return_depart=1150, items=[
        poi(1, 570, 690).model_copy(update={"travel_depart": 540}), meal(10, 720, 780).model_copy(update={"travel_depart": 690}),
        poi(2, 800, 920).model_copy(update={"travel_depart": 780}), meal(11, 1080, 1140).model_copy(update={"travel_depart": 1050})])])
    objective_metrics(plan, PROFILE.model_copy(update={"transport": "transit"}), snapshot_leg(router), POIS, RESTAURANTS)
    assert router.seen == [(date(2026, 10, 29), slot) for slot in ("09:00", "11:30", "13:00", "17:30", "19:00")]


def test_judge_reads_the_methods_own_snapshot_first(tmp_path):
    """裁判先读该方法生成时的高德记录，再读用例裁判快照；补查的路段只并回裁判快照，不带上别的方法的生成记录。"""
    from types import SimpleNamespace
    from eval.fair_compare import judge_view, merge_judge, pinned_reference
    judge = SimpleNamespace(cache={"drive|a|b": {"min": 30}, "walk|a|c": {"m": 900}}, records={"drive|a|b": {}, "walk|a|c": {}})
    router = SimpleNamespace(cache={"drive|a|b": {"min": 22}}, records={"drive|a|b": {"fetched_at": "生成时"}})
    view = judge_view(judge, router)
    assert view.cache["drive|a|b"] == {"min": 22} and view.cache["walk|a|c"] == {"m": 900}
    view.cache["transit|a|b"], view.records["transit|a|b"] = {"min": 40}, {}
    merge_judge(judge, view, router)
    assert judge.cache["drive|a|b"] == {"min": 30} and judge.cache["transit|a|b"] == {"min": 40}
    # 参考日：首次采集取两天后起的第一个工作日；已有设计沿用；没有设计的重算不设
    import json
    from datetime import date
    first = date.fromisoformat(pinned_reference(tmp_path, "capture"))
    assert first.weekday() < 5 and (first - date.today()).days >= 1
    (tmp_path / "design.json").write_text(json.dumps({"reference_date": "2026-10-29"}), encoding="utf-8")
    assert pinned_reference(tmp_path, "replay") == "2026-10-29"
    assert pinned_reference(tmp_path / "missing", "rescore") is None
    import os
    os.environ["TTA_REFERENCE_DATE"] = "2026-10-30"
    try:
        assert pinned_reference(tmp_path / "fresh", "capture") == "2026-10-30"  # 几次重复采集显式给同一天
        assert pinned_reference(tmp_path, "capture") == "2026-10-29"  # 已有设计仍以设计为准
    finally:
        del os.environ["TTA_REFERENCE_DATE"]


def test_j1_rechecks_stated_taxi_legs_as_taxi():
    from eval.fair_compare import stated_taxi_pairs
    unknown = Item(kind="poi", ref_id=None, name="库外地点", start=700, end=720)
    plan = stated(day(poi(1, 570, 690), unknown, meal(10, 730, 790)), "打车")
    home, first, lunch = (30.24, 120.14), (30.25, 120.15), (30.252, 120.152)
    assert stated_taxi_pairs(plan, PROFILE, POIS, RESTAURANTS) == {(*home, *first), (*lunch, *home)}  # 库外地点之后那段不采用
