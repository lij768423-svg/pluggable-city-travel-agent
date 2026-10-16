"""节奏与用餐规则常量（调度、校验、提示词共用）。"""
from __future__ import annotations

from .geo import hm


def activity_window(profile, day: int = 1) -> dict[str, int]:
    window = dict(PACE[profile.pace])
    if profile.include_evening and profile.day_end is None:
        evening_end = 20 * 60 + 30 if profile.companions in {"elderly", "family_kids"} or profile.pace == "relaxed" else 21 * 60
        window["end"] = max(window["end"], evening_end)
    if profile.day_start is not None:
        window["start"] = profile.day_start
    if profile.day_end is not None:
        window["end"] = profile.day_end
    for override in profile.daily_windows:
        if override.day == day:
            window.update(start=override.start, end=override.end)
    return window


def required_meals(profile, day: int = 1) -> list[str]:
    window = activity_window(profile, day)
    return [name for name, bounds in (("lunch", LUNCH), ("dinner", DINNER))
            if max(window["start"], bounds[0]) + MEAL_MIN <= window["end"] and window["start"] <= bounds[1]]


def walking_limit(profile) -> int:
    return profile.max_walking_m or {"elderly":3500, "family_kids":4500}.get(profile.companions, 8000)

# per_day: 每天景点数；start/end: 每天离开/回到酒店的时间窗（分钟）
PACE: dict[str, dict[str, int]] = {
    "relaxed": {"per_day": 3, "start": 9 * 60 + 30, "end": 19 * 60 + 30, "max_stamina": 2},
    "normal": {"per_day": 3, "start": 9 * 60, "end": 20 * 60, "max_stamina": 3},
    "intense": {"per_day": 4, "start": 8 * 60 + 30, "end": 21 * 60 + 30, "max_stamina": 3},
}
LUNCH = (11 * 60, 13 * 60 + 30)  # 午餐开始时间窗
DINNER = (17 * 60 + 30, 19 * 60 + 30)
MEAL_MIN = 60  # 用餐时长
TOL = 5  # 时间比较容差（分钟）


def day_window_text(pace: str) -> str:
    c = PACE[pace]
    text = f"每天活动时间约 {hm(c['start'])}–{hm(c['end'])}（含往返酒店），午餐 {hm(LUNCH[0])}–{hm(LUNCH[1])} 之间开始"
    return text + f"，晚餐 {hm(DINNER[0])}–{hm(min(DINNER[1], c['end'] - MEAL_MIN - 20))} 之间开始"
