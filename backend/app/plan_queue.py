"""同时生成的行程数封顶，其余按先来后到排队（2026-10-29 用户：放到 Linux.do 前“按建议做”）。

高德 key 每秒最多 3 次请求，一份规划约 75 次；很多人同时点时一起抢只会都变慢，所以只同时生成几份。
排队的人每隔几秒收到一次自己排第几位；排队时离开页面就退出队伍，扣掉的次数由调用方退回。
"""
from __future__ import annotations

import threading
from collections import deque
from contextlib import contextmanager
from typing import Callable, Iterator

from . import config

WAIT_SECONDS = 5   # 排队时多久报一次位置；报位置也是在探测对方是否已离开


class QueueFull(Exception):
    """排队的人已到上限：不扣次数，请对方过几分钟再来。"""


class PlanQueue:
    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._waiting: deque[object] = deque()
        self._running = 0

    def check_room(self) -> None:
        if config.PLAN_CONCURRENCY > 0 and config.PLAN_QUEUE_MAX > 0:
            with self._cond:
                if self._running >= config.PLAN_CONCURRENCY and len(self._waiting) >= config.PLAN_QUEUE_MAX:
                    raise QueueFull()

    @contextmanager
    def slot(self, notify: Callable[[dict], None] | None = None, on_leave: Callable[[], None] | None = None) -> Iterator[None]:
        """轮到才进入；排队时 notify 报 {"type": "queue", "position": 第几位, "limit": 同时几份}，它抛出（对方已离开）就出队、调 on_leave 后再抛。"""
        limit = config.PLAN_CONCURRENCY
        if limit <= 0:
            yield
            return
        ticket = object()
        with self._cond:
            self._waiting.append(ticket)
            try:
                while not (self._waiting[0] is ticket and self._running < limit):
                    if notify:
                        notify({"type": "queue", "position": self._waiting.index(ticket) + 1, "limit": limit})
                    self._cond.wait(timeout=WAIT_SECONDS)
            except BaseException:
                self._waiting.remove(ticket)
                self._cond.notify_all()
                if on_leave:
                    on_leave()
                raise
            self._waiting.popleft()
            self._running += 1
            self._cond.notify_all()   # 后面的人前进了一位，马上告诉他们
        try:
            yield
        finally:
            with self._cond:
                self._running -= 1
                self._cond.notify_all()

    def snapshot(self) -> dict:
        with self._cond:
            return {"running": self._running, "waiting": len(self._waiting), "limit": config.PLAN_CONCURRENCY}


PLAN_QUEUE = PlanQueue()
