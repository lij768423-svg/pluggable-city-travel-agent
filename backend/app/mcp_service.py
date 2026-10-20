"""有界任务、账号和幂等业务；协议适配器不持有调度逻辑。"""
from __future__ import annotations


import hashlib
import json
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import HTTPException
from pydantic import ValidationError

from .cities import DEFAULT_CITY, CityDisabled, city_config, enabled_cities, ensure_enabled, reference_city
from . import accounts, config
from .amap_limits import PLAN_HTTP_LIMIT, RequestBudget, operation_budget
from .mcp_domain import (MCPError, delivery_status, prepare_candidates,
                         public_candidates, run_selected)
from .mcp_ledger import RequestLedger
from .planning_context import allowed_pois, poi_catalog
from .poi_catalog import PlanningCatalog, published_catalog, use_catalog
from .presentation import serialize_plan
from .schemas import ExternalSelection, MealEditPreviewRequest, UserProfile
from .sessions import SESSIONS, SessionNotFound, StaleRevision, PreviewNotFound

TTL = 6 * 3600
MAX_JOBS = 64


@dataclass
class Job:
    owner: str
    identifier: str = field(default_factory=lambda: secrets.token_urlsafe(18))
    created: float = field(default_factory=time.time)
    status: str = "preparing"
    revision: int = 0
    profile: UserProfile | None = None
    candidates: list = field(default_factory=list)
    report: dict = field(default_factory=dict)
    candidate_set_id: str = ""
    version: str = ""
    # 准备时固定的景点目录；生成、后台线程和后续会话都使用它，而不是当时的全局发布版
    catalog: PlanningCatalog | None = None
    cancelled: threading.Event = field(default_factory=threading.Event)
    budget: RequestBudget = field(default_factory=lambda: RequestBudget(PLAN_HTTP_LIMIT))
    output: dict = field(default_factory=dict)
    plan_id: str | None = None
    progress: dict = field(default_factory=dict)
    stage_calls: dict = field(default_factory=dict)
    observed_calls: int = 0


