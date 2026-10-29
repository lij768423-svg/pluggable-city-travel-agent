"""规划前的“我是这么理解的”（2026-10-28 用户决定）：一句话加表单 → 画像，每个字段记下从哪来
（你说的、你填的、推断、默认），再做不调用高德的可行性预检，列出需要留意的地方和一键修改。用户确认后才开始规划；网页与 MCP 共用。

用户往往只说一句很模糊的话，一直追问很烦：能推断的直接填上并标明依据，只有问题项才需要用户看。
开车只在用户明说时考虑；自驾路线（停车、取车）暂不支持，先按打车安排并提示。
"""
from __future__ import annotations

from datetime import date, timedelta
import math
import re
from typing import Any

from .bundles import bundles, umbrella_of
from .calendar import china_today, closed_on, date_label, resolve_date_phrase
from .cities import city_config, current_city
from .data import load_pois
from .geo import haversine_km, travel as estimate_travel
from .llm import LLM
from .matching import match_name, match_place, norm
from .presentation import hotel_presets
from .nodes.parse import HELPER_KEYS, parse_query
from .rules import HARD_INTERSTOP_RIDE_MIN, TYPICAL_DAILY_SPEND_P10, interstop_ride_allowed
from .travel import calibrated
from .walk_ends import walk_center
from .schemas import (COMPANION_LABELS, DEFAULT_DAILY_BUDGET, FAMILIARITY_LEVELS, PACE_LABELS, SPICE_LEVELS, TRANSPORT_LABELS, FieldView, Fix,
                      Issue, TripContext, Understanding, UserProfile)

FIELDS = (("city", "城市"), ("start_date", "日期"), ("days", "天数"), ("companions", "同行"), ("familiarity", "对这座城市"),
          ("budget", "预算"), ("transport", "出行"), ("hotel", "起点"), ("interests", "想玩"), ("pace", "节奏"),
          ("spice", "口味"), ("must_visit", "必去"), ("avoid", "不去"))
OPTIONAL = {"must_visit", "avoid"}  # 没说就不列
DEFAULT_NOTES = {"start_date": "没说哪天", "days": "没说几天", "companions": "没说和谁去",
                 "familiarity": "没说是否来过", "transport": "没说怎么出行", "hotel": "没说从哪出发：从第一站开始，去第一站和回去的路程不算",
                 "interests": "没说想玩什么", "pace": "没说节奏", "spice": "没说口味"}


def given(value: Any) -> bool:
    return value not in (None, "", [], {})


def display(key: str, profile: UserProfile) -> str:
    if key == "city":
        return city_config(profile.city).name
    if key == "start_date":
        return date_label(profile.start_date) if profile.start_date else "没定（公交按下一个工作日的班次查）"
    if key == "days":
        return f"{profile.days} 天"
    if key == "budget":
        # 预算是整趟的人均总额；写明天数和每天平均，免得被当成每天的预算
        return f"人均共 {profile.budget:.0f} 元（{profile.days} 天，平均每天 {profile.budget / profile.days:.0f} 元；含门票、餐饮、市内交通）"
    if key == "hotel":
        return profile.hotel.name if profile.origin_known else "不定（从第一站开始，结束后不算回程）"
    if key == "interests":
        return "、".join(profile.interests) or "综合推荐"
    if key in ("must_visit", "avoid"):
        return "、".join(getattr(profile, key))
    labels = {"companions": COMPANION_LABELS, "familiarity": FAMILIARITY_LEVELS, "transport": TRANSPORT_LABELS,
              "pace": PACE_LABELS, "spice": SPICE_LEVELS}
    return labels[key][getattr(profile, key)]


def resolve_origin(name: str, city: str, picked: list[dict[str, Any]] | None = None) -> dict[str, Any] | None:
    """用户说的出发地 → 起点：先找这轮对话里在表上搜过选过的地点，再找这座城市的预设起点，再找景点库；
    都找不到返回 None（不调用高德）。"""
    picked = [place for place in picked or [] if all(key in place for key in ("name", "lat", "lon"))]
    if (hit := match_name(name, {index: place["name"] for index, place in enumerate(picked)})) is not None:
        return {key: picked[hit][key] for key in ("name", "lat", "lon")}
    presets = hotel_presets(city)
    if (hit := match_name(name, {index: preset["name"] for index, preset in enumerate(presets)})) is not None:
        return {key: presets[hit][key] for key in ("name", "lat", "lon")}
    places = load_pois()
    if (hit := match_place(name, places)) is not None:
        return {"name": places[hit].name, "lat": places[hit].lat, "lon": places[hit].lon}
    return None


