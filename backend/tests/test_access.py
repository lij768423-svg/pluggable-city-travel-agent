import pytest
from fastapi.testclient import TestClient

from app import access, accounts, api, config
from tests.test_accounts import account_store, register  # noqa: F401  复用账号存储与假的规划结果

PLAN = {"form": {"days": 1}, "select_mode": "rule"}


@pytest.fixture
def public(monkeypatch, account_store):  # noqa: F811
    monkeypatch.setattr(config, "REQUIRE_LOGIN_TO_PLAN", True)
    monkeypatch.setattr(config, "PLAN_LIMIT_USER", 2)
    monkeypatch.setattr(config, "PLAN_LIMIT_IP", 3)
    monkeypatch.setattr(config, "INVITE_CODES", frozenset({"lake-2026"}))
    monkeypatch.setattr(config, "PUBLIC_HOSTS", frozenset({"travel.example"}))
    monkeypatch.setattr(config, "RATE_LIMIT", True)
    monkeypatch.setattr(access, "_hits", {})
    return account_store


def join(client, username="traveler_one", code="lake-2026"):
    return client.post("/api/auth/register", json={"username": username, "password": "a-test-password-2026", "invite_code": code})


def test_registration_needs_the_invite_code(public):
    client = TestClient(api.app)
    assert join(client, code=None).status_code == 403
    assert join(client, code="wrong").status_code == 403
    assert join(client).status_code == 200
    assert client.get("/api/meta").json()["access"] == {"login_to_plan": True, "invite_required": True}


def test_unlimited_accounts_skip_daily_counts(public, monkeypatch):
    monkeypatch.setattr(config, "UNLIMITED_USERS", frozenset({"traveler_one"}))
    owner = TestClient(api.app)
    assert join(owner).status_code == 200
    assert owner.get("/api/auth/me").json()["quota"] is None
    for _ in range(5):   # 超过账号 2 次、IP 3 次的上限
        assert owner.post("/api/plan", json=PLAN).status_code == 200
    # 不限次的账号不占同一 IP 的额度，同 IP 的普通账号照常按自己的 2 次计
    other = TestClient(api.app)
    assert join(other, username="traveler_two").status_code == 200
    assert other.get("/api/auth/me").json()["quota"] == {"limit": 2, "used": 0, "remaining": 2}
    assert [other.post("/api/plan", json=PLAN).status_code for _ in range(3)] == [200, 200, 429]
    # 不限次也仍然要求登录
    assert TestClient(api.app).post("/api/plan", json=PLAN).status_code == 401


def test_planning_needs_login_and_counts_per_account(public):
    client = TestClient(api.app)
    assert client.post("/api/plan", json=PLAN).status_code == 401
    assert client.post("/api/plan/stream", json=PLAN).status_code == 401
    assert join(client).status_code == 200
    assert client.get("/api/auth/me").json()["quota"] == {"limit": 2, "used": 0, "remaining": 2}
    assert client.post("/api/plan", json=PLAN).status_code == 200
    assert client.post("/api/plan/stream", json=PLAN).status_code == 200
    blocked = client.post("/api/plan", json=PLAN)
    assert blocked.status_code == 429 and "明天" in blocked.json()["detail"]
    assert client.get("/api/auth/me").json()["quota"]["remaining"] == 0


def test_ip_limit_covers_several_accounts(public):
    first, second = TestClient(api.app), TestClient(api.app)
    join(first, "traveler_one"), join(second, "traveler_two")
    assert [first.post("/api/plan", json=PLAN).status_code for _ in range(2)] == [200, 200]
    assert second.post("/api/plan", json=PLAN).status_code == 200
    assert second.post("/api/plan", json=PLAN).status_code == 429   # 同一 IP 当天 3 次已满


def test_stay_audit_is_hidden_only_on_the_public_host(public):
    client = TestClient(api.app)
    assert client.get("/api/stay-audit", headers={"host": "travel.example"}).status_code == 404
    assert client.get("/stay-audit.html", headers={"host": "travel.example"}).status_code == 404
    assert client.get("/api/stay-audit").status_code == 200


def test_external_lookups_are_rate_limited(public, monkeypatch):
    monkeypatch.setitem(access.RATE_LIMITS, "media", (2, 600))
    client = TestClient(api.app)
    codes = [client.get("/api/place-media", params={"kind": "poi", "id": 999999}).status_code for _ in range(3)]
    assert codes[:2] == [404, 404] and codes[2] == 429


def test_everything_is_open_when_switches_are_off(account_store):  # noqa: F811
    client = TestClient(api.app)
    assert client.post("/api/plan", json=PLAN).status_code == 200
    assert client.get("/api/auth/me").json()["quota"] is None
    assert client.get("/api/meta").json()["access"] == {"login_to_plan": False, "invite_required": False}


def test_understanding_needs_login_but_not_a_daily_count(public):
    # 规划前的“我是这么理解的”要调一次模型：未登录不给用；登录后不扣每天的生成次数
    client = TestClient(api.app)
    body = {"form": {"city": "hangzhou", "days": 4, "budget": 300, "transport": "taxi"}}
    assert client.post("/api/plan/understand", json=body).status_code == 401
    assert join(client).status_code == 200
    reply = client.post("/api/plan/understand", json=body)
    assert reply.status_code == 200
    assert "budget_tight" in {issue["code"] for issue in reply.json()["issues"]}
    assert reply.json()["profile"]["days"] == 4
    assert client.get("/api/auth/me").json()["quota"]["used"] == 0
