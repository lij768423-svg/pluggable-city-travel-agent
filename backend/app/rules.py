"""节奏与用餐规则常量（调度、校验、提示词共用）。"""
from __future__ import annotations

from .geo import hm


# 单段乘车舒适线：超过只提示并加排程成本，不再直接拦截（2026-10-18 用户决定改为软上限）
MAX_INTERSTOP_RIDE_MIN = 25
# 单段乘车封顶：超过仍硬性拦截
HARD_INTERSTOP_RIDE_MIN = 45
# 这是片区偏好阈值，不是硬约束；用于让排程优先把相邻地点放在同一天。
LOCALITY_PREFERENCE_MIN = 15
# 每天从起点出发的首段、回起点的尾段不限时长、不提示（2026-10-18 用户决定：出游前跑远路很常见）
MUST_VISIT_POLICY = "required"


def interstop_ride_allowed(minutes: int, mode: str) -> bool:
    return mode in {"步行", "原地"} or minutes <= HARD_INTERSTOP_RIDE_MIN


def interstop_ride_long(minutes: int, mode: str) -> bool:
    """超过舒适线但未到封顶的乘车段：允许排入，需提示。"""
    return mode not in {"步行", "原地"} and minutes > MAX_INTERSTOP_RIDE_MIN


def locality_transition_penalty(minutes: int, mode: str) -> int:
    """给跨片区的非步行连接一个软成本，步行仍按散步处理；超过舒适线的部分加倍计，让排程尽量避开长段。"""
    if mode in {"步行", "原地"}:
        return 0
    return max(0, minutes - LOCALITY_PREFERENCE_MIN) + 2 * max(0, minutes - MAX_INTERSTOP_RIDE_MIN)



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