def understand(query: str, form: dict[str, Any] | None = None, context: TripContext | None = None,
               llm: LLM | None = None, today: date | None = None, places: list[dict[str, Any]] | None = None) -> Understanding:
    """网页：一句话先交给模型解析出用户明说的字段，再合表单、推断与预检。places：这轮对话里在表上搜过选过的起点。"""
    return explain(parse_query(query, llm), query, form, context, today, places)


def explain(stated: dict[str, Any], query: str = "", form: dict[str, Any] | None = None,
            context: TripContext | None = None, today: date | None = None,
            places: list[dict[str, Any]] | None = None) -> Understanding:
    """stated 是用户明说的字段（网页来自解析，MCP 来自宿主 AI 整理的画像）；不调用模型。"""
    today = today or china_today()
    said = {key: value for key, value in stated.items() if key not in HELPER_KEYS and given(value)}
    # 表单里的空列表是用户清空的（去掉全部必去、想玩改回不限），不能让一句话里说过的再顶回来
    form = {key: value for key, value in (form or {}).items() if given(value) or value == []}
    data, inferred = dict(said), {}
    if stated.get("date_phrase") and (resolved := resolve_date_phrase(stated["date_phrase"], today)):
        data["start_date"], inferred["start_date"] = resolved, f"你说“{stated['date_phrase']}”"
    city = form.get("city") or data.get("city") or current_city().key  # 没给城市时与画像的缺省城市一致
    origin_missing = None
    if origin := (stated.get("origin") or "").strip():
        if place := resolve_origin(origin, city, places):
            data["hotel"] = place
            if norm(place["name"]) == norm(origin):
                said["hotel"] = place
            else:
                inferred["hotel"] = f"你说从“{origin}”出发，按{place['name']}算"
        else:
            origin_missing = origin
    if context:
        name = city_config(city).name
        if context.home_city in {city, name} and "familiarity" not in data:
            data["familiarity"], inferred["familiarity"] = "local", f"常住城市是{name}"
        if context.usual_transport and "transport" not in data:
            data["transport"], inferred["transport"] = context.usual_transport, "按你平时常用的出行方式"
    if "hotel" not in form and "hotel" not in data and "origin_known" not in form:
        data["origin_known"] = False  # 没说也没选起点：不拿默认点当出发和回程的地方
    profile = UserProfile.model_validate({**data, **form})
    fields = []
    for key, label in FIELDS:
        if key in OPTIONAL and not getattr(profile, key):
            continue
        if key in form:
            source, note = "form", ""
        elif key in said:
            source, note = "said", ""
        elif key in inferred:
            source, note = "inferred", inferred[key]
        else:
            source, note = "default", DEFAULT_NOTES.get(key, "")
            if key == "budget":
                note = f"没说预算，按每天 {DEFAULT_DAILY_BUDGET} 元 × {profile.days} 天估"
        fields.append(FieldView(key=key, label=label, display=display(key, profile), source=source, note=note))
    issues = prechecks(profile, query, stated, today)
    if origin_missing and "hotel" not in form:
        issues.insert(0, Issue(code="origin_not_found", level="warning", fields=["hotel"],
                               message=f"没找到「{origin_missing}」这个出发地，先按{profile.hotel.name}算；可以换个叫法，或在问卷里用地图选"))
    return Understanding(profile=profile, fields=fields, issues=issues)


