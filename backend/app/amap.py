"""高德 Web服务 REST：真实路程（排程与校验都以它为准；估算模型只做粗筛与兜底）。

- 输入坐标为数据集的 BD-09，请求前转 GCJ-02。
- 步行：用"距离测量"接口（type=3）批量取真实步行距离，时间 = 距离 / 75 m/min
  （高德步行路线规划的 ETA 恒为该速度：2026-10-14 对 408 段缓存实测 p10 = p90 = 75）。
  步行距离一定不小于直线距离，所以直线 ≥ 1.5 km 的路段不查步行。
- 驾车：距离测量 type=1 批量（与驾车路线规划的时长一致，已实测）；打车 = 驾车时长 + 5 分钟叫车。
- 公交：没有批量接口，只能逐段查路线规划（最快方案）。
- 个人 key 的 QPS 上限实测为 3（5 QPS 时 40% 被拒），这里限速 3 QPS 并对限流码重试。
- 全部结果写入 data/amap_cache.jsonl（键与旧版兼容）：评测可复现，也节省配额。
"""
from __future__ import annotations

import json
import copy
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Callable, Iterable
from typing import Any

import httpx

from . import config
from .geo import SAME_PLACE_KM, bd09_to_gcj02, haversine_km
from .amap_limits import RequestBudget, RequestBudgetExceeded, amap_limiter

BASE = "https://restapi.amap.com"
WALK_MAX_M = 1500
WALK_M_PER_MIN = 75.0
TAXI_WAIT_MIN = 5
BATCH = 100  # 距离测量接口单次最多 100 个起点
RETRY_CODES = {"10019", "10020", "10021"}  # QPS / 并发超限

Pt = str  # "lon,lat"（GCJ-02，6 位小数）
TravelFn = Callable[[float, float, float, float], tuple[int, str, float]]


class AmapError(RuntimeError):
    pass


class AmapBudgetExceeded(AmapError):
    pass


class MissingSnapshotError(RuntimeError):
    pass


def pt(lat: float, lon: float) -> Pt:
    """BD-09 → GCJ-02 的 'lon,lat' 字符串（缓存键与请求参数共用）。"""
    g = bd09_to_gcj02(lat, lon)
    return f"{g[1]:.6f},{g[0]:.6f}"


def straight_km(o: Pt, d: Pt) -> float:
    (lo1, la1), (lo2, la2) = (map(float, o.split(",")), map(float, d.split(",")))
    return haversine_km(la1, lo1, la2, lo2)


