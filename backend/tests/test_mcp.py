"""MCP合同回归：全部合成/冻结输入，禁止真实外部调用。"""
from __future__ import annotations

import json
import threading
from datetime import date, datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from app import accounts, api
from app.amap_limits import RequestBudget, RequestBudgetExceeded, operation_budget
from app.mcp_domain import MCPError, delivery_status, prepare_candidates, run_selected
from app.mcp_service import MCPService
from app.schemas import (DayPlan, ExternalSelection, Hotel, Item, Itinerary, LegEvidence,
                         UserProfile, ValidationReport)
from app.sessions import SessionStore

OWNER = "user:tester"
PROFILE = {"days": 1, "budget": 1500, "start_date": "2026-10-22", "include_evening": False,
           "hotel": {"name": "用户指定测试起点", "lat": 30.254433, "lon": 120.169771}}


@pytest.fixture
def service(tmp_path):
    store = accounts.AccountStore(tmp_path / "accounts.sqlite3")
    sessions = SessionStore()
    svc = MCPService(store=store, sessions=sessions, ledger_path=tmp_path / "requests.sqlite3", site_url="https://test.invalid")
    yield svc
    svc.close()


def prepared(service, n=0, owner=OWNER):
    return service.prepare(owner, PROFILE, f"prepare-{n:04d}")


def start(service, p, request="generate-0000", owner=OWNER):
    return service.generate(owner, p["job_id"], p["preparation_revision"], p["candidate_set_id"],
                            [{"poi_id": c["poi_id"], "reason": "符合用户兴趣"} for c in p["candidates"][:2]], request)


def verified_result():
    profile = UserProfile.model_validate(PROFILE)
    ev = LegEvidence(minutes=5, mode="步行", cost=0, source="amap", service_date=profile.start_date,
                     fetched_at=datetime.now(timezone.utc).isoformat())
    return {"profile": profile,
            "itinerary": Itinerary(days=[DayPlan(day=1, date=profile.start_date,
                              items=[Item(kind="poi", ref_id=1, name="合成景点", start=600, end=660, travel_evidence=ev)],
                              return_leg=ev)]),
            "report": ValidationReport(passed=True, constraint_status="passed", arrangement_status="complete",
                                       evidence_status="verified", unknowns=[])}


def test_delivery_gate_preserves_partial_and_actual_legs():
    r = verified_result()
    assert delivery_status(r)["status"] == "ready"
    r["itinerary"].days[0].return_leg.source = "estimate"
    assert delivery_status(r)["code"] == "missing_leg"
    r = verified_result()
    r["itinerary"].days[0].items[0].travel_evidence.fetched_at = (datetime.now(timezone.utc) - timedelta(minutes=16)).isoformat()
    assert delivery_status(r)["status"] == "data_unavailable"
    r = verified_result()
    r["report"] = ValidationReport(passed=True, arrangement_status="complete", evidence_status="partial", unknowns=["票价未知"])
    assert delivery_status(r)["code"] == "incomplete_evidence"


def test_budget_cap_context_and_cancellation(monkeypatch):
    from app.travel import Travel
    budget = RequestBudget(120)
    token = operation_budget.set(budget)
    try:
        assert Travel("estimate").budget is budget
    finally:
        operation_budget.reset(token)
    for _ in range(120):
        budget.consume()
    with pytest.raises(RequestBudgetExceeded):
        budget.consume()
    event = threading.Event(); event.set()
    with pytest.raises(RequestBudgetExceeded):
        RequestBudget(120, cancelled=event).consume()


def test_prepare_input_is_explicit_and_owner_isolated(service):
    p = service.prepare(OWNER, {}, "missing-0000")
    assert p["status"] == "needs_input"
    assert set(p["missing_fields"]) == {"days", "budget", "start_date", "hotel"}
    with pytest.raises(MCPError):
        service.get_job("user:other", p["job_id"])
    p2 = service.prepare(OWNER, PROFILE, "missing-0001", p["job_id"])
    assert p2["status"] == "prepared"
    assert p2["preparation_revision"] == 2


