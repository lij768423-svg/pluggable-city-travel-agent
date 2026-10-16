import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import accounts, api
from app.schemas import DayPlan, Itinerary, UserProfile, ValidationReport
from app.sessions import SessionStore


@pytest.fixture
def account_store(tmp_path, monkeypatch):
    store = accounts.AccountStore(tmp_path / "accounts.sqlite3")
    monkeypatch.setattr(accounts, "STORE", store)
    monkeypatch.setattr(accounts, "_attempts", {})
    monkeypatch.setattr(api, "SESSIONS", SessionStore())
    result = {"profile": UserProfile(days=1), "itinerary": Itinerary(days=[DayPlan(day=1, items=[])]),
              "restaurants": {}, "candidates": [], "selected": [], "report": ValidationReport(passed=True),
              "log": [], "usage": {}, "seconds": 0}
    monkeypatch.setattr(api, "run_plan", lambda *args, **kwargs: result)
    return store


def register(client, username="traveler_one"):
    response = client.post("/api/auth/register", json={"username": username, "password": "a-test-password-2026"})
    assert response.status_code == 200, response.text
    return response


@pytest.mark.parametrize("logged_in", [False, True])
@pytest.mark.parametrize("streamed", [False, True])
def test_dated_plan_delivery_and_history(account_store, logged_in, streamed):
    result = api.run_plan()
    result["profile"] = UserProfile(days=1, start_date="2026-10-20")
    client = TestClient(api.app)
    if logged_in:
        register(client)
    request = {"form": {"days": 1, "start_date": "2026-10-20"}, "select_mode": "rule"}
    response = client.post("/api/plan/stream" if streamed else "/api/plan", json=request)
    assert response.status_code == 200
    if streamed:
        events = [json.loads(line) for line in response.text.splitlines()]
        assert events[-1]["type"] == "result"
        payload = events[-1]["plan"]
    else:
        payload = response.json()
    assert payload["profile"]["start_date"] == "2026-10-20"
    if not logged_in:
        register(client)
        payload["history_id"] = client.post(f"/api/plans/{payload['plan_id']}/save").json()["history_id"]
    history = client.get(f"/api/history/{payload['history_id']}").json()
    assert history["plan"]["profile"]["start_date"] == "2026-10-20"
    assert history["request"] == request | {"query": "", "enable_repair": True, "guide_mode": "off", "travel_source": None}


def test_registration_login_session_rotation_logout(account_store):
    with TestClient(api.app) as client:
        response = register(client)
        assert "HttpOnly" in response.headers["set-cookie"] and "SameSite=strict" in response.headers["set-cookie"]
        original = client.cookies.get(accounts.AUTH_COOKIE)
        assert client.get("/api/auth/me").json()["user"]["username"] == "traveler_one"
        assert client.post("/api/auth/login", json={"username": "traveler_one", "password": "wrong-password"}).status_code == 401
        assert client.post("/api/auth/login", json={"username": "TRAVELER_ONE", "password": "a-test-password-2026"}).status_code == 200
        assert client.cookies.get(accounts.AUTH_COOKIE) != original
        assert account_store.user(original) is None
        token = client.cookies.get(accounts.AUTH_COOKIE)
        assert client.post("/api/auth/logout").status_code == 200
        assert account_store.user(token) is None
        assert client.get("/api/auth/me").json()["user"] is None
        assert client.get("/api/history").status_code == 401
    with account_store.connect() as database:
        stored = database.execute("SELECT * FROM users").fetchone()
        assert "a-test-password" not in str(tuple(stored))