class MCPService:
    def __init__(self, *, store=None, sessions=None, ledger_path: Path | None = None,
                 site_url: str = "", daily_limit: int = 0, allow_unverified_draft: bool = False):
        parsed = urlparse(site_url)
        if site_url and (parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.username or parsed.query or parsed.fragment):
            raise ValueError("MCP 网站基址无效")
        self.store = store or accounts.STORE
        self.sessions = sessions or SESSIONS
        self.ledger = RequestLedger(ledger_path or config.DATA_DIR / "accounts" / "mcp-requests.sqlite3")
        self.site_url = site_url.rstrip("/")
        self.daily_limit = daily_limit
        self.allow_unverified_draft = allow_unverified_draft
        self.jobs: dict[str, Job] = {}
        self.responses: dict[tuple, tuple[str, dict]] = {}
        self.lock = threading.RLock()
        self.operations = threading.Lock()
        self.pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="mcp-plan")
        self.closed = False

    def close(self):
        self.closed = True
        with self.lock:
            for job in self.jobs.values():
                if job.status in {"preparing", "needs_input", "queued", "running"}:
                    job.cancelled.set()
                    job.status = "cancelled"
        self.pool.shutdown(wait=True, cancel_futures=True)

    def _once(self, owner: str, operation: str, request_id: str, body: dict, fn) -> dict:
        if not isinstance(request_id, str) or not 8 <= len(request_id) <= 100:
            raise MCPError("invalid_request_id", "request_id 需要8–100字符，同一操作重试必须复用")
        digest = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        key = owner, operation, request_id
        # 不等待无限堆积的预览/准备；进度读取不经过该锁。
        if not self.operations.acquire(blocking=False):
            raise MCPError("busy", "另一个准备或修改操作正在进行，请稍后复用相同 request_id", retryable=True)
        try:
            previous = self.responses.get(key)
            disk = self.ledger.get(*key)
            if previous or disk:
                old_digest = previous[0] if previous else disk["digest"]
                if old_digest != digest:
                    raise MCPError("idempotency_conflict", "同一 request_id 的内容不能改变")
                if previous:
                    payload = deepcopy(previous[1])
                    if payload.get("code") == "save_pending":
                        payload = self._save(payload["plan_id"])
                        self.responses[key] = digest, payload
                        refs = {k: payload[k] for k in ("job_id", "plan_id", "history_id", "revision") if k in payload}
                        self.ledger.put(*key, digest, payload.get("status", "unknown"), refs)
                    return payload
                if disk["state"] != "retryable":
                    return {"status": "expired", "code": "interrupted_or_expired", **disk["reference"],
                            "message": "服务已重启或运行态已过期；该请求不会自动重放，请检查历史后用新 request_id"}
            if len(self.responses) >= 512:
                self.responses.pop(next(iter(self.responses)))
            self.ledger.put(*key, digest, "started", {})
            try:
                payload = fn()
            except MCPError as error:
                payload = error.payload
            except (SessionNotFound, PreviewNotFound):
                payload = {"status": "expired", "code": "expired", "message": "行程或预览已过期"}
            except StaleRevision:
                payload = {"status": "error", "code": "stale_revision", "message": "行程版本已变化，请先读取最新版本"}
            except (ValidationError, ValueError):
                payload = {"status": "error", "code": "invalid_input", "message": "请检查字段及候选ID"}
            except Exception:
                payload = {"status": "failed", "code": "operation_failed", "message": "操作暂未完成，请检查进度和历史；未自动重放"}
            if not payload.get("retryable"):
                self.responses[key] = digest, deepcopy(payload)
            references = {k: payload[k] for k in ("job_id", "plan_id", "history_id", "revision") if k in payload}
            self.ledger.put(*key, digest, "retryable" if payload.get("retryable") else payload.get("status", "unknown"), references)
            return payload
        finally:
            self.operations.release()

    def _job(self, owner: str, job_id: str) -> Job:
        with self.lock:
            job = self.jobs.get(job_id)
            if job is None or job.owner != owner:
                raise MCPError("not_found", "任务不存在")
            if time.time() - job.created > TTL:
                job.cancelled.set()
                job.status = "expired"
                raise MCPError("expired", "准备或任务已过期")
            return job

    def prepare(self, owner: str, profile_data: dict, request_id: str, job_id: str | None = None,
                origin_query: str | None = None) -> dict:
        body = {"profile": profile_data, "job_id": job_id, "origin_query": origin_query}
        def action():
            with self.lock:
                for identifier, old in list(self.jobs.items()):
                    if time.time() - old.created > TTL and old.status not in {"queued", "running"}:
                        del self.jobs[identifier]
                if job_id:
                    job = self._job(owner, job_id)
                    if job.status not in {"preparing", "needs_input"}:
                        raise MCPError("invalid_state", "已启动的任务不能改画像，请新建准备任务")
                else:
                    if len(self.jobs) >= MAX_JOBS:
                        finished = next((identifier for identifier, old in self.jobs.items()
                                         if old.status not in {"queued", "running", "preparing", "needs_input"}), None)
                        if finished is not None:
                            del self.jobs[finished]
                    if self.closed or len(self.jobs) >= MAX_JOBS:
                        raise MCPError("busy", "任务存储已满或正在关闭，请稍后重试", retryable=True)
                    job = Job(owner)
                    job.budget.cancelled = job.cancelled
                    self.jobs[job.identifier] = job
                job.revision += 1
                job.candidate_set_id = ""
                job.profile = None
                job.catalog = None
            data = deepcopy(profile_data)
            city = data.get("city") or DEFAULT_CITY
            try:
                ensure_enabled(city)
            except ValueError as error:  # 含 CityDisabled 与未知城市
                raise MCPError("city_unavailable", str(error) if isinstance(error, CityDisabled) else "未知城市",
                               enabled=list(enabled_cities())) from None
            missing = [key for key in ("days", "budget", "start_date") if data.get(key) is None]
            origins = []
            if origin_query:
                if not 2 <= len(origin_query) <= 80:
                    raise MCPError("invalid_origin", "起点查询需2–80字")
                from .amap import get_router
                from .places import normalize_place
                router = get_router().fork(job.budget)
                try:
                    raw = router._get("v3/place/text", {"keywords": origin_query, "city": city_config(city).adcode, "citylimit": "true", "offset": 10, "page": 1})
                    origins = [p for item in raw.get("pois", []) if (p := normalize_place(item, city)) is not None]
                except Exception:
                    return {"status": "data_unavailable", "code": "origin_unavailable", "job_id": job.identifier,
                            "preparation_revision": job.revision, "amap_http_calls": job.budget.used}
            if "hotel" not in data or not all(key in data["hotel"] for key in ("name", "lat", "lon")):
                missing.append("hotel")
            if missing or origins:
                job.status = "needs_input"
                return {"status": job.status, "job_id": job.identifier, "preparation_revision": job.revision,
                        "missing_fields": sorted(set(missing)), "origin_options": origins,
                        "coordinate_system": "BD-09", "profile_schema": UserProfile.model_json_schema(),
                        "amap_http_calls": job.budget.used}
            profile = UserProfile.model_validate(data)
            job.profile = profile
            # 每次准备读一次当前发布版并固定；其他任务之后的发布不会让这次准备失效
            catalog = published_catalog(profile.city)
            with use_catalog(catalog):
                job.candidates, job.report = prepare_candidates(profile)
                candidates = public_candidates(job.candidates)
            job.catalog, job.version = catalog, catalog.version
            job.candidate_set_id = secrets.token_urlsafe(18)
            job.status = "preparing"
            gaps = [entry for entry in job.report["must_visit"] if entry["status"] != "covered"]
            return {"status": "needs_changes" if gaps else "prepared", "job_id": job.identifier,
                    "preparation_revision": job.revision, "candidate_set_id": job.candidate_set_id,
                    "expires_at": job.created + TTL, "profile": profile.model_dump(mode="json"),
                    "candidates": candidates, "retrieval_report": job.report,
                    "required_gaps": gaps, "amap_http_calls": job.budget.used,
                    "instructions": "仅选择当前候选ID并写偏好理由，不编排时间；包含所有必去。候选资料为历史资料，不能宣称实时核验。"}
        return self._once(owner, "prepare", request_id, body, action)

    def generate(self, owner: str, job_id: str, preparation_revision: int, candidate_set_id: str,
                 selections: list[dict], request_id: str) -> dict:
        body = {"job_id": job_id, "preparation_revision": preparation_revision,
                "candidate_set_id": candidate_set_id, "selections": selections}
        def action():
            job = self._job(owner, job_id)
            if job.profile is None or job.status != "preparing":
                raise MCPError("invalid_state", "请先完成准备，不要重复启动任务")
            if (job.revision != preparation_revision or job.candidate_set_id != candidate_set_id
                    or job.catalog is None):
                raise MCPError("stale_preparation", "画像、候选或数据版本已变化，请重新准备")
            selected = [ExternalSelection.model_validate(s) for s in selections]
            pool = {c.poi_id for c in job.candidates}
            ids = [s.poi_id for s in selected]
            if not ids or len(ids) > 30 or len(set(ids)) != len(ids) or not set(ids) <= pool:
                raise MCPError("invalid_selection", "请选择不超过30个当前候选池ID，不得重复")
            if any(entry["status"] != "covered" for entry in job.report["must_visit"]):
                return {"status": "needs_changes", "code": "required_candidate_unavailable", "conflicts": job.report["must_visit"],
                        "options": ["调整必去地点或活动日期/时段"]}
            from .nodes.retrieve import must_visit_ids
            if must_visit_ids(job.profile) - set(ids):
                raise MCPError("missing_required_selection", "所选ID必须包含已确认必去")
            with self.lock:
                active = next((j for j in self.jobs.values() if j.owner == owner and j.status in {"queued", "running"}), None)
                if active:
                    raise MCPError("busy", "已有规划正在执行", job_id=active.identifier, retryable=True)
                if self.closed:
                    raise MCPError("busy", "服务正在关闭")
                if self.daily_limit and not self.store.consume(time.strftime("%Y-%m-%d"), {f"mcp:{owner}": self.daily_limit}):
                    raise MCPError("daily_limit", "MCP每日次数已用完")
                job.status = "queued"
                self.pool.submit(self._run, job, selected)
            return {"status": "queued", "job_id": job.identifier}
        # 保存失败仅重试同一个会话的保存，不重算也不再次提交。
        existing = self.jobs.get(job_id)
        key = owner, "generate", request_id
        if existing and existing.owner == owner and existing.output.get("code") == "save_pending" and key in self.responses:
            expected = hashlib.sha256(json.dumps(body, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            if self.responses[key][0] == expected:
                with self.operations:
                    existing.output = self._save(existing.plan_id)
                    existing.status = existing.output["status"]
        return self._once(owner, "generate", request_id, body, action)

    def _run(self, job: Job, selected: list):
        token = operation_budget.set(job.budget)
        # 线程池不复制ContextVar：显式安装该任务准备时固定的目录
        catalog_token = poi_catalog.set(job.catalog)
        try:
            if job.cancelled.is_set():
                return
            job.status = "running"
            def progress(event):
                job.progress = {k: event[k] for k in ("stage", "status", "round") if k in event}
                stage = event.get("stage", "unknown")
                delta = max(0, job.budget.used - job.observed_calls)
                job.stage_calls[stage] = job.stage_calls.get(stage, 0) + delta
                job.observed_calls = job.budget.used
                if job.cancelled.is_set():
                    raise MCPError("cancelled", "任务已取消")
            result = run_selected(job.profile, job.candidates, selected, job.report, progress=progress)
            outcome = delivery_status(result, allow_unverified_draft=self.allow_unverified_draft)
            if job.budget.used >= PLAN_HTTP_LIMIT and outcome["status"] == "data_unavailable":
                outcome["code"] = "quota_exhausted"
            if job.cancelled.is_set():
                return
            if outcome["status"] in {"ready", "draft"}:
                result["delivery_status"] = outcome["status"]
                request = {"source": "mcp", "delivery_status": outcome["status"],
                           "allow_unverified_draft": self.allow_unverified_draft, "select_mode": "external_ai", "enable_repair": True, "guide_mode": "off",
                           "input": {"query": "", "form": job.profile.model_dump(mode="json"), "select_mode": "llm", "enable_repair": True}}
                with self.lock:
                    if job.cancelled.is_set():
                        return
                    session = self.sessions.create(result, request, job.owner)
                    job.plan_id = session.plan_id
                    job.output = self._save(session.plan_id)
            else:
                job.output = {**outcome, "report": result["report"].model_dump(),
                              "adjustments": result.get("adjustments", [])}
            job.status = job.output["status"]
        except Exception:
            if not job.cancelled.is_set():
                job.status = "failed"
                job.output = {"status": "failed", "code": "planning_failed", "message": "规划未完成；请检查数据来源后用新请求准备"}
        finally:
            poi_catalog.reset(catalog_token)
            operation_budget.reset(token)
            if job.cancelled.is_set():
                job.status = "cancelled"
                job.output = {"status": "cancelled"}

    def _save(self, plan_id: str) -> dict:
        session = self.sessions.get(plan_id)
        result, request, revision, owner = self.sessions.snapshot(plan_id)
        if request.get("source") == "mcp":
            qualification = delivery_status(result, allow_unverified_draft=self.allow_unverified_draft)
            if qualification["status"] not in {"ready", "draft"}:
                return {**qualification, "plan_id": plan_id, "revision": revision, "applied": True}
            result["delivery_status"] = qualification["status"]
            request["delivery_status"] = qualification["status"]
        payload = serialize_plan(result, request, plan_id, revision)
        try:
            history_id = self.store.save(owner[5:], plan_id, request["input"], payload)
        except (OSError, accounts.sqlite3.Error):
            return {"status": "save_pending", "code": "save_pending", "plan_id": plan_id,
                    "revision": revision, "applied": True, "message": "当前版本已生效，历史保存待重试；复用原修改请求"}
        self.sessions.bind_history(plan_id, history_id)
        return {"status": result.get("delivery_status", "ready"), "plan_id": plan_id, "history_id": history_id, "revision": revision,
                "site_url": f"{self.site_url}/?trip={quote(history_id)}" if self.site_url else None,
                "plan": {**payload, "history_id": history_id}, "server_llm_calls": 0, "server_llm_tokens": 0}

    def get_job(self, owner: str, job_id: str) -> dict:
        job = self._job(owner, job_id)
        return {**deepcopy(job.output), "status": job.status, "job_id": job_id, "progress": deepcopy(job.progress),
                "amap_http_calls": job.budget.used, "amap_http_limit": PLAN_HTTP_LIMIT, "stage_http_calls": deepcopy(job.stage_calls),
                "server_llm_calls": 0, "server_llm_tokens": 0}

    def get_trip(self, owner: str, trip_id: str, expected_revision: int | None = None) -> dict:
        try:
            session = self.sessions.get(trip_id)
        except SessionNotFound:
            try:
                history = self.store.get(owner[5:], trip_id)
            except HTTPException:
                raise MCPError("not_found", "行程不存在") from None
            session = self.sessions.find_history(owner, trip_id)
            if session is None:
                return {"status": "readonly", "edit_state": "expired", "plan": history["plan"]}
        if session.owner != owner:
            raise MCPError("not_found", "行程不存在")
        result, request, revision, snapshot_owner = self.sessions.snapshot(session.plan_id)
        if snapshot_owner != owner:
            raise MCPError("not_found", "行程不存在")
        if expected_revision is not None and expected_revision != revision:
            raise MCPError("stale_revision", "行程版本已变化", revision=revision)
        return {"status": result.get("delivery_status", request.get("delivery_status", "ready")), "plan": serialize_plan(result, request, session.plan_id, revision),
                "server_llm_calls": 0, "server_llm_tokens": 0}

    def preview(self, owner: str, trip_id: str, base_revision: int, edit: dict, request_id: str) -> dict:
        body = {"trip_id": trip_id, "base_revision": base_revision, "edit": edit}
        def action():
            from .meal_edits import build_preview
            session = self.sessions.get(trip_id)
            if session.owner != owner:
                raise MCPError("not_found", "行程不存在")
            try:
                ensure_enabled(reference_city(session.result().get("poi_catalog")))
            except CityDisabled as error:
                raise MCPError("city_disabled", str(error)) from None
            if session.current != base_revision:
                raise StaleRevision()
            request = MealEditPreviewRequest.model_validate({**edit, "revision": base_revision})
            if request.action != "replace":
                raise MCPError("unsupported_edit", "首版仅替换备选餐厅，不代替用户确认自行安排")
            if not 1 <= request.day <= len(session.result()["itinerary"].days):
                raise MCPError("invalid_meal", "日期不在行程中")
            day = session.result()["itinerary"].days[request.day - 1]
            if request.item_index is None or request.item_index >= len(day.items):
                raise MCPError("invalid_meal", "餐次不存在")
            if request.restaurant_id not in {a.ref_id for a in day.items[request.item_index].alternatives}:
                raise MCPError("invalid_restaurant", "只能从当前餐次提供的备选ID选择")
            budget = RequestBudget(PLAN_HTTP_LIMIT)
            budget_token = operation_budget.set(budget)
            pool_token = allowed_pois.set(frozenset(session.result().get("mcp_candidate_ids", [])))
            try:
                updated, change = build_preview(deepcopy(session.result()), request)
            finally:
                operation_budget.reset(budget_token)
                allowed_pois.reset(pool_token)
            outcome = delivery_status(updated, allow_unverified_draft=self.allow_unverified_draft)
            if outcome["status"] not in {"ready", "draft"}:
                return {**outcome, "amap_http_calls": budget.used}
            updated["delivery_status"] = outcome["status"]
            preview = self.sessions.add_preview(trip_id, base_revision, updated, change)
            return {"status": "preview", "preview_id": preview.preview_id, "base_revision": base_revision,
                    "expires_at": preview.expires_at, "change": change,
                    "plan": serialize_plan(updated, session.request, trip_id, base_revision, change),
                    "amap_http_calls": budget.used, "confirmation_required": True}
        return self._once(owner, "preview", request_id, body, action)

    def confirm(self, owner: str, trip_id: str, base_revision: int, preview_id: str, request_id: str) -> dict:
        body = {"trip_id": trip_id, "base_revision": base_revision, "preview_id": preview_id}
        def action():
            from .meal_edits import validate_preview
            session = self.sessions.get(trip_id)
            if session.owner != owner:
                raise MCPError("not_found", "行程不存在")
            try:
                ensure_enabled(reference_city(session.result().get("poi_catalog")))
            except CityDisabled as error:
                raise MCPError("city_disabled", str(error)) from None
            preview = self.sessions.get_preview(trip_id, base_revision, preview_id)
            updated = deepcopy(preview.result)
            updated["report"] = validate_preview(updated)
            outcome = delivery_status(updated, allow_unverified_draft=self.allow_unverified_draft)
            if outcome["status"] not in {"ready", "draft"}:
                return outcome
            for key in ("tickets", "meals", "transport", "total"):
                if abs(updated["report"].totals.get(key, 0) - preview.result["report"].totals.get(key, 0)) > 0.01:
                    raise MCPError("changed_evidence", "费用依据变化，请重新预览")
            self.sessions.commit_preview(trip_id, base_revision, preview_id, updated["report"])
            return self._save(trip_id)
        return self._once(owner, "confirm", request_id, body, action)

    def cancel(self, owner: str, job_id: str) -> dict:
        with self.lock:
            job = self._job(owner, job_id)
            if job.status not in {"queued", "running", "preparing", "needs_input"}:
                return {"status": job.status, "job_id": job_id}
            job.cancelled.set()
            job.status = "cancelled"
            return {"status": "cancelled", "job_id": job_id}