class AmapRouter:
    def __init__(self, key: str | None = None, qps: float = 3.0, cache_path=None):
        self.key = key or config.AMAP_WEB_KEY or ("offline-placeholder" if os.environ.get("TTA_OFFLINE") == "1" else "")
        if not self.key:
            raise AmapError("AMAP_WEB_KEY 未设置（需要高德\"Web服务\"类型的 key）")
        self.offline = os.environ.get("TTA_OFFLINE") == "1"
        snapshot = os.environ.get("TTA_ROUTE_SNAPSHOT")
        self.cache_path = Path(snapshot) if self.offline and snapshot else (Path(cache_path) if cache_path else None)
        self.persist = cache_path is not None and not self.offline
        self.ttl = 900.0
        self.cache: dict[str, Any] = {}
        self.records: dict[str, dict[str, Any]] = {}
        if self.cache_path and self.cache_path.exists():
            for line in self.cache_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rec = json.loads(line)
                    self.cache[rec["k"]] = rec["v"]
                    self.records[rec["k"]] = rec
        self.interval = 1.0 / qps
        self.calls = 0
        self.cache_hits = 0
        self._last = 0.0
        self._lock = threading.Lock()
        self._client = httpx.Client(timeout=15)
        self.budget: RequestBudget | None = None
        self.service_date = None
        self.departure_time = "09:00"
        self.walk_limit_m = WALK_MAX_M

    def fork(self, budget: RequestBudget) -> AmapRouter:
        router = copy.copy(self)
        router.calls = router.cache_hits = 0
        router.budget = budget
        return router

    # ── 底层 ──────────────────────────────────────────
    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if self.offline:
            raise MissingSnapshotError(f"离线快照缺少 {path}，已禁止联网")
        for attempt in range(3):
            if self.interval >= 1 / 3:
                amap_limiter.wait()
            try:
                if self.budget:
                    self.budget.consume()
            except RequestBudgetExceeded as error:
                raise AmapBudgetExceeded(str(error)) from None
            self.calls += 1
            try:
                response = self._client.get(f"{BASE}/{path}", params={**params, "key": self.key})
                response.raise_for_status()
                d = response.json()
            except (httpx.HTTPError, ValueError):
                raise AmapError("高德连接失败，请稍后重试") from None
            if not isinstance(d, dict):
                raise AmapError("高德返回格式异常")
            if d.get("status") == "1":
                return d
            if str(d.get("infocode")) in RETRY_CODES:
                time.sleep(1.5 * (attempt + 1))
                continue
            code = str(d.get("infocode", "unknown"))
            raise AmapError(f"高德请求未成功（{code if code.isdigit() else 'unknown'}）")
        raise AmapError(f"{path}: 多次限流重试仍失败")

    def _put(self, k: str, v: Any) -> None:
        with self._lock:
            self.cache[k] = v
            record = {"k": k, "v": v, "schema": 2, "provider": "amap", "fetched_epoch": time.time(),
                      "fetched_at": datetime.now(timezone.utc).isoformat()}
            self.records[k] = record
            if self.persist and self.cache_path:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                with self.cache_path.open("a", encoding="utf-8") as cache_file:
                    cache_file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def has(self, k: str) -> bool:
        if k not in self.cache:
            return False
        record = self.records.get(k, {})
        return self.offline or time.time() - record.get("fetched_epoch", 0) < self.ttl

    def get(self, k: str) -> Any:
        self.cache_hits += 1
        return self.cache[k]

    # ── 批量预取：步行距离 / 驾车 ──────────────────────
    def prefetch(self, pairs: Iterable[tuple[Pt, Pt]], need_drive: bool = True) -> None:
        """按终点分组批量查询缺失的步行（直线 < 1.5 km）与驾车（步行 > 1.5 km）数据。"""
        pairs = [(o, d) for o, d in pairs if o != d]
        walk_need: dict[Pt, list[Pt]] = {}
        for o, d in pairs:
            if straight_km(o, d) < self.walk_limit_m / 1000 and not self.has(f"walk|{o}|{d}"):
                walk_need.setdefault(d, []).append(o)
        for d, origins in walk_need.items():
            self._batch("walk", origins, d)
        if not need_drive:
            return
        drive_need: dict[Pt, list[Pt]] = {}
        for o, d in pairs:
            if self._walk_m(o, d, live=False) is None and not self.has(f"drive|{o}|{d}"):
                drive_need.setdefault(d, []).append(o)
        for d, origins in drive_need.items():
            self._batch("drive", origins, d)

    def _batch(self, kind: str, origins: list[Pt], dest: Pt) -> None:
        for i in range(0, len(origins), BATCH):
            chunk = origins[i:i + BATCH]
            res = self._get("v3/distance", {"origins": "|".join(chunk), "destination": dest,
                                            "type": 3 if kind == "walk" else 1})["results"]
            for o, r in zip(chunk, res):
                if r.get("distance") in (None, ""):
                    continue
                m, s = int(r["distance"]), int(r.get("duration") or 0)
                if kind == "walk":
                    self._put(f"walk|{o}|{dest}", {"m": m, "min": m / WALK_M_PER_MIN})
                else:
                    self._put(f"drive|{o}|{dest}", {"m": m, "min": s / 60, "taxi_cost": None})

    # ── 单段 ─────────────────────────────────────────
    def _walk_m(self, o: Pt, d: Pt, live: bool = True) -> int | None:
        """实际步行距离在本次规划阈值内时返回米数，否则 None。"""
        if straight_km(o, d) >= self.walk_limit_m / 1000:
            return None
        k = f"walk|{o}|{d}"
        if not self.has(k):
            if not live:
                return None
            self._batch("walk", [o], d)
        v = self.get(k) if self.has(k) else None
        return v["m"] if v and v["m"] <= self.walk_limit_m else None

    def drive(self, o: Pt, d: Pt, live: bool = True) -> dict | None:
        k = f"drive|{o}|{d}"
        if not self.has(k):
            if not live:
                return None
            self._batch("drive", [o], d)
        return self.get(k) if self.has(k) else None

    def transit(self, o: Pt, d: Pt, live: bool = True) -> dict | None | bool:
        """返回公交最快方案；没有公交方案返回 None；未缓存且不允许联网返回 False。"""
        k = self.transit_key(o, d)
        if not self.has(k):
            if not live:
                return False
            params = {"origin": o, "destination": d, "city": "杭州", "cityd": "杭州"}
            if self.service_date:
                params.update(date=self.service_date.isoformat(), time=self.departure_time)
            ts = self._get("v3/direction/transit/integrated", params)["route"].get("transits") or []
            v = None
            if ts:
                best = min(ts, key=lambda t: int(t["duration"]))
                cost = best.get("cost")
                v = {"min": int(best["duration"]) / 60, "cost": float(cost) if cost not in (None, "", []) else None,
                     "walk_m": int(best.get("walking_distance") or 0)}
            self._put(k, v)
        return self.get(k)

    def transit_key(self, origin: Pt, destination: Pt) -> str:
        suffix = f"|{self.service_date.isoformat()}|{self.departure_time}" if self.service_date else ""
        return f"transit|{origin}|{destination}{suffix}"

    def leg_detail(self, lat1: float, lon1: float, lat2: float, lon2: float) -> dict[str, Any]:
        """评测标定用：一段路的步行 / 驾车 / 公交真实数据（BD-09 输入）。"""
        o, d = pt(lat1, lon1), pt(lat2, lon2)
        if o == d or straight_km(o, d) < SAME_PLACE_KM:
            return {"walk": {"m": 0, "min": 0}}
        w = self._walk_m(o, d)
        if w is not None:
            return {"walk": {"m": w, "min": w / WALK_M_PER_MIN}}
        return {"walk": {"m": None, "min": None}, "drive": self.drive(o, d), "transit": self.transit(o, d)}

    def travel_fn(self, scenario: str = "taxi") -> TravelFn:
        """与 geo.travel 同签名（BD-09 输入），全部用真实数据（联网）。scenario: taxi | transit。"""
        from .travel import Travel  # 避免循环导入
        return Travel("amap", scenario, router=self, live_budget=None).leg


_router: AmapRouter | None = None
_router_lock = threading.Lock()


def get_router() -> AmapRouter:
    global _router
    with _router_lock:
        if _router is None:
            _router = AmapRouter()
    return _router
