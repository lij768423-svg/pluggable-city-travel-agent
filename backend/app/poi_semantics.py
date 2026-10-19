from __future__ import annotations

import math

from .calendar import trip_date
from .experiences import LOCALITY_CELL_DEGREES
from .schemas import POI, FieldEvidence, UserProfile


EVENING_START_MIN = 17 * 60


def evening_only(place: POI) -> bool:
    """晚间型为项目安排规则，不表示已经核验了商户或船班营业。"""
    return place.open_min >= 16 * 60 or any(word in place.name for word in ("夜游", "夜市"))


def visit_open_min(place: POI) -> int:
    """所有确定性时间线共用的最早游览时间，不写回目录。"""
    return max(place.open_min, EVENING_START_MIN) if evening_only(place) else place.open_min


def enrich_semantics(place: POI) -> POI:
    region = f"grid:{math.floor(place.lat / LOCALITY_CELL_DEGREES)}:{math.floor(place.lon / LOCALITY_CELL_DEGREES)}"
    kind = "full_day" if place.day_anchor else "continuous_walk" if place.visit_group else "independent"
    if "游船" in place.name and not place.day_anchor:
        kind = "session"
    evidence = dict(place.field_evidence)
    evidence["region"] = FieldEvidence(basis="rule", reference="app/poi_semantics.py:0.02-degree-coordinate-grid; not an administrative area")
    evidence["experience_kind"] = FieldEvidence(basis="curator_inference" if place.day_anchor or place.visit_group else "rule",
        reference="app/assets/poi_reviews.json" if place.day_anchor or place.visit_group else "app/poi_semantics.py:name/type classification; duration unchanged")
    for field in ("booking_required", "onsite_dining", "visit_group", "experience_conditions"):
        evidence.setdefault(field, FieldEvidence(basis="unknown", reference="未提供已核验记录"))
    return place.model_copy(update={"region_id": region, "region_label": place.visit_group_label or region,
                                   "experience_kind": kind, "field_evidence": evidence})


def experience_notices(place: POI, profile: UserProfile, day: int | None = None,
                       start: int | None = None, end: int | None = None) -> list[str]:
    from .rules import activity_window
    notices = []
    for condition in place.experience_conditions:
        dates = [trip_date(profile, number) for number in ([day] if day is not None else range(1, profile.days + 1))]
        month_ok = not condition.months or any(date and date.month in condition.months for date in dates)
        if start is None or end is None:
            time_ok = any(min(activity_window(profile, number)["end"], condition.end_min) >
                          max(activity_window(profile, number)["start"], condition.start_min)
                          for number in ([day] if day is not None else range(1, profile.days + 1)))
        else:
            time_ok = start >= condition.start_min and end <= condition.end_min
        if condition.months and not profile.start_date:
            notices.append(f"{condition.label}：出游月份未确定，体验条件待确认")
        elif not month_ok or not time_ok:
            notices.append(f"{condition.label}：所选月份或时段不满足体验条件，保留普通游览")
        elif condition.weather_required:
            notices.append(f"{condition.label}：尚未核验当日天气，不承诺该体验")
    return notices


def safe_reason(reason: str, place: POI, profile: UserProfile, day: int | None = None,
                start: int | None = None, end: int | None = None) -> str:
    if experience_notices(place, profile, day, start, end):
        return f"{place.type}；按普通游览安排，季节景观与天气另行确认"
    return reason
