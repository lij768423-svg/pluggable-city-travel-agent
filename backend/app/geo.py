"""距离、通行时间估算与坐标系转换。

景点表为 BD-09，餐厅表为 GCJ-02，不能用景点核验结论覆盖所有表。
餐厅在 data.load_restaurants 中归一成内部 BD-09；地图和高德请求前转回 GCJ-02。
"""
from __future__ import annotations

import math

DETOUR = 1.3  # 直线距离 → 道路距离的绕行系数
WALK_KM = 1.5  # 不超过此距离按步行
WALK_KMH = 5.0
RIDE_KMH = 25.0  # 地铁/打车综合速度
RIDE_OVERHEAD_MIN = 10  # 候车、进出站
SAME_PLACE_KM = 0.02


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def travel(lat1: float, lon1: float, lat2: float, lon2: float, walk_limit_m: int = 1500) -> tuple[int, str, float]:
    """返回 (分钟, 方式, 费用元)。"""
    d = haversine_km(lat1, lon1, lat2, lon2) * DETOUR
    if d / DETOUR < SAME_PLACE_KM:
        return 0, "原地", 0.0
    if d <= walk_limit_m / 1000:
        return max(1, round(d / WALK_KMH * 60)), "步行", 0.0
    minutes = round(d / RIDE_KMH * 60) + RIDE_OVERHEAD_MIN
    cost = round(3 + 0.5 * d, 1)  # 公共交通/打车混合的粗略估算
    return minutes, "公交/打车", cost


def bd09_to_gcj02(lat: float, lon: float) -> tuple[float, float]:
    x, y = lon - 0.0065, lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * math.pi * 3000 / 180)
    t = math.atan2(y, x) - 0.000003 * math.cos(x * math.pi * 3000 / 180)
    return z * math.sin(t), z * math.cos(t)


def hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def gcj02_to_bd09(lat: float, lon: float) -> tuple[float, float]:
    z = math.sqrt(lon * lon + lat * lat) + 0.00002 * math.sin(lat * math.pi * 3000 / 180)
    angle = math.atan2(lat, lon) + 0.000003 * math.cos(lon * math.pi * 3000 / 180)
    return z * math.sin(angle) + 0.006, z * math.cos(angle) + 0.0065


def parse_hm(s: str) -> int:
    h, m = str(s).strip().split(":")[:2]
    return min(1440, int(h) * 60 + int(m))
