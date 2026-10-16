"""全项目唯一的数据结构定义（AGENTS.md §5）。时间统一用"当天分钟数"（0–1440）。"""
from __future__ import annotations

from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 兴趣标签固定词表：画像解析、景点打标签、检索打分共用
INTERESTS: list[str] = [
    "自然风光", "人文历史", "宗教寺庙", "博物馆展览", "园林古镇", "拍照出片", "茶文化",
    "亲子游乐", "主题乐园", "购物街区", "夜景夜游", "户外徒步", "小众安静", "艺术文创", "美食探店",
]
AUDIENCES: list[str] = ["亲子", "老人", "情侣", "朋友", "独行"]
DIETARY: list[str] = ["不吃辣", "只吃中餐", "清真", "不吃海鲜"]

Pace = Literal["relaxed", "normal", "intense"]
Companions = Literal["solo", "couple", "friends", "family_kids", "elderly"]
Transport = Literal["taxi", "transit"]  # 景点之间怎么走：打车 | 公交/地铁（≤1.5 km 一律步行）


class StayEstimate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    poi_id: int = Field(ge=0)
    low_min: int = Field(gt=0, le=1440, strict=True)
    high_min: int = Field(gt=0, le=1440, strict=True)
    typical_min: int = Field(gt=0, le=1440, strict=True)
    scope: str = Field(min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=300)
    confidence: Literal["high", "medium", "low"]

    @model_validator(mode="after")
    def ordered_interval(self) -> "StayEstimate":
        if not self.low_min <= self.typical_min <= self.high_min:
            raise ValueError("要求 low_min <= typical_min <= high_min")
        return self


class StayEstimateBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[StayEstimate] = Field(min_length=1)


class StayReference(StayEstimate):
    name: str = Field(min_length=1)
    source: Literal["llm_estimate"]
    model: str = Field(min_length=1)
    generated_at: str = Field(min_length=1)
    spread: dict[str, int]
    samples: list[StayEstimate] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def samples_match_reference(self) -> "StayReference":
        if any(sample.poi_id != self.poi_id for sample in self.samples):
            raise ValueError("停留参考样本POI身份不一致")
        expected_spread = {}
        for key in ("low_min", "high_min", "typical_min"):
            values = sorted(getattr(sample, key) for sample in self.samples)
            if getattr(self, key) != values[1]:
                raise ValueError("停留参考值不是三个样本的中位数")
            expected_spread[key] = values[-1] - values[0]
        expected_spread["max_min"] = max(expected_spread.values())
        if self.spread != expected_spread:
            raise ValueError("停留参考spread与样本不一致")
        return self


class MapPoint(BaseModel):
    lat: float = Field(ge=29, le=31, allow_inf_nan=False)
    lon: float = Field(ge=118, le=121, allow_inf_nan=False)

    def coordinate(self) -> str:
        return f"{self.lon:.6f},{self.lat:.6f}"


class NavigationLeg(BaseModel):
    origin: MapPoint
    destination: MapPoint
    mode: Literal["步行", "打车", "公交/地铁", "原地"]
    departure: int = Field(ge=0, le=1440)


class NavigationRequest(BaseModel):
    date: Date | None = None
    legs: list[NavigationLeg] = Field(min_length=1, max_length=16)


class PlaceReference(BaseModel):
    kind: Literal["poi", "meal"]
    id: int


class PlaceDetailsRequest(BaseModel):
    places: list[PlaceReference] = Field(min_length=1, max_length=12)


class Hotel(BaseModel):
    name: str = Field("西湖湖滨（默认）", min_length=1, max_length=160)
    lat: float = Field(30.254433, ge=29, le=31, allow_inf_nan=False)
    lon: float = Field(120.169771, ge=118, le=121, allow_inf_nan=False)


class DayWindow(BaseModel):
    day: int = Field(ge=1, le=5)
    start: int = Field(ge=0, le=1320)
    end: int = Field(ge=120, le=1440)

    @model_validator(mode="after")
    def valid_window(self):
        if self.end - self.start < 120:
            raise ValueError("每天至少预留两小时，结束时间须晚于出发时间")
        return self


