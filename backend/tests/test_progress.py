import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from app.graph import run_plan
from app.progress import PlanningStopped, stream_plan_events
from tests.conftest import needs_data


@needs_data
def test_observation_preserves_result_and_node_order(mock_llm):
    events = []
    kwargs = dict(form={"days": 1, "budget": 800}, llm=mock_llm, select_mode="rule", enable_repair=False)
    original = run_plan(**kwargs)
    observed = run_plan(**kwargs, progress=events.append)
    assert observed["itinerary"].model_dump() == original["itinerary"].model_dump()
    assert observed["report"].model_dump() == original["report"].model_dump()
    expected = ["parse", "retrieve", "select", "schedule", "meals", "validate", "finalize"]
    # 开始事件的 detail 为空；带 detail 的 running 事件是执行途中的实时数量，不代表新步骤
    assert [event["stage"] for event in events if event["status"] == "running" and not event["detail"]] == expected
    assert {event["stage"] for event in events if event["status"] == "running"} == set(expected)
    assert [event["stage"] for event in events if event["status"] == "completed"] == expected
    assert all(event["detail"] for event in events if event["status"] == "completed")


@needs_data
def test_cancel_stops_before_node_execution(mock_llm):
    events = []

    def stop(event):
        events.append(event)
        if event["stage"] == "select":
            raise PlanningStopped()

    with pytest.raises(PlanningStopped):
        run_plan(form={"days": 1}, llm=mock_llm, progress=stop)
    assert mock_llm.usage.calls == 0
    assert events[-1]["status"] == "running"


def test_stream_emits_progress_before_work_finishes():
    from threading import Event
    release = Event()

    def work(emit):
        emit({"type": "progress", "stage": "parse", "status": "running"})
        assert release.wait(timeout=3)
        return {"ready": True}

    async def run():
        stream = stream_plan_events(work, lambda: asyncio.sleep(0, result=False))
        assert json.loads(await anext(stream))["type"] == "connected"
        assert json.loads(await anext(stream))["type"] == "progress"
        release.set()
        result = json.loads(await anext(stream))
        assert result == {"type": "result", "plan": {"ready": True}}
        await stream.aclose()

    asyncio.run(run())


def test_repair_reports_real_repeated_nodes(monkeypatch):
    from types import SimpleNamespace
    from app import graph

    monkeypatch.setattr(graph, "parse_node", lambda state: {"profile": SimpleNamespace(days=1)})
    monkeypatch.setattr(graph, "retrieve_node", lambda state: {"candidates": []})
    monkeypatch.setattr(graph, "select_node", lambda state: {"selected": []})
    monkeypatch.setattr(graph, "schedule_node", lambda state: {"day_orders": [[]]})
    monkeypatch.setattr(graph, "meals_node", lambda state: {"itinerary": SimpleNamespace(days=[])})
    monkeypatch.setattr(graph, "validate_node", lambda state: {"report": SimpleNamespace(
        passed=bool(state.get("repair_round")), experience_status="complete", violations=["conflict"])})
    monkeypatch.setattr(graph, "repair_node", lambda state: {"repair_round": 1, "repair_changed": True})
    events = []
    graph.build_graph(events.append).invoke({"repair_round": 0, "log": []})
    running = [event for event in events if event["status"] == "running"]
    assert [event["stage"] for event in running] == [
        "parse", "retrieve", "select", "schedule", "meals", "validate", "repair", "schedule", "meals", "validate"]
    assert running[-1]["round"] == 1


def test_disconnect_stops_worker_at_next_boundary():
    from threading import Event
    entered, release, stopped = Event(), Event(), Event()

    def work(emit):
        entered.set()
        assert release.wait(timeout=3)
        try:
            emit({"type": "progress"})
        except PlanningStopped:
            stopped.set()
            raise
        pytest.fail("disconnected request continued")

    async def run():
        stream = stream_plan_events(work, lambda: asyncio.sleep(0, result=False))
        await anext(stream)
        assert await asyncio.to_thread(entered.wait, 3)
        await stream.aclose()
        release.set()
        assert await asyncio.to_thread(stopped.wait, 3)

    asyncio.run(run())


def test_stream_failure_is_safe_and_does_not_emit_result(monkeypatch, caplog):
    from app import api

    def fail(*args, **kwargs):
        kwargs["progress"]({"type": "progress", "stage": "parse", "status": "running"})
        raise RuntimeError("private upstream credential must not appear")

    monkeypatch.setattr(api, "run_plan", fail)
    response = TestClient(api.app).post("/api/plan/stream", json={"query": "杭州"})
    events = [json.loads(line) for line in response.text.splitlines()]
    assert [event["type"] for event in events] == ["connected", "progress", "error"]
    assert "credential" not in response.text
    assert "credential" not in caplog.text
    assert "stage=parse type=RuntimeError" in caplog.text
    assert response.headers["x-accel-buffering"] == "no"
    assert TestClient(api.app).post("/api/plan/stream", json={"select_mode": "invalid"}).status_code == 422


def test_delivery_failure_does_not_blame_model():
    def work(emit):
        emit({"type": "progress", "stage": "finalize", "status": "completed"})
        raise TypeError("private result must not appear")

    async def run():
        return [json.loads(line) async for line in stream_plan_events(work, lambda: asyncio.sleep(0, result=False))]

    events = asyncio.run(run())
    assert events[-1]["type"] == "error"
    assert "结果整理或保存" in events[-1]["message"]
    assert "模型" not in events[-1]["message"]
    assert "private" not in events[-1]["message"]


@needs_data
def test_stream_result_has_session_and_serialized_days(monkeypatch, mock_llm):
    from app import api
    original = api.run_plan
    monkeypatch.setattr(api, "run_plan", lambda *args, **kwargs: original(*args, **kwargs, llm=mock_llm))
    response = TestClient(api.app).post("/api/plan/stream", json={
        "form": {"days": 1, "budget": 800}, "select_mode": "rule", "enable_repair": False,
    })
    events = [json.loads(line) for line in response.text.splitlines()]
    assert events[-1]["type"] == "result"
    assert events[-1]["plan"]["plan_id"]
    assert len(events[-1]["plan"]["days"]) == 1
