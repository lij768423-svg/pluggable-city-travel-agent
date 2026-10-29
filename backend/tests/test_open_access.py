"""放到 Linux.do 前的三道闸（2026-10-29 用户：“按建议做”）：全站每日上限、生成排队、一句话需求每日次数。"""
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app import access, accounts, api, config
from app.plan_queue import PlanQueue, QueueFull
from tests.test_access import PLAN, join, public  # noqa: F401  复用公网开关与注册
from tests.test_accounts import account_store  # noqa: F401


def test_site_wide_daily_cap_blocks_everyone_without_charging_the_account(public, monkeypatch):
    monkeypatch.setattr(config, "PLAN_LIMIT_SITE", 2)
    monkeypatch.setattr(config, "PLAN_LIMIT_IP", 0)
    first, second = TestClient(api.app), TestClient(api.app)
    assert join(first).status_code == 200 and join(second, username="traveler_two").status_code == 200
    assert first.post("/api/plan", json=PLAN).status_code == 200
    assert second.post("/api/plan", json=PLAN).status_code == 200
    blocked = first.post("/api/plan", json=PLAN)
    assert blocked.status_code == 429 and "名额" in blocked.json()["detail"]
    assert first.get("/api/auth/me").json()["quota"]["remaining"] == 1   # 全站满了不扣账号的次数


def test_ip_cap_says_it_is_the_network(public, monkeypatch):
    monkeypatch.setattr(config, "PLAN_LIMIT_IP", 1)
    first, second = TestClient(api.app), TestClient(api.app)
    assert join(first).status_code == 200 and join(second, username="traveler_two").status_code == 200
    assert first.post("/api/plan", json=PLAN).status_code == 200
    blocked = second.post("/api/plan", json=PLAN)
    assert blocked.status_code == 429 and "网络" in blocked.json()["detail"]


def test_understanding_counts_each_new_text_once_per_account(public, monkeypatch):
    monkeypatch.setattr(config, "UNDERSTAND_LIMIT_USER", 2)
    monkeypatch.setattr(access, "_understood", {})
    user = {"id": "u1", "username": "traveler_one"}
    access.guard_understand(user, "")              # 只改表、没写文字的不计
    access.guard_understand(user, "周末去西湖")
    access.guard_understand(user, "周末去西湖")      # 同一段文字改表后重算，不再计
    access.guard_understand(user, "周末去西湖，带老人")
    with pytest.raises(HTTPException) as blocked:
        access.guard_understand(user, "周末去西湖，带老人，别太累")
    assert blocked.value.status_code == 429 and "详细填写" in blocked.value.detail
    access.guard_understand({"id": "u2", "username": "traveler_two"}, "周末去西湖，带老人，别太累")   # 别的账号照常
    access.guard_understand(None, "未开登录时不按账号计")


def test_understand_endpoint_applies_the_daily_count(public, monkeypatch):
    monkeypatch.setattr(config, "UNDERSTAND_LIMIT_USER", 1)
    monkeypatch.setattr(access, "_understood", {})
    monkeypatch.setattr(api, "understand", lambda *args, **kwargs: SimpleNamespace(model_dump=lambda mode=None: {"ok": True}))
    client = TestClient(api.app)
    assert join(client).status_code == 200
    ask = lambda text: client.post("/api/plan/understand", json={"query": text, "form": {"city": "hangzhou"}})  # noqa: E731
    assert ask("周末去西湖").status_code == 200
    assert ask("周末去西湖").status_code == 200
    assert ask("改成三天").status_code == 429


def test_queue_runs_a_limited_number_at_once_and_reports_positions(monkeypatch):
    monkeypatch.setattr(config, "PLAN_CONCURRENCY", 1)
    monkeypatch.setattr("app.plan_queue.WAIT_SECONDS", 0.05)
    queue = PlanQueue()
    started, release, events, order = threading.Event(), threading.Event(), [], []

    def first():
        with queue.slot():
            started.set()
            release.wait(5)

    def second():
        with queue.slot(events.append):
            order.append("second")

    holder = threading.Thread(target=first)
    holder.start()
    assert started.wait(5)
    waiter = threading.Thread(target=second)
    waiter.start()
    for _ in range(200):
        if events:
            break
        time.sleep(0.01)
    assert events[0] == {"type": "queue", "position": 1, "limit": 1} and not order
    assert queue.snapshot() == {"running": 1, "waiting": 1, "limit": 1}
    release.set()
    holder.join(5)
    waiter.join(5)
    assert order == ["second"] and queue.snapshot() == {"running": 0, "waiting": 0, "limit": 1}


def test_leaving_the_queue_gives_the_place_back(monkeypatch):
    monkeypatch.setattr(config, "PLAN_CONCURRENCY", 1)
    queue = PlanQueue()
    left = []

    def gone(event):
        raise RuntimeError("对方已离开")

    with queue.slot():
        with pytest.raises(RuntimeError):
            with queue.slot(gone, on_leave=lambda: left.append(True)):
                pytest.fail("不该轮到")
        assert left == [True] and queue.snapshot()["waiting"] == 0


def test_full_queue_is_refused_before_charging(public, monkeypatch):
    monkeypatch.setattr(config, "PLAN_CONCURRENCY", 1)
    monkeypatch.setattr(config, "PLAN_QUEUE_MAX", 1)
    queue = PlanQueue()
    queue._running, queue._waiting = 1, __import__("collections").deque([object()])
    with pytest.raises(QueueFull):
        queue.check_room()
    monkeypatch.setattr(access, "PLAN_QUEUE", queue)
    client = TestClient(api.app)
    assert join(client).status_code == 200
    blocked = client.post("/api/plan/stream", json=PLAN)
    assert blocked.status_code == 429 and "排队" in blocked.json()["detail"]
    assert client.get("/api/auth/me").json()["quota"]["remaining"] == 2


def test_refund_gives_back_one_count_and_never_goes_negative(public):
    store = accounts.STORE
    assert store.consume("2026-10-29", {"user:a": 3, "site": 10})
    store.refund("2026-10-29", ["user:a", "site", "ip:never-charged"])
    assert store.usage("2026-10-29", "user:a") == 0 and store.usage("2026-10-29", "site") == 0
    assert store.usage("2026-10-29", "ip:never-charged") == 0
