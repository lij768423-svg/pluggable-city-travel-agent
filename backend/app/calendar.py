from __future__ import annotations

from datetime import date, datetime, timedelta
import os
import re
from zoneinfo import ZoneInfo

from .schemas import POI, UserProfile

WEEKDAYS = "一二三四五六日"
CHINA = ZoneInfo("Asia/Shanghai")


def china_today() -> date:
    return datetime.now(CHINA).date()


def resolve_date_phrase(phrase: str, today: date) -> date | None:
    """“周末”“明天”“下周六”这类说法换成具体日期；认不出（如“国庆”）返回 None，不猜。

    周末取最近的周六，今天是周六或周日就取今天；“周X”取今天起最近的那一天；“下周X”“下周末”取下一周。
    """
    text = re.sub(r"\s", "", phrase or "")
    for word, offset in (("大后天", 3), ("后天", 2), ("明天", 1), ("明日", 1), ("今天", 0), ("今日", 0)):
        if word in text:
            return today + timedelta(days=offset)
    monday = today - timedelta(days=today.weekday())
    if re.search(r"下个?周末", text):
        return monday + timedelta(days=12)
    if "周末" in text:
        return today if today.weekday() >= 5 else monday + timedelta(days=5)
    match = re.search(r"(下个?)?(?:周|星期|礼拜)([一二三四五六日天])", text)
    if match is None:
        return None
    weekday = min("一二三四五六日天".index(match.group(2)), 6)
    if match.group(1):
        return monday + timedelta(days=7 + weekday)
    return today + timedelta(days=(weekday - today.weekday()) % 7)


def trip_date(profile: UserProfile, day: int):
    return profile.start_date + timedelta(days=day - 1) if profile.start_date else None


def reference_date(today: date | None = None) -> date:
    """没填出游日期时按哪天的公交班次查路线（2026-10-26 用户决定）：规划当天（中国时间）之后的下一个工作日。

    评测由 TTA_REFERENCE_DATE 冻结，采集与回放查的是同一天。开放与闭馆仍只看 trip_date，不用这个日期。
    """
    pinned = os.environ.get("TTA_REFERENCE_DATE")
    if pinned:
        return date.fromisoformat(pinned)
    day = (today or china_today()) + timedelta(days=1)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day


def date_label(value: date) -> str:
    return f"{value.month}月{value.day}日（周{WEEKDAYS[value.weekday()]}）"


def closed_on(poi: POI, profile: UserProfile, day: int) -> bool:
    date = trip_date(profile, day)
    rules = poi.opening_rules
    if rules is None:
        return False
    if date is None:
        return rules.dates_listed_only
    if date in rules.open_dates:
        return False
    if rules.dates_listed_only:
        return True
    if rules.open_months and date.month not in rules.open_months:
        return True
    return date in rules.closed_dates or date.weekday() in rules.closed_weekdays


def date_notices(poi: POI, profile: UserProfile, day: int) -> list[str]:
    from .poi_semantics import experience_notices
    review = [text for text in (poi.review_note, poi.seasonal_note) if text]
    review.extend(experience_notices(poi, profile, day))
    if profile.start_date is None:
        # 预约要求与日期无关，没填日期也要说（2026-10-29 灵隐：免票但须实名预约，行程里一直没提）
        booking = poi.opening_rules is not None and poi.opening_rules.booking_required is True
        return review + (["需要提前预约，本计划未代为预约"] if booking else []) + ["未填写出游日期，尚未核验当天开放情况"]
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
