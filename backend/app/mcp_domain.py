"""外部 AI 的确定性业务入口。此模块不取得模型，也不调用 parse/select。"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone, timedelta

from .data import load_pois
from .graph import build_graph, finish_result
from .meal_edits import freeze_preview_locations, validate_preview
from .nodes.retrieve import retrieve_with_report, must_visit_ids
from .planning_context import allowed_pois, poi_catalog
from .poi_catalog import current_catalog
from .restaurants import RestaurantCatalog
from .schemas import ExternalSelection, UserProfile
from .travel import Travel


class MCPError(Exception):
    def __init__(self, code: str, message: str, *, retryable: bool = False, **details):
        super().__init__(message)
        self.payload = {"status": "error", "code": code, "message": message, "retryable": retryable, **details}


def data_version() -> str:
    """当前上下文目录版本；任务准备后固定在 Job 上，不再与全局发布版比较。"""
    return current_catalog().version


def prepare_candidates(profile: UserProfile) -> tuple[list, dict]:
    return retrieve_with_report(profile)


def public_candidates(candidates: list) -> list[dict]:
    places = load_pois()
    return [{**c.model_dump(mode="json"), "name": places[c.poi_id].name,
             "summary": places[c.poi_id].summary, "price": places[c.poi_id].price,
             "open_min": places[c.poi_id].open_min, "close_min": places[c.poi_id].close_min,
             "min_hours": places[c.poi_id].min_hours,
             "facts_are_historical": True,
             # 补充地点：只在逐日准入日期开放，票价可能是已知上界
             **({"catalog_source": "supplement",
                 "available_dates_only": bool(places[c.poi_id].opening_rules and places[c.poi_id].opening_rules.dates_listed_only),
                 "price_basis": "exact" if places[c.poi_id].price_verified else
                     "inferred" if places[c.poi_id].field_evidence["price"].basis == "curator_inference" else "upper_bound"}
                if places[c.poi_id].catalog_source == "supplement" else {})} for c in candidates]


def run_selected(profile: UserProfile, candidates: list, selections: list[ExternalSelection],
                 retrieval_report: dict, *, progress=None, route=None, catalog=None,
                 use_solver: bool = True) -> dict:
    """复用确定性图及收尾，不经过默认 run_plan 的模型取得/自然语言节点。"""
    ids = [s.poi_id for s in selections]
    pool = {c.poi_id for c in candidates}
    if not ids or len(ids) != len(set(ids)) or not set(ids) <= pool:
        raise MCPError("invalid_selection", "只可选择当前候选池内不重复的 ID")
    required = must_visit_ids(profile)
    if required - set(ids):
        raise MCPError("missing_required_selection", "所选景点缺少已确认必去地点", required_ids=sorted(required))
    from .schemas import Selection
    selected = []
    adjustments = []
    for entry in selections:
        # 理由不承诺宿主还未看到的时段；排程后无法确定性重写自然语言事实。
        temporal = re.search(r"上午|下午|早上|早晨|午后|晚上|晚间|日落|日出|\d{1,2}[:：]\d{2}|\d{1,2}\s*[点时]|morning|afternoon|evening|sunrise|sunset", entry.reason, re.I)
        reason = "依据你的偏好选择；实际游玩时段以排程和校验结果为准" if temporal else entry.reason
        if temporal:
            adjustments.append({"kind": "reason", "poi_id": entry.poi_id, "before": entry.reason,
                                "after": reason, "reason": "移除未经排程确认的时段承诺", "source": "rule"})
        selected.append(Selection(poi_id=entry.poi_id, reason=reason, source="external_ai"))
    priority = {entry.poi_id: entry.priority for entry in selections}
    candidates = [c.model_copy(update={"score": c.score + priority.get(c.poi_id, 0) / 100}) for c in candidates]
    route = route or Travel("amap", profile.transport)
    route.allow_taxi_fallback = profile.allow_taxi_fallback
    catalog = catalog or RestaurantCatalog(route)
    places = current_catalog()
    token = allowed_pois.set(frozenset(pool))
    catalog_token = poi_catalog.set(places)
    started = time.monotonic()
    try:
        out = build_graph(progress, external_selection=True).invoke({
            "profile": profile, "candidates": candidates, "selected": selected,
            "initial_selected_ids": ids, "retrieval_report": retrieval_report,
            "travel": route, "catalog": catalog, "travel_source": "amap",
            "frozen_candidates": True, "enable_repair": True, "enable_refine": True,
            "use_solver": use_solver, "meal_factor": 1.0, "repair_round": 0, "log": [],
        }, {"recursion_limit": 50})
        out["usage"] = {"calls": 0, "cached_calls": 0, "prompt_tokens": 0, "completion_tokens": 0,
                        "total_tokens": 0, "seconds": 0}
        out["seconds"] = round(time.monotonic() - started, 2)
        out["poi_catalog"] = places.reference()
        out = finish_result(out, progress)
        # 使用定稿证据重新校验，不因额外联网刷新而产生检查/交付差异。
        freeze_preview_locations(out)
        out["report"] = validate_preview(out)
        after = [i.ref_id for d in out["itinerary"].days for i in d.items if i.kind == "poi"]
        if not set(after) <= pool:
            raise MCPError("candidate_pool_changed", "排程超出冻结候选池，结果未交付")
        for identifier in ids:
            if identifier not in after:
                adjustments.append({"kind": "remove", "poi_id": identifier, "before": "selected", "after": None,
                                    "reason": "确定性排程或修复为时间、餐饮或交通腾出空间", "source": "rule"})
        for identifier in after:
            if identifier not in ids:
                adjustments.append({"kind": "add", "poi_id": identifier, "before": None, "after": "scheduled",
                                    "reason": "从冻结候选池补充可用体验并重新校验", "source": "rule"})
        if [i for i in ids if i in after] != [i for i in after if i in ids]:
            adjustments.append({"kind": "reorder", "before": ids, "after": after,
                                "reason": "按地理、开放时段和餐饮顺序排程", "source": "rule"})
        out["adjustments"] = adjustments
        out["mcp_candidate_ids"] = sorted(pool)
        return out
    finally:
        poi_catalog.reset(catalog_token)
        allowed_pois.reset(token)


def delivery_status(result: dict, *, now: datetime | None = None, allow_unverified_draft: bool = False) -> dict:
    """统一门槛映射：证据 partial 继续阻止正式交付，不自动提升 verified。"""
    report = result["report"]
    missing = []
    if len(result["itinerary"].days) != result["profile"].days or any(
            not any(item.kind == "poi" for item in day.items) for day in result["itinerary"].days):
        return {"status": "needs_changes", "code": "no_valid_plan_found", "proven_infeasible": False,
                "message": "存在空白旅行日，有限候选未组成完整行程", "options": ["增加候选地点或减少天数"]}
    now = now or datetime.now(timezone.utc)
    for day in result["itinerary"].days:
        if not day.items:
            continue
        for label, evidence in [(item.name, item.travel_evidence) for item in day.items] + [("返程", day.return_leg)]:
            valid = evidence is not None and evidence.available and evidence.source in {"amap", "same_place"}
            if valid and evidence.source == "amap":
                try:
                    fetched = datetime.fromisoformat(evidence.fetched_at.replace("Z", "+00:00"))
                    valid = fetched.tzinfo is not None and timedelta(seconds=-60) <= now - fetched <= timedelta(minutes=15)
                except (AttributeError, ValueError):
                    valid = False
                valid = valid and evidence.service_date == day.date
            if not valid:
                missing.append({"day": day.day, "leg": label, "reason": "缺少有效真实路线证据"})
    if missing:
        return {"status": "data_unavailable", "code": "missing_leg", "gaps": missing,
                "options": ["稍后重试真实路线查询", "选择资料可用的起点或日期后重新准备"]}
    if not report.passed or report.hard:
        return {"status": "needs_changes", "code": "no_valid_plan_found", "proven_infeasible": False,
                "conflicts": [v.model_dump() for v in report.hard],
                "message": "有限候选和有界修复未找到满足全部硬条件的方案，并非数学无解证明",
                "options": ["调整必去地点", "提高预算", "调整日期或活动时段"]}
    if not report.complete_executable and allow_unverified_draft and report.arrangement_status == "complete":
        return {"status": "draft", "code": "unverified_test_draft", "unknowns": report.unknowns,
                "arrangement_status": report.arrangement_status, "evidence_status": report.evidence_status,
                "complete_executable": False, "requires_verification": True,
                "message": "测试草稿：硬条件与真实路线通过，开放时间、票价等仍待核实"}
    if not report.complete_executable:
        return {"status": "data_unavailable", "code": "incomplete_evidence", "unknowns": report.unknowns,
                "arrangement_status": report.arrangement_status, "evidence_status": report.evidence_status,
                "options": ["补齐开放、价格或餐饮证据后重试"],
                "message": "未达到现有统一交付门槛，未保存正式行程"}
    return {"status": "ready", "code": "verified"}
