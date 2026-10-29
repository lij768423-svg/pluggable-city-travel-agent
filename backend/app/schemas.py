"""全项目唯一的数据结构定义（AGENTS.md §5）。时间统一用"当天分钟数"（0–1440）。"""
from __future__ import annotations

from datetime import date as Date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# 兴趣标签固定词表：画像解析、景点打标签、检索打分共用
INTERESTS: list[str] = [
    "自然风光", "人文历史", "宗教寺庙", "博物馆展览", "园林古镇", "拍照出片", "特色文化",
    "亲子游乐", "主题乐园", "购物街区", "夜景夜游", "户外徒步", "小众安静", "艺术文创", "美食探店",
]
# 2026-10-20 用户决定：“茶文化”改为更通用的“特色文化”（南京等城市不以茶文化为特色）。
# 景点数据里的旧标签不改（会改变原库摘要、使已发布补充目录失效），匹配和展示时按别名归一。
INTEREST_ALIASES: dict[str, str] = {"茶文化": "特色文化"}


def interest_list(values) -> list[str]:
    """按别名归一后的兴趣标签，保持原顺序、去重。"""
    return list(dict.fromkeys(INTEREST_ALIASES.get(value, value) for value in values))


def interest_tags(values) -> set[str]:
    return set(interest_list(values))

AUDIENCES: list[str] = ["亲子", "老人", "情侣", "朋友", "独行"]
# 口味（辣度）：不吃辣会排除辣味菜系，爱吃辣只是优先推荐。2026-10-26 起替代原"饮食忌口"
# （清真、只吃中餐、不吃海鲜已删除：数据核验不了，也少有人需要）
SPICE_LEVELS: dict[str, str] = {"no": "不吃辣", "any": "微辣都行", "love": "爱吃辣"}
Spice = Literal["no", "any", "love"]
# 对这座城市的熟悉程度（2026-10-25 用户决定）：决定知名度加分的权重，第一次来最高、本地人最低
FAMILIARITY_LEVELS: dict[str, str] = {"first": "第一次来", "visited": "来过", "local": "本地人"}
Familiarity = Literal["first", "visited", "local"]
# 景点知名度档位：城市地标 / 知名景点 / 未标注（数据见 data/<city>/popularity.json）
POPULARITY_LABELS: dict[str, str] = {"landmark": "城市地标", "known": "知名景点", "": "一般"}

Pace = Literal["relaxed", "normal", "intense"]
Companions = Literal["solo", "couple", "friends", "family_kids", "elderly"]
# 没说预算时每天人均多少元（门票、餐饮、市内交通）。2026-10-28 用户："一般来说本地大概在 300 人均"；
# 系统行程每天花费中位数约 140（公交）–210（打车）元（e1-dev12-v3），300 留有余量。
DEFAULT_DAILY_BUDGET = 300
PACE_LABELS: dict[str, str] = {"relaxed": "轻松", "normal": "适中", "intense": "紧凑"}
COMPANION_LABELS: dict[str, str] = {"solo": "独自", "couple": "情侣", "friends": "朋友", "family_kids": "带孩子", "elderly": "带老人"}
TRANSPORT_LABELS: dict[str, str] = {"taxi": "打车", "transit": "公交地铁"}
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
    # 通用坐标先做数值粗筛；所属城市范围须在导航解析行程上下文后校验。
    lat: float = Field(ge=18, le=54, allow_inf_nan=False)
    lon: float = Field(ge=73, le=136, allow_inf_nan=False)

    def coordinate(self) -> str:
        return f"{self.lon:.6f},{self.lat:.6f}"


class NavigationLeg(BaseModel):
    origin: MapPoint
    destination: MapPoint
    mode: Literal["步行", "打车", "公交/地铁", "原地"]
    departure: int = Field(ge=0, le=1440)