def test_idempotency_changed_body_and_stale_candidates(service):
    p = prepared(service)
    assert prepared(service) == p
    with pytest.raises(MCPError, match="内容不能改变"):
        service.prepare(OWNER, {**PROFILE, "budget": 2000}, "prepare-0000")
    p2 = service.prepare(OWNER, PROFILE, "reprepare-0001", p["job_id"])
    rejected = start(service, p)
    assert rejected["code"] == "stale_preparation"
    invalid = service.generate(OWNER, p2["job_id"], p2["preparation_revision"], p2["candidate_set_id"],
                               [{"poi_id": 999999, "reason": "假地点"}], "invalid-0000")
    assert invalid["code"] == "invalid_selection"


def test_70_generations_no_daily_quota_and_duplicate_no_replay(service, monkeypatch):
    calls = []
    done = threading.Event()
    def execute(job, selections):
        calls.append(job.identifier)
        job.output = {"status": "data_unavailable", "code": "synthetic"}
        job.status = "data_unavailable"
        done.set()
    monkeypatch.setattr(service, "_run", execute)
    monkeypatch.setattr(service.store, "consume", lambda *a, **k: pytest.fail("MCP unlimited consumed website quota"))
    for n in range(70):
        done.clear()
        p = prepared(service, n)
        first = start(service, p, f"generate-{n:04d}")
        assert done.wait(5)
        assert start(service, p, f"generate-{n:04d}") == first
    assert len(calls) == 70


def test_persistent_idempotency_never_restarts_paid_job(service, monkeypatch):
    p = prepared(service)
    service.responses.clear()  # 模拟只有持久摘要仍在的新进程
    recovered = prepared(service)
    assert recovered["status"] == "expired"
    assert recovered["job_id"] == p["job_id"]
    with service.ledger.connect() as db:
        text = json.dumps(db.execute("SELECT * FROM mcp_requests").fetchall())
    assert "hotel" not in text and "candidates" not in text


