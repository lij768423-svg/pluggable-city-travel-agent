"""合理性精修的离线对照：同一组固定画像与选点，比较"现有流程"与"现有流程 + 合理性精修"。

基线 = 排程 + 餐饮环节（含餐饮联合比较、空档补点、夜游）+ 校验；不调用模型、不联网、估算交通。
用法：PYTHONPATH=. uv run python eval/refine_eval.py --output ../data/refine-20261016/refine.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.data import load_pois, load_restaurants  # noqa: E402
from app.nodes.meals import meals_node  # noqa: E402
from app.nodes.retrieve import must_visit_ids, retrieve, score_poi  # noqa: E402
from app.nodes.schedule import schedule_node  # noqa: E402
from app.nodes.validate import validate_itinerary  # noqa: E402
from app.reasonableness import WEIGHTS, score_itinerary  # noqa: E402
from app.refine import refine  # noqa: E402
from app.schemas import Selection, UserProfile  # noqa: E402
from app.travel import Travel  # noqa: E402
from route_quality import cases  # noqa: E402


def summary(itinerary, report, profile) -> dict:
    graded = score_itinerary(itinerary, profile, load_pois(), load_restaurants())
    return {"overall": graded["overall"], "scores": graded["scores"], "issues": len(graded["issues"]),
            "hard": len(report.hard), "missing_meals": sum(item.code.startswith("missing_") for item in report.violations),
            "visits": sum(item.kind == "poi" for day in itinerary.days for item in day.items),
            "travel_min": sum(day.return_travel_min + sum(item.travel_min for item in day.items) for day in itinerary.days)}


def run(case: dict) -> dict:
    started = time.monotonic()
    profile = UserProfile.model_validate(case["profile"])
    pois = load_pois()
    must = must_visit_ids(profile)
    candidates = retrieve(profile)
    known = {candidate.poi_id for candidate in candidates}
    candidates += [score_poi(pois[identifier], profile, must) for identifier in case["selected"] if identifier not in known]
    state = {"profile": profile, "candidates": candidates, "travel": Travel("estimate", profile.transport), "catalog": None,
             "selected": [Selection(poi_id=identifier, reason="固定选点对照", source="rule") for identifier in case["selected"]],
             "enable_repair": True, "use_solver": True, "meal_factor": 1.0, "dropped": []}
    state.update(schedule_node(state))
    state.update(meals_node(state))
    restaurants = load_restaurants()
    state["report"] = validate_itinerary(state["itinerary"], profile, travel_fn=state["travel"].leg, restaurants=restaurants)
    baseline = summary(state["itinerary"], state["report"], profile)
    middle = time.monotonic()
    refined = refine(state)
    after = summary(refined["itinerary"], refined["report"], profile)
    return {"id": case["id"], "before": baseline, "after": after, "log": refined["log"],
            "seconds_baseline": round(middle - started, 2), "seconds_refine": round(time.monotonic() - middle, 2)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--only", nargs="*")
    args = parser.parse_args()
    rows = []
    for case in cases():
        if args.only and case["id"] not in args.only:
            continue
        rows.append(run(case))
        row = rows[-1]
        print(f"{row['id']}: {row['before']['overall']} → {row['after']['overall']}  hard {row['before']['hard']}→{row['after']['hard']}"
              f"  visits {row['before']['visits']}→{row['after']['visits']}  {row['seconds_refine']}s  " + " | ".join(row["log"][1:]), flush=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"scope": "fixed selection; schedule + meals (incl. gap fill, evening) + validate, then refine; estimated travel; no model/network",
                                       "results": rows}, ensure_ascii=False, indent=2))
    def mean(key, side, sub=None):
        values = [row[side][key] if sub is None else row[side]["scores"][sub] for row in rows]
        values = [value for value in values if value is not None]
        return sum(values) / len(values) if values else float("nan")
    print("\n| 指标 | 现有流程 | + 精修 |\n|---|---:|---:|")
    print(f"| 综合（不含停留） | {mean('overall', 'before'):.1f} | {mean('overall', 'after'):.1f} |")
    for key in WEIGHTS:
        if key != "stay":
            print(f"| {key} | {mean('scores', 'before', key):.1f} | {mean('scores', 'after', key):.1f} |")
    for key in ("hard", "missing_meals", "visits", "travel_min", "issues"):
        print(f"| {key} | {sum(row['before'][key] for row in rows)} | {sum(row['after'][key] for row in rows)} |")
    print(f"| 精修耗时（秒，合计） | — | {sum(row['seconds_refine'] for row in rows):.1f} |")


if __name__ == "__main__":
    main()