class UserProfile(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)
    days: int = Field(2, ge=1, le=5)
    budget: float = Field(1500, gt=0, description="人均总预算（元），含门票、餐饮、市内交通，不含住宿")
    pace: Pace = "normal"
    interests: list[str] = Field(default_factory=list)
    dietary: list[str] = Field(default_factory=list)
    companions: Companions = "solo"
    transport: Transport = "taxi"
    hotel: Hotel = Field(default_factory=Hotel)
    must_visit: list[str] = Field(default_factory=list, description="点名要去的景点名称")
    avoid: list[str] = Field(default_factory=list, description="明确不去的景点名称")
    notes: str = ""
    start_date: Date | None = None
    day_start: int | None = Field(None, ge=0, le=1320)
    day_end: int | None = Field(None, ge=120, le=1440)
    daily_windows: list[DayWindow] = Field(default_factory=list)
    max_walking_m: int | None = Field(None, ge=500, le=30000)
    allow_taxi_fallback: bool = False
    include_evening: bool = True
    food_preference: Literal["balanced", "local", "quick", "quality"] = "balanced"
    meal_budget_min: float | None = Field(None, ge=0, le=2000)
    meal_budget_max: float | None = Field(None, gt=0, le=2000)

    @field_validator("budget", mode="before")
    @classmethod
    def positive_budget(cls, value: float) -> float:
        if isinstance(value, (int, float)) and value <= 0:
            raise ValueError("总预算需大于0，包含餐饮和市内交通；不确定时请留空，0不代表不限预算")
        return value

    @model_validator(mode="after")
    def valid_preferences(self):
        from .rules import activity_window
        window = activity_window(self)
        start, end = window["start"], window["end"]
        if end - start < 120:
            raise ValueError("活动时段至少需要两小时，结束时间须晚于出发时间")
        if len({window.day for window in self.daily_windows}) != len(self.daily_windows):
            raise ValueError("同一天不能重复设置活动时段")
        if any(window.day > self.days for window in self.daily_windows):
            raise ValueError("活动时段不能超出旅行天数")
        if self.meal_budget_min is not None and self.meal_budget_max is not None and self.meal_budget_min > self.meal_budget_max:
            raise ValueError("每餐预算下限不能大于上限")
        return self


class OpeningRules(BaseModel):
    closed_weekdays: list[int] = Field(default_factory=list)
    closed_dates: list[Date] = Field(default_factory=list)
    open_dates: list[Date] = Field(default_factory=list)
    last_entry: int | None = None
    booking_required: bool | None = None
    source_url: str = ""
    verified_at: Date | None = None
    holiday_unknown: bool = True


class POI(BaseModel):
    id: int
    name: str
    type: str
    lat: float
    lon: float
    open_min: int
    close_min: int
    price: float
    min_hours: float
    max_hours: float
    interests: list[str] = Field(default_factory=list)
    stamina: int = 2
    audience: list[str] = Field(default_factory=list)
    indoor: bool = False
    summary: str = ""
    opening_rules: OpeningRules | None = None
    duration_source: str = "ChinaTravel 2026.08.2，游览范围待核验"
    walking_m: int | None = None
    recommendable: bool = True
    review_note: str = ""
    price_verified: bool = False
    visit_group: str = ""
    visit_group_label: str = ""
    visit_role: Literal["attraction", "walk_segment", "viewpoint"] = "attraction"
    seasonal_note: str = ""
    day_anchor: bool = False
    onsite_dining: bool = False
    family_only: bool = False


class Restaurant(BaseModel):
    id: int
    name: str
    lat: float
    lon: float
    price: float
    cuisine: str
    open_min: int
    close_min: int
    dishes: str = ""
    external_id: str = ""
    provider: str = "chinatravel"
    address: str = ""
    brand: str = ""
    rating: float | None = Field(None, ge=0, le=5)
    price_known: bool = True
    hours_known: bool = True
    opening_text: str = ""
    opening_periods: list[tuple[int, int]] = Field(default_factory=list)
    fetched_at: str | None = None
    dietary_verified: list[str] = Field(default_factory=list)


class LegEvidence(BaseModel):
    minutes: int
    mode: str
    cost: float
    walking_m: int = 0
    source: Literal["amap", "estimate", "same_place"]
    cost_source: str = "estimate"
    fetched_at: str | None = None
    fallback_reason: str | None = None
    available: bool = True
    service_date: Date | None = None


class MealAlternative(BaseModel):
    ref_id: int
    name: str
    cuisine: str
    price: float
    rating: float | None = None
    address: str = ""
    detour_min: int
    travel_source: str
    provider: str
    fetched_at: str | None = None
    start: int
    end: int


