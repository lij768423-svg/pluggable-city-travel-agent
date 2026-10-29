"""对比基线 ①：纯 LLM 一次性生成行程（只给画像，不给数据库与工具）。

输出的地点名称按名称匹配回数据库；匹配不上记为 unknown_place（幻觉或库外地点），由共用校验器判定。
用法：uv run python -m baselines.pure_llm "杭州2天，喜欢人文历史"
"""
from __future__ import annotations

import sys
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.data import load_pois, load_restaurants
from app.geo import parse_hm
from app.llm import LLM, chat_json, get_llm
from app.matching import match_name
from app.prompts import BASELINE_SYSTEM, profile_to_text, baseline_system
from app.schemas import DayPlan, Item, Itinerary, UserProfile


# 整份行程一次输出，思考按系统设置（adaptive）进行。2026-10-24 c01 试采集中 8192（被抬到 12000）全被思考用完、正文为空，
# 所以给一次性出整份行程的基线留足余量；32000 已用一次实际调用确认接口接受。
LONG_OUTPUT_TOKENS = 32000


class _BItem(BaseModel):
    type: Literal["attraction", "restaurant"]
    name: str
    start: str
    end: str
    cost: float = 0
    reason: str = ""

    @field_validator("start", "end")
    @classmethod
    def _hm(cls, v: str) -> str:
        parse_hm(v)
        return v


class _BDay(BaseModel):
    day: int
    items: list[_BItem] = Field(default_factory=list)


class BaselineOut(BaseModel):
    days: list[_BDay]


def run_pure_llm(profile: UserProfile, llm: LLM | None = None) -> Itinerary:
    llm = llm or get_llm()
    out: BaselineOut = chat_json(llm, [
        {"role": "system", "content": baseline_system()},
        {"role": "user", "content": "用户画像：\n" + profile_to_text(profile)},
    ], BaselineOut, tag="baseline_pure_llm", max_tokens=LONG_OUTPUT_TOKENS)
    poi_names = {i: p.name for i, p in load_pois().items()}
    rest_names = {i: r.name for i, r in load_restaurants().items()}
    days = []
    for d in out.days:
        items = []
        for it in d.items:
            start, end = parse_hm(it.start), parse_hm(it.end)
            if it.type == "attraction":
                items.append(Item(kind="poi", ref_id=match_name(it.name, poi_names), name=it.name,
                                  start=start, end=end, cost=it.cost, reason=it.reason))
            else:
                items.append(Item(kind="meal", ref_id=match_name(it.name, rest_names), name=it.name,
                                  start=start, end=end, cost=it.cost, reason=it.reason,
                                  meal="lunch" if start < 15 * 60 else "dinner"))
        days.append(DayPlan(day=d.day, items=items))
    return Itinerary(days=days)


if __name__ == "__main__":
    from app.nodes.parse import parse_profile
    from app.nodes.validate import validate_itinerary

    prof = parse_profile(sys.argv[1] if len(sys.argv) > 1 else "杭州2天，喜欢人文历史，预算1500", get_llm())
    it = run_pure_llm(prof)
    rep = validate_itinerary(it, prof)
    for d in it.days:
        print(f"第{d.day}天")
        for i in d.items:
            print(f"  {i.start // 60:02d}:{i.start % 60:02d}-{i.end // 60:02d}:{i.end % 60:02d} {i.name} {'' if i.ref_id is not None else '[库外]'}")
    print("passed:", rep.passed, rep.totals)
    for v in rep.violations:
        print(" ", v.severity, v.code, v.day, v.item or "", v.message)
