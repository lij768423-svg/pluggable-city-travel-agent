"""规划会话：保存每次规划的完整结果和后续修改的各个版本，供"换餐厅后重新规划"与"撤销"使用。

- 只放在内存里：服务重启后会话失效，前端提示重新生成（不把含高德数据的结果落盘，见 amap.py 的留存约定）。
- 容量：最多 32 个会话、每个会话保留最近 12 个版本、6 小时未访问即过期。
- 版本号从 0 开始递增；修改必须基于当前版本（乐观锁），并发的第二个修改会得到 StaleRevision。
"""
from __future__ import annotations

import secrets
import threading
import time
from copy import deepcopy
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

MAX_SESSIONS = 32
MAX_REVISIONS = 12
TTL_SECONDS = 6 * 3600
PREVIEW_TTL_SECONDS = 10 * 60
MAX_PREVIEWS = 4


class SessionNotFound(KeyError):
    pass


class StaleRevision(ValueError):
    pass


class PreviewNotFound(ValueError):
    pass


@dataclass
class MealPreview:
    preview_id: str
    base_revision: int
    result: dict[str, Any]
    change: dict[str, Any]
    expires_at: float


@dataclass
class PlanSession:
    plan_id: str
    request: dict[str, Any]
    owner: str | None = None
    revisions: OrderedDict[int, dict[str, Any]] = field(default_factory=OrderedDict)
    current: int = 0
    next_revision: int = 0
    touched: float = field(default_factory=time.time)
    previews: OrderedDict[str, MealPreview] = field(default_factory=OrderedDict)

    def result(self, revision: int | None = None) -> dict[str, Any]:
        revision = self.current if revision is None else revision
        if revision not in self.revisions:
            raise SessionNotFound(f"版本 {revision} 已不存在")
        return self.revisions[revision]


