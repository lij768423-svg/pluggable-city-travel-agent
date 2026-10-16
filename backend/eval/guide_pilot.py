from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from app import config
from app.data import load_pois
from app.graph import run_plan
from app.guides import corpus_status, retrieve_guides
from app.llm import OpenAICompatLLM, SnapshotLLM
from app.nodes.retrieve import retrieve_node
from app.nodes.select import select_llm, target_count
from app.schemas import UserProfile
from scripts.freeze_manifest import create_manifest


def cases() -> list[dict]:
    themes = [("湖岸", ["自然风光", "拍照出片"], []), ("寺庙", ["宗教寺庙", "人文历史"], ["灵隐寺"]),
              ("工艺", ["博物馆展览", "艺术文创"], []), ("茶", ["茶文化", "小众安静"], []),
              ("街区", ["购物街区", "人文历史"], []), ("遗产", ["人文历史", "园林古镇"], [])]
    variants = [(1, "solo", "normal", 8000), (2, "family_kids", "relaxed", 6000),
                (3, "elderly", "relaxed", 4000), (5, "friends", "normal", 12000)]
    return [{"id": f"GP-{theme_index+1:02}-{variant_index+1}", "label": f"{name}·{days}天·{companions}",
             "profile": UserProfile(days=days, budget=days*500, interests=interests, must_visit=must_visit,
                  companions=companions, pace=pace, max_walking_m=walking, start_date="2026-11-07",
                  day_start=540, day_end=1230, include_evening=True, transport="taxi").model_dump(mode="json")}
            for theme_index, (name, interests, must_visit) in enumerate(themes)
            for variant_index, (days, companions, pace, walking) in enumerate(variants)]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def capture_case(case: dict, mode: str, directory: Path) -> dict:
    profile = UserProfile.model_validate(case["profile"])
    candidates = retrieve_node({"profile": profile})["candidates"]
    evidence = retrieve_guides(candidates, profile) if mode == "pilot" else []
    llm = SnapshotLLM(directory / "responses", OpenAICompatLLM())
    try:
        selected = select_llm(candidates, profile, target_count(profile, len(candidates)), llm, evidence)
        return {"case": case["id"], "mode": mode, "ok": True, "records": llm.records,
                "selected": [selection.model_dump() for selection in selected], "evidence": [entry["id"] for entry in evidence]}
    except Exception as error:
        return {"case": case["id"], "mode": mode, "ok": False, "error_type": type(error).__name__, "records": llm.records}


def evaluate(case: dict, mode: str, directory: Path) -> dict:
    llm = SnapshotLLM(directory / "responses")
    try:
        result = run_plan(form=case["profile"], llm=llm, guide_mode=mode, travel_source="estimate")
        items = [item for day in result["itinerary"].days for item in day.items]
        selected = {selection.poi_id: selection for selection in result["selected"]}
        poi_items = [item for item in items if item.kind == "poi"]
        citations = sum(bool(selected[item.ref_id].guide_ids) for item in poi_items if item.ref_id in selected and not item.evening)
        idle_minutes = sum(max(0, current.start - previous.end - current.travel_min)
                           for day in result["itinerary"].days for previous, current in zip(day.items, day.items[1:]))
        meals = [item.ref_id for item in items if item.kind == "meal"]
        return {"case": case["id"], "mode": mode, "ok": True, "passed": result["report"].passed,
                "hard": len(result["report"].hard), "experience": result["report"].experience_status,
                "violations": [violation.model_dump() for violation in result["report"].violations],
                "unknowns": result["report"].unknowns, "totals": result["report"].totals,
                "poi_count": len(poi_items), "meal_count": sum(item.kind == "meal" for item in items),
                "travel_minutes": sum(item.travel_min for item in items) + sum(day.return_travel_min for day in result["itinerary"].days),
                "idle_minutes": idle_minutes, "repeated_restaurants": len(meals) - len(set(meals)),
                "cited_pois": citations, "evidence_count": len(result.get("guide_evidence", [])),
                "seconds": result["seconds"], "usage": result["usage"],
                "itinerary": result["itinerary"].model_dump(mode="json"),
                "selection": [selection.model_dump() for selection in result["selected"]]}
    except Exception as error:
        return {"case": case["id"], "mode": mode, "ok": False, "passed": False,
                "error_type": type(error).__name__, "error": str(error)[:300]}


