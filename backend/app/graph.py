"""LangGraph 状态图：parse → retrieve → select → schedule → meals → validate →（repair → schedule）|（refine）→ END。

refine（合理性精修，app/refine.py）默认关闭，enable_refine=True 时在修复循环结束后运行一次。
"""
from __future__ import annotations

import operator
import time
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph

from .llm import LLM, Usage, get_llm
from .nodes.meals import meals_node
from .nodes.parse import parse_node
from .nodes.repair import MAX_ROUNDS, repair_node
from .cities import DEFAULT_CITY, ensure_enabled
from .planning_context import poi_catalog
from .poi_catalog import published_catalog, use_catalog
from .refine import refine_node
from .nodes.retrieve import retrieve_node
from .nodes.schedule import schedule_node
from .nodes.select import select_node
from .nodes.validate import validate_node
from .schemas import Candidate, Itinerary, Selection, UserProfile, ValidationReport
from .progress import Ticker, release_ticker, stage_event, use_ticker


class PlanState(TypedDict, total=False):
    # 输入
    query: str
    form: dict[str, Any]
    llm: Any
    select_mode: str  # "llm" | "rule"（消融）
    guide_mode: str
    guide_evidence: list[dict]
    selection_stats: dict
    retrieval_report: dict
    initial_selected_ids: list[int]
    schedule_attempts: Annotated[list[dict], operator.add]
    enable_repair: bool
    enable_refine: bool
    refined: bool
    refine_scores: dict
    use_solver: bool
    travel_source: str | None  # "amap" | "estimate" | None（有 Web服务 key 就用高德）
    travel: Any  # app.travel.Travel（本次规划的路程来源）
    catalog: Any
    frozen_candidates: bool
    repair_fingerprints: list[str]
    repair_removed_ids: list[int]
    repair_pending_additions: list[int]  # 体验补点试排后，由 meals 检查实际片区顺序才采纳
    best_candidate: Any
    # 中间结果
    profile: UserProfile
    candidates: list[Candidate]
    selected: list[Selection]
    day_orders: list[list[int]]
    dropped: list[int]
    meal_factor: float
    itinerary: Itinerary
    report: ValidationReport
    repair_round: int
    repair_changed: bool
    log: Annotated[list[str], operator.add]


def _finish(state: PlanState) -> str:
    return "refine" if state.get("enable_refine") and not state.get("refined") else END


def _after_validate(state: PlanState) -> str:
    if (state["report"].passed and state["report"].experience_status == "complete") or not state.get("enable_repair", True) or state.get("repair_round", 0) >= MAX_ROUNDS:
        return _finish(state)
    return "repair"


def _after_repair(state: PlanState) -> str:
    return "schedule" if state.get("repair_changed") else _finish(state)


def build_graph(progress=None, *, external_selection: bool = False):
    g = StateGraph(PlanState)
    for name, fn in [("parse", parse_node), ("retrieve", retrieve_node), ("select", select_node),
                     ("schedule", schedule_node), ("meals", meals_node), ("validate", validate_node),
                     ("repair", repair_node), ("refine", refine_node)]:
        if external_selection and name in {"parse", "retrieve", "select"}:
            continue
        if progress is None:
            g.add_node(name, fn)
        else:
            def observed(state, node=fn, stage=name):
                progress(stage_event(stage, "running", state))
                token = use_ticker(Ticker(lambda detail: progress(
                    {"type": "progress", "stage": stage, "status": "running",
                     "round": state.get("repair_round", 0), "detail": detail}), stage))
                try:
                    update = node(state)
                finally:
                    release_ticker(token)
                progress(stage_event(stage, "completed", {**state, **update}))
                return update
            g.add_node(name, observed)
    if external_selection:
        g.add_edge(START, "schedule")
    else:
        g.add_edge(START, "parse")
        g.add_edge("parse", "retrieve")
        g.add_edge("retrieve", "select")
        g.add_edge("select", "schedule")
    g.add_edge("schedule", "meals")
    g.add_edge("meals", "validate")
    g.add_conditional_edges("validate", _after_validate, {"repair": "repair", "refine": "refine", END: END})
    g.add_conditional_edges("repair", _after_repair, {"schedule": "schedule", "refine": "refine", END: END})
    g.add_edge("refine", END)
    return g.compile()


