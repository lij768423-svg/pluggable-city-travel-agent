"""对比基线 R：ReAct 工具调用智能体（docs/评测协议v2.md 第 2 节）。

同一模型，按"思考 → 调用工具 → 看结果"循环自己排行程，没有求解器，也不能调用校验器：
- list_candidates：返回与本系统 B 选点时相同的候选清单（select.candidate_lines）；
- travel_time：两点间路程，与 B 用同一个路程来源（travel.make_travel，按画像的出行方式，评测时读快照）；
- restaurants_near：某地点附近的餐厅；
- finish：提交最终行程（格式同 A′，只能用编号）。
最多 MAX_STEPS 步、路程查询最多 MAX_TRAVEL_CALLS 次；步数用完还没提交，就再要求一次立即提交，仍失败则报错。
"""
from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.amap import AmapError
from app.cities import current_city
from app.data import load_pois, load_restaurants
from app.llm import LLM, chat_json, get_llm
from app.nodes.select import candidate_lines
from app.prompts import profile_to_text
from app.schemas import Candidate, Itinerary, UserProfile
from baselines.candidate_llm import IdPlan, nearby_restaurants, restaurant_lines, to_itinerary
from baselines.pure_llm import LONG_OUTPUT_TOKENS

MAX_STEPS = 30
MAX_TRAVEL_CALLS = 40
NEAR_LIMIT = 8

_SYSTEM = """你是__CITY__旅行规划智能体，可以调用工具查询信息，再给出完整的逐日行程（含午餐、晚餐）。
每一步只输出一个 JSON 对象，不要输出其他文字：
{"thought": "简短思考", "action": "工具名", "args": {参数}}
可用工具：
- list_candidates：无参数。返回候选景点清单（编号、类型、开放时间、建议时长、门票、离起点距离、片区等）。
- travel_time：{"from": 地点, "to": 地点}。地点写 "origin"（每天的起点/终点）、"poi:编号" 或 "restaurant:编号"。返回路上分钟、方式和车费。最多查询 __TRAVEL__ 次。
- restaurants_near：{"place": 地点}。返回该地点附近的餐厅（编号、菜系、人均、营业时间）。
- finish：{"days": [{"day": 1, "items": [{"type": "attraction" 或 "restaurant", "id": 编号, "start": "HH:MM", "end": "HH:MM", "reason": "一句理由"}]}]}。提交最终行程后结束。
要求：只能使用工具返回过的编号；时间要可执行（开放时间、建议时长、路上时间）；每天从起点出发、回到起点；满足必去、不去、饮食禁忌和预算。
你最多可以走 __STEPS__ 步，请在步数用完前调用 finish。
"""


class Step(BaseModel):
    thought: str = ""
    action: Literal["list_candidates", "travel_time", "restaurants_near", "finish"]
    args: dict[str, Any] = Field(default_factory=dict)


class ReactError(RuntimeError):
    pass