class NavigationRequest(BaseModel):
    # 旧客户端省略时按杭州；历史会话过期时仍能按显式城市查询路线。
    city: str = Field(default="hangzhou", min_length=1, max_length=40)
    plan_id: str | None = Field(None, max_length=64)

    @field_validator("city")
    @classmethod
    def known_city(cls, value: str) -> str:
        from .cities import city_config
        city_config(value)
        return value

    date: Date | None = None
    legs: list[NavigationLeg] = Field(min_length=1, max_length=16)


class PlaceReference(BaseModel):
    kind: Literal["poi", "meal"]
    id: int


class PlaceDetailsRequest(BaseModel):
    places: list[PlaceReference] = Field(min_length=1, max_length=12)
    # 所属行程；按该行程固定的目录版本解析地点，缺省或无权访问时读当前发布版
    plan_id: str | None = Field(None, max_length=64)


def _context_city() -> str:
    from .cities import current_city
    return current_city().key


def _default_origin() -> dict:
    from .cities import current_city
    return current_city().default_origin


class Hotel(BaseModel):
    name: str = Field(default_factory=lambda: _default_origin()["name"], min_length=1, max_length=160)
    lat: float = Field(default_factory=lambda: _default_origin()["lat"], ge=18, le=54, allow_inf_nan=False)
    lon: float = Field(default_factory=lambda: _default_origin()["lon"], ge=73, le=136, allow_inf_nan=False)


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
    # 城市键（app/assets/cities.json）。没写时取当前任务城市，无任务上下文（如读取 C2 之前的旧记录）即杭州。
    # 画像只校验城市存在，是否启用由新建规划、修改等入口检查，保证旧记录总能读出
    city: str = Field(default_factory=lambda: _context_city(), min_length=1, max_length=40)
    days: int = Field(2, ge=1, le=5)
    budget: float = Field(DEFAULT_DAILY_BUDGET * 2, gt=0, description="人均总预算（元），含门票、餐饮、市内交通，不含住宿；没说时按天数估")
    pace: Pace = "normal"
    interests: list[str] = Field(default_factory=list)
    spice: Spice = "any"
    familiarity: Familiarity = "first"  # 没说时按第一次来，页面显示可改
    preferred_regions: list[str] = Field(default_factory=list)
    companions: Companions = "solo"
    transport: Transport = "taxi"
    hotel: Hotel = Field(default_factory=Hotel)
    origin_known: bool = Field(True, description="定了起点（酒店等）。False：没定，每天从第一站开始、最后一项结束，去第一站和回去的路程不计")
    must_visit: list[str] = Field(default_factory=list, description="点名要去的景点名称")
    avoid: list[str] = Field(default_factory=list, description="明确不去的景点名称")
    notes: str = ""
    start_date: Date | None = None
    day_start: int | None = Field(None, ge=0, le=1320)
    day_end: int | None = Field(None, ge=120, le=1440)
    daily_windows: list[DayWindow] = Field(default_factory=list)
    max_walking_m: int | None = Field(None, ge=500, le=30000)
    allow_taxi_fallback: bool = True
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

    @field_validator("interests")
    @classmethod
    def normalized_interests(cls, value: list[str]) -> list[str]:
        return interest_list(value)  # 旧行程里的“茶文化”读出来即为“特色文化”

    @field_validator("city")
    @classmethod
    def known_city(cls, value: str) -> str:
        from .cities import city_config
        city_config(value)
        return value

    @model_validator(mode="before")
    @classmethod
    def default_budget(cls, data):
        # 没说预算时按天数估（每天人均 DEFAULT_DAILY_BUDGET 元），不再一律 1500
        if isinstance(data, dict) and data.get("budget") in (None, ""):
            try:
                days = int(data.get("days") or 2)
            except (TypeError, ValueError):
                return data  # 天数格式不对交给字段校验报错
            data = {**data, "budget": DEFAULT_DAILY_BUDGET * min(5, max(1, days))}
        return data

    @model_validator(mode="before")
    @classmethod
    def legacy_dietary(cls, data):
        # 旧行程、旧脚本或外部 AI 仍可能传 dietary：含"不吃辣"即视为不吃辣，其余忌口不再支持，直接丢弃
        if isinstance(data, dict) and "dietary" in data:
            data = dict(data)
            legacy = data.pop("dietary") or []
            if "spice" not in data and "不吃辣" in legacy:
                data["spice"] = "no"
        return data

    @model_validator(mode="before")
    @classmethod
    def city_default_origin(cls, data):
        # 没给起点时用所选城市的默认起点，而不是调用方上下文的城市
        if isinstance(data, dict) and data.get("hotel") is None:
            from .cities import city_config
            try:
                origin = city_config(data.get("city") or _context_city()).default_origin
            except ValueError:
                return data  # 未知城市交给字段校验报错
            data = {**data, "hotel": dict(origin)}
        return data

    @model_validator(mode="after")
    def valid_preferences(self):
        from .cities import city_config
        settings = city_config(self.city)
        if not settings.contains(self.hotel.lat, self.hotel.lon):
            raise ValueError(f"起点须在{settings.name}范围内")
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
    open_months: list[int] = Field(default_factory=list)
    closed_weekdays: list[int] = Field(default_factory=list)
    closed_dates: list[Date] = Field(default_factory=list)
    open_dates: list[Date] = Field(default_factory=list)
    last_entry: int | None = None
    booking_required: bool | None = None
    source_url: str = ""
    verified_at: Date | None = None
    holiday_unknown: bool = True
    # 只在 open_dates 列出的日期开放（补充地点逐日准入结果）；未列出或未填日期一律不可排程
    dates_listed_only: bool = False

    @field_validator("open_months")
    @classmethod
    def valid_months(cls, months: list[int]) -> list[int]:
        if any(month < 1 or month > 12 for month in months):
            raise ValueError("开放月份须为 1–12")
        return sorted(set(months))


class MealEditPreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    day: int = Field(ge=1, le=5)
    action: Literal["replace", "self_arranged"] = "replace"
    item_index: int | None = Field(None, ge=0, le=60)
    restaurant_id: int | None = None
    meal: Literal["lunch", "dinner"] | None = None
    estimated_cost: float | None = Field(None, ge=0, le=2000, allow_inf_nan=False)

    @model_validator(mode="after")
    def valid_edit(self) -> "MealEditPreviewRequest":
        if self.action == "replace" and (self.item_index is None or self.restaurant_id is None):
            raise ValueError("更换餐厅需提供原餐次和新餐厅")
        if self.action == "self_arranged" and (self.meal is None or self.estimated_cost is None):
            raise ValueError("自行安排需明确餐次和预留费用")
        return self


class MealEditCommitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=0)
    preview_id: str = Field(min_length=12, max_length=100)


class FieldEvidence(BaseModel):
    basis: Literal["dataset", "model_estimate", "rule", "curator_inference", "observed", "manual_review", "unknown"]
    reference: str
    source_date: Date | None = None
    reviewed_at: Date | None = None


class ExperienceCondition(BaseModel):
    label: str
    months: list[int] = Field(default_factory=list)
    start_min: int = Field(0, ge=0, le=1440)
    end_min: int = Field(1440, ge=0, le=1440)
    weather_required: bool = False
    reference: str

    @model_validator(mode="after")
    def valid_condition(self) -> "ExperienceCondition":
        if self.end_min <= self.start_min or any(month < 1 or month > 12 for month in self.months):
            raise ValueError("体验条件的时段或月份无效")
        return self


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
    region_id: str = ""
    region_label: str = ""
    experience_kind: Literal["independent", "continuous_walk", "full_day", "session"] = "independent"
    experience_conditions: list[ExperienceCondition] = Field(default_factory=list)
    field_evidence: dict[str, FieldEvidence] = Field(default_factory=dict)
    # 补充地点登记的别名；原库条目为空，名称匹配行为不变
    aliases: list[str] = Field(default_factory=list)
    catalog_source: Literal["chinatravel", "supplement"] = "chinatravel"
    popularity: Literal["landmark", "known", ""] = ""  # 知名度档位，见 POPULARITY_LABELS
    popularity_source: str = ""


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
    interests: list[str] = Field(default_factory=list)
    region_id: str = ""
    region_label: str = ""
    area_id: str = ""
    area_label: str = ""
    place_type: str = ""
    experience_id: str = ""
    experience_kind: str = "independent"
    member_ids: list[int] = Field(default_factory=list)
    available_days: list[int] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)


