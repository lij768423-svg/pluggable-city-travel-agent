"""需求解析：一句话 → UserProfile；表单字段优先于解析结果。"""
from __future__ import annotations

from typing import Any
from datetime import date
import re

from pydantic import BaseModel, Field

from ..llm import LLM, chat_json, get_llm
from ..prompts import PARSE_SYSTEM
from ..schemas import DIETARY, INTERESTS, UserProfile, interest_list


class _Parsed(BaseModel):
    days: int | None = None
    budget: float | None = None
    pace: str | None = None
    interests: list[str] = Field(default_factory=list)
    preferred_regions: list[str] = Field(default_factory=list)
    dietary: list[str] = Field(default_factory=list)
    companions: str | None = None
    transport: str | None = None
    must_visit: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    notes: str = ""
    start_date: date | None = None
    day_start: int | None = None
    day_end: int | None = None
    max_walking_m: int | None = None
    allow_taxi_fallback: bool | None = None
    food_preference: str | None = None
    meal_budget_min: float | None = None
    meal_budget_max: float | None = None


def parse_profile(query: str, llm: LLM | None = None, form: dict[str, Any] | None = None) -> UserProfile:
    data: dict[str, Any] = {}
    if query and query.strip():
        p: _Parsed = chat_json(llm or get_llm(), [
            {"role": "system", "content": PARSE_SYSTEM},
            {"role": "user", "content": query.strip()},
        ], _Parsed, tag="parse", temperature=0.0, max_tokens=2048)
        data = p.model_dump(exclude_none=True)
        data["interests"] = [i for i in interest_list(data.get("interests", [])) if i in INTERESTS][:5]
        data["dietary"] = [d for d in data.get("dietary", []) if d in DIETARY]
        if data.get("pace") not in ("relaxed", "normal", "intense"):
            data.pop("pace", None)
        if data.get("companions") not in ("solo", "couple", "friends", "family_kids", "elderly"):
            data.pop("companions", None)
        if data.get("transport") not in ("taxi", "transit"):
            data.pop("transport", None)
        transport_text = re.sub(r"地铁站|地铁口", "起点", query)
        if data.get("transport") == "transit" and not re.search(r"公交|地铁|公共交通|坐车省钱", transport_text):
            data.pop("transport", None)
        if data.get("food_preference") not in ("balanced", "local", "quick", "quality"):
            data.pop("food_preference", None)
        if "days" in data:
            data["days"] = min(5, max(1, int(data["days"])))
        if "budget" in data and data["budget"] <= 0:
            data.pop("budget")
    for k, v in (form or {}).items():
        if v not in (None, "", []):
            data[k] = v
    return UserProfile.model_validate(data)


def parse_node(state: dict) -> dict:
    prof = parse_profile(state.get("query", ""), state["llm"], state.get("form"))
    return {"profile": prof, "log": [f"需求解析：{prof.days} 天，节奏 {prof.pace}，兴趣 {prof.interests or '无'}，预算 {prof.budget:.0f}"]}
