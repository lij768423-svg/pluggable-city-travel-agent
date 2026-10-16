import argparse
import json
import time
from pathlib import Path

from app.data import load_pois
from app.llm import OpenAICompatLLM
from app.nodes.retrieve import retrieve
from app.nodes.select import select_jev, select_llm, target_count
from app.schemas import UserProfile


def main():
    parser = argparse.ArgumentParser(description="Two cold selection comparisons; makes two Jev and two MiniMax calls.")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    cases = [UserProfile(days=2, companions="solo", interests=["人文历史", "小众安静"], transport="transit"),
             UserProfile(days=2, companions="family_kids", interests=["主题乐园"], pace="intense", budget=1500)]
    results = []
    for profile in cases:
        candidates = retrieve(profile)
        count = target_count(profile, len(candidates))
        jev_picks, jev_stats = select_jev(candidates, profile, count, cache=False)
        model = OpenAICompatLLM(cache=False)
        started = time.perf_counter()
        baseline = select_llm(candidates, profile, count, model)
        baseline_seconds = round(time.perf_counter() - started, 3)
        places = load_pois()
        record = {"profile": profile.model_dump(mode="json"), "candidate_count": len(candidates),
                  "jev": {"stats": jev_stats, "selected": [{"id": item.poi_id, "name": places[item.poi_id].name, "source": item.source} for item in jev_picks]},
                  "baseline": {"seconds": baseline_seconds, "usage": model.usage.as_dict(),
                               "selected": [{"id": item.poi_id, "name": places[item.poi_id].name} for item in baseline]},
                  "scope": "selection_only; no route quality or user satisfaction claim"}
        results.append(record)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"case": len(results), "candidates": len(candidates), "jev_seconds": jev_stats["seconds"],
                          "baseline_seconds": baseline_seconds, "confident": jev_stats["confident_candidates"]}), flush=True)


if __name__ == "__main__":
    main()