def test_cancel_stops_before_save(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def blocked(*args, **kwargs):
        entered.set(); assert release.wait(5)
        return verified_result()
    monkeypatch.setattr("app.mcp_service.run_selected", blocked)
    monkeypatch.setattr(service.store, "save", lambda *a: pytest.fail("cancelled task saved history"))
    p = prepared(service)
    start(service, p)
    assert entered.wait(5)
    service.cancel(OWNER, p["job_id"])
    release.set()
    service.pool.shutdown(wait=True)
    assert service.get_job(OWNER, p["job_id"])["status"] == "cancelled"


def test_external_graph_no_server_model_or_candidate_escape(monkeypatch):
    def forbidden(*a, **k):
        pytest.fail("MCP called server model")
    for path in ("app.graph.get_llm", "app.llm.get_llm", "app.llm.chat_json",
                 "app.nodes.parse.get_llm", "app.nodes.parse.chat_json", "app.nodes.select.evaluate_jev"):
        monkeypatch.setattr(path, forbidden)
    profile = UserProfile.model_validate(PROFILE)
    candidates, report = prepare_candidates(profile)
    from app.restaurants import RestaurantCatalog
    from app.travel import Travel
    selections = [ExternalSelection(poi_id=c.poi_id, reason="上午去这里适合兴趣") for c in candidates[:2]]
    result = run_selected(profile, candidates[:5], selections, report,
                          route=Travel("estimate"), catalog=RestaurantCatalog(online=False), use_solver=False)
    assert result["usage"]["calls"] == 0
    actual = {i.ref_id for day in result["itinerary"].days for i in day.items if i.kind == "poi"}
    assert actual <= {c.poi_id for c in candidates[:5]}
    assert any(a["kind"] == "reason" for a in result["adjustments"])
    assert delivery_status(result)["status"] == "data_unavailable"


def test_same_history_revision_and_no_stale_history_overwrite(service, monkeypatch, mock_llm):
    from app.graph import run_plan
    result = run_plan(form=PROFILE, llm=mock_llm, travel_source="estimate", use_solver=False)
    session = service.sessions.create(result, {"select_mode": "llm", "guide_mode": "off", "enable_repair": True,
                                              "input": {"form": PROFILE}}, OWNER)
    saved = service._save(session.plan_id)
    history_id = saved["history_id"]
    service.sessions.append(session.plan_id, 0, result)
    latest = service._save(session.plan_id)
    assert latest["history_id"] == history_id and latest["revision"] == 1
    service.store.save(OWNER[5:], session.plan_id, {}, saved["plan"])
    assert service.store.get(OWNER[5:], history_id)["plan"]["history_revision"] == 1
    monkeypatch.setattr(api, "SESSIONS", service.sessions)
    monkeypatch.setattr(accounts, "STORE", service.store)
    monkeypatch.setattr(accounts, "current_user", lambda *a: {"id": OWNER[5:], "username": "tester"})
    web = TestClient(api.app)
    linked = web.get(f"/api/trips/{history_id}").json()["plan"]
    chat = service.get_trip(OWNER, history_id)["plan"]
    assert linked["plan_id"] == chat["plan_id"] == session.plan_id
    assert linked["revision"] == chat["revision"] == 1
    assert linked["days"] == chat["days"]
    monkeypatch.setattr(accounts, "current_user", lambda *a: {"id": "other", "username": "other"})
    assert web.get(f"/api/trips/{history_id}").status_code == 404


def test_save_failure_retry_does_not_repeat_commit(service, monkeypatch):
    # 一次确认已提交但保存失败；相同幂等键只重试保存。
    mutations = []
    saves = []
    def action():
        mutations.append(1)
        return {"status": "save_pending", "code": "save_pending", "plan_id": "synthetic"}
    body = {"test": "one-confirm"}
    first = service._once(OWNER, "confirm", "confirm-0000", body, action)
    assert first["code"] == "save_pending"
    def save(plan_id):
        saves.append(plan_id)
        return {"status": "ready", "revision": 1}
    monkeypatch.setattr(service, "_save", save)
    assert service._once(OWNER, "confirm", "confirm-0000", body, action)["status"] == "ready"
    assert mutations == [1] and saves == ["synthetic"]


def test_actual_http_retry_counting_and_121st_never_sent(monkeypatch):
    from app.amap import AmapRouter, AmapBudgetExceeded
    sent = []
    router = AmapRouter(key="test-not-a-secret", qps=100, cache_path=False)
    router.budget = RequestBudget(120)
    class Response:
        def raise_for_status(self):
            pass
        def json(self):
            return {"status": "1"}
    monkeypatch.setattr(router._client, "get", lambda *a, **k: sent.append(1) or Response())
    for _ in range(120):
        router._get("test", {})
    with pytest.raises(AmapBudgetExceeded):
        router._get("test", {})
    assert len(sent) == 120 and router.budget.used == 120


def test_busy_does_not_start_second_job(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    def blocked(*a, **k):
        entered.set(); assert release.wait(5)
        return verified_result()
    monkeypatch.setattr("app.mcp_service.run_selected", blocked)
    monkeypatch.setattr(service, "_save", lambda *a: {"status": "ready"})
    p1, p2 = prepared(service, 1), prepared(service, 2)
    start(service, p1, "generate-busy1")
    assert entered.wait(5)
    second = start(service, p2, "generate-busy2")
    assert second["code"] == "busy"
    release.set()


def test_confirm_once_stale_and_consumed_preview(service, monkeypatch, mock_llm):
    from app.graph import run_plan
    result = run_plan(form=PROFILE, llm=mock_llm, travel_source="estimate", use_solver=False)
    meta = {"source": "mcp", "select_mode": "external_ai", "enable_repair": True, "guide_mode": "off", "input": {"form": PROFILE}}
    session = service.sessions.create(result, meta, OWNER)
    preview = service.sessions.add_preview(session.plan_id, 0, result, {"test": "synthetic"})
    # 本测试验证提交/幂等，与前面的真实证据门槛测试独立。
    monkeypatch.setattr("app.mcp_service.delivery_status", lambda *a, **k: {"status": "ready"})
    monkeypatch.setattr("app.meal_edits.validate_preview", lambda r: r["report"])
    first = service.confirm(OWNER, session.plan_id, 0, preview.preview_id, "confirm-real1")
    assert first["status"] == "ready" and first["revision"] == 1
    again = service.confirm(OWNER, session.plan_id, 0, preview.preview_id, "confirm-real1")
    assert again == first and session.current == 1
    replay = service.confirm(OWNER, session.plan_id, 0, preview.preview_id, "confirm-real2")
    assert replay["code"] == "stale_revision" and session.current == 1
    with pytest.raises(MCPError):
        service.get_trip("user:other", session.plan_id)


def test_session_expiry_returns_readonly_history(service, mock_llm):
    from app.graph import run_plan
    result = run_plan(form=PROFILE, llm=mock_llm, travel_source="estimate", use_solver=False)
    session = service.sessions.create(result, {"select_mode": "llm", "guide_mode": "off", "enable_repair": True, "input": {}}, OWNER)
    saved = service._save(session.plan_id)
    session.touched = 0
    expired = service.get_trip(OWNER, saved["history_id"])
    assert expired["status"] == "readonly" and expired["edit_state"] == "expired"
    assert expired["plan"]["history_id"] == saved["history_id"]


def test_unverified_draft_never_relaxes_route_hard_or_meals():
    result = verified_result()
    result["report"] = ValidationReport(passed=True, arrangement_status="complete", evidence_status="partial", unknowns=["票价待核实"])
    outcome = delivery_status(result, allow_unverified_draft=True)
    assert outcome["status"] == "draft" and outcome["requires_verification"]
    assert result["report"].evidence_status == "partial" and not result["report"].complete_executable
    result["itinerary"].days[0].return_leg.source = "estimate"
    assert delivery_status(result, allow_unverified_draft=True)["status"] == "data_unavailable"
    result = verified_result()
    result["report"] = ValidationReport(passed=False, constraint_status="failed", arrangement_status="complete")
    assert delivery_status(result, allow_unverified_draft=True)["status"] == "needs_changes"
    result["report"] = ValidationReport(passed=True, arrangement_status="missing")
    assert delivery_status(result, allow_unverified_draft=True)["status"] == "data_unavailable"
    result["itinerary"].days[0].items = []
    assert delivery_status(result, allow_unverified_draft=True)["status"] == "needs_changes"


def test_prepare_gives_host_ai_the_same_candidate_facts_as_internal_selection(service):
    """宿主 AI 拿到的候选资料与网站内部选点提示词逐字等价：用 MCP 返回的结构化字段按内部格式重拼，必须得到同一段文本。"""
    from app.nodes.select import _candidate_line, candidate_lines, target_count
    from app.poi_catalog import use_catalog
    from app.prompts import SELECT_RULES, select_system
    p = prepared(service)
    job = service.jobs[p["job_id"]]
    with use_catalog(job.catalog):
        internal = candidate_lines(job.candidates)
    by_id = {c["poi_id"]: c for c in p["candidates"]}
    rebuilt = []
    for area in p["areas"]:
        rebuilt.append(f"【片区：{area['label']}｜相邻片区：{'、'.join(area['adjacent']) or '无'}｜距起点最近约 {area['nearest_km']} km】")
        rebuilt.extend(_candidate_line({**by_id[i], "id": i}) for i in area["candidate_ids"])
    assert "\n".join(rebuilt) == internal
    assert sorted(i for area in p["areas"] for i in area["candidate_ids"]) == sorted(by_id)
    assert {"type", "stamina", "audience", "max_hours", "evening_only", "onsite_dining", "seasonal_note"} <= set(p["candidates"][0])
    assert p["suggested_count"] == target_count(UserProfile.model_validate(p["profile"]), len(p["candidates"]))
    assert p["selection_guide"].startswith(SELECT_RULES) and SELECT_RULES in select_system()
    # 原有字段保持不变，旧的宿主调用不受影响
    assert {"poi_id", "score", "open_min", "close_min", "min_hours", "facts_are_historical"} <= set(p["candidates"][0])


def test_prepare_explains_sources_and_issues_without_calling_a_model(service):
    # 宿主 AI 给的画像逐项标明来源；上下文里常住杭州就推断为本地人；预算偏紧给一键修改（2026-10-28 用户决定）
    tight = {**PROFILE, "days": 4, "budget": 300, "transport": "taxi"}
    p = service.prepare(OWNER, tight, "explain-0000", context={"home_city": "杭州"})
    fields = {field["key"]: field for field in p["understanding"]["fields"]}
    assert fields["days"]["source"] == "said" and fields["familiarity"]["source"] == "inferred"
    issue = next(item for item in p["understanding"]["issues"] if item["code"] == "budget_tight")
    assert {"transport": "transit"} in [fix["patch"] for fix in issue["fixes"]]
    missing = service.prepare(OWNER, {}, "explain-0001")
    assert missing["status"] == "needs_input" and missing["understanding"]["fields"]
