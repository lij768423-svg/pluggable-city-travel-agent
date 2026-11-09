"""E2 消融开关（docs/评测协议v2.md 第 2 节，2026-11-08 用户：“提前做 E2”）。"""
from types import SimpleNamespace

import pytest

from app import graph, planning_quality
from app.data import load_pois
from app.graph import MAX_ROUNDS, _after_validate, run_plan
from app.nodes import schedule as schedule_module
from app.nodes.retrieve import POPULARITY_WEIGHT, needs_landmark, popularity_bonus, use_popularity
from app.nodes.schedule import split_days
from app.poi_semantics import evening_only
from app.rules import walking_limit
from app.schemas import UserProfile
from app.travel import Travel
from eval.ablation import E2_METHODS
from eval.fair_compare import PLAN_KWARGS
from eval.objective_metrics import daily_walk_limit_m, system_walk_limit_m
from tests.conftest import needs_data

FORM = {"days": 2, "interests": ["人文历史"], "budget": 3000, "pace": "normal"}
DEFAULTS = {"select_mode": "llm", "enable_repair": True, "enable_refine": False, "use_solver": True, "travel_source": None,
            "day_split": "area", "max_repair_rounds": MAX_ROUNDS, "popularity": "tiered", "guide_mode": "off"}
FILL_REASON = "补充在当天可用时间内"


def effective(kwargs: dict) -> dict:
    """把 run_plan 参数换成实际生效的各项开关（空档补点不传时跟随修复开关）。"""
    out = {**DEFAULTS, **kwargs}
    out["enable_gap_fill"] = out["enable_repair"] if kwargs.get("enable_gap_fill") is None else kwargs["enable_gap_fill"]
    out["repair_loop"] = out.pop("enable_repair")
    return out


def test_each_e2_arm_changes_one_switch_except_c():
    base = effective(PLAN_KWARGS["B"])
    for method in E2_METHODS:
        if method == "B":
            continue
        changed = {key for key, value in effective(PLAN_KWARGS[method]).items() if base[key] != value}
        assert len(changed) == (2 if method == "C" else 1), (method, changed)
    assert effective(PLAN_KWARGS["C"])["enable_gap_fill"] is False  # C 两个一起关，与协议第 2 节一致


@needs_data
@pytest.mark.parametrize("kwargs, expected", [({}, True), ({"enable_repair": False}, False),
                                              ({"enable_repair": False, "enable_gap_fill": True}, True),
                                              ({"enable_gap_fill": False}, False)])
def test_gap_fill_switch_reaches_the_meals_step(mock_llm, monkeypatch, kwargs, expected):
    seen = []
    real = graph.meals_node

    def spy(state):
        seen.append(state.get("enable_gap_fill"))
        return real(state)
    monkeypatch.setattr(graph, "meals_node", spy)
    monkeypatch.setattr(graph, "_GRAPH", None)
    result = run_plan("", FORM, llm=mock_llm, select_mode="rule", **kwargs)
    assert seen and set(seen) == {expected}
    if not expected:
        assert not any((item.reason or "").startswith(FILL_REASON)
                       for day in result["itinerary"].days for item in day.items if item.kind == "poi")


@pytest.mark.parametrize("limit", [1, 3, 5])
def test_repair_stops_at_the_round_limit(limit):
    report = SimpleNamespace(passed=False, experience_status="complete")
    state = {"report": report, "enable_repair": True, "max_repair_rounds": limit}
    assert _after_validate({**state, "repair_round": limit - 1}) == "repair"
    assert _after_validate({**state, "repair_round": limit}) != "repair"


def test_round_limit_defaults_to_three():
    report = SimpleNamespace(passed=False, experience_status="complete")
    assert MAX_ROUNDS == 3
    assert _after_validate({"report": report, "enable_repair": True, "repair_round": 2}) == "repair"
    assert _after_validate({"report": report, "enable_repair": True, "repair_round": 3}) != "repair"


@pytest.mark.parametrize("kwargs", [{"day_split": "zone"}, {"popularity": "loud"}, {"max_repair_rounds": 9},
                                    {"max_repair_rounds": -1}])
def test_unknown_switch_values_are_rejected(kwargs):
    with pytest.raises(ValueError):
        run_plan("", FORM, select_mode="rule", **kwargs)


