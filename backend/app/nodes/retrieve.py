"""检索：规则打分召回候选（可解释的分项得分会展示给用户/写进论文）。

score = 0.6·兴趣匹配 + 0.2·体力/人群匹配 + 0.2·距酒店衰减 − 门票惩罚；必去 +1。
先过滤明显不可行的（开放时长不足、单程过远、用户不去）。
"""
from __future__ import annotations

import math

from ..data import load_pois
from ..geo import haversine_km, travel
from ..matching import norm, same_attraction
from ..rules import PACE, activity_window
from ..schemas import POI, Candidate, UserProfile
from ..calendar import closed_on

TOP_K = 30
MAX_KM = 40  # 每天回酒店的前提下，单程超过约 40 km（如千岛湖）不现实
AUDIENCE_OF = {"family_kids": "亲子", "elderly": "老人", "couple": "情侣", "friends": "朋友", "solo": "独行"}


def _name_hit(name: str, wanted: list[str]) -> bool:
    n = norm(name)
    return any(norm(w) and (norm(w) in n or n in norm(w)) for w in wanted)


def max_stamina(p: UserProfile) -> int:
    m = PACE[p.pace]["max_stamina"]
    return min(m, 2) if p.companions in ("elderly", "family_kids") else m


def must_visit_ids(p: UserProfile) -> set[int]:
    """每个"必去"名称只对应一个最匹配的景点（如"西湖"不能把 12 个名字带西湖的景点都算作必去）。"""
    from ..matching import match_name
    names = {i: poi.name for i, poi in load_pois().items()}
    return {i for m in p.must_visit if (i := match_name(m, names)) is not None}


def score_poi(poi: POI, p: UserProfile, must_ids: set[int] | None = None) -> Candidate:
    if p.interests:
        hit = len(set(p.interests) & set(poi.interests))
        tag = min(1.0, hit / min(len(p.interests), 3))
    else:
        tag = 0.5
    st_fit = 1.0 if poi.stamina <= max_stamina(p) else 0.2
    aud = 1.0 if AUDIENCE_OF[p.companions] in poi.audience else 0.0
    fit = 0.7 * st_fit + 0.3 * aud
    d = haversine_km(p.hotel.lat, p.hotel.lon, poi.lat, poi.lon)
    dist = math.exp(-d / 8)
    price_pen = 0.3 if poi.price > p.budget * 0.25 else 0.0
    must = 1.0 if poi.id in (must_ids if must_ids is not None else must_visit_ids(p)) else 0.0
    s = 0.6 * tag + 0.2 * fit + 0.2 * dist - price_pen + must
    return Candidate(poi_id=poi.id, score=round(s, 4), breakdown={
        "interest": round(tag, 3), "fit": round(fit, 3), "distance_km": round(d, 2),
        "distance": round(dist, 3), "price_penalty": price_pen, "must_visit": must})


def feasible_alone(poi: POI, p: UserProfile) -> bool:
    """单独去这一个景点是否可能：开放时长够、来回 + 游玩放得进一天。"""
    if not poi.recommendable:
        return False
    if haversine_km(p.hotel.lat, p.hotel.lon, poi.lat, poi.lon) > MAX_KM:
        return False
    go, _, _ = travel(p.hotel.lat, p.hotel.lon, poi.lat, poi.lon)
    need = poi.min_hours * 60
    for day in range(1, p.days + 1):
        c = activity_window(p, day)
        open_from, open_to = max(poi.open_min, c["start"] + go), min(poi.close_min, c["end"] - go)
        if not closed_on(poi, p, day) and open_to - open_from >= need:
            return True
    return False


def retrieve(p: UserProfile, k: int = TOP_K) -> list[Candidate]:
    cands = []
    must = must_visit_ids(p)
    for poi in load_pois().values():
        if poi.family_only and p.companions != "family_kids" and poi.id not in must:
            continue
        if set(p.interests) == {"主题乐园"} and "主题乐园" not in poi.interests and poi.id not in must:
            continue
        if _name_hit(poi.name, p.avoid) or not feasible_alone(poi, p):
            continue
        cands.append(score_poi(poi, p, must))
    cands.sort(key=lambda c: -c.score)
    places = load_pois()
    unique: list[Candidate] = []
    for candidate in cands:
        if not any(same_attraction(places[candidate.poi_id], places[existing.poi_id]) for existing in unique):
            unique.append(candidate)
    return unique[:k]


def retrieve_node(state: dict) -> dict:
    from ..travel import make_travel
    from ..restaurants import RestaurantCatalog

    p = state["profile"]
    cands = retrieve(p)
    # 画像确定后建立本次规划的路程来源（排程、时间线、校验共用）；检索粗筛仍用估算模型
    route = state.get("travel") or make_travel(state.get("travel_source"), p.transport)
    src = "估算模型" if route.source == "estimate" else f"高德真实路线（{route.stats()['mode_name']}）"
    route.allow_taxi_fallback = p.allow_taxi_fallback
    return {"candidates": cands, "travel": route, "catalog":state.get("catalog") or RestaurantCatalog(route),
            "log": [f"检索：召回 {len(cands)} 个候选（规则打分）；路程来源：{src}"]}
