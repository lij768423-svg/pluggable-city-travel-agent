from __future__ import annotations

from datetime import timedelta

from .schemas import POI, UserProfile


def trip_date(profile: UserProfile, day: int):
    return profile.start_date + timedelta(days=day - 1) if profile.start_date else None


def closed_on(poi: POI, profile: UserProfile, day: int) -> bool:
    date = trip_date(profile, day)
    rules = poi.opening_rules
    if date is None or rules is None:
        return False
    if date in rules.open_dates:
        return False
    return date in rules.closed_dates or date.weekday() in rules.closed_weekdays


def date_notices(poi: POI, profile: UserProfile, day: int) -> list[str]:
    review = [text for text in (poi.review_note, poi.seasonal_note) if text]
    if profile.start_date is None:
        return review + ["未填写出游日期，尚未核验当天开放情况"]
    if poi.opening_rules is None:
        return review + ["仅有历史开放时段，具体日期与预约要求待确认"]
    notes = review
    if poi.opening_rules.holiday_unknown:
        notes.append("常规开放规则已有来源，临时闭馆及节假日安排仍需确认")
    if poi.opening_rules.booking_required is True:
        notes.append("需要提前预约，本计划未代为预约")
    elif poi.opening_rules.booking_required is None:
        notes.append("预约要求待确认")
    return notes
