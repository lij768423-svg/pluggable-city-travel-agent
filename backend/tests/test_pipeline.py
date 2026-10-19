import itertools

import pytest

from app.graph import run_plan
from app.nodes.validate import FEASIBILITY
from app.schemas import UserProfile
from tests.conftest import needs_data

pytestmark = needs_data

PROFILES = [
    dict(days=d, pace=pace, companions=comp, interests=ints, budget=3000)
    for d, pace, comp, ints in itertools.product(
        [1, 3], ["relaxed", "normal", "intense"], ["solo", "elderly"],
        [["人文历史"], ["自然风光", "拍照出片"], ["亲子游乐", "主题乐园"]])
]


@pytest.mark.parametrize("form", PROFILES, ids=lambda f: f"{f['days']}d-{f['pace']}-{f['companions']}-{'+'.join(f['interests'])}")
def test_rule_pipeline_is_always_feasible(form, mock_llm):
    """性质测试：规则选点 + 调度 + 时间线，在多种画像下都不应出现可行性违例。"""
    r = run_plan("", form, llm=mock_llm, select_mode="rule")
    feas = [v for v in r["report"].violations if v.code in FEASIBILITY or v.code == "interstop_ride_limit"]
    assert not feas, [v.message for v in feas]
    assert len(r["itinerary"].days) == form["days"]
    assert mock_llm.usage.calls == 0  # 只有表单、规则选点：不调用 LLM


def test_llm_pipeline_with_mock(mock_llm):
    r = run_plan("杭州两天，喜欢人文历史", llm=mock_llm)
    assert r["profile"].days == 2 and r["profile"].interests == ["人文历史"]
    assert any(selection.source == "llm" for selection in r["selected"])
    added = [selection for selection in r["selected"] if selection.source == "rule"]
    assert all(selection.reason == "补充在当天可用时间内，结合兴趣与沿途位置安排" for selection in added)
    if added:
        assert any("空档修复" in line for line in r["log"])
    assert r["report"].passed, [v.message for v in r["report"].violations]
    assert mock_llm.usage.by_tag == {"parse": 1, "select": 1}
    pois = [i for d in r["itinerary"].days for i in d.items if i.kind == "poi"]
    assert all(i.reason for i in pois)


def test_repair_handles_tight_budget(mock_llm):
    r = run_plan("", {"days": 2, "budget": 120, "pace": "intense", "interests": ["主题乐园"]}, llm=mock_llm,
                 select_mode="rule")
    assert r["repair_round"] >= 1
    assert any("修复" in line for line in r["log"])


def test_form_overrides_parsed_query(mock_llm):
    r = run_plan("杭州两天", {"days": 4, "pace": "relaxed"}, llm=mock_llm, select_mode="rule")
    assert r["profile"].days == 4 and r["profile"].pace == "relaxed"
    assert isinstance(r["profile"], UserProfile)


def test_must_visit_survives_rule_select_and_repair(mock_llm):
    """回归：c03（特种兵 + 必去灵隐寺/宋城 + 低预算）规则选点时宋城曾被舍弃。"""
    # 与 2026-10-14 评测中 c03 的真实解析画像一致（宋城门票 300 > 预算 25%，被扣分后排不进前 N）
    form = {"days": 2, "budget": 600, "pace": "intense", "interests": ["拍照出片"], "must_visit": ["灵隐寺", "宋城"]}
    r = run_plan("", form, llm=mock_llm, select_mode="rule")
    assert "must_visit_missing" not in {v.code for v in r["report"].violations}, r["log"]


def test_vegetarian_gets_meals_when_possible(mock_llm):
    r = run_plan("", {"days": 2, "interests": ["博物馆展览"], "dietary": ["只吃中餐"], "budget": 2000}, llm=mock_llm,
                 select_mode="rule")
    meals = [i for d in r["itinerary"].days for i in d.items if i.kind == "meal"]
    assert meals, "只吃中餐也应安排到部分正餐（放宽距离后）"


def test_lunch_uses_wait_before_late_opening(mock_llm):
    """回归：c06 中 12:00 才开门的美术馆前空等，没吃午饭。"""
    from app.nodes.meals import build_itinerary
    from app.schemas import Selection, UserProfile
    from app.data import load_pois

    late = next(p for p in load_pois().values() if p.open_min >= 12 * 60 and p.close_min >= 17 * 60 and p.price < 100)
    prof = UserProfile(days=1, pace="relaxed", budget=2000)
    it = build_itinerary([[late.id]], prof, [Selection(poi_id=late.id, reason="x")])
    kinds = [i.kind for i in it.days[0].items]
    assert kinds[0] == "meal", kinds


def test_must_visit_maps_to_single_poi(mock_llm):
    """回归（c09）："西湖"只能对应一个景点，不能把所有名字带"西湖"的景点都当作必去。"""
    from app.nodes.retrieve import must_visit_ids
    from app.schemas import UserProfile
    assert len(must_visit_ids(UserProfile(must_visit=["西湖"]))) == 1
    r = run_plan("", {"days": 1, "pace": "normal", "must_visit": ["西湖"], "interests": ["自然风光"]}, llm=mock_llm,
                 select_mode="rule")
    from app.data import load_pois
    from app.experiences import experience_count
    assert experience_count([selection.poi_id for selection in r["selected"]], load_pois()) <= 4 and r["report"].passed, r["log"]
