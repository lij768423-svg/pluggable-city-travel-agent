"""规则召回：可行性粗筛、连续体验去重、条件覆盖，然后按分数补齐。"""
from __future__ import annotations

import re

from collections import Counter
import math

from ..areas import area_index
from ..calendar import closed_on
from ..data import load_pois
from ..bundles import bundles, umbrella_of, visit_hours
from ..experiences import experience_key
from ..geo import haversine_km, travel
from ..matching import match_place, norm, same_attraction
from ..poi_semantics import experience_notices
from ..rules import PACE, activity_window
from ..schemas import POI, Candidate, UserProfile, interest_list, interest_tags

TOP_K = 30
AREA_MEMBERS = 6  # 片区补齐：每个片区最多纳入的成员数
THEME_NEAR_KM = 15  # 只选“主题乐园”时，起点这一范围内的乐园少于 天数×3 个，亲子游乐地点也纳入候选
MAX_KM = 40
AUDIENCE_OF = {"family_kids": "亲子", "elderly": "老人", "couple": "情侣", "friends": "朋友", "solo": "独行"}
# 知名度（2026-10-25 用户决定）：档位取值 × 熟悉程度权重加进召回分。第一次来最看重地标，来过居中，本地人最低；
# 兴趣含"小众安静"时反向，越有名越减分。用户点名的必去另有 +1，始终优先
POPULARITY_VALUE = {"landmark": 1.0, "known": 0.6, "": 0.0}
POPULARITY_WEIGHT = {"first": 0.3, "visited": 0.15, "local": 0.05}
NICHE_WEIGHT = 0.2


def popularity_bonus(poi: POI, profile: UserProfile) -> float:
    value = POPULARITY_VALUE[poi.popularity]
    if "小众安静" in profile.interests:
        return -NICHE_WEIGHT * value
    return POPULARITY_WEIGHT[profile.familiarity] * value


def needs_landmark(profile: UserProfile) -> bool:
    """第一次来且行程不少于 2 天：至少安排一处城市地标（用户回避的在召回时已排除）。"""
    return profile.familiarity == "first" and profile.days >= 2


def _name_hit(name: str, wanted: list[str]) -> bool:
    normalized = norm(name)
    return any(norm(word) and (norm(word) in normalized or normalized in norm(word)) for word in wanted)


def max_stamina(profile: UserProfile) -> int:
    maximum = PACE[profile.pace]["max_stamina"]
    return min(maximum, 2) if profile.companions in ("elderly", "family_kids") else maximum


def must_visit_ids(profile: UserProfile) -> set[int]:
    places = load_pois()
    mapping = bundles()
    # 必去说的是同一景区里的某一处（如灵隐寺）时，算作这个景区（见 app/bundles.py）
    return {umbrella_of(identifier, mapping) for name in profile.must_visit if (identifier := match_place(name, places)) is not None}


def score_poi(poi: POI, profile: UserProfile, must_ids: set[int] | None = None) -> Candidate:
    hit = len(set(profile.interests) & interest_tags(poi.interests))
    tag = min(1.0, hit / min(len(profile.interests), 3)) if profile.interests else 0.5
    stamina = 1.0 if poi.stamina <= max_stamina(profile) else 0.2
    audience = 1.0 if AUDIENCE_OF[profile.companions] in poi.audience else 0.0
    fit = 0.7 * stamina + 0.3 * audience
    distance_km = haversine_km(profile.hotel.lat, profile.hotel.lon, poi.lat, poi.lon)
    distance = math.exp(-distance_km / 8)
    price_penalty = 0.3 if poi.price > profile.budget * 0.25 else 0.0
    must = float(poi.id in (must_ids if must_ids is not None else must_visit_ids(profile)))
    notices = experience_notices(poi, profile)
    seasonal_penalty = 0.08 if notices else 0.0
    popularity = popularity_bonus(poi, profile)
    score = 0.6 * tag + 0.2 * fit + 0.2 * distance - price_penalty + must - seasonal_penalty + popularity
    areas = area_index(load_pois())
    return Candidate(poi_id=poi.id, score=round(score, 4), interests=interest_list(poi.interests),
        region_id=poi.region_id, region_label=poi.region_label, place_type=poi.type,
        area_id=areas.area_of(poi.id), area_label=areas.label(areas.area_of(poi.id)),
        experience_id=experience_key(poi), experience_kind=poi.experience_kind, notices=notices,
        breakdown={"interest": round(tag, 3), "fit": round(fit, 3), "distance_km": round(distance_km, 2),
                   "distance": round(distance, 3), "price_penalty": price_penalty,
                   "must_visit": must, "seasonal_penalty": seasonal_penalty, "popularity": round(popularity, 3)})