class _Tools:
    def __init__(self, profile: UserProfile, candidates: list[Candidate], travel) -> None:
        self.profile, self.candidates, self.travel = profile, candidates, travel
        self.pois, self.restaurants = load_pois(), load_restaurants()
        self.allowed_pois = {c.poi_id for c in candidates}
        self.seen_restaurants: set[int] = set()
        self.travel_calls = 0

    def locate(self, ref: str) -> tuple[float, float, str]:
        ref = str(ref).strip()
        if ref == "origin":
            return self.profile.hotel.lat, self.profile.hotel.lon, "起点"
        kind, _, number = ref.partition(":")
        identifier = int(number)
        if kind == "poi" and identifier in self.allowed_pois:
            place = self.pois[identifier]
        elif kind == "restaurant" and identifier in self.seen_restaurants:
            place = self.restaurants[identifier]
        else:
            raise ValueError(f"未知地点 {ref}：只能用 origin、候选景点 poi:编号 或查询过的 restaurant:编号")
        return place.lat, place.lon, place.name

    def call(self, step: Step) -> str:
        try:
            if step.action == "list_candidates":
                return candidate_lines(self.candidates)
            if step.action == "travel_time":
                if self.travel_calls >= MAX_TRAVEL_CALLS:
                    return f"路程查询次数已用完（{MAX_TRAVEL_CALLS} 次），请根据已有信息安排并尽快 finish。"
                a, b = self.locate(step.args.get("from", "")), self.locate(step.args.get("to", ""))
                self.travel_calls += 1
                minutes, mode, cost = self.travel.leg(a[0], a[1], b[0], b[1])
                return json.dumps({"from": a[2], "to": b[2], "minutes": minutes, "mode": mode, "cost": round(cost, 1),
                                   "remaining_queries": MAX_TRAVEL_CALLS - self.travel_calls}, ensure_ascii=False)
            if step.action == "restaurants_near":
                lat, lon, name = self.locate(step.args.get("place", ""))
                near = nearby_restaurants([(lat, lon)], self.restaurants, per_place=NEAR_LIMIT, limit=NEAR_LIMIT)
                self.seen_restaurants.update(r.id for r in near)
                return f"{name} 附近：\n" + (restaurant_lines(near, (self.profile.hotel.lat, self.profile.hotel.lon)) or "3 公里内没有餐厅")
        except (ValueError, TypeError, AmapError) as error:  # 地点写错或路线接口报错：如实告诉模型，由它自己处理
            return f"工具调用出错：{error}"
        return "未知工具"

    def itinerary(self, args: dict[str, Any]) -> Itinerary:
        plan = IdPlan.model_validate(args)
        return to_itinerary(plan, self.pois, self.restaurants, self.allowed_pois, self.seen_restaurants)


def run_react(profile: UserProfile, candidates: list[Candidate], travel, llm: LLM | None = None) -> tuple[Itinerary, dict]:
    """返回（行程, 轨迹统计）。travel 需提供 leg(lat1, lon1, lat2, lon2) -> (分钟, 方式, 车费)。"""
    llm = llm or get_llm()
    tools = _Tools(profile, candidates, travel)
    system = (_SYSTEM.replace("__CITY__", current_city().name).replace("__TRAVEL__", str(MAX_TRAVEL_CALLS))
              .replace("__STEPS__", str(MAX_STEPS)))
    messages = [{"role": "system", "content": system}, {"role": "user", "content": "用户画像：\n" + profile_to_text(profile)}]
    actions: list[str] = []
    for number in range(MAX_STEPS + 1):
        if number == MAX_STEPS:
            messages.append({"role": "user", "content": "步数已用完，请立即调用 finish 提交最终行程。"})
        # 每步都可能长时间思考：dev-12 r1 中有 8 次单步把 12000 个输出 token 全用在思考上、正文为空，所以与 A、A′ 用同一上限
        step: Step = chat_json(llm, messages, Step, tag="baseline_react", max_tokens=LONG_OUTPUT_TOKENS)
        actions.append(step.action)
        messages.append({"role": "assistant", "content": step.model_dump_json()})
        if step.action == "finish":
            try:
                itinerary = tools.itinerary(step.args)
            except ValueError as error:
                if number == MAX_STEPS:
                    raise ReactError(f"最终行程格式无效：{str(error)[:200]}") from None
                messages.append({"role": "user", "content": f"行程格式无效，请修正后重新 finish：{str(error)[:300]}"})
                continue
            return itinerary, {"steps": len(actions), "actions": actions, "travel_calls": tools.travel_calls,
                               "restaurants_seen": len(tools.seen_restaurants)}
        if number < MAX_STEPS:
            messages.append({"role": "user", "content": "工具结果：\n" + tools.call(step)})
    raise ReactError(f"{MAX_STEPS} 步内没有提交行程")


if __name__ == "__main__":
    import sys

    from app.geo import hm
    from app.nodes.parse import parse_profile
    from app.nodes.retrieve import retrieve
    from app.travel import make_travel

    prof = parse_profile(sys.argv[1] if len(sys.argv) > 1 else "杭州2天，喜欢人文历史，预算1500", get_llm())
    itinerary, trace = run_react(prof, retrieve(prof), make_travel(None, prof.transport))
    print(trace)
    for d in itinerary.days:
        print(f"第{d.day}天")
        for i in d.items:
            print(f"  {hm(i.start)}-{hm(i.end)} {i.name}")
