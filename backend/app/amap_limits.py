from __future__ import annotations

from contextvars import ContextVar
import threading
import time
from dataclasses import dataclass, field


# 单次规划的高德 HTTP 上限（2026-10-20 用户让 Claude 定：120→200；杭州 20 画像实测均值约 84 次，4 天行程常用满 120）
PLAN_HTTP_LIMIT = 200


class RequestBudgetExceeded(RuntimeError):
    pass


@dataclass
class RequestBudget:
    limit: int | None = PLAN_HTTP_LIMIT
    used: int = 0
    ceiling: int | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    cancelled: threading.Event | None = None

    def consume(self) -> None:
        with self.lock:
            if self.cancelled is not None and self.cancelled.is_set():
                raise RequestBudgetExceeded("任务已取消")
            limit = min(value for value in (self.limit, self.ceiling) if value is not None) if any(value is not None for value in (self.limit, self.ceiling)) else None
            if limit is not None and self.used >= limit:
                raise RequestBudgetExceeded("本次规划的数据查询额度已用完")
            self.used += 1


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
