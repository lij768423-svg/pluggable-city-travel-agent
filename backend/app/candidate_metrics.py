from __future__ import annotations
from .schemas import interest_tags

from .data import load_pois
from .experiences import experience_count
from .matching import match_place


def candidate_metrics(state: dict) -> dict:
    places = load_pois()
    profile = state["profile"]
    candidates = {candidate.poi_id for candidate in state.get("candidates", [])}
    selected = {selection.poi_id for selection in state.get("selected", [])}
    initial_candidates = set(state.get("retrieval_report", {}).get("candidate_ids", candidates))
    initial_selected = set(state.get("initial_selected_ids", selected))
    scheduled = {identifier for order in state.get("day_orders", []) for identifier in order}
    delivered = {item.ref_id for day in state["itinerary"].days for item in day.items if item.kind == "poi" and item.ref_id in places}
    def ratio(numerator: int, denominator: int) -> dict:
        return {"numerator": numerator, "denominator": denominator,
                "rate": numerator / denominator if denominator else None}
    required = {name: match_place(name, places) for name in dict.fromkeys(profile.must_visit)}
    return {"must_visit_recall": ratio(sum(identifier in initial_candidates for identifier in required.values()), len(required)),
        "must_visit_delivered": ratio(sum(identifier in delivered for identifier in required.values()), len(required)),
        "selected_to_scheduler_drop": ratio(len(selected - scheduled), len(selected)),
        "selected_to_delivery_drop": ratio(len(selected - delivered), len(selected)),
        "initial_selected_to_delivery_drop": ratio(len(initial_selected - delivered), len(initial_selected)),
        "initial_candidate_count": len(initial_candidates), "expanded_candidate_count": len(candidates),
        "expanded_candidate_ids": sorted(candidates - initial_candidates),
        "schedule_attempts": state.get("schedule_attempts", []),
        "candidate_to_delivery_unused": ratio(len(candidates - delivered), len(candidates)),
        "scheduler_dropped_ids": sorted(selected - scheduled), "delivery_dropped_ids": sorted(selected - delivered),
        "interest_coverage": [{"name": interest,
            "candidate_count": sum(interest in interest_tags(places[identifier].interests) for identifier in candidates),
            "selected_count": sum(interest in interest_tags(places[identifier].interests) for identifier in selected),
            "delivered_count": sum(interest in interest_tags(places[identifier].interests) for identifier in delivered)}
            for interest in dict.fromkeys(profile.interests)],
        "delivered_experiences": experience_count(delivered, places),
        "route_conflicts": [{"code": issue.code, "day": issue.day, "item": issue.item, "message": issue.message}
            for issue in state["report"].violations if issue.code in {"interstop_ride_limit", "transport_unavailable", "travel_time"}],
        "continuous_duplicates": len(state.get("retrieval_report", {}).get("continuous_duplicates", []))}
