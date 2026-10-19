"""规划用的路程来源（排程、时间线、校验共用一个实例，保证三者口径一致）。

source:
  - "estimate"：旧估算模型 geo.travel（直线 × 1.3 + 分段速度）。只用于消融对比。
  - "amap"：高德真实路程。scenario 为用户选的出行方式：taxi（打车）| transit（公交/地铁）。
live:
  - True：未缓存的路段联网查询（受单次规划调用上限 live_budget 约束）。
  - False：只读缓存，未缓存的路段用标定兜底模型（data/travel_calibration.json）——用于"补空档"等大量试探。
任何一段用了兜底模型都会计数（approx_legs），并在规划日志与页面中如实显示。
"""
from __future__ import annotations

import json
from functools import lru_cache
from contextlib import contextmanager
from datetime import timedelta
from typing import Any

from . import config
from .geo import SAME_PLACE_KM, haversine_km, travel as estimate_travel
from .amap_limits import RequestBudget, operation_budget
from .rules import MAX_INTERSTOP_RIDE_MIN
from .schemas import LegEvidence

# 标定文件缺失时的默认值：2026-10-14 用 406 段高德真实路线拟合（scripts/calibrate_travel.py）
DEFAULT_CAL = {"walk_detour": 1.362, "drive_min": {"a": 17.574, "b": 1.51}, "transit_min": {"a": 33.334, "b": 4.517},
               "taxi_cost": {"a": 1.894, "b": 4.952}}
MODE_NAMES = {"taxi": "打车", "transit": "公交/地铁"}
# 公交慢时改打车（2026-10-18 用户决定默认开启）：公交超过舒适线、打车含等车至少快这么多分钟才换
TAXI_UPGRADE_SAVING_MIN = 10
# 人均每天预算低于此值时只坐公交，不自动改打车
TAXI_UPGRADE_MIN_DAILY_BUDGET = 100
# 只把短途换成打车；单段车费超过此值（如下沙到市中心）继续坐公交/地铁
TAXI_UPGRADE_MAX_FARE = 30


@lru_cache(maxsize=1)
def calibration() -> dict[str, Any]:
    p = config.DATA_DIR / "travel_calibration.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else DEFAULT_CAL


def calibrated(lat1: float, lon1: float, lat2: float, lon2: float, scenario: str, walk_limit_m: int = 1500) -> tuple[int, str, float]:
    """兜底模型：以直线距离为自变量的线性拟合。"""
    from .amap import TAXI_WAIT_MIN, WALK_M_PER_MIN
    c = calibration()
    km = haversine_km(lat1, lon1, lat2, lon2)
    if km < SAME_PLACE_KM:
        return 0, "原地", 0.0
    walk_m = km * 1000 * c["walk_detour"]
    if walk_m <= walk_limit_m:
        return max(1, round(walk_m / WALK_M_PER_MIN)), "步行", 0.0
    if scenario == "transit":
        return round(c["transit_min"]["a"] + c["transit_min"]["b"] * km), "公交/地铁", 2.0
    cost = round(max(0.0, c["taxi_cost"]["a"] + c["taxi_cost"]["b"] * km), 1)
    return round(c["drive_min"]["a"] + c["drive_min"]["b"] * km) + TAXI_WAIT_MIN, "打车", cost