# 景点表里混进来的餐厅（2026-10-28 “望湖楼茶餐厅”午饭后又被当景点停留 75 分钟）：吃饭交给选餐，不当景点排
RESTAURANT_LISTING = re.compile(r"餐厅|饭店|酒家|酒楼|食府|面馆|饭馆|菜馆|大排档|食堂")


def restaurant_listing(poi: POI) -> bool:
    return poi.type in {"其它", "餐饮", "美食"} and bool(RESTAURANT_LISTING.search(poi.name))


def eligibility(poi: POI, profile: UserProfile) -> tuple[list[int], str | None]:
    if not poi.recommendable:
        return [], "not_recommendable"
    if restaurant_listing(poi):
        return [], "restaurant_listing"
    if haversine_km(profile.hotel.lat, profile.hotel.lon, poi.lat, poi.lon) > MAX_KM:
        return [], "distance_limit"
    from ..poi_semantics import visit_open_min
    go, _, _ = travel(profile.hotel.lat, profile.hotel.lon, poi.lat, poi.lon)
    days = []
    open_days = []
    for day in range(1, profile.days + 1):
        if closed_on(poi, profile, day):
            continue
        open_days.append(day)
        window = activity_window(profile, day)
        start = max(visit_open_min(poi), window["start"] + go)
        end = min(poi.close_min, window["end"] - go)
        if poi.opening_rules and poi.opening_rules.last_entry is not None and start > poi.opening_rules.last_entry:
            continue
        if end - start >= visit_hours(poi)[0] * 60:
            days.append(day)
    if days:
        return days, None
    if not open_days:
        return [], "date_closed"
    if poi.opening_rules and poi.opening_rules.last_entry is not None and all(
            max(visit_open_min(poi), activity_window(profile, day)["start"]) > poi.opening_rules.last_entry for day in open_days):
        return [], "last_entry"
    fits_without_travel = any(min(poi.close_min, activity_window(profile, day)["end"]) -
                             max(visit_open_min(poi), activity_window(profile, day)["start"]) >= visit_hours(poi)[0] * 60
                             for day in open_days)
    return [], "route_infeasible_estimate" if fits_without_travel else "time_window"


def feasible_alone(poi: POI, profile: UserProfile) -> bool:
    return bool(eligibility(poi, profile)[0])


def region_matches(place: POI, name: str) -> bool:
    return name in {place.region_id, place.region_label}


def coverage_entry(name: str, identifiers: set[int], eligible: set[int], recalled: set[int],
                   excluded: dict[int, str]) -> dict:
    possible = identifiers & eligible
    covered = identifiers & recalled
    status = "covered" if covered else "data_missing" if not identifiers else "conditions_unmatched" if not possible else "capacity"
    return {"name": name, "database_count": len(identifiers), "eligible_count": len(possible),
            "candidate_count": len(covered), "status": status,
            "reasons": dict(Counter(excluded[identifier] for identifier in identifiers if identifier in excluded))}