class Selection(BaseModel):
    poi_id: int
    reason: str
    source: Literal["llm", "rule", "repair", "jev", "external_ai"] = "llm"
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
    enter_point: tuple[float, float] | None = None  # 长堤这类一头进、另一头出的游览：进入处（BD-09）；None 即地点坐标
    leave_point: tuple[float, float] | None = None  # 离开处，下一段路从这里算
    travel_mode: str = ""
    travel_depart: int | None = None  # 这段路按几点出发查的（分钟，排时间线时记下；定稿、校验、裁判都按它查公交）
    cost: float = 0.0  # 门票或人均餐费
    reason: str = ""
    meal: Literal["lunch", "dinner"] | None = None
    travel_evidence: LegEvidence | None = None
    alternatives: list[MealAlternative] = Field(default_factory=list)
    notices: list[str] = Field(default_factory=list)
    evening: bool = False
    area: str = ""  # 所属片区名（景点），由时间线生成时填写
    meal_detour_min: int | None = None
    meal_context: Literal["restaurant", "park_exit"] = "restaurant"


class ReservedMeal(BaseModel):
    meal: Literal["lunch", "dinner"]
    start: int
    end: int
    reason: str
    inside_poi_id: int | None = None
    estimated_cost: float = Field(0, ge=0)
    status: Literal["pending", "self_arranged"] = "pending"


class DayPlan(BaseModel):
    day: int
    items: list[Item] = Field(default_factory=list)
    return_travel_min: int = 0
    return_depart: int | None = None  # 返程按几点出发查的（分钟）
    notes: list[str] = Field(default_factory=list)
    date: Date | None = None
    return_leg: LegEvidence | None = None
    walking_m: int = 0
    reserved_meals: list[ReservedMeal] = Field(default_factory=list)
    areas: list[str] = Field(default_factory=list)  # 当天到访片区名，按顺序


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
    constraint_status: Literal["passed", "failed"] | None = None
    arrangement_status: Literal["complete", "needs_confirmation", "missing"] = "missing"
    evidence_status: Literal["verified", "partial"] = "partial"
    complete_executable: bool = False

    @model_validator(mode="after")
    def derive_execution_status(self) -> "ValidationReport":
        if self.constraint_status is None:
            self.constraint_status = "passed" if self.passed else "failed"
        self.complete_executable = (
            self.constraint_status == "passed"
            and self.arrangement_status == "complete"
            and self.evidence_status == "verified"
        )
        return self

    @property
    def hard(self) -> list[Violation]:
        return [v for v in self.violations if v.severity == "hard"]


class ExternalSelection(BaseModel):
    """宿主 AI 只交候选 ID、理由与偏好权重，不能排时间。"""
    model_config = ConfigDict(extra="forbid")
    poi_id: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)
    priority: int = Field(0, ge=0, le=100)


# 补充景点独立数据合同；未接入旧POI数值字段前先保留未知值。
# 用户2026-10-18确认：来源未写截止日时，可按项目复核期限使用，最长为核对日起90天
REVIEW_HORIZON_DAYS = 90


class SupplementEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    basis: Literal["observed", "manual_review", "project_recommendation", "model_estimate"]
    origin: Literal["independent_publication", "user_survey", "project_planning", "amap"]
    authority: Literal["official", "operator", "observer", "project", "unknown"] = "unknown"
    reference: str = Field(min_length=1, max_length=2000)
    source_date: Date | None = None
    observed_at: Date | None = None
    reviewed_at: Date | None = None
    valid_from: Date | None = None
    valid_until: Date | None = None
    # source：来源给出的适用期；review_horizon：来源未写截止日，截止日是项目复核期限而非来源有效期
    validity_basis: Literal["source", "review_horizon"] = "source"
    # 由规则推断得出、不是来源直接陈述（如按条例推断免费全天）；展示为待确认，不算已核实
    inferred: bool = False
    storage_allowed: bool = False

    @model_validator(mode="after")
    def ordered_validity(self) -> "SupplementEvidence":
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("证据有效期倒置")
        if self.validity_basis == "review_horizon":
            if self.reviewed_at is None or self.valid_from is None or self.valid_until is None:
                raise ValueError("复核期限须有核对日、起始日和截止日")
            if self.valid_until > self.reviewed_at + timedelta(days=REVIEW_HORIZON_DAYS):
                raise ValueError(f"复核期限不能超过核对日起{REVIEW_HORIZON_DAYS}天")
        return self


class SupplementCoordinate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lon: float = Field(ge=-180, le=180, allow_inf_nan=False)
    crs: Literal["GCJ02", "BD09", "WGS84", "unknown"]


class SupplementInterval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: int = Field(ge=0, lt=1440, strict=True)
    end: int = Field(gt=0, le=1440, strict=True)

    @model_validator(mode="after")
    def continuous_window(self) -> "SupplementInterval":
        if self.end <= self.start:
            raise ValueError("跨夜/倒置时段首版不支持")
        return self


class SupplementRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_key: str = Field(pattern=r"^[a-z0-9][a-z0-9:._-]{2,119}$")
    internal_id: int | None = Field(default=None, ge=1_000_000, le=2**53-1, strict=True)
    name: str = Field(min_length=1, max_length=150)
    aliases: list[str] = Field(default_factory=list, max_length=30)
    district: Literal["上城区", "拱墅区", "西湖区", "滨江区", "萧山区", "余杭区",
                      "临平区", "钱塘区", "富阳区", "临安区", "桐庐县", "淳安县", "建德市"]
    area: str = ""
    state: Literal["needs_review", "published", "archived"] = "needs_review"
    kind: Literal["attraction", "subplace", "facility"] = "attraction"
    place_type: str = Field("", max_length=30)
    experience_group: str = ""
    position: SupplementCoordinate | None = None
    entrance: SupplementCoordinate | None = None
    ticket_price_upper: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    ticket_price_exact: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    weekly_opening: dict[int, list[SupplementInterval]] = Field(default_factory=dict)
    date_opening: dict[str, list[SupplementInterval]] = Field(default_factory=dict)
    closed_dates: list[Date] = Field(default_factory=list)
    duration_min_hours: float | None = Field(default=None, gt=0, le=24, allow_inf_nan=False)
    duration_max_hours: float | None = Field(default=None, gt=0, le=24, allow_inf_nan=False)
    duration_scope: str = ""
    interests: list[str] = Field(default_factory=list)
    audience: list[str] = Field(default_factory=list)
    evidence: dict[str, SupplementEvidence] = Field(default_factory=dict)
    source_conflicts: list[str] = Field(default_factory=list)
    review_note: str = ""

    @model_validator(mode="after")
    def consistent_fields(self) -> "SupplementRecord":
        if (self.ticket_price_exact is not None and (
                self.ticket_price_upper is None or self.ticket_price_exact > self.ticket_price_upper)):
            raise ValueError("精确票价不能缺少或超过已知上界")
        if (self.duration_min_hours is not None and self.duration_max_hours is not None
                and self.duration_min_hours > self.duration_max_hours):
            raise ValueError("规划停留区间倒置")
        if any(day not in range(7) for day in self.weekly_opening):
            raise ValueError("星期键只能0到6，0为周一")
        for key in self.date_opening:
            if Date.fromisoformat(key).isoformat() != key:
                raise ValueError("日期键须为YYYY-MM-DD")
        for intervals in [*self.weekly_opening.values(), *self.date_opening.values()]:
            ordered = sorted(intervals, key=lambda item: item.start)
            if any(a.end > b.start for a, b in zip(ordered, ordered[1:])):
                raise ValueError("开放区间不能重叠")
        return self