class Travel:
    def __init__(self, source: str = "amap", scenario: str = "taxi", router=None, live_budget: int | None = 120):
        self.source = source
        self.scenario = scenario if scenario in MODE_NAMES else "taxi"
        self.budget = operation_budget.get() or RequestBudget(live_budget)
        self.router = router
        if source == "amap" and router is None:
            from .amap import get_router
            self.router = get_router()
        if self.router is not None and hasattr(self.router, "fork"):
            self.router = self.router.fork(self.budget)
        self.live_budget = live_budget
        self._calls0 = self.router.calls if self.router else 0
        self.approx_legs = 0  # 定稿行程里用了兜底模型的路段数
        self.approx_planning = 0  # 求解矩阵/试探中用兜底模型的路段数（公交模式下属预期）
        self.errors: list[str] = []
        self.evidence: dict[tuple, LegEvidence] = {}
        self.final_keys: set[tuple] | None = None
        self.allow_taxi_fallback = False
        self.taxi_upgrade = False
        self.service_date = None
        self.departure_time = "09:00"
        self.walk_limit_m = 1500

    @contextmanager
    def for_day(self, profile, day: int):
        previous = self.service_date, self.departure_time
        self.set_day(profile, day)
        try:
            yield self
        finally:
            self.service_date, self.departure_time = previous
            if self.router:
                self.router.service_date, self.router.departure_time = previous

    def set_day(self, profile, day: int) -> None:
        from .rules import activity_window
        self.service_date = profile.start_date + timedelta(days=day - 1) if profile.start_date else None
        window = activity_window(profile, day)
        self.departure_time = f"{window['start'] // 60:02d}:{window['start'] % 60:02d}"
        self.allow_taxi_fallback = profile.allow_taxi_fallback
        self.taxi_upgrade = profile.allow_taxi_fallback and profile.budget / profile.days >= TAXI_UPGRADE_MIN_DAILY_BUDGET
        self.walk_limit_m = min(1500 if profile.companions in {"family_kids", "elderly"} else 2500, profile.max_walking_m or 2500)
        if self.router:
            self.router.service_date, self.router.departure_time = self.service_date, self.departure_time
            self.router.walk_limit_m = self.walk_limit_m

    def edge_key(self, lat1: float, lon1: float, lat2: float, lon2: float) -> tuple:
        return (round(lat1, 6), round(lon1, 6), round(lat2, 6), round(lon2, 6), self.scenario, self.service_date, self.departure_time, self.walk_limit_m)

    @property
    def live_calls(self) -> int:
        return self.budget.used

    def _can_go_live(self) -> bool:
        limits = [value for value in (self.live_budget, self.budget.ceiling) if value is not None]
        return not limits or self.live_calls < min(limits)

    @contextmanager
    def planning_limits(self):
        previous = self.budget.ceiling
        if self.live_budget is not None:
            limit = max(0, self.live_budget - min(24, self.live_budget // 4))
            self.budget.ceiling = min(previous, limit) if previous is not None else limit
        try:
            yield
        finally:
            self.budget.ceiling = previous

    def prefetch_walks(self, center: tuple[float, float], points: list[tuple[float, float]]) -> None:
        """一次请求查完多个地点到 center 的步行距离（距离测量接口一次最多 100 个起点、1 个终点）。

        反方向（center → 各点）同样写入缓存：步行路网基本是双向的，正反距离按相同处理。
        只查直线距离在步行上限内、且还没有缓存的点；查不到时静默跳过，后续逐段查询照常兜底。
        """
        router = self.router
        if self.source != "amap" or router is None or not hasattr(router, "_batch") or not self._can_go_live():
            return
        from .amap import AmapError, pt, straight_km
        c = pt(*center)
        near = sorted({p for p in (pt(*point) for point in points)
                       if p != c and straight_km(p, c) < self.walk_limit_m / 1000 and not router.has(f"walk|{p}|{c}")})
        if not near:
            return
        try:
            router._batch("walk", near, c)
        except AmapError:
            return
        for p in near:
            forward, reverse = f"walk|{p}|{c}", f"walk|{c}|{p}"
            if router.has(forward) and not router.has(reverse):
                router._put(reverse, router.cache[forward])

    def walk_known(self, lat1: float, lon1: float, lat2: float, lon2: float) -> bool:
        """缓存里已经确认能步行（或就是同一处）时返回 True；只读缓存，不发请求。"""
        if self.source != "amap" or self.router is None:
            return False
        from .amap import pt, straight_km
        o, d = pt(lat1, lon1), pt(lat2, lon2)
        if o == d or straight_km(o, d) < SAME_PLACE_KM:
            return True
        key = f"walk|{o}|{d}"
        return self.router.has(key) and self.router.cache[key]["m"] <= self.walk_limit_m

    def leg(self, lat1: float, lon1: float, lat2: float, lon2: float, live: bool = True) -> tuple[int, str, float]:
        detail = self.detail(lat1, lon1, lat2, lon2, live=live)
        return detail.minutes, detail.mode, detail.cost

    def detail(self, lat1: float, lon1: float, lat2: float, lon2: float, live: bool = True) -> LegEvidence:
        key = self.edge_key(lat1, lon1, lat2, lon2)
        existing = self.evidence.get(key)
        if existing is not None and (existing.source != "estimate" or not live or not self._can_go_live()):
            return existing
        minutes, mode, cost, source, walking, reason, available, fetched = self._resolve(lat1, lon1, lat2, lon2, live)
        detail = LegEvidence(minutes=minutes, mode=mode, cost=cost, walking_m=walking, source=source,
                             cost_source="free" if cost == 0 else "estimate" if mode == "打车" or source == "estimate" else "amap",
                             fetched_at=fetched, fallback_reason=reason, available=available, service_date=self.service_date)
        self.evidence[key] = detail
        self.approx_legs = sum(value.source == "estimate" for value in self.evidence.values()) if live else self.approx_legs
        return detail

    @staticmethod
    def _taxi_cost(drive: dict, o, d) -> float:
        cost = drive.get("taxi_cost")
        if cost is None:  # 距离测量接口不返回打车费，用标定的费用模型
            from .amap import straight_km
            c = calibration()["taxi_cost"]
            cost = round(max(0.0, c["a"] + c["b"] * straight_km(o, d)), 1)
        return cost

    def _taxi_upgrade(self, o, d, coordinates: tuple[float, float, float, float], transit_min: int, live: bool):
        """公交超过舒适线且打车明显更快时，返回打车路段；否则 None。

        排程试探（不联网）时打车未缓存，就用标定模型估打车，来源如实标为估算；定稿路段会再联网核实。
        """
        from .amap import TAXI_WAIT_MIN
        if not self.taxi_upgrade or transit_min <= MAX_INTERSTOP_RIDE_MIN:
            return None
        drive = self.router.drive(o, d, live=live)
        if drive is not None:
            minutes, fare, source = round(drive["min"]) + TAXI_WAIT_MIN, self._taxi_cost(drive, o, d), "amap"
            fetched = self.router.records.get(f"drive|{o}|{d}", {}).get("fetched_at")
        elif not live:
            minutes, mode, fare = calibrated(*coordinates, "taxi", self.walk_limit_m)
            if mode != "打车":
                return None
            source, fetched = "estimate", None
        else:
            return None
        if minutes > transit_min - TAXI_UPGRADE_SAVING_MIN or fare > TAXI_UPGRADE_MAX_FARE:
            return None
        return minutes, "打车", fare, source, 0, f"公交约{transit_min}分钟，改打车更快", True, fetched

    def _resolve(self, lat1: float, lon1: float, lat2: float, lon2: float, live: bool):
        if self.source == "estimate":
            minutes, mode, cost = estimate_travel(lat1, lon1, lat2, lon2, self.walk_limit_m)
            return minutes, mode, cost, "estimate", round(minutes * 75) if mode == "步行" else 0, "使用估算模型", True, None
        from .amap import TAXI_WAIT_MIN, WALK_M_PER_MIN, AmapError, pt, straight_km
        o, d = pt(lat1, lon1), pt(lat2, lon2)
        if o == d or straight_km(o, d) < SAME_PLACE_KM:
            return 0, "原地", 0.0, "same_place", 0, None, True, None
        final = live  # 定稿路段（时间线/校验）才计入 approx_legs；试探与求解矩阵另计
        live = live and self._can_go_live()
        try:
            r = self.router
            w = r._walk_m(o, d, live=live)
            if w is None and live is False and straight_km(o, d) < self.walk_limit_m / 1000 and not r.has(f"walk|{o}|{d}"):
                raise LookupError  # 不知道能否步行 → 兜底
            if w is not None:
                return max(1, round(w / WALK_M_PER_MIN)), "步行", 0.0, "amap", int(w), None, True, r.records.get(f"walk|{o}|{d}", {}).get("fetched_at")
            if self.scenario == "transit":
                t = r.transit(o, d, live=live)
                if t is False:
                    # 公交未缓存（排程试探）：已预取打车时用估算公交比较，避免求解器只看到慢公交
                    upgraded = self._taxi_upgrade(o, d, (lat1, lon1, lat2, lon2), calibrated(lat1, lon1, lat2, lon2, "transit", self.walk_limit_m)[0], False)
                    if upgraded:
                        return upgraded
                    raise LookupError
                if t is not None:
                    minutes = round(t["min"])
                    upgraded = self._taxi_upgrade(o, d, (lat1, lon1, lat2, lon2), minutes, live)
                    if upgraded:
                        return upgraded
                    return minutes, "公交/地铁", t["cost"] if t["cost"] is not None else 2.0, "amap", int(t.get("walk_m") or 0), None, True, r.records.get(r.transit_key(o, d), {}).get("fetched_at")
                if not self.allow_taxi_fallback:
                    minutes, mode, cost = calibrated(lat1, lon1, lat2, lon2, self.scenario, self.walk_limit_m)
                    return minutes, mode, cost, "estimate", 0, "未查到公交方案，未改为打车", False, None
                # 没有公交方案：按打车处理（如实标注）
            dr = r.drive(o, d, live=live)
            if dr is None:
                raise LookupError
            return round(dr["min"]) + TAXI_WAIT_MIN, "打车", self._taxi_cost(dr, o, d), "amap", 0, "公交不可用，已按允许的打车替代" if self.scenario == "transit" else None, True, r.records.get(f"drive|{o}|{d}", {}).get("fetched_at")
        except (LookupError, AmapError) as e:
            if self.router.offline:
                from .amap import MissingSnapshotError
                raise MissingSnapshotError("离线快照不完整，禁止用估算冒充路线回放") from None
            if isinstance(e, AmapError):
                self.errors.append(str(e)[:120])
            if final:
                self.approx_legs += 1
            else:
                self.approx_planning += 1
            minutes, mode, cost = calibrated(lat1, lon1, lat2, lon2, self.scenario, self.walk_limit_m)
            return minutes, mode, cost, "estimate", round(minutes * WALK_M_PER_MIN) if mode == "步行" else 0, str(e) or ("请求额度用完或快照缺少路段"), True, None

    def finalize(self, itinerary, profile, restaurants) -> None:
        from .data import load_pois
        self.final_keys = set()
        for day in itinerary.days:
            with self.for_day(profile, day.day):
                position = (profile.hotel.lat, profile.hotel.lon)
                day.walking_m = 0
                for item in day.items:
                    place = load_pois().get(item.ref_id) if item.kind == "poi" else restaurants.get(item.ref_id)
                    if place is None:
                        continue
                    destination = (place.lat, place.lon)
                    item.travel_evidence = self.detail(*position, *destination)
                    day.walking_m += item.travel_evidence.walking_m + (getattr(place, "walking_m", None) or 0)
                    self.final_keys.add(self.edge_key(*position, *destination))
                    position = destination
                if day.items:
                    destination = (profile.hotel.lat, profile.hotel.lon)
                    day.return_leg = self.detail(*position, *destination)
                    day.walking_m += day.return_leg.walking_m
                    self.final_keys.add(self.edge_key(*position, *destination))
        self.approx_legs = sum(self.evidence[key].source == "estimate" for key in self.final_keys)

    def matrix(self, points: list[tuple[float, float]]) -> list[list[int]]:
        with self.planning_limits():
            return self._matrix(points)

    def _matrix(self, points: list[tuple[float, float]]) -> list[list[int]]:
        """求解器用的分钟矩阵。打车：批量预取后全部用真实数据；公交：已缓存的用真实值，其余用兜底模型（不逐段联网）。"""
        from .progress import tick_add
        if self.source == "estimate":
            rows = []
            for a in points:
                rows.append([estimate_travel(a[0], a[1], b[0], b[1], self.walk_limit_m)[0] for b in points])
                tick_add("legs", len(points), "已算 {n} 段交通")
            return rows
        from .amap import AmapError, pt
        if self._can_go_live():
            try:
                pts = [pt(*p) for p in points]
                self.router.prefetch([(o, d) for o in pts for d in pts], need_drive=self.scenario == "taxi" or self.taxi_upgrade)
            except AmapError as e:
                self.errors.append(str(e)[:120])
        live = self.scenario == "taxi"
        rows = []
        for a in points:
            rows.append([self.leg(a[0], a[1], b[0], b[1], live=live)[0] if a != b else 0 for b in points])
            tick_add("legs", len(points), "已算 {n} 段交通")
        return rows

    def stats(self) -> dict[str, Any]:
        return {"source": self.source, "scenario": self.scenario, "mode_name": MODE_NAMES[self.scenario],
                "live_calls": self.live_calls, "approx_legs": self.approx_legs, "approx_planning": self.approx_planning,
                "errors": list(dict.fromkeys(self.errors))[:3], "final_legs": len(self.final_keys or []),
                "cache_policy": "explicit_snapshot" if self.router and self.router.offline else "memory_15min",
                "live_budget": self.live_budget}


def make_travel(source: str | None, scenario: str) -> Travel:
    """source=None 时：配置了 Web服务 key 就用高德，否则退回估算模型。"""
    if source is None:
        source = "amap" if config.AMAP_WEB_KEY else "estimate"
    return Travel(source, scenario)
