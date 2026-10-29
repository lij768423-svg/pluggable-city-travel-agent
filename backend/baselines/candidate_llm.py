"""对比基线 A′：候选感知纯大模型（docs/评测协议v2.md 第 2 节）。

与本系统 B 拿到同一份画像文本（prompts.profile_to_text）和同一份候选清单（select.candidate_lines，即 B 选点时模型看到的内容），
另给候选附近的餐厅清单；由模型一次性给出带时间的逐日行程。不给路线工具，不经过调度、校验与修复。
地点只能用清单里的编号；编号不在清单里的项按库外地点处理（ref_id=None），由共用裁判判定。
用法：uv run python -m baselines.candidate_llm "杭州2天，喜欢人文历史"
"""
from __future__ import annotations

import sys
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from app.cities import current_city
from app.data import load_pois, load_restaurants
from app.geo import haversine_km, hm, parse_hm
from app.llm import LLM, chat_json, get_llm
from app.nodes.select import candidate_lines
from app.prompts import profile_to_text
from app.schemas import Candidate, DayPlan, Item, Itinerary, Restaurant, UserProfile
from baselines.pure_llm import LONG_OUTPUT_TOKENS

NEAR_KM = 3.0
PER_PLACE = 3
MAX_RESTAURANTS = 60

_SYSTEM = """你是__CITY__旅行规划师。根据用户画像，从给定的候选景点和餐厅中挑选地点，直接给出完整的逐日行程（含午餐、晚餐）。
要求：
- 只能使用清单里的编号（景点用候选 id，餐厅用餐厅 id），不要编造清单外的地点；
- 时间要可执行：考虑开放时间、建议游玩时长和地点之间路上的时间（清单给出了离起点的距离与所属片区，可据此估计）；
- 每天从起点出发、回到起点；满足必去、不去、饮食禁忌和预算。
只输出 JSON，格式：
{"days": [{"day": 1, "items": [
  {"type": "attraction" 或 "restaurant", "id": 编号, "start": "HH:MM", "end": "HH:MM", "reason": "一句理由"}
]}]}
"""


class IdItem(BaseModel):
    type: Literal["attraction", "restaurant"]
    id: int
    start: str
    end: str
    reason: str = ""

    @field_validator("start", "end")
    @classmethod
    def _hm(cls, value: str) -> str:
        parse_hm(value)
        return value


class IdDay(BaseModel):
    day: int
    items: list[IdItem] = Field(default_factory=list)


class IdPlan(BaseModel):
    days: list[IdDay]


def nearby_restaurants(points: list[tuple[float, float]], restaurants: dict[int, Restaurant],
                       per_place: int = PER_PLACE, limit: int = MAX_RESTAURANTS) -> list[Restaurant]:
    """每个地点就近取几家（不超过 NEAR_KM），去重后按首次出现顺序截断。"""
    chosen: dict[int, Restaurant] = {}
    for lat, lon in points:
        near = sorted((haversine_km(lat, lon, r.lat, r.lon), r.id) for r in restaurants.values())
        for distance, identifier in near[:per_place]:
            if distance <= NEAR_KM:
                chosen.setdefault(identifier, restaurants[identifier])
    return list(chosen.values())[:limit]


def restaurant_lines(items: list[Restaurant], origin: tuple[float, float]) -> str:
    return "\n".join(f"id={r.id} | {r.name} | {r.cuisine} | 人均约 {r.price:.0f} 元 | 营业 {hm(r.open_min)}-{hm(r.close_min)} | "
                     f"距起点 {haversine_km(*origin, r.lat, r.lon):.1f} km | 招牌：{r.dishes[:40] or '未提供'}" for r in items)


def to_itinerary(plan: IdPlan, pois: dict, restaurants: dict[int, Restaurant],
                 allowed_pois: set[int], allowed_restaurants: set[int]) -> Itinerary:
    """编号映射回数据；不在给定清单里的编号视为库外地点，交给裁判判定。"""
    days = []
    for day in plan.days:
        items = []
        for entry in day.items:
            start, end = parse_hm(entry.start), parse_hm(entry.end)
            if entry.type == "attraction":
                place = pois.get(entry.id) if entry.id in allowed_pois else None
                items.append(Item(kind="poi", ref_id=place.id if place else None, name=place.name if place else f"清单外景点 id={entry.id}",
                                  start=start, end=end, cost=place.price if place else 0.0, reason=entry.reason))
            else:
                place = restaurants.get(entry.id) if entry.id in allowed_restaurants else None
                items.append(Item(kind="meal", ref_id=place.id if place else None, name=place.name if place else f"清单外餐厅 id={entry.id}",
                                  start=start, end=end, cost=place.price if place else 0.0, reason=entry.reason,
                                  meal="lunch" if start < 15 * 60 else "dinner"))
        days.append(DayPlan(day=day.day, items=items))
    return Itinerary(days=days)


def run_candidate_llm(profile: UserProfile, candidates: list[Candidate], llm: LLM | None = None) -> Itinerary:
    llm = llm or get_llm()
    pois, restaurants = load_pois(), load_restaurants()
    origin = (profile.hotel.lat, profile.hotel.lon)
    shortlist = nearby_restaurants([(pois[c.poi_id].lat, pois[c.poi_id].lon) for c in candidates] + [origin], restaurants)
    plan: IdPlan = chat_json(llm, [
        {"role": "system", "content": _SYSTEM.replace("__CITY__", current_city().name)},
        {"role": "user", "content": f"用户画像：\n{profile_to_text(profile)}\n\n候选景点：\n{candidate_lines(candidates)}"
                                    f"\n\n候选餐厅：\n{restaurant_lines(shortlist, origin)}"},
    ], IdPlan, tag="baseline_candidate_llm", max_tokens=LONG_OUTPUT_TOKENS)
    return to_itinerary(plan, pois, restaurants, {c.poi_id for c in candidates}, {r.id for r in shortlist})


if __name__ == "__main__":
    from app.nodes.parse import parse_profile
    from app.nodes.retrieve import retrieve

    prof = parse_profile(sys.argv[1] if len(sys.argv) > 1 else "杭州2天，喜欢人文历史，预算1500", get_llm())
    for d in run_candidate_llm(prof, retrieve(prof)).days:
        print(f"第{d.day}天")
        for i in d.items:
            print(f"  {hm(i.start)}-{hm(i.end)} {i.name}")
