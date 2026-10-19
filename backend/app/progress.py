from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import time
import traceback
from collections.abc import Callable
from threading import Event
from typing import Any


class PlanningStopped(Exception):
    pass


class Ticker:
    """节点执行途中上报“已经做了多少”。按 interval 限流，避免刷屏。"""

    def __init__(self, sink, stage: str = "", interval: float = 0.25):
        self.sink, self.stage, self.interval = sink, stage, interval
        self.counts: dict[str, int] = {}
        self.seen: dict[str, set] = {}
        self.last: float | None = None

    def wants(self, only) -> bool:
        return not only or self.stage in only

    def say(self, detail: str) -> None:
        now = time.monotonic()
        if self.last is not None and now - self.last < self.interval:
            return
        self.last = now
        self.sink(detail)

    def add(self, key: str, amount: int, template: str) -> None:
        self.counts[key] = self.counts.get(key, 0) + amount
        self.say(template.format(n=self.counts[key]))

    def track(self, key: str, ids, template: str) -> None:
        """按去重后的个数上报，适合同一批对象被反复扫描的情况。"""
        bucket = self.seen.setdefault(key, set())
        bucket.update(ids)
        self.say(template.format(n=len(bucket)))


_TICKER: contextvars.ContextVar[Ticker | None] = contextvars.ContextVar("plan_ticker", default=None)


def use_ticker(ticker: Ticker | None):
    return _TICKER.set(ticker)


def release_ticker(token) -> None:
    _TICKER.reset(token)


def tick(detail: str, only=()) -> None:
    """没有订阅者时什么都不做，所以评测和测试里可以随便调用。only 限定只在某些步骤里上报。"""
    ticker = _TICKER.get()
    if ticker is not None and ticker.wants(only):
        ticker.say(detail)


def tick_add(key: str, amount: int, template: str, only=()) -> None:
    ticker = _TICKER.get()
    if ticker is not None and ticker.wants(only):
        ticker.add(key, amount, template)


def tick_track(key: str, ids, template: str, only=()) -> None:
    ticker = _TICKER.get()
    if ticker is not None and ticker.wants(only):
        ticker.track(key, ids, template)


def stage_event(stage: str, status: str, state: dict[str, Any]) -> dict[str, Any]:
    detail = ""
    if status == "completed":
        if stage == "parse":
            detail = f"已确认 {state['profile'].days} 天行程与出行条件"
        elif stage == "retrieve":
            detail = f"已找到 {len(state.get('candidates', []))} 个候选地点"
            coverage = state.get("retrieval_report", {}).get("interest_coverage", [])
            if coverage:
                detail += f" · 兴趣覆盖 {sum(entry['status'] == 'covered' for entry in coverage)}/{len(coverage)} 项"
        elif stage == "select":
            detail = f"已选出 {len(state.get('selected', []))} 个地点"
            selection = state.get("selection_stats", {})
            if selection:
                if selection.get("status") == "fallback":
                    detail += f" · Jev 调用失败（尝试 {selection['seconds']} 秒），已由原模型完成选点"
                elif selection.get("cached"):
                    detail += f" · 已复用 Jev 评分（缓存读取 {selection['seconds']} 秒）"
                else:
                    detail += f" · Jev 实时评分成功，用时 {selection['seconds']} 秒"
        elif stage == "schedule":
            detail = f"已计算 {len(state.get('day_orders', []))} 天的游览顺序"
        elif stage == "meals":
            meals = sum(item.kind == "meal" for day in state["itinerary"].days for item in day.items)
            detail = f"已安排 {meals} 次正餐，已检查夜间活动条件"
        elif stage == "validate":
            report = state["report"]
            detail = "已完成约束检查" if report.passed else f"发现 {len(report.violations)} 项冲突，正在判断可否调整"
        elif stage == "repair":
            detail = "已调整候选方案，准备重新计算" if state.get("repair_changed") else "已完成调整检查，保留待完善提示"
        elif stage == "refine":
            scores = state.get("refine_scores") or {}
            detail = f"已按合理性指标精修，综合分 {scores.get('before', 0):.1f} → {scores.get('after', 0):.1f}" if scores else "已完成合理性精修"
        elif stage == "finalize":
            detail = "行程数据已整理，正在准备展示"
    return {"type": "progress", "stage": stage, "status": status,
            "round": state.get("repair_round", 0), "detail": detail}


async def stream_plan_events(work: Callable, disconnected: Callable):
    events: asyncio.Queue = asyncio.Queue()
    stopped = Event()
    loop = asyncio.get_running_loop()
    current_stage = "connected"

    def emit(event: dict) -> None:
        nonlocal current_stage
        if stopped.is_set():
            raise PlanningStopped()
        if event.get("type") == "progress":
            current_stage = event.get("stage", current_stage)
        loop.call_soon_threadsafe(events.put_nowait, event)

    def produce() -> None:
        try:
            result = work(emit)
            emit({"type": "result", "plan": result})
        except PlanningStopped:
            pass
        except Exception as error:
            from pydantic import ValidationError
            frames = [(frame.filename, frame.lineno, frame.name)
                      for frame in traceback.extract_tb(error.__traceback__)]
            logging.getLogger(__name__).error("Planning failed: stage=%s type=%s frames=%s",
                                            current_stage, type(error).__name__, frames)
            message = ("出游条件格式不正确，请检查日期、活动时段、预算和起点。"
                       if isinstance(error, ValidationError) else
                       "行程已计算，但结果整理或保存未完成。你的条件已保留，可以重试。"
                       if current_stage == "finalize" else
                       "规划暂未完成，外部数据或模型可能暂不可用。你的条件已保留，可以重试。")
            if not stopped.is_set():
                loop.call_soon_threadsafe(events.put_nowait, {"type": "error", "message": message})

    worker = asyncio.create_task(asyncio.to_thread(produce))
    try:
        yield json.dumps({"type": "connected"}) + "\n"
        while not await disconnected():
            try:
                event = await asyncio.wait_for(events.get(), timeout=10)
            except TimeoutError:
                event = {"type": "heartbeat"}
            yield json.dumps(event, ensure_ascii=False) + "\n"
            if event["type"] in {"result", "error"}:
                break
    finally:
        stopped.set()
        worker.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)