@needs_data
def test_run_plan_keeps_within_one_repair_round(mock_llm):
    result = run_plan("", {**FORM, "companions": "elderly", "budget": 400}, llm=mock_llm, select_mode="rule", max_repair_rounds=1)
    assert result.get("repair_round", 0) <= 1


class Areas:
    """假片区：只用来证明按容量分天不看片区。"""

    def __init__(self, label):
        self.label = label

    def area_of(self, identifier):
        return self.label(identifier)

    def areas_of(self, identifiers):
        return list(dict.fromkeys(self.area_of(identifier) for identifier in identifiers))

    def home_areas(self, lat, lon):
        return set()

    def compatible(self, first, second, home=None):
        return first == second


@needs_data
def test_capacity_split_does_not_depend_on_areas(monkeypatch):
    pois = load_pois()
    ids = [identifier for identifier, place in sorted(pois.items())
           if place.recommendable and not place.day_anchor and not evening_only(place) and place.visit_role == "attraction"][:9]
    profile = UserProfile(days=3)
    results = []
    for label in (lambda identifier: "same", lambda identifier: f"area{identifier}"):
        fake = Areas(label)
        monkeypatch.setattr(schedule_module, "area_index", lambda *args, fake=fake, **kwargs: fake)
        monkeypatch.setattr(planning_quality, "area_index", lambda *args, fake=fake, **kwargs: fake)
        results.append(split_days(ids, profile, Travel("estimate", profile.transport), day_split="capacity"))
    assert results[0] == results[1]
    assert sorted(identifier for day in results[0] for identifier in day) == sorted(ids)
    assert all(results[0])


@needs_data
def test_popularity_modes():
    # 知名度数据（scripts/build_popularity.py 生成）不进 git，公开版没有；这里自己标一个地标，不依赖数据
    landmark = next(iter(load_pois().values())).model_copy(update={"popularity": "landmark"})
    local, first = UserProfile(days=2, familiarity="local"), UserProfile(days=2)
    assert popularity_bonus(landmark, local) == pytest.approx(POPULARITY_WEIGHT["local"])
    assert not needs_landmark(local) and needs_landmark(first)
    with use_popularity("flat"):  # 不分档：不论熟悉程度都按第一次来
        assert popularity_bonus(landmark, local) == pytest.approx(POPULARITY_WEIGHT["first"])
        assert needs_landmark(local)
    with use_popularity("off"):  # 去掉知名度项：不加分，也不做首访地标保证
        assert popularity_bonus(landmark, first) == 0
        assert not needs_landmark(first)
    assert popularity_bonus(landmark, local) == pytest.approx(POPULARITY_WEIGHT["local"])  # 出了 with 恢复


@needs_data
def test_run_plan_sets_popularity_for_that_run_only(mock_llm):
    off = run_plan("", FORM, llm=mock_llm, select_mode="rule", popularity="off")
    assert all(candidate.breakdown.get("popularity", 0) == 0 for candidate in off["candidates"])
    if not any(place.popularity for place in load_pois().values()):
        pytest.skip("没有知名度数据（scripts/build_popularity.py 生成，不进 git）")
    on = run_plan("", FORM, llm=mock_llm, select_mode="rule")
    assert any(candidate.breakdown.get("popularity", 0) > 0 for candidate in on["candidates"])


@pytest.mark.parametrize("companions", ["solo", "couple", "friends", "family_kids", "elderly"])
@pytest.mark.parametrize("max_walking_m", [None, 1000, 3000, 12000])
def test_judge_walking_limits_match_the_system(companions, max_walking_m):
    """J2 的 U5（每日步行）和 J2′（单段步行）为保持独立，没有 import 系统代码，阈值是抄过去的。
    这里核对两边一致：改了其中一边就会失败，要同时改另一边，并在评测协议的变更记录里写一笔。"""
    profile = UserProfile(days=1, companions=companions, max_walking_m=max_walking_m)
    assert daily_walk_limit_m(profile) == walking_limit(profile)
    travel = Travel("estimate", profile.transport)
    travel.set_day(profile, 1)
    assert system_walk_limit_m(profile) == travel.walk_limit_m
