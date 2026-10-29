from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
import threading
import time
from dataclasses import dataclass, field


# 单次规划的高德 HTTP 上限（2026-10-20 用户让 Claude 定：120→200；杭州 20 画像实测均值约 84 次，4 天行程常用满 120）
PLAN_HTTP_LIMIT = 200
# 按天数放宽（2026-10-26 用户："最好不要估计"）：dev-12 中 5 天行程用满 200 次后，最后几段改用估算，
# 9% 的路段是估的。上限 = max(200, 80 + 60 × 天数)：1–2 天 200，3 天 260，5 天 380；定稿路段另按每天预留
PLAN_HTTP_PER_DAY = 60
PLAN_HTTP_BASE = 80
FINAL_LEGS_PER_DAY = 12  # 定稿与校验要查的路段，每天预留这么多次（不超过总额的四分之一）


def plan_http_limit(days: int | None) -> int:
    return max(PLAN_HTTP_LIMIT, PLAN_HTTP_BASE + PLAN_HTTP_PER_DAY * (days or 1))
# 单次规划给餐厅附近搜索预留的请求（每次规划最多搜 12 处），排路线不能占用（2026-10-20 用户：“先修这个”）：
# 原先路线查询可以把额度用满，修复回路后几轮再找餐厅时已无额度，整天缺餐（苏州 17 号 200/200 缺 4 餐）
RESTAURANT_RESERVE = 12
# 当前请求的用途：route（默认）或 restaurant；餐厅搜索在 restaurant_requests() 内发出
amap_purpose: ContextVar[str] = ContextVar("amap_purpose", default="route")


@contextmanager
def restaurant_requests():
    token = amap_purpose.set("restaurant")
    try:
        yield
    finally:
        amap_purpose.reset(token)


class RequestBudgetExceeded(RuntimeError):
    pass


@dataclass
class RequestBudget:
    limit: int | None = PLAN_HTTP_LIMIT
    used: int = 0
    ceiling: int | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancelled: threading.Event | None = None
    reserve: int = 0  # 留给餐厅搜索的请求数
    reserve_used: int = 0

    def cap(self, restaurant: bool = False) -> int | None:
        """当前用途最多可用到第几次：餐厅搜索只受总额度约束；其余还要扣掉排程试探上限和未用完的餐厅预留。"""
        if restaurant:
            return self.limit
        caps = [value for value in (self.limit, self.ceiling) if value is not None]
        if not caps:
            return None
        held = self.reserve - self.reserve_used if self.limit is not None else 0
        return min(min(caps), self.limit - held) if self.limit is not None else min(caps)

    def consume(self) -> None:
        with self.lock:
            if self.cancelled is not None and self.cancelled.is_set():
                raise RequestBudgetExceeded("任务已取消")
            restaurant = amap_purpose.get() == "restaurant"
            limit = self.cap(restaurant)
            if limit is not None and self.used >= limit:
                raise RequestBudgetExceeded("本次规划的数据查询额度已用完")
            self.used += 1
            if restaurant and self.reserve_used < self.reserve:
                self.reserve_used += 1


class SharedRateLimiter:
    def __init__(self, interval: float = 0.4):
        self.interval = interval
        self.next_at = 0.0
        self.lock = threading.Lock()

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            scheduled = max(now, self.next_at)
            self.next_at = scheduled + self.interval
        time.sleep(max(0.0, scheduled - now))


amap_limiter = SharedRateLimiter()


# MCP 一次逻辑操作内的全部高德请求共用；网页默认保持原行为。
operation_budget: ContextVar[RequestBudget | None] = ContextVar("operation_budget", default=None)
