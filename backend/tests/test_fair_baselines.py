"""E1 基线 A′ 与 R：输入与本系统一致、编号映射、工具循环与步数上限（docs/评测协议v2.md 第 2 节）。"""
import json
import re

import pytest

from app.llm import MockLLM
from app.nodes.retrieve import retrieve
from app.nodes.select import candidate_lines
from app.schemas import UserProfile
from tests.conftest import needs_data

pytestmark = needs_data
PROFILE = UserProfile(days=1, budget=1500, interests=["人文历史"])


def restaurant_ids(text):
    return [int(x) for x in re.findall(r"^id=(\d+) \|", text.split("候选餐厅：")[-1], flags=re.M)]


def test_candidate_llm_sees_system_candidates_and_maps_ids():
    from baselines.candidate_llm import run_candidate_llm
    candidates = retrieve(PROFILE)
    seen = {}

    def respond(messages):
        text = messages[-1]["content"]
        seen["text"] = text
        first, second = candidates[0].poi_id, candidates[1].poi_id
        food = restaurant_ids(text)[0]
        return json.dumps({"days": [{"day": 1, "items": [
            {"type": "attraction", "id": first, "start": "09:30", "end": "11:00"},
            {"type": "restaurant", "id": food, "start": "11:40", "end": "12:40"},
            {"type": "attraction", "id": second, "start": "13:10", "end": "15:00"},
            {"type": "attraction", "id": 99999999, "start": "15:30", "end": "16:30"}]}]})

    itinerary = run_candidate_llm(PROFILE, candidates, MockLLM({"baseline_candidate_llm": respond}))
    assert candidate_lines(candidates) in seen["text"]  # 与 B 选点时模型看到的候选清单逐字相同
    items = itinerary.days[0].items
    assert [item.ref_id for item in items[:3]] == [candidates[0].poi_id, restaurant_ids(seen["text"])[0], candidates[1].poi_id]
    assert items[1].meal == "lunch" and items[3].ref_id is None  # 清单外编号按库外地点交给裁判


class FakeTravel:
    def __init__(self):
        self.calls = []

    def leg(self, lat1, lon1, lat2, lon2):
        self.calls.append((lat1, lon1, lat2, lon2))
        return 12, "打车", 20.0


def test_react_uses_tools_then_finishes():
    from baselines.react_agent import run_react
    candidates = retrieve(PROFILE)
    first = candidates[0].poi_id
    state = {}

    def step(messages):
        count = state.setdefault("n", 0)
        state["n"] += 1
        if count == 0:
            return {"thought": "先看候选", "action": "list_candidates", "args": {}}
        if count == 1:
            assert candidate_lines(candidates) in messages[-1]["content"]
            return {"thought": "查路程", "action": "travel_time", "args": {"from": "origin", "to": f"poi:{first}"}}
        if count == 2:
            assert '"minutes": 12' in messages[-1]["content"]
            return {"thought": "找餐厅", "action": "restaurants_near", "args": {"place": f"poi:{first}"}}
        food = [int(x) for x in re.findall(r"^id=(\d+) \|", messages[-1]["content"], flags=re.M)][0]
        state["food"] = food
        return {"thought": "提交", "action": "finish", "args": {"days": [{"day": 1, "items": [
            {"type": "attraction", "id": first, "start": "09:30", "end": "11:30"},
            {"type": "restaurant", "id": food, "start": "12:00", "end": "13:00"},
            {"type": "restaurant", "id": 99999999, "start": "18:00", "end": "19:00"}]}]}}

    travel = FakeTravel()
    llm = MockLLM({"baseline_react": lambda messages: json.dumps(step(messages), ensure_ascii=False)})
    itinerary, trace = run_react(PROFILE, candidates, travel, llm)
    assert trace["actions"] == ["list_candidates", "travel_time", "restaurants_near", "finish"]
    assert trace["travel_calls"] == 1 and len(travel.calls) == 1
    items = itinerary.days[0].items
    assert [item.ref_id for item in items] == [first, state["food"], None]  # 没查到过的餐厅编号不算数