def test_origin_validation_and_secure_cookie(account_store):
    client = TestClient(api.app, base_url="https://testserver")
    assert client.post("/api/auth/register", headers={"Origin": "https://evil.example"}, json={"username": "outsider", "password": "password-123456"}).status_code == 403
    response = register(client)
    assert "Secure" in response.headers["set-cookie"]
    assert client.post("/api/auth/logout", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert client.get("/api/auth/me").json()["user"] is not None


def test_validation_duplicate_and_throttle(account_store):
    client = TestClient(api.app)
    register(client)
    assert client.post("/api/auth/register", json={"username": "TRAVELER_ONE", "password": "a-test-password-2026"}).status_code == 409
    invalid = client.post("/api/auth/register", json={"username": "a", "password": "short"})
    assert invalid.status_code == 422 and all("input" not in item for item in invalid.json()["detail"])
    for attempt in range(10):
        client.post("/api/auth/login", json={"username": "traveler_one", "password": "wrong-password"})
    assert client.post("/api/auth/login", json={"username": "traveler_one", "password": "wrong-password"}).status_code == 429


def test_history_is_persistent_and_strictly_owned(account_store):
    first, second = TestClient(api.app), TestClient(api.app)
    register(first)
    register(second, "traveler_two")
    request_data = {"query": "slow trip", "form": {"days": 1}, "select_mode": "rule"}
    result = first.post("/api/plan", json=request_data).json()
    identifier, plan_id = result["history_id"], result["plan_id"]
    assert len(first.get("/api/history").json()["items"]) == 1
    assert second.get("/api/history").json()["items"] == []
    assert second.get(f"/api/history/{identifier}").status_code == 404
    assert second.delete(f"/api/history/{identifier}").status_code == 404
    assert second.post(f"/api/plans/{plan_id}/save").status_code == 404
    assert second.post(f"/api/plans/{plan_id}/revert", json={"revision": 0, "base_revision": 0}).status_code == 404
    assert second.post(f"/api/plans/{plan_id}/swap-meal", json={"revision": 0, "day": 1, "item_index": 0, "restaurant_id": 1}).status_code == 404
    restored = accounts.AccountStore(account_store.path)
    user = first.get("/api/auth/me").json()["user"]
    saved = restored.get(user["id"], identifier)
    assert saved["request"]["query"] == "slow trip"
    assert saved["plan"]["history_readonly"] and saved["plan"]["plan_id"] is None
    assert first.post(f"/api/plans/{plan_id}/save").json()["history_id"] == identifier
    assert len(first.get("/api/history").json()["items"]) == 1
    assert first.delete(f"/api/history/{identifier}").status_code == 200
    assert first.get(f"/api/history/{identifier}").status_code == 404


def test_guest_stream_can_only_be_claimed_by_same_browser(account_store):
    guest, stranger = TestClient(api.app), TestClient(api.app)
    response = guest.post("/api/plan/stream", json={"form": {"days": 1}, "select_mode": "rule"})
    assert guest.cookies.get(accounts.GUEST_COOKIE)
    result = [json.loads(line) for line in response.text.splitlines() if json.loads(line)["type"] == "result"][0]["plan"]
    assert "history_id" not in result
    plan_id = result["plan_id"]
    assert guest.post(f"/api/plans/{plan_id}/save").status_code == 401
    register(stranger, "stranger")
    assert stranger.post(f"/api/plans/{plan_id}/save").status_code == 404
    register(guest, "owner")
    saved = guest.post(f"/api/plans/{plan_id}/save")
    assert saved.status_code == 200 and saved.json()["history_id"]
    assert api.SESSIONS.get(plan_id).owner.startswith("user:")
    guest.post("/api/auth/logout")
    register(guest, "another")
    assert guest.post(f"/api/plans/{plan_id}/save").status_code == 404
    assert guest.post("/api/plans/expired/save").status_code == 410


def test_storage_failure_does_not_discard_generated_plan(account_store, monkeypatch):
    client = TestClient(api.app)
    register(client)
    def unavailable(*args):
        raise sqlite3.OperationalError("disk full")
    monkeypatch.setattr(account_store, "save", unavailable)
    response = client.post("/api/plan", json={"form": {"days": 1}, "select_mode": "rule"})
    assert response.status_code == 200 and response.json()["days"]
    assert response.json()["history_error"]


def test_snapshots_strip_provider_payloads_and_paginate(account_store):
    user = account_store.register("owner", "password-for-tests")
    snapshot = {"profile": {"days": 1, "interests": []}, "days": [{"items": [{"name": "餐厅", "detail": {"rating": 5}, "restaurant": {"rating": 5}, "travel_evidence": {}, "alternatives": [{"rating": 4}]}], "return_leg": {}}]}
    identifiers = [account_store.save(user["id"], str(index), {}, snapshot) for index in range(21)]
    assert len(account_store.list(user["id"])["items"]) == 20
    assert account_store.list(user["id"])["has_more"]
    assert len(account_store.list(user["id"], 20)["items"]) == 1
    saved = account_store.get(user["id"], identifiers[0])["plan"]
    assert "rating" not in json.dumps(saved)
    assert "return_leg" not in saved["days"][0]
    assert snapshot["days"][0]["items"][0]["restaurant"]["rating"] == 5
