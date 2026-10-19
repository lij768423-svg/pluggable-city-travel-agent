from __future__ import annotations

from pathlib import Path

from .common import GROUPS, SnapshotError, read_json


SEMANTIC_FIELDS = ("status", "error_type", "error", "input_contract", "evaluator_sha256", "flags", "itinerary", "report",
                   "metrics", "meals", "actual_candidates", "selected_ids", "repair_rounds", "model_response")


def compare(first: Path, second: Path) -> dict:
    before, after = read_json(first), read_json(second)
    if before["snapshot_sha256"] != after["snapshot_sha256"]:
        raise SnapshotError("两次回放来自不同快照")
    if before.get("execution_budget") != after.get("execution_budget"):
        raise SnapshotError("两次回放使用了不同运行预算")
    left = {(row["case_id"], row["group"]): row for row in before["results"]}
    right = {(row["case_id"], row["group"]): row for row in after["results"]}
    if left.keys() != right.keys() or len(left) != len(before["results"]) or len(right) != len(after["results"]):
        raise SnapshotError("回放案例/分组缺失或重复")
    differences = [{"case_id": key[0], "group": key[1], "fields": [field for field in SEMANTIC_FIELDS
                    if left[key].get(field) != right[key].get(field)]} for key in left]
    differences = [entry for entry in differences if entry["fields"]]
    checks = 0
    for result in (before, after):
        for row in result["results"]:
            if row["status"] != "ok":
                continue
            rides = [item["travel_min"] for day in row["itinerary"]["days"] for index, item in enumerate(day["items"])
                     if index > 0 and item["travel_mode"] not in {"步行", "原地"}]
            if row["metrics"]["internal_ride_min"] != sum(rides) or row["metrics"]["internal_max_ride_min"] != max(rides, default=0):
                raise SnapshotError("内部交通指标与原始时间线不符")
            expected = row["report"]["constraint_status"] == "passed" and row["report"]["arrangement_status"] == "complete" and row["report"]["evidence_status"] == "verified"
            if row["report"]["complete_executable"] != expected:
                raise SnapshotError("完整通过字段不等于三维状态的合取")
            checks += 1
        for name in GROUPS:
            rows = [row for row in result["results"] if row["group"] == name]
            expected = sum(row["status"] == "ok" and row["report"]["complete_executable"] for row in rows)
            summary = result["summaries"]["all"]["groups"][name]
            if summary["requests"] != len(rows) or summary["counts"]["complete_executable"] != expected:
                raise SnapshotError("汇总分母或完整通过分子错误")
    return {"snapshot_sha256": before["snapshot_sha256"], "rows": len(left),
            "semantic_matches": len(left) - len(differences), "differences": differences,
            "independently_recomputed_rows": checks, "excluded_fields": ["replay_seconds", "created_at", "log"],
            "scope": "Semantic replay and arithmetic checks, not a real-world quality or performance claim"}