_GRAPH = None


def run_plan(query: str = "", form: dict[str, Any] | None = None, *, llm: LLM | None = None,
             select_mode: str = "llm", enable_repair: bool = True, use_solver: bool = True,
             travel_source: str | None = None, guide_mode: str = "off", progress=None,
             enable_refine: bool = False) -> dict[str, Any]:
    if guide_mode not in {"off", "pilot"}:
        raise ValueError("未知攻略模式")
    if select_mode not in {"llm", "rule", "jev"}:
        raise ValueError("未知选点模式")
    if select_mode == "jev" and guide_mode != "off":
        raise ValueError("Jev 选点试验暂不与攻略试验混用")
    global _GRAPH
    if _GRAPH is None:
        _GRAPH = build_graph()
    llm = llm or get_llm()
    if hasattr(llm, "fork"):
        llm = llm.fork()
        llm.on_output = progress
    before = Usage()
    before.add(llm.usage)
    t0 = time.time()
    graph = build_graph(progress) if progress is not None else _GRAPH
    # 整个任务固定同一目录版本；中途发布新版只影响下一次规划。城市由表单决定，未启用的城市不能新建规划
    city = (form or {}).get("city") or DEFAULT_CITY
    ensure_enabled(city)
    catalog = poi_catalog.get() or published_catalog(city)
    if catalog.city != city:
        raise ValueError("任务目录与所选城市不一致")
    with use_catalog(catalog):
        out = graph.invoke({"query": query, "form": form or {}, "llm": llm, "select_mode": select_mode,
                             "enable_repair": enable_repair, "enable_refine": enable_refine, "use_solver": use_solver, "meal_factor": 1.0,
                             "travel_source": travel_source, "guide_mode": guide_mode,
                             "repair_round": 0, "log": []}, {"recursion_limit": 50})
        if progress is not None:
            progress(stage_event("finalize", "running", out))
        used = Usage()
        used.add(llm.usage)
        delta = {k: (v - getattr(before, k) if isinstance(v, (int, float)) else v)
                 for k, v in vars(used).items() if k != "by_tag"}
        out["usage"] = {**delta, "total_tokens": delta["prompt_tokens"] + delta["completion_tokens"]}
        out["seconds"] = round(time.time() - t0, 2)
        out["poi_catalog"] = catalog.reference()
        return finish_result(out, progress)


def finish_result(out: dict, progress=None) -> dict:
    """网页与外部选点入口共用的定稿及统计；不调用模型。"""
    best = out.pop("best_candidate", None)
    if best is not None:
        for field in ("itinerary", "report", "selected", "day_orders", "dropped"):
            out[field] = best[field]
    from .candidate_metrics import candidate_metrics
    out["candidate_metrics"] = candidate_metrics(out)
    from .meal_edits import meal_metrics
    out["meal_metrics"] = meal_metrics(out)
    out.pop("llm", None)
    route = out.pop("travel", None)
    catalog = out.pop("catalog", None)
    out["restaurants"] = catalog.restaurants if catalog else {}
    if catalog:
        out["log"].extend(list(dict.fromkeys(catalog.notices)))
        out["restaurant_stats"] = {"online_candidates":catalog.online_count, "searches":len(catalog.queries)}
    if route:
        route.finalize(out["itinerary"], out["profile"], out["restaurants"])
    out["travel_stats"] = route.stats() if route is not None else {"source": "estimate"}
    if progress is not None:
        progress(stage_event("finalize", "completed", out))
    return out
