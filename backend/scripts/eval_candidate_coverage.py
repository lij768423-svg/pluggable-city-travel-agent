"""离线重放已冻结的旧候选池，比较相同画像在阶段一召回后的覆盖变化。"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    arguments = parser.parse_args()
    os.environ["TTA_OFFLINE"] = "1"
    from app.config import CITY_DIR
    from app.data import load_pois
    from app.experiences import experience_key
    from app.nodes.retrieve import retrieve_with_report
    from app.schemas import UserProfile

    places = load_pois()
    cases = json.loads(arguments.baseline.read_text())
    rows = []
    for case in cases:
        profile = UserProfile.model_validate(case["profile"])
        candidates, report = retrieve_with_report(profile)
        def summarize(identifiers: set[int]) -> dict:
            coverage = {interest: any(interest in places[identifier].interests for identifier in identifiers)
                        for interest in dict.fromkeys(profile.interests)}
            groups = Counter(experience_key(places[identifier]) for identifier in identifiers if places[identifier].visit_group)
            return {"candidate_count": len(identifiers), "interest_covered": sum(coverage.values()),
                    "interest_requested": len(coverage), "missing_interests": [interest for interest, covered in coverage.items() if not covered],
                    "continuous_extra_members": sum(max(0, count - 1) for count in groups.values())}
        previous_ids = {candidate["poi_id"] for candidate in case["baseline"]}
        current_ids = {candidate.poi_id for candidate in candidates}
        rows.append({"id": case["id"], "profile": profile.model_dump(mode="json"),
                     "before": summarize(previous_ids), "after": summarize(current_ids),
                     "baseline_ids": sorted(previous_ids), "current_ids": sorted(current_ids), "retrieval": report})
    totals = {}
    for variant in ("before", "after"):
        requested = sum(row[variant]["interest_requested"] for row in rows)
        covered = sum(row[variant]["interest_covered"] for row in rows)
        totals[variant] = {"requests": len(rows), "interest_coverage": {"numerator": covered, "denominator": requested,
            "rate": covered / requested if requested else None},
            "continuous_extra_members": sum(row[variant]["continuous_extra_members"] for row in rows)}
    digests = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(CITY_DIR.glob("*.parquet"))}
    result = {"scope": "21 fixed synthetic regression profiles when using the captured 2026-10-17 baseline; candidate coverage only; not a holdout or full itinerary quality experiment",
              "network_calls": 0, "data_sha256": digests,
              "baseline_sha256": hashlib.sha256(arguments.baseline.read_bytes()).hexdigest(), "totals": totals, "cases": rows}
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with arguments.output.open("x", encoding="utf-8") as output:
        json.dump(result, output, ensure_ascii=False, indent=2)
    print(json.dumps(totals, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
