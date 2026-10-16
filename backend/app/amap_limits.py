from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field


class RequestBudgetExceeded(RuntimeError):
    pass


@dataclass
class RequestBudget:
    limit: int | None = 120
    used: int = 0
    ceiling: int | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def consume(self) -> None:
        with self.lock:
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
