"""两处必去相距太远（2026-10-29）：之间单段乘车超过 45 分钟封顶，排不进同一天。

不放宽封顶（2026-10-18 用户决定）；规划前在确认表提示并给一键修改，规划后漏排的必去写明原因，修复不再为它白删别的景点。
"""
from datetime import date
import json

from app.data import load_pois
from app.graph import run_plan
from app.llm import MockLLM
from app.nodes.validate import far_must_partner
from app.understanding import understand

WEDNESDAY = date(2026, 10, 28)


def by_name(name: str) -> int:
    return next(identifier for identifier, place in load_pois().items() if place.name == name)


def issues(**form) -> dict:
    result = understand("", {"city": "hangzhou", **form}, llm=MockLLM({"parse": json.dumps({})}), today=WEDNESDAY)
    return {item.code: item for item in result.issues}


def plan(days: int, must: list[str]) -> dict:
    form = {"city": "hangzhou", "days": days, "pace": "normal", "transport": "transit", "must_visit": must, "origin_known": False}
    return run_plan("", form, llm=MockLLM({}), select_mode="rule", travel_source="estimate")


def test_far_apart_must_visits_warn_before_planning_and_offer_another_day():
    # 灵隐与湘湖直线约 16 公里：估算约 61 分钟，打车也超过 45 分钟
    issue = issues(days=1, transport="transit", must_visit=["灵隐寺", "湘湖"])["must_far_apart"]
    assert issue.level == "warning"
    assert "「灵隐寺」和「湘湖」" in issue.message and "45 分钟" in issue.message
    assert [fix.patch for fix in issue.fixes] == [{"days": 2}]
    assert "must_far_apart" not in issues(days=2, transport="transit", must_visit=["灵隐寺", "湘湖"])


def test_taxi_is_offered_only_when_it_brings_the_ride_under_the_cap():
    # 湘湖与杭州动物园：公交估计 48 分钟以上，打车约 41 分钟
    issue = issues(days=1, transport="transit", must_visit=["湘湖", "杭州动物园"])["must_far_apart"]
    assert [fix.patch for fix in issue.fixes] == [{"days": 2}, {"transport": "taxi"}]
    assert "改打车" in issue.message
    assert "must_far_apart" not in issues(days=1, transport="taxi", must_visit=["湘湖", "杭州动物园"])


def test_near_must_visits_are_not_flagged():
    assert "must_far_apart" not in issues(days=1, transport="transit", must_visit=["灵隐寺", "杭州动物园"])


def test_partner_is_reported_only_when_every_day_already_has_a_far_must_visit():
    lingyin, xianghu = by_name("灵隐飞来峰景区"), by_name("湘湖")
    must = {lingyin, xianghu}
    far = lambda *points: (61, "公交/地铁", 2.0)
    assert far_must_partner(xianghu, [[lingyin]], must, far) == (lingyin, 61, "公交/地铁")
    assert far_must_partner(xianghu, [[lingyin], []], must, far) is None  # 另一天还空着，不是封顶的问题
    assert far_must_partner(xianghu, [[lingyin]], must, lambda *points: (40, "公交/地铁", 2.0)) is None


def test_one_day_far_must_visit_says_why_and_repair_does_not_delete_for_it():
    out = plan(1, ["灵隐寺", "湘湖"])
    missing = [violation for violation in out["report"].violations if violation.code == "must_visit_missing"]
    assert len(missing) == 1
    message = missing[0].message
    assert "灵隐寺" in message and "湘湖" in message and "45 分钟" in message and "排不进同一天" in message
    assert not any("腾出时间" in line for line in out["log"])
    assert any("不再为它删点" in line for line in out["log"])


def test_the_same_pair_fits_on_two_days():
    out = plan(2, ["灵隐寺", "湘湖"])
    assert not any(violation.code == "must_visit_missing" for violation in out["report"].violations)
