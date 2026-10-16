from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

from app.config import CITY_DIR
from app.data import load_pois, load_restaurants
from app.nodes.meals import build_itinerary
from app.nodes.schedule import schedule
from app.nodes.validate import validate_itinerary
from app.schemas import Selection, UserProfile
from app.travel import Travel


def cases() -> list[dict]:
    scenarios = [
        ("west-lake-walk", [36, 100, 109, 20, 91, 9], 2),
        ("park-day", [1, 20, 91, 36, 100, 109], 2),
        ("long-museums", [29, 30, 118, 202, 0, 6], 3),
        ("canal-and-lake", [88, 269, 309, 324, 36, 100, 109, 20], 3),
        ("short-last-day", [29, 202, 0, 6, 20, 91], 2),
        ("five-days", [1, 28, 0, 6, 29, 202, 20, 91, 36, 100, 109, 118], 5),
        ("food-and-budget", [20, 91, 57, 190, 9, 118], 2),
        ("two-parks", [1, 28, 20, 91], 2),
    ]
    result = []
    for name, selected, days in scenarios:
        for pace in ("normal", "relaxed", "intense"):
            profile = {"days": days, "pace": pace, "budget": 2500, "start_date": "2026-10-20",
                       "transport": "transit" if pace != "intense" else "taxi",
                       "companions": "elderly" if pace == "relaxed" else "solo", "include_evening": False}
            if name == "short-last-day":
                profile["daily_windows"] = [{"day": 2, "start": 540, "end": 840}]
            if name == "food-and-budget":
                profile.update(budget=500, dietary=["素食"] if pace == "relaxed" else ["不吃辣"])
            if name == "park-day":
                profile["must_visit"] = ["宋城"]
            result.append({"id": f"{name}-{pace}", "profile": profile, "selected": selected})
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    places = load_pois()
    rows = []
    for case in cases():
        started = time.monotonic()
        profile = UserProfile.model_validate(case["profile"])
        route = Travel("estimate", profile.transport)
        required = {identifier for identifier in case["selected"] if any(name in places[identifier].name for name in profile.must_visit)}
        orders, dropped, log = schedule(case["selected"], profile, {identifier: 1.0 for identifier in case["selected"]}, route, must=required)
        selected = [Selection(poi_id=identifier, reason="固定选点对照", source="rule") for identifier in case["selected"]]
        try:
            from app.planning_quality import choose_meal_aware_plan
        except ImportError:
            itinerary = build_itinerary(orders, profile, selected, route=route, live=False)
        else:
            orders, itinerary, messages = choose_meal_aware_plan(orders, profile, selected, route=route, live=False)
            log.extend(messages)
        report = validate_itinerary(itinerary, profile, travel_fn=route.leg, restaurants=load_restaurants())
        rows.append({**case, "orders": orders, "dropped": dropped, "report": report.model_dump(mode="json"),
                     "travel_min": sum(day.return_travel_min + sum(item.travel_min for item in day.items) for day in itinerary.days),
                     "visits": sum(item.kind == "poi" for day in itinerary.days for item in day.items),
                     "itinerary": itinerary.model_dump(mode="json"), "log": log,
                     "seconds": round(time.monotonic() - started, 3)})
        print(case["id"], "hard", len(report.hard), "travel", rows[-1]["travel_min"], flush=True)
    assets = Path(__file__).resolve().parents[1] / "app" / "assets"
    manifest = {f"data/hangzhou/{path.name}": hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(CITY_DIR.glob("*.parquet"))}
    manifest.update({f"backend/app/assets/{path.relative_to(assets)}": hashlib.sha256(path.read_bytes()).hexdigest()
                     for path in sorted(assets.rglob("*.json"))})
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({"scope": "fixed selection; schedule + meals; frozen local data; estimated transport; no model/network/repair/night enrichment",
                                      "cases_sha256": hashlib.sha256(json.dumps(cases(), sort_keys=True).encode()).hexdigest(),
                                      "data_sha256": manifest, "results": rows}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