def test_react_rejects_unknown_places_and_respects_step_limit(monkeypatch):
    import baselines.react_agent as react
    monkeypatch.setattr(react, "MAX_STEPS", 3)
    candidates = retrieve(PROFILE)
    replies = []

    def step(messages):
        replies.append(messages[-1]["content"])
        return json.dumps({"thought": "", "action": "travel_time", "args": {"from": "origin", "to": "poi:99999999"}})

    with pytest.raises(react.ReactError):
        react.run_react(PROFILE, candidates, FakeTravel(), MockLLM({"baseline_react": step}))
    assert len(replies) == 4  # 3 步 + 1 次要求立即提交
    assert any("未知地点" in reply for reply in replies) and "步数已用完" in replies[-1]


def test_react_travel_query_budget(monkeypatch):
    import baselines.react_agent as react
    monkeypatch.setattr(react, "MAX_TRAVEL_CALLS", 1)
    candidates = retrieve(PROFILE)
    first = candidates[0].poi_id
    seen = []

    def step(messages):
        seen.append(messages[-1]["content"])
        if len(seen) <= 2:
            return json.dumps({"action": "travel_time", "args": {"from": "origin", "to": f"poi:{first}"}})
        return json.dumps({"action": "finish", "args": {"days": [{"day": 1, "items": [
            {"type": "attraction", "id": first, "start": "09:30", "end": "11:30"}]}]}})

    travel = FakeTravel()
    _, trace = react.run_react(PROFILE, candidates, travel, MockLLM({"baseline_react": step}))
    assert trace["travel_calls"] == 1 and len(travel.calls) == 1 and "次数已用完" in seen[2]


def test_fair_compare_system_config_matches_protocol():
    from eval.fair_compare import DEFAULT_METHODS, METHODS, PLAN_KWARGS
    assert DEFAULT_METHODS == ["A", "Ap", "R", "B", "D"] and set(PLAN_KWARGS) <= set(METHODS)
    assert PLAN_KWARGS["B"] == {"select_mode": "llm", "enable_repair": True, "enable_refine": False, "travel_source": "amap"}
    assert PLAN_KWARGS["Bplus"]["enable_refine"] is True
    assert all(kwargs["enable_refine"] is False for name, kwargs in PLAN_KWARGS.items() if name != "Bplus")


class SpyLLM(MockLLM):
    def __init__(self, responses):
        super().__init__(responses)
        self.max_tokens = []

    def complete(self, messages, *, tag="misc", temperature=0.2, max_tokens=4096):
        self.max_tokens.append(max_tokens)
        return super().complete(messages, tag=tag, temperature=temperature, max_tokens=max_tokens)


def test_one_shot_baselines_leave_room_for_thinking():
    """A、A′ 一次输出整份行程：输出上限要给 adaptive 思考留足余量（c01 试采集中 12000 被思考用完）。"""
    from baselines.candidate_llm import run_candidate_llm
    from baselines.pure_llm import LONG_OUTPUT_TOKENS, run_pure_llm
    plan = json.dumps({"days": [{"day": 1, "items": []}]})
    spy = SpyLLM({"baseline_pure_llm": plan, "baseline_candidate_llm": plan})
    run_pure_llm(PROFILE, spy)
    run_candidate_llm(PROFILE, retrieve(PROFILE), spy)
    assert spy.max_tokens == [LONG_OUTPUT_TOKENS, LONG_OUTPUT_TOKENS] and LONG_OUTPUT_TOKENS >= 32000



def test_react_steps_leave_room_for_thinking():
    """R 每步与 A、A′ 用同一输出上限（dev-12 r1 中有 8 次单步 12000 个输出 token 全被思考用完）。"""
    from baselines.pure_llm import LONG_OUTPUT_TOKENS
    from baselines.react_agent import run_react
    finish = json.dumps({"action": "finish", "args": {"days": [{"day": 1, "items": []}]}})
    spy = SpyLLM({"baseline_react": finish})
    run_react(PROFILE, retrieve(PROFILE), FakeTravel(), spy)
    assert spy.max_tokens == [LONG_OUTPUT_TOKENS]