class PlanningAdmission(BaseModel):
    model_config = ConfigDict(extra="forbid")
    canonical_key: str
    ticket_price_upper: float | None = None
    ticket_price_exact: float | None = None
    duration_min_hours: float | None = None
    duration_max_hours: float | None = None
    position: SupplementCoordinate | None = None
    entrance: SupplementCoordinate | None = None
    admitted: bool = False
    issues: list[str] = Field(default_factory=list)
    allowed_dates: list[Date] = Field(default_factory=list)
    intervals: dict[str, list[SupplementInterval]] = Field(default_factory=dict)
    rejected_dates: dict[str, list[str]] = Field(default_factory=dict)


class CatalogSnapshot(BaseModel):
    """内容保存在不可变字符串中；每次读取返回新的模型，禁止跨任务原地修改。"""
    model_config = ConfigDict(frozen=True, extra="forbid")
    digest: str
    payload: str

    @model_validator(mode="after")
    def verified_payload(self) -> "CatalogSnapshot":
        import hashlib
        import json
        if hashlib.sha256(self.payload.encode()).hexdigest() != self.digest:
            raise ValueError("目录摘要不一致")
        data = json.loads(self.payload)
        if data.get("schema_version") != 1 or not data.get("base_digest"):
            raise ValueError("不支持的目录schema/缺原库摘要")
        ids, keys = set(), set()
        for item in data["records"]:
            record = SupplementRecord.model_validate(item)
            if (record.internal_id is None or record.internal_id in ids
                    or record.canonical_key in keys or record.state != "published"):
                raise ValueError("目录身份重复、缺失或未发布")
            ids.add(record.internal_id)
            keys.add(record.canonical_key)
        return self

    def records(self) -> list[SupplementRecord]:
        import json
        return [SupplementRecord.model_validate(item) for item in json.loads(self.payload)["records"]]

    def aliases(self) -> dict[str, tuple[int, ...]]:
        from .poi_catalog import normalize_alias
        result: dict[str, set[int]] = {}
        for record in self.records():
            for name in [record.name, *record.aliases]:
                key = normalize_alias(name)
                if key:
                    result.setdefault(key, set()).add(record.internal_id)
        return {key: tuple(sorted(ids)) for key, ids in result.items()}


class TripContext(BaseModel):
    """接入方（手机助手、导航 App、聊天 AI）知道的用户情况，都可不填；网页不传。开车暂不支持，所以不收“有没有车”。"""
    model_config = ConfigDict(extra="forbid")
    home_city: str | None = Field(None, max_length=40, description="常住城市（城市键或中文名）")
    usual_transport: Transport | None = None


class FieldView(BaseModel):
    key: str
    label: str
    display: str
    source: Literal["said", "form", "inferred", "default"]
    note: str = ""


class Fix(BaseModel):
    label: str
    patch: dict


class Issue(BaseModel):
    code: str
    level: Literal["blocker", "warning", "info"]
    message: str
    fields: list[str] = Field(default_factory=list)
    fixes: list[Fix] = Field(default_factory=list)


class Understanding(BaseModel):
    """规划前给用户确认的“我是这么理解的”：画像、每个字段的来源、需要留意的问题。"""
    profile: UserProfile
    fields: list[FieldView]
    issues: list[Issue]