def retrieve_with_report(profile: UserProfile, k: int = TOP_K) -> tuple[list[Candidate], dict]:
    places = load_pois()
    grouped = bundles()
    must_mapping = {name: (None if (hit := match_place(name, places)) is None else umbrella_of(hit, grouped))
                    for name in dict.fromkeys(profile.must_visit)}
    must = {identifier for identifier in must_mapping.values() if identifier is not None}
    eligible = []
    excluded = {}
    theme_only = set(profile.interests) == {"主题乐园"}
    theme_relaxed = theme_only and sum(
        1 for poi in places.values() if poi.recommendable and "主题乐园" in poi.interests
        and haversine_km(profile.hotel.lat, profile.hotel.lon, poi.lat, poi.lon) <= THEME_NEAR_KM) < profile.days * 3
    for poi in places.values():
        reason = None
        bundle = grouped.get(poi.id)
        if bundle and bundle.umbrella != poi.id:
            reason = "bundled"  # 并进所在景区，一次游览
        elif _name_hit(poi.name, profile.avoid) or (bundle and any(_name_hit(name, profile.avoid) for name in bundle.names)):
            reason = "explicit_avoid"
        elif poi.family_only and profile.companions != "family_kids" and poi.id not in must:
            reason = "audience_mismatch"
        elif (theme_only and "主题乐园" not in poi.interests and poi.id not in must
              and not (theme_relaxed and "亲子游乐" in interest_tags(poi.interests))):
            reason = "theme_mismatch"
        elif poi.price > profile.budget:
            reason = "ticket_over_budget"
        days, feasibility = eligibility(poi, profile) if reason is None else ([], reason)
        if reason or feasibility:
            excluded[poi.id] = reason or feasibility
            continue
        if bundle:
            # 伞形景区按成员里最有名的那处和全部兴趣来打分，免得“灵隐寺”的名气和兴趣标签丢掉
            members = [places[identifier] for identifier in (poi.id, *bundle.member_ids) if identifier in places]
            poi = poi.model_copy(update={"interests": list(dict.fromkeys(tag for place in members for tag in place.interests)),
                                         "popularity": max((place.popularity for place in members), key=lambda value: POPULARITY_VALUE[value])})
        candidate = score_poi(poi, profile, must)
        candidate.available_days = days
        eligible.append(candidate)
    eligible.sort(key=lambda candidate: (-candidate.score, candidate.poi_id))
    unique = []
    aliases = []
    continuous = []
    preferred = sorted(eligible, key=lambda candidate: (
        candidate.poi_id not in must, places[candidate.poi_id].visit_role == "viewpoint", -candidate.score, candidate.poi_id))
    for candidate in preferred:
        place = places[candidate.poi_id]
        duplicate = next((existing for existing in unique
                          if same_attraction(place, places[existing.poi_id]) and candidate.poi_id not in must), None)
        same_group = [existing for existing in unique if place.visit_group and
                      places[existing.poi_id].visit_group == place.visit_group]
        redundant = same_group and set(place.interests) <= {interest for existing in same_group for interest in existing.interests}
        redundant = redundant and set(candidate.available_days) <= {day for existing in same_group for day in existing.available_days}
        redundant = redundant and not any(region_matches(place, name) and not any(
            region_matches(places[existing.poi_id], name) for existing in same_group) for name in profile.preferred_regions)
        if duplicate:
            aliases.append({"poi_id": candidate.poi_id, "representative": duplicate.poi_id})
        elif redundant and candidate.poi_id not in must:
            continuous.append({"poi_id": candidate.poi_id, "representative": same_group[0].poi_id})
        else:
            unique.append(candidate)
    unique.sort(key=lambda candidate: (-candidate.score, candidate.poi_id))
    for candidate in unique:
        group = places[candidate.poi_id].visit_group
        candidate.member_ids = [entry.poi_id for entry in eligible
                                if group and places[entry.poi_id].visit_group == group]
    limit = max(0, k)
    result = [candidate for candidate in unique if candidate.poi_id in must][:limit]
    landmark = None
    if needs_landmark(profile) and not any(places[candidate.poi_id].popularity == "landmark" for candidate in result):
        landmark = next((candidate for candidate in unique if places[candidate.poi_id].popularity == "landmark"), None)
        if landmark is not None and len(result) < limit:
            result.append(landmark)  # 首访地标保证：分数最高的地标先进候选，选点阶段再确保选上一处
    remaining_interests = set(profile.interests) - {interest for candidate in result for interest in candidate.interests}
    remaining_regions = {name for name in profile.preferred_regions
                         if not any(region_matches(places[candidate.poi_id], name) for candidate in result)}
    while len(result) < limit and (remaining_interests or remaining_regions):
        options = [candidate for candidate in unique if candidate not in result]
        if not options:
            break
        def gain(candidate: Candidate) -> int:
            return len(remaining_interests & set(candidate.interests)) + sum(
                region_matches(places[candidate.poi_id], name) for name in remaining_regions)
        addition = max(options, key=lambda candidate: (gain(candidate), candidate.score, -candidate.poi_id))
        if gain(addition) == 0:
            break
        result.append(addition)
        remaining_interests -= set(addition.interests)
        remaining_regions = {name for name in remaining_regions if not region_matches(places[addition.poi_id], name)}
    # 片区补齐：片区分（成员前三名分数之和）靠前的片区各纳入一组成员，让选点能按片区凑够一天
    by_area: dict[str, list[Candidate]] = {}
    for candidate in unique:
        by_area.setdefault(candidate.area_id, []).append(candidate)
    filled_areas = sorted(by_area, key=lambda area: (-sum(c.score for c in by_area[area][:3]), area))[:profile.days + 2]
    for area in filled_areas:
        for candidate in by_area[area][:AREA_MEMBERS]:
            if len(result) < limit and candidate not in result:
                result.append(candidate)
    for candidate in unique:
        if len(result) >= limit:
            break
        if candidate not in result:
            result.append(candidate)
    eligible_ids = {candidate.poi_id for candidate in eligible}
    recalled = {candidate.poi_id for candidate in result}
    interests = [coverage_entry(name, {identifier for identifier, place in places.items() if name in interest_tags(place.interests)},
                               eligible_ids, recalled, excluded) for name in dict.fromkeys(profile.interests)]
    regions = [coverage_entry(name, {identifier for identifier, place in places.items() if region_matches(place, name)},
                             eligible_ids, recalled, excluded) for name in dict.fromkeys(profile.preferred_regions)]
    required = [coverage_entry(name, {identifier} if identifier is not None else set(), eligible_ids, recalled, excluded)
                | {"poi_id": identifier} for name, identifier in must_mapping.items()]
    count = len(required)
    return result, {"strategy": "coverage_v1", "limit": limit, "database_count": len(places),
        "landmark_guarantee": {"needed": needs_landmark(profile), "added_id": landmark.poi_id if landmark else None,
                               "candidate_landmarks": sum(places[c.poi_id].popularity == "landmark" for c in result)},
        "eligible_count": len(eligible), "candidate_count": len(result), "interest_coverage": interests,
        "candidate_ids": [candidate.poi_id for candidate in result],
        "region_coverage": regions, "must_visit": required, "theme_relaxed": theme_relaxed,
        "area_coverage": [{"area_id": area, "label": by_area[area][0].area_label,
                           "candidate_count": sum(candidate.area_id == area for candidate in result)} for area in filled_areas],
        "must_visit_recall": {"numerator": sum(entry["status"] == "covered" for entry in required),
                              "denominator": count, "rate": sum(entry["status"] == "covered" for entry in required) / count if count else None},
        "excluded_counts": dict(Counter(excluded.values())), "alias_duplicates": aliases,
        "continuous_duplicates": continuous, "route_evidence": "estimated_prefilter_not_verified_navigation"}


