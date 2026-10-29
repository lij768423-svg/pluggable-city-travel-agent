from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.abc
import importlib.util
import os
from pathlib import Path
import random
import sys
import time

from .common import GROUPS, ROUTE_MODULES, SnapshotError, block_network, read_json, selection_contract, verify_snapshot, write_json


class BaselineFinder(importlib.abc.MetaPathFinder):
    def __init__(self, root: Path):
        self.root = root

    def find_spec(self, fullname: str, path=None, target=None):
        if fullname in ROUTE_MODULES:
            return importlib.util.spec_from_file_location(fullname, self.root / (fullname.replace(".", "/") + ".py"))
        return None


def run_case(root: Path, group: str, identifier: str) -> dict:
    if "app.graph" in sys.modules or "app.nodes.schedule" in sys.modules:
        raise SnapshotError("评测必须在全新子进程执行，禁止复用已导入的规划模块")
    manifest = verify_snapshot(root)
    inputs = read_json(root / "inputs.json")
    case = next(case for case in inputs["cases"] if case["id"] == identifier)
    record = {"case_id": identifier, "group": group, "split": case["split"], "flags": case["flags"],
              "snapshot_sha256": hashlib.sha256((root / "manifest.json").read_bytes()).hexdigest(),
              "input_contract": selection_contract(case, GROUPS[group]["coverage"]),
              "evaluator_sha256": inputs["evaluator_sha256"], "llm_live_calls": 0, "network_attempts": 0}
    started = time.perf_counter()
    for name in ("LLM_API_KEY", "AMAP_WEB_KEY", "AMAP_JS_KEY", "AMAP_JS_SECURITY", "TYPESAFE_API_KEY"):
        os.environ[name] = ""
    os.environ["TTA_OFFLINE"] = "1"
    os.environ["TTA_ONLINE_RESTAURANTS"] = "0"
    os.environ["TTA_DATA_DIR"] = str(root / "unused-data")
    sys.path.insert(0, str(root / "code"))
    if not GROUPS[group]["route"]:
        sys.meta_path.insert(0, BaselineFinder(root / "baseline"))
    random.seed(case["flags"]["seed"])
    with block_network() as attempts:
        try:
            import numpy
            numpy.random.seed(case["flags"]["seed"])
            from app import config, data
            from app.schemas import POI, Restaurant, Candidate, Selection, UserProfile, LegEvidence
            from app.travel import Travel
            all_places = {place["id"]: POI.model_validate(place) for place in read_json(root / "places.json")}
            all_restaurants = {place["id"]: Restaurant.model_validate(place) for place in read_json(root / "restaurants.json")}
            pool = case["pools"]["coverage" if GROUPS[group]["coverage"] else "baseline"]
            allowed_ids = {item["poi_id"] for item in pool["candidates"]}
            places = {place_id: all_places[place_id] for place_id in allowed_ids}
            restaurants = {place_id: all_restaurants[place_id] for place_id in case["restaurant_ids"]}
            data.load_pois = lambda: places
            # 任务目录（app/poi_catalog.py）从原库加载函数构建；冻结池同样替换它，旧代码无此函数
            if hasattr(data, "load_base_pois"):
                data.load_base_pois = lambda: places
            data.load_restaurants = lambda: restaurants
            config.AMAP_WEB_KEY, config.ONLINE_RESTAURANTS = "", False
            matrix = read_json(root / case["route_file"])
            points = {key: index for index, key in enumerate(matrix["points"])}
            size = len(points)
            if not size or any(len(legs) != size * size for legs in matrix["matrices"].values()):
                raise SnapshotError("路线矩阵维度不完整")

            class FrozenTravel(Travel):
                def __init__(self, source: str, scenario: str):
                    super().__init__("estimate", scenario, live_budget=0)
                    self.source = source

                def matrix(self, locations: list[tuple[float, float]]) -> list[list[int]]:
                    return [[self.leg(*before, *after, live=False)[0] for after in locations] for before in locations]

                def _resolve(self, lat1: float, lon1: float, lat2: float, lon2: float, live: bool):
                    # 估算路程与日期、出发时刻无关，只按步行条件对应；真实路线快照仍要求日期与出发时段一致
                    # （2026-10-26 起系统按每段出发时刻、参考日查路线，旧的真实路线快照需要重建）
                    timeless = matrix["source"] == "estimate"
                    context = next((context for context in matrix["contexts"] if context["walk_limit_m"] == self.walk_limit_m and (timeless or (
                        context["date"] == (str(self.service_date) if self.service_date else None) and context["departure"] == self.departure_time))), None)
                    if context is None:
                        record["route_snapshot_misses"] = record.get("route_snapshot_misses", 0) + 1
                        raise SnapshotError("路线快照缺少日期、出发时段或步行条件；禁止估算回退")
                    try:
                        before = points[f"{lat1:.6f},{lon1:.6f}"]
                        after = points[f"{lat2:.6f},{lon2:.6f}"]
                        detail = LegEvidence.model_validate(matrix["matrices"][context["matrix"]][before * size + after])
                    except (KeyError, IndexError):
                        record["route_snapshot_misses"] = record.get("route_snapshot_misses", 0) + 1
                        raise SnapshotError("路线快照缺少路段；禁止联网或估算回退") from None
                    return detail.minutes, detail.mode, detail.cost, detail.source, detail.walking_m, detail.fallback_reason, detail.available, detail.fetched_at

            from app import graph
            from app.nodes import retrieve, validate
            from app.restaurants import RestaurantCatalog
            validate.load_pois = lambda: all_places
            record["evaluator_sha256"] = hashlib.sha256(Path(validate.__file__).read_bytes()).hexdigest()
            if record["evaluator_sha256"] != inputs["evaluator_sha256"] or graph.MAX_ROUNDS != case["flags"]["max_repair_rounds"]:
                raise SnapshotError("最终评价器或修复预算与冻结配置不一致")
            candidates = [Candidate.model_validate(item) for item in pool["candidates"]]
            selected = [Selection.model_validate(item) for item in pool["selected"]]
            retrieve.retrieve = lambda profile, k=30: deepcopy(candidates[:k])

            def pinned_retrieve(state: dict) -> dict:
                route = FrozenTravel(matrix["source"], state["profile"].transport)
                return {"candidates": deepcopy(candidates), "travel": route,
                        "catalog": RestaurantCatalog(route, initial=deepcopy(restaurants), online=False),
                        "retrieval_report": {"strategy": "factorial_frozen", "candidate_ids": sorted(allowed_ids)},
                        "log": ["冻结候选、餐厅与路线；禁止补入池外地点"]}

            def pinned_select(state: dict) -> dict:
                return {"selected": deepcopy(selected), "initial_selected_ids": [item.poi_id for item in selected],
                        "log": ["重放已冻结选点，不重新调用模型"]}

            graph.retrieve_node, graph.select_node, graph._GRAPH = pinned_retrieve, pinned_select, None
            from app.llm import MockLLM
            result = graph.run_plan("", case["profile"], llm=MockLLM({}), select_mode=case["flags"]["select_mode"],
                enable_repair=case["flags"]["enable_repair"], enable_refine=case["flags"]["enable_refine"],
                use_solver=case["flags"]["use_solver"], travel_source=matrix["source"])
            visited = {item.ref_id for day in result["itinerary"].days for item in day.items if item.kind == "poi"}
            observed = visited | {item.poi_id for item in result["selected"]} | {item.poi_id for item in result["candidates"]}
            if not observed <= allowed_ids or not set(result["restaurants"]) <= set(restaurants):
                raise SnapshotError("补点、夜游或餐厅候选逃逸冻结池")
            if record.get("route_snapshot_misses", 0):
                raise SnapshotError("运行中曾缺少路线快照，禁止将捕获异常后的结果视为完整回放")
            record["llm_live_calls"] = result["usage"]["calls"]
            from app.meal_edits import freeze_preview_locations, validate_preview, meal_metrics
            freeze_preview_locations(result)
            result["report"] = validate_preview(result)
            from .metrics import itinerary_metrics
            record.update(status="ok", report=result["report"].model_dump(mode="json"),
                metrics=itinerary_metrics(result, all_places), meals=meal_metrics(result),
                itinerary=result["itinerary"].model_dump(mode="json"), log=result["log"],
                actual_candidates=sorted(item.poi_id for item in result["candidates"]),
                selected_ids=[item.poi_id for item in result["selected"]], flags=case["flags"],
                model_response=pool["selection_source"], repair_rounds=result.get("repair_round", 0))
        except Exception as error:
            record.update(status="error", error_type=type(error).__name__, error=str(error)[:400])
        record["network_attempts"] = attempts["blocked"]
    record["replay_seconds"] = round(time.perf_counter() - started, 4)
    record["route_policy"] = {"source": matrix["source"]} if "matrix" in locals() else {"source": manifest["source"]}
    return record


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("group", choices=GROUPS)
    parser.add_argument("case")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    write_json(args.output, run_case(args.snapshot.resolve(), args.group, args.case))


if __name__ == "__main__":
    main()