class SessionStore:
    def __init__(self, max_sessions: int = MAX_SESSIONS, max_revisions: int = MAX_REVISIONS, ttl: float = TTL_SECONDS):
        self.max_sessions, self.max_revisions, self.ttl = max_sessions, max_revisions, ttl
        self._items: OrderedDict[str, PlanSession] = OrderedDict()
        self._lock = threading.Lock()

    def _expire(self) -> None:
        now = time.time()
        for plan_id in [key for key, session in self._items.items() if now - session.touched > self.ttl]:
            self._items.pop(plan_id)
        while len(self._items) > self.max_sessions:
            self._items.popitem(last=False)

    def create(self, result: dict[str, Any], request: dict[str, Any], owner: str | None = None) -> PlanSession:
        with self._lock:
            session = PlanSession(plan_id=secrets.token_urlsafe(9), request=dict(request), owner=owner)
            session.revisions[0] = result
            session.next_revision = 1
            self._items[session.plan_id] = session
            self._expire()
            return session

    def get(self, plan_id: str) -> PlanSession:
        with self._lock:
            self._expire()
            session = self._items.get(plan_id)
            if session is None:
                raise SessionNotFound(plan_id)
            session.touched = time.time()
            self._items.move_to_end(plan_id)
            return session

    def snapshot(self, plan_id: str):
        """把内容和revision一起读取，避免网页与MCP并发修改产生版本/内容错配。"""
        with self._lock:
            self._expire()
            session = self._items.get(plan_id)
            if session is None:
                raise SessionNotFound(plan_id)
            session.touched = time.time()
            return deepcopy(session.result()), deepcopy(session.request), session.current, session.owner

    def bind_history(self, plan_id: str, history_id: str) -> None:
        with self._lock:
            self._expire()
            session = self._items.get(plan_id)
            if session is not None:
                session.request["history_id"] = history_id

    def find_history(self, owner: str, history_id: str):
        with self._lock:
            self._expire()
            session = next((s for s in self._items.values() if s.owner == owner
                            and s.request.get("history_id") == history_id), None)
            if session is not None:
                session.touched = time.time()
                self._items.move_to_end(session.plan_id)
            return session

    def append(self, plan_id: str, base_revision: int, result: dict[str, Any]) -> int:
        """在 base_revision 之上追加新版本；base_revision 不是当前版本时拒绝（防止并发覆盖）。"""
        with self._lock:
            self._expire()
            session = self._items.get(plan_id)
            if session is None:
                raise SessionNotFound(plan_id)
            if base_revision != session.current:
                raise StaleRevision(f"当前版本是 {session.current}，不是 {base_revision}")
            revision = session.next_revision
            session.next_revision += 1
            session.revisions[revision] = result
            session.current = revision
            while len(session.revisions) > self.max_revisions:
                session.revisions.popitem(last=False)
            session.touched = time.time()
            return revision

    def claim(self, plan_id: str, guest_owner: str, owner: str) -> PlanSession:
        with self._lock:
            self._expire()
            session = self._items.get(plan_id)
            if session is None:
                raise SessionNotFound(plan_id)
            if session.owner not in (guest_owner, owner):
                raise PermissionError("行程不属于当前账号")
            session.owner = owner
            return session

    def add_preview(self, plan_id: str, base_revision: int, result: dict[str, Any], change: dict[str, Any]) -> MealPreview:
        with self._lock:
            self._expire()
            session = self._items.get(plan_id)
            if session is None:
                raise SessionNotFound(plan_id)
            if session.current != base_revision:
                raise StaleRevision()
            preview = MealPreview(secrets.token_urlsafe(18), base_revision, deepcopy(result), deepcopy(change),
                                  time.time() + PREVIEW_TTL_SECONDS)
            session.previews[preview.preview_id] = preview
            while len(session.previews) > MAX_PREVIEWS:
                session.previews.popitem(last=False)
            return deepcopy(preview)

    def get_preview(self, plan_id: str, base_revision: int, preview_id: str) -> MealPreview:
        with self._lock:
            self._expire()
            session = self._items.get(plan_id)
            if session is None:
                raise SessionNotFound(plan_id)
            if session.current != base_revision:
                raise StaleRevision()
            preview = session.previews.get(preview_id)
            if preview is None or preview.expires_at <= time.time():
                raise PreviewNotFound()
            if preview.base_revision != base_revision:
                raise StaleRevision()
            return deepcopy(preview)

    def commit_preview(self, plan_id: str, base_revision: int, preview_id: str, report) -> tuple[dict[str, Any], dict[str, Any], int]:
        if not report.passed or report.hard:
            raise ValueError("预览未通过硬校验")
        with self._lock:
            self._expire()
            session = self._items.get(plan_id)
            if session is None:
                raise SessionNotFound(plan_id)
            if session.current != base_revision:
                raise StaleRevision()
            preview = session.previews.get(preview_id)
            if preview is None or preview.expires_at <= time.time():
                raise PreviewNotFound()
            if preview.base_revision != base_revision:
                raise StaleRevision()
            if not preview.result["report"].passed or preview.result["report"].hard:
                raise ValueError("预览未通过硬校验")
            result = deepcopy(preview.result)
            result["report"] = report.model_copy(deep=True)
            revision = session.next_revision
            session.next_revision += 1
            session.revisions[revision] = result
            session.current = revision
            session.previews.clear()
            while len(session.revisions) > self.max_revisions:
                session.revisions.popitem(last=False)
            session.touched = time.time()
            return result, deepcopy(preview.change), revision

    def revert(self, plan_id: str, revision: int, base_revision: int) -> tuple[dict[str, Any], int]:
        """恢复旧内容并追加递增版本；拒绝过期的撤销请求。"""
        with self._lock:
            self._expire()
            session = self._items.get(plan_id)
            if session is None:
                raise SessionNotFound(plan_id)
            if base_revision != session.current:
                raise StaleRevision(f"当前版本是 {session.current}，不是 {base_revision}")
            if revision not in session.revisions:
                raise SessionNotFound(f"版本 {revision} 已不存在")
            result = session.revisions[revision]
            restored_revision = session.next_revision
            session.next_revision += 1
            session.revisions[restored_revision] = result
            session.current = restored_revision
            while len(session.revisions) > self.max_revisions:
                session.revisions.popitem(last=False)
            session.touched = time.time()
            return result, restored_revision


SESSIONS = SessionStore()
