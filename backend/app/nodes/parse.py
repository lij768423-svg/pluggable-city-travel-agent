"""需求解析：一句话 → UserProfile；表单字段优先于解析结果。"""
from __future__ import annotations

from typing import Any
from datetime import date
import re

from pydantic import BaseModel, Field

from ..calendar import china_today, resolve_date_phrase
from ..llm import LLM, chat_json, get_llm
from ..prompts import PARSE_SYSTEM
from ..schemas import FAMILIARITY_LEVELS, INTERESTS, SPICE_LEVELS, UserProfile, interest_list


class _Parsed(BaseModel):
    days: int | None = None
    budget: float | None = None
    pace: str | None = None
    interests: list[str] = Field(default_factory=list)
    preferred_regions: list[str] = Field(default_factory=list)
    spice: str | None = None
    familiarity: str | None = None
    companions: str | None = None
    transport: str | None = None
    must_visit: list[str] = Field(default_factory=list)
    avoid: list[str] = Field(default_factory=list)
    notes: str = ""
    start_date: date | None = None
    date_phrase: str | None = None
    day_start: int | None = None
    day_end: int | None = None
    max_walking_m: int | None = None
    allow_taxi_fallback: bool | None = None
    food_preference: str | None = None
    meal_budget_min: float | None = None
    meal_budget_max: float | None = None
    origin: str | None = None


# parse_query 额外带回的说明字段，不是画像字段；origin 是出发地原话，由理解层对到起点
HELPER_KEYS = ("date_phrase", "days_requested", "origin")


def parse_query(query: str, llm: LLM | None = None) -> dict[str, Any]:
    """一句话 → 用户明说的字段（没说的不填，好区分“你说的”和“默认的”）。
    另带 date_phrase（“周末”“明天”这类相对日期的原话）和 days_requested（说了超过 5 天时的原值）。"""
    data: dict[str, Any] = {}
    if query and query.strip():
        p: _Parsed = chat_json(llm or get_llm(), [
            {"role": "system", "content": PARSE_SYSTEM},
            {"role": "user", "content": query.strip()},
        ], _Parsed, tag="parse", temperature=0.0, max_tokens=2048)
        data = p.model_dump(exclude_none=True)
        data["interests"] = [i for i in interest_list(data.get("interests", [])) if i in INTERESTS][:5]
        if data.get("spice") not in SPICE_LEVELS:
            data.pop("spice", None)
        if data.get("familiarity") not in FAMILIARITY_LEVELS:
            data.pop("familiarity", None)
        if data.get("pace") not in ("relaxed", "normal", "intense"):
            data.pop("pace", None)
        if data.get("companions") not in ("solo", "couple", "friends", "family_kids", "elderly"):
            data.pop("companions", None)
        if data.get("transport") not in ("taxi", "transit"):
            data.pop("transport", None)
        transport_text = re.sub(r"地铁站|地铁口", "起点", query)
        if data.get("transport") == "transit" and not re.search(r"公交|地铁|公共交通|坐车省钱|不打车|别打车|不想打车", transport_text):
            data.pop("transport", None)
        if data.get("allow_taxi_fallback") is False and "transport" not in data:
            data["transport"] = "transit"  # 说了“别打车”却没说怎么走：不能再按默认的打车排
        if data.get("food_preference") not in ("balanced", "local", "quick", "quality"):
            data.pop("food_preference", None)
        if "days" in data:
            requested = int(data["days"])
            data["days"] = min(5, max(1, requested))
            if requested > 5:
                data["days_requested"] = requested
        if "budget" in data and data["budget"] <= 0:
            data.pop("budget")
        if not (data.get("date_phrase") or "").strip() or "start_date" in data:
            data.pop("date_phrase", None)
        if not (data.get("origin") or "").strip():
            data.pop("origin", None)
    return data


def parse_profile(query: str, llm: LLM | None = None, form: dict[str, Any] | None = None) -> UserProfile:
    stated = parse_query(query, llm)
    data = {key: value for key, value in stated.items() if key not in HELPER_KEYS}
    if stated.get("date_phrase") and (resolved := resolve_date_phrase(stated["date_phrase"], china_today())):
        data["start_date"] = resolved
    for k, v in (form or {}).items():
        if v not in (None, "", []):
            data[k] = v
    return UserProfile.model_validate(data)


def parse_node(state: dict) -> dict:
    prof = parse_profile(state.get("query", ""), state["llm"], state.get("form"))
    return {"profile": prof, "log": [f"需求解析：{prof.days} 天，节奏 {prof.pace}，兴趣 {prof.interests or '无'}，预算 {prof.budget:.0f}"]}