class Candidate(BaseModel):
    poi_id: int
    score: float
    breakdown: dict[str, float] = Field(default_factory=dict)


class Selection(BaseModel):
    poi_id: int
    reason: str
    source: Literal["llm", "rule", "repair", "jev"] = "llm"
    guide_ids: list[str] = Field(default_factory=list)


class GuideSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    title: str
    url: str
    publisher: str
    kind: Literal["official_public_facts", "open_license", "authorized_xhs"]
    published_at: Date | None = None
    visited_at: Date | None = None
    fetched_at: Date
    content_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    permission: Literal["facts_only", "CC-BY-SA-4.0", "written_permission", "unverified"]
    permission_reference: str
    usage_scope: Literal["local_research_selection"] = "local_research_selection"
    training_allowed: Literal[False] = False
    withdrawn: bool = False
    attribution: str


class GuidePlace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    raw_name: str
    poi_id: int | None = None
    canonical_name: str | None = None
    confidence: float = Field(ge=0, le=1)
    basis: str


class GuideSample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    source_id: str
    source_locator: str
    cluster_id: str
    split: Literal["knowledge", "holdout"] = "knowledge"
    title: str = Field(max_length=100)
    places: list[GuidePlace] = Field(min_length=1, max_length=8)
    facts: str = Field(min_length=1, max_length=500)
    relation: Literal["nearby", "theme_only", "single"]
    interests: list[str] = Field(default_factory=list)
    audiences: list[Companions] = Field(default_factory=list)
    audience_basis: Literal["source", "curator_inference", "unknown"] = "unknown"
    months: list[int] = Field(default_factory=list)
    season_note: str | None = None
    duration_minutes: int | None = Field(None, ge=1, le=1440)
    duration_scope: str | None = None
    queue_note: str | None = None
    caveats: list[str] = Field(default_factory=list)
    review: Literal["agent_checked", "human_checked", "pending", "rejected"]
    reviewed_at: Date
    reviewer: str

    @model_validator(mode="after")
    def valid_evidence(self) -> "GuideSample":
        if any(month < 1 or month > 12 for month in self.months):
            raise ValueError("攻略月份须为 1–12")
        if self.duration_minutes is not None and not self.duration_scope:
            raise ValueError("攻略时长必须注明适用范围")
        if self.audiences and self.audience_basis == "unknown":
            raise ValueError("人群标签必须注明来源或整理推断")
        return self


class GuideCorpus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: str
    sources: list[GuideSource]
    samples: list[GuideSample]
    limitations: list[str]


class Item(BaseModel):
    kind: Literal["poi", "meal"]
    ref_id: int | None = None  # poi.id / restaurant.id；基线中名称匹配失败时为 None
    name: str
    start: int
    end: int
    travel_min: int = 0  # 从上一地点（或酒店）出发的交通时间
    travel_mode: str = ""
    cost: float = 0.0  # 门票或人均餐费
    reason: str = ""
    meal: Literal["lunch", "dinner"] | None = None
    travel_evidence: LegEvidence | None = None
    alternatives: list[MealAlternative] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    evening: bool = False


class ReservedMeal(BaseModel):
    meal: Literal["lunch", "dinner"]
    start: int
    end: int
    reason: str
    inside_poi_id: int | None = None
    estimated_cost: float = Field(0, ge=0)


class DayPlan(BaseModel):
    day: int
    items: list[Item] = Field(default_factory=list)
    return_travel_min: int = 0
    notes: list[str] = Field(default_factory=list)
    date: Date | None = None
    return_leg: LegEvidence | None = None
    walking_m: int = 0
    reserved_meals: list[ReservedMeal] = Field(default_factory=list)


class Itinerary(BaseModel):
    days: list[DayPlan] = Field(default_factory=list)
    transport_cost: float = 0.0


class Violation(BaseModel):
    code: str
    severity: Literal["hard", "soft"]
    day: int | None = None
    item: str | None = None
    message: str


class ValidationReport(BaseModel):
    passed: bool
    violations: list[Violation] = Field(default_factory=list)
    totals: dict[str, float] = Field(default_factory=dict)
    experience_status: Literal["complete", "needs_attention"] = "needs_attention"
    data_status: Literal["verified", "partial"] = "partial"
    unknowns: list[str] = Field(default_factory=list)

    @property
    def hard(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "hard"]
