"""协议无关的行程展示序列化；网页与 MCP 共用。"""
from __future__ import annotations
from typing import Any
from fastapi.encoders import jsonable_encoder
from . import config
from .data import load_pois, load_restaurants, tag_source
from .geo import bd09_to_gcj02, hm
from .place_details import remember_restaurants, describe_place
from .poi_catalog import result_catalog

def gcj(lat: float, lon: float) -> dict[str, float]:
    la, lo = bd09_to_gcj02(lat, lon)
    return {"lat": round(la, 6), "lon": round(lo, 6)}


def serialize_plan(r: dict[str, Any], request_meta: dict[str, Any], plan_id: str | None = None,
                   revision: int | None = None, change: dict[str, Any] | None = None) -> dict[str, Any]:
    """把规划结果转成前端格式（新规划、换餐厅、撤销共用）；地点按该结果自己的目录版本解析。"""
    with result_catalog(r):
        return _serialize_plan(r, request_meta, plan_id, revision, change)


def _serialize_plan(r: dict[str, Any], request_meta: dict[str, Any], plan_id: str | None,
                    revision: int | None, change: dict[str, Any] | None) -> dict[str, Any]:
    from .candidate_metrics import candidate_metrics
    from .meal_edits import meal_metrics
    P, R = load_pois(), r.get("restaurants", load_restaurants())
    restaurant_ids = {reference for day in r["itinerary"].days for item in day.items if item.kind == "meal"
                      for reference in [item.ref_id, *(alternative.ref_id for alternative in item.alternatives)]}
    remember_restaurants(R[reference] for reference in restaurant_ids if reference in R)
    cand = {c.poi_id: c for c in r["candidates"]}
    src = {s.poi_id: s.source for s in r["selected"]}
    cited = {selection.poi_id: set(selection.guide_ids) for selection in r["selected"]}
    days = []
    for d in r["itinerary"].days:
        items = []
        for i in d.items:
            place = P.get(i.ref_id) if i.kind == "poi" else R.get(i.ref_id)
            alternatives = []
            for alternative in i.alternatives:
                restaurant = R.get(alternative.ref_id)
                alternatives.append({**alternative.model_dump(),
                                     **(gcj(restaurant.lat, restaurant.lon) if restaurant else {}),
                                     "detail": describe_place(restaurant, "meal") if restaurant else None})
            items.append({**i.model_dump(), "start_hm": hm(i.start), "end_hm": hm(i.end),
                          "alternatives": alternatives,
                          **(gcj(place.lat, place.lon) if place else {}),
                          "type": getattr(place, "type", getattr(place, "cuisine", "")),
                          "visit_group": getattr(place, "visit_group", ""),
                          "visit_group_label": getattr(place, "visit_group_label", ""),
                          "visit_role": getattr(place, "visit_role", "attraction"),
                          "source": "rule" if i.evening else src.get(i.ref_id) if i.kind == "poi" else None,
                          "detail": describe_place(place, i.kind) if place else None,
                          "guide_evidence": [entry for entry in r.get("guide_evidence", []) if not i.evening and i.kind == "poi" and entry["id"] in cited.get(i.ref_id, set())],
                          "price_verified": getattr(place, "price_verified", False),
                          "restaurant": place.model_dump(include={"provider", "external_id", "address", "rating", "fetched_at", "hours_known", "opening_text", "price_known"}) if i.kind == "meal" and place else None,
                          "score": cand[i.ref_id].breakdown | {"score": cand[i.ref_id].score} if i.kind == "poi" and i.ref_id in cand else None})
        days.append({**d.model_dump(exclude={"items"}), "items": items})
    prof = r["profile"]
    from .guides import corpus_status
    return jsonable_encoder({
        "profile": prof.model_dump() | {"hotel_gcj": gcj(prof.hotel.lat, prof.hotel.lon)},
        "days": days,
        "report": r["report"].model_dump(),
        "candidates": [{"id": c.poi_id, "name": P[c.poi_id].name, "score": c.score, **c.breakdown,
                        **c.model_dump(include={"interests", "region_id", "region_label", "area_id", "area_label", "place_type", "experience_id", "experience_kind", "member_ids", "available_days", "notices"}),
                        "selected": c.poi_id in src, **gcj(P[c.poi_id].lat, P[c.poi_id].lon)} for c in r["candidates"]],
        "dropped": [P[x].name for x in r.get("dropped", [])],
        "log": r["log"], "usage": r["usage"], "seconds": r["seconds"],
        "plan_id": plan_id, "revision": revision, "change": change,
        # 只有叠加补充目录时才输出目录身份；原库行程的输出与历史格式保持不变
        **({"poi_catalog": r["poi_catalog"]} if (r.get("poi_catalog") or {}).get("supplement_digest") else {}),
        **({"history_id": request_meta["history_id"]} if request_meta.get("history_id") else {}),
        **({"delivery_status": r.get("delivery_status", request_meta.get("delivery_status", "ready")),
            "verification_notice": "测试草稿：开放时间、票价等仍待核实，请出发前确认"
                if r.get("delivery_status", request_meta.get("delivery_status")) == "draft" else None}
           if request_meta.get("source") == "mcp" else {}),
        "meta": {"travel": r.get("travel_stats", {}), "restaurants": r.get("restaurant_stats", {}), "select_mode": request_meta["select_mode"], "enable_repair": request_meta["enable_repair"], "repair_rounds": r.get("repair_round", 0),
                 "operation": r.get("operation", "plan"), "source": request_meta.get("source", "web"),
                 "server_llm_calls": r.get("usage", {}).get("calls", 0),
                 "adjustments": r.get("adjustments", []),
                 "llm_model": "external_ai" if request_meta.get("source") == "mcp" else config.LLM_MODEL, "llm_thinking": None if request_meta.get("source") == "mcp" else config.LLM_THINKING, "tag_source": tag_source(),
                 "selection": r.get("selection_stats", {}),
                 "retrieval": r.get("retrieval_report", {}), "candidate_metrics": candidate_metrics(r),
                 "meals": meal_metrics(r),
                 "guide_mode": request_meta["guide_mode"], "guide_snapshot": corpus_status() if request_meta["guide_mode"] == "pilot" else None,
                 "guide_status": "experimental_pending_human_evaluation" if request_meta["guide_mode"] == "pilot" else "off"},
    })