def retrieve(profile: UserProfile, k: int = TOP_K) -> list[Candidate]:
    return retrieve_with_report(profile, k)[0]


def retrieve_node(state: dict) -> dict:
    from ..travel import make_travel
    from ..restaurants import RestaurantCatalog

    profile = state["profile"]
    candidates, report = retrieve_with_report(profile)
    route = state.get("travel") or make_travel(state.get("travel_source"), profile.transport, profile.days)
    source = "估算模型" if route.source == "estimate" else f"高德真实路线（{route.stats()['mode_name']}）"
    route.allow_taxi_fallback = profile.allow_taxi_fallback
    route.open_from(profile)
    covered = sum(entry["status"] == "covered" for entry in report["interest_coverage"])
    missing = [f"{entry['name']}：{entry['status']}" for entry in report["interest_coverage"] + report["region_coverage"] + report["must_visit"] if entry["status"] != "covered"]
    return {"candidates": candidates, "retrieval_report": report, "travel": route,
            "catalog": state.get("catalog") or RestaurantCatalog(route),
            "log": [f"检索：召回 {len(candidates)} 个候选，兴趣覆盖 {covered}/{len(report['interest_coverage'])}；连续体验去重 {len(report['continuous_duplicates'])}；路程来源：{source}"] + missing}