def prechecks(profile: UserProfile, query: str, stated: dict[str, Any], today: date) -> list[Issue]:
    """规划前就能看出的问题：不调用高德、不调用模型。"""
    issues: list[Issue] = []
    if profile.start_date and profile.start_date < today:
        tomorrow = today + timedelta(days=1)
        issues.append(Issue(code="date_past", level="blocker", message=f"出游日期 {date_label(profile.start_date)}已经过去",
                            fields=["start_date"], fixes=[Fix(label=f"改为明天 {date_label(tomorrow)}", patch={"start_date": tomorrow.isoformat()})]))
    floor = TYPICAL_DAILY_SPEND_P10[profile.transport]
    per_day = profile.budget / profile.days
    if per_day < floor:
        fixes = [Fix(label="改坐公交地铁", patch={"transport": "transit"})] if profile.transport == "taxi" else []
        need = math.ceil(profile.days * floor / 50) * 50
        fixes.append(Fix(label=f"预算改为人均共 {need} 元", patch={"budget": need}))
        issues.append(Issue(code="budget_tight", level="warning", fields=["budget", "transport"], fixes=fixes,
                            message=f"人均每天约 {per_day:.0f} 元，{TRANSPORT_LABELS[profile.transport]}出行的行程通常每天 {floor} 元以上；"
                                    "会先压低餐费、少选收费景点，仍可能超预算"))
    places = load_pois()
    for name in profile.must_visit:
        identifier = match_place(name, places)
        if identifier is None:
            issues.append(Issue(code="must_not_found", level="warning", fields=["must_visit"],
                                message=f"景点库里没找到「{name}」，规划时会跳过；可以换个叫法",
                                fixes=[Fix(label=f"去掉「{name}」", patch={"must_visit": [n for n in profile.must_visit if n != name]})]))
        elif profile.start_date and all(closed_on(places[identifier], profile, day) for day in range(1, profile.days + 1)):
            issues.append(Issue(code="must_closed", level="warning", fields=["must_visit", "start_date"],
                                message=f"「{name}」在你出游的这几天闭馆"))
    if issue := far_must_issue(profile, places):
        issues.append(issue)
    if re.search(r"自驾|开车|驾车|租车", query or ""):
        issues.append(Issue(code="no_driving", level="info", fields=["transport"], message="暂不支持自驾路线（停车、取车），先按打车安排"))
    if stated.get("days_requested"):
        issues.append(Issue(code="days_capped", level="info", fields=["days"],
                            message=f"最多规划 5 天，先按 5 天安排（你说的是 {stated['days_requested']} 天）"))
    return issues


def far_must_issue(profile: UserProfile, places: dict) -> Issue | None:
    """必去之间单段乘车超过封顶的排不进同一天（2026-10-29）：按“单段不超封顶”把必去连成几组，组数多于天数就提示。

    不调用高德：估算模型和城市标定模型取较快的那个，两个都超过封顶才算排不到一起，宁可漏报也不误报。
    """
    grouped = bundles()
    named: dict[int, str] = {}
    for name in profile.must_visit:
        if (hit := match_place(name, places)) is not None:
            umbrella = umbrella_of(hit, grouped)
            named.setdefault(umbrella if umbrella in places else hit, name)
    if len(named) < 2:
        return None

    def ride(a: int, b: int, transport: str) -> tuple[int, str]:
        points = (*walk_center(places[a]), *walk_center(places[b]))
        return min(estimate_travel(*points)[:2], calibrated(*points, transport)[:2])

    def apart(transport: str) -> list[list[int]]:
        groups: list[list[int]] = []
        for identifier in named:
            linked = [group for group in groups if any(interstop_ride_allowed(*ride(identifier, other, transport)) for other in group)]
            groups = [group for group in groups if group not in linked] + [[identifier, *(x for group in linked for x in group)]]
        return groups

    groups = apart(profile.transport)
    if len(groups) <= profile.days:
        return None
    a, b = min(((x, y) for index, group in enumerate(groups) for other in groups[index + 1:] for x in group for y in other),
               key=lambda pair: ride(*pair, profile.transport)[0])
    km = haversine_km(places[a].lat, places[a].lon, places[b].lat, places[b].lon)
    detail = (f"「{named[a]}」和「{named[b]}」相距约 {km:.0f} 公里，{TRANSPORT_LABELS[profile.transport]}估计要 "
              f"{ride(a, b, profile.transport)[0]} 分钟以上")
    message = (f"{detail}，超过景点之间单段 {HARD_INTERSTOP_RIDE_MIN} 分钟的上限（只有每天出发和返程不限），排不进同一天"
               if len(groups) == 2 else
               f"必去分在 {len(groups)} 处彼此较远的地方（如{detail}），超过景点之间单段 {HARD_INTERSTOP_RIDE_MIN} 分钟的上限"
               "（只有每天出发和返程不限），每处要单独占一天")
    message += f"；按 {profile.days} 天规划会有必去排不上"
    fixes = [Fix(label=f"改为 {len(groups)} 天", patch={"days": len(groups)})] if len(groups) <= 5 else []
    if profile.transport == "transit" and len(apart("taxi")) <= profile.days:
        message += f"；改打车估计约 {ride(a, b, 'taxi')[0]} 分钟，有望排到一起"
        fixes.append(Fix(label="改打车", patch={"transport": "taxi"}))
    return Issue(code="must_far_apart", level="warning", fields=["must_visit", "days", "transport"], message=message, fixes=fixes)