def summarize(results: list[dict]) -> dict:
    groups = {}
    for mode in ("off", "pilot"):
        rows = [row for row in results if row["mode"] == mode]
        valid = [row for row in rows if row["ok"]]
        groups[mode] = {"attempted": len(rows), "completed": len(valid), "passed": sum(row["passed"] for row in rows),
                       "hard_violations": sum(row.get("hard", 0) for row in rows),
                       "experience_complete": sum(row.get("experience") == "complete" for row in rows),
                       **{f"mean_{field}": round(statistics.mean(row[field] for row in valid), 2) if valid else None
                          for field in ("poi_count", "meal_count", "travel_minutes", "idle_minutes", "repeated_restaurants", "cited_pois", "seconds")}}
    pairs = []
    for identifier in sorted({row["case"] for row in results}):
        matched = {row["mode"]: row for row in results if row["case"] == identifier}
        baseline, pilot = matched["off"], matched["pilot"]
        pairs.append({"case": identifier, "new_failure": baseline["passed"] and not pilot["passed"],
                      "travel_delta": pilot["travel_minutes"] - baseline["travel_minutes"] if baseline["ok"] and pilot["ok"] else None,
                      "new_hard_codes": sorted({violation["code"] for violation in pilot.get("violations",[]) if violation["severity"] == "hard"} - {violation["code"] for violation in baseline.get("violations",[]) if violation["severity"] == "hard"}),
                      "hard_delta": pilot.get("hard",0) - baseline.get("hard",0),
                      "selection_changed": {selection["poi_id"] for selection in baseline.get("selection",[])} != {selection["poi_id"] for selection in pilot.get("selection",[])}})
    return {"conditions": "24 development diagnostic cases; frozen LLM outputs; estimated travel; historical restaurant parquet; no live API during scoring",
            "groups": groups, "pairs": pairs, "new_failure_cases": [pair["case"] for pair in pairs if pair["new_failure"]],
            "new_hard_cases": [pair["case"] for pair in pairs if pair["new_hard_codes"] or pair["hard_delta"] > 0],
            "human_responses": 0, "promotion": "not_approved_pending_human_and_real_route_validation",
            "limitations": ["单次冻结输出，不是重复随机试验", "未使用独立封存测试集", "估算交通与历史餐饮不代表实时实际路线", "未获得真实用户盲评结果"]}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--capture", action="store_true")
    args = parser.parse_args()
    directory = args.directory.resolve()
    config.ONLINE_RESTAURANTS = False
    if args.capture:
        directory.mkdir(parents=True, exist_ok=False)
        cohort = cases()
        (directory / "cases.json").write_text(json.dumps(cohort, ensure_ascii=False, indent=2))
        manifest = create_manifest()
        manifest.update({"corpus": corpus_status(), "cases_sha256": digest(directory / "cases.json"),
                         "model": config.LLM_MODEL, "thinking": config.LLM_THINKING, "temperature": 0.2,
                         "expected_selection_requests": 48, "travel": "estimate", "restaurants": "historical_parquet",
                         "llm_generation_seed": None, "split": "development_diagnostic", "human_responses": 0})
        (directory / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
        results = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(capture_case, case, mode, directory) for case in cohort for mode in ("off", "pilot")]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                (directory / "capture.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
                print(result["case"], result["mode"], result["ok"], flush=True)
        (directory / "response_hashes.json").write_text(json.dumps({path.name: digest(path) for path in sorted((directory / "responses").glob("*.json"))}, indent=2))
    else:
        os.environ["TTA_OFFLINE"] = "1"
        manifest = json.loads((directory / "manifest.json").read_text())
        if manifest["working_tree_hash"] != create_manifest()["working_tree_hash"]:
            raise SystemExit("代码或数据已改变，必须重新冻结实验；拒绝混用版本")
        if manifest["model"] != config.LLM_MODEL or manifest["thinking"] != config.LLM_THINKING:
            raise SystemExit("模型配置与冻结版本不一致")
        if digest(directory / "cases.json") != manifest["cases_sha256"]:
            raise SystemExit("用例集已改变")
        for name, expected in json.loads((directory / "response_hashes.json").read_text()).items():
            if digest(directory / "responses" / name) != expected:
                raise SystemExit("响应快照已改变")
        cohort = json.loads((directory / "cases.json").read_text())
        results = []
        for case in cohort:
            for mode in ("off", "pilot"):
                result = evaluate(case, mode, directory)
                results.append(result)
                print(case["id"], mode, result["passed"], flush=True)
        (directory / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=2))
        (directory / "summary.json").write_text(json.dumps(summarize(results), ensure_ascii=False, indent=2))
        print(json.dumps(summarize(results)["groups"], ensure_ascii=False))


if __name__ == "__main__":
    main()
