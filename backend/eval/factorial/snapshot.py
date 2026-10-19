from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess

from .common import GROUPS, ROUTE_MODULES, SnapshotError, block_network, digest, offline_environment, read_json, runtime, write_json


def builtin_cases() -> list[dict]:
    base = {"budget": 1800, "pace": "normal", "start_date": "2026-10-20", "include_evening": False,
            "transport": "taxi", "day_start": 540, "day_end": 1200,
            "hotel": {"name": "龙翔桥附近固定起点", "lat": 30.2608, "lon": 120.1702}}
    definitions = [
        ("r_lakeside", "regression", {"days": 1, "interests": ["自然风光", "人文历史"], "pace": "relaxed"}),
        ("r_songcheng", "regression", {"days": 1, "interests": ["主题乐园"], "must_visit": ["宋城"], "companions": "family_kids"}),
        ("r_afternoon", "regression", {"days": 1, "interests": ["自然风光", "拍照出片"], "day_start": 840, "day_end": 1140}),
        ("r_transit", "regression", {"days": 2, "transport": "transit", "interests": ["自然风光", "人文历史"]}),
        ("h_museums", "holdout", {"days": 2, "interests": ["博物馆展览", "艺术文创"], "companions": "elderly", "pace": "relaxed"}),
        ("h_local_food", "holdout", {"days": 1, "budget": 500, "interests": ["园林古镇", "美食探店"], "food_preference": "local", "dietary": ["不吃辣"]}),
        ("h_short_last", "holdout", {"days": 3, "interests": ["茶文化", "人文历史"], "daily_windows": [{"day": 3, "start": 540, "end": 840}]}),
        ("h_five_days", "holdout", {"days": 5, "budget": 3500, "interests": ["自然风光", "园林古镇", "博物馆展览"], "pace": "relaxed"}),
    ]
    return [{"id": identifier, "split": split, "profile": {**base, **overrides}} for identifier, split, overrides in definitions]


def git_source(root: Path, revision: str, module: str) -> bytes:
    name = "backend/" + module.replace(".", "/") + ".py"
    return subprocess.check_output(["git", "show", f"{revision}:{name}"], cwd=root)


def point_key(lat: float, lon: float) -> str:
    return f"{lat:.6f},{lon:.6f}"


def make_matrix(profile, points: list[tuple[float, float]], source: str, router=None) -> dict:
    from app.travel import Travel
    route = Travel(source, profile.transport, router=router, live_budget=0)
    route.set_day(profile, 1)
    coordinates = sorted({point_key(*point): point for point in points}.items())
    contexts, matrices = [], {}
    for day in range(0, profile.days + 2):
        if 1 <= day <= profile.days:
            route.set_day(profile, day)
        else:
            route.service_date, route.departure_time = None, "09:00"
            if day == 0:
                route.walk_limit_m = 1500
            if route.router is not None:
                route.router.service_date = None
                route.router.departure_time = "09:00"
                route.router.walk_limit_m = route.walk_limit_m
        context = {"date": str(route.service_date) if route.service_date else None,
                   "departure": route.departure_time, "walk_limit_m": route.walk_limit_m}
        matrix_key = str(context["walk_limit_m"]) if source == "estimate" else digest(context)
        if matrix_key not in matrices:
            matrices[matrix_key] = [route.detail(*origin, *destination, live=False).model_dump(mode="json")
                                   for _, origin in coordinates for _, destination in coordinates]
        contexts.append({**context, "matrix": matrix_key})
    return {"source": source, "points": [key for key, _ in coordinates], "contexts": contexts, "matrices": matrices}


@offline_environment()
def create_snapshot(root: Path, output: Path, *, baseline: str, cases: list[dict] | None = None,
                    restaurant_limit: int = 64, use_solver: bool = False,
                    route_source: str = "estimate", route_snapshot: Path | None = None,
                    selections: Path | None = None) -> dict:
    root, output = root.resolve(), output.resolve()
    if output.is_relative_to(root / "backend/app") or output.is_relative_to(root / "backend/eval/factorial"):
        raise SnapshotError("快照输出不能位于被复制的源目录内")
    if output.exists():
        raise SnapshotError("快照目录已存在，不覆盖既有实验")
    if not 1 <= restaurant_limit <= 432 or route_source not in {"estimate", "amap"}:
        raise SnapshotError("餐厅预算或路线来源无效")
    if route_source == "amap" and (route_snapshot is None or not route_snapshot.is_file()):
        raise SnapshotError("真实导航回放必须提供已有路线缓存；不会自动联网补齐")
    os.environ["TTA_OFFLINE"] = "1"
    if route_snapshot:
        os.environ["TTA_ROUTE_SNAPSHOT"] = str(route_snapshot.resolve())
    from app import config
    from app.data import load_pois, load_restaurants
    from app.geo import haversine_km
    from app.nodes import retrieve as current
    from app.nodes.select import complete_experiences, select_rule, target_count
    from app.restaurants import valid_location
    from app.schemas import UserProfile

    revision = subprocess.check_output(["git", "rev-parse", "--verify", f"{baseline}^{{commit}}"], cwd=root, text=True).strip()
    places, restaurants = load_pois(), load_restaurants()
    model_responses = read_json(selections) if selections else None
    temporary = output.with_name(output.name + ".building")
    if temporary.exists():
        raise SnapshotError("存在未完成的快照目录，请选择新的输出位置")
    temporary.mkdir(parents=True)
    for path in (root / "backend/app").rglob("*"):
        if path.is_file() and path.suffix in {".py", ".json", ".md"} and "__pycache__" not in path.parts:
            target = temporary / "code/app" / path.relative_to(root / "backend/app")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
    for path in Path(__file__).parent.glob("*.py"):
        target = temporary / "code/eval/factorial" / path.name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    for module in (*ROUTE_MODULES, "app.nodes.retrieve"):
        target = temporary / "baseline" / (module.replace(".", "/") + ".py")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(git_source(root, revision, module))
    legacy_path = temporary / "baseline/app/nodes/retrieve.py"
    spec = importlib.util.spec_from_file_location("app.nodes._factorial_legacy_retrieve", legacy_path)
    legacy = importlib.util.module_from_spec(spec)
    exec(compile(legacy_path.read_bytes(), str(legacy_path), "exec"), legacy.__dict__)
    write_json(temporary / "places.json", [place.model_dump(mode="json") for place in places.values()])
    write_json(temporary / "restaurants.json", [restaurant.model_dump(mode="json") for restaurant in restaurants.values()])
    prepared = []
    identifiers = set()
    with block_network():
        router = None
        if route_source == "amap":
            from app.amap import AmapRouter
            router = AmapRouter(key="offline-placeholder", cache_path=route_snapshot)
        for case in cases if cases is not None else builtin_cases():
            identifier = case["id"]
            if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", identifier) or identifier in identifiers or case["split"] not in {"regression", "holdout"}:
                raise SnapshotError("案例编号重复、为空或分组不合法")
            identifiers.add(identifier)
            profile = UserProfile.model_validate(case["profile"])
            profile_json = profile.model_dump(mode="json")
            old_pool = legacy.retrieve(profile)
            new_pool, retrieval = current.retrieve_with_report(profile)
            pools = {}
            for name, candidates in (("baseline", old_pool), ("coverage", new_pool)):
                shared = [current.score_poi(places[candidate.poi_id], profile).model_copy(update={
                    "available_days": current.eligibility(places[candidate.poi_id], profile)[0]}) for candidate in candidates]
                if model_responses is None:
                    selected = complete_experiences(select_rule(shared, profile, target_count(profile, len(shared))), shared, profile)
                    response_meta = {"mode": "rule", "model": None, "thinking": None, "raw_response": None}
                else:
                    from app.llm import MockLLM
                    from app.nodes.select import select_llm
                    entry = model_responses[identifier][name]
                    if entry["profile_sha256"] != digest(profile_json) or entry["candidates_sha256"] != digest([candidate.model_dump(mode="json") for candidate in shared]):
                        raise SnapshotError("模型响应的画像或候选输入哈希不匹配")
                    if not entry.get("model") or not entry.get("prompt_sha256") or not entry.get("raw_response"):
                        raise SnapshotError("模型快照缺实际模型、提示词哈希或原始回答")
                    selected = complete_experiences(select_llm(shared, profile, target_count(profile, len(shared)),
                        MockLLM({"select": entry["raw_response"]})), shared, profile)
                    if "selected" in entry and [item["poi_id"] for item in entry["selected"]] != [item.poi_id for item in selected]:
                        raise SnapshotError("冻结选点与原始模型回答的重放结果不一致")
                    response_meta = {**entry, "mode": "frozen_llm"}
                candidate_ids = {candidate.poi_id for candidate in shared}
                if len({item.poi_id for item in selected}) != len(selected) or any(item.poi_id not in candidate_ids for item in selected):
                    raise SnapshotError("冻结选点包含库外、池外或重复地点")
                pools[name] = {"candidates": [candidate.model_dump(mode="json") for candidate in shared],
                               "selected": [item.model_dump(mode="json") for item in selected], "selection_source": response_meta}
            union = {item["poi_id"] for pool in pools.values() for item in pool["candidates"]}
            anchors = [(profile.hotel.lat, profile.hotel.lon), *((places[place_id].lat, places[place_id].lon) for place_id in union)]
            allowed_restaurants = sorted((restaurant for restaurant in restaurants.values() if valid_location(restaurant)),
                key=lambda restaurant: (min(haversine_km(*anchor, restaurant.lat, restaurant.lon) for anchor in anchors), restaurant.id))[:restaurant_limit]
            route_file = f"routes/{identifier}.json.gz"
            matrix = make_matrix(profile, [*anchors, *((restaurant.lat, restaurant.lon) for restaurant in allowed_restaurants)], route_source, router)
            write_json(temporary / route_file, matrix)
            prepared.append({"id": identifier, "split": case["split"], "profile": profile_json, "pools": pools,
                "restaurant_ids": [restaurant.id for restaurant in allowed_restaurants], "route_file": route_file,
                "retrieval": retrieval, "flags": {"select_mode": "llm" if model_responses else "rule", "selection_replay": True,
                    "enable_repair": True, "enable_refine": True, "max_repair_rounds": 3,
                    "use_solver": use_solver, "guide_mode": "off", "online_restaurants": False,
                    "route_source": route_source, "candidate_pool_locked": True, "seed": 20261017}})
    evaluator = temporary / "code/app/nodes/validate.py"
    write_json(temporary / "inputs.json", {"cases": prepared, "evaluator_sha256": hashlib.sha256(evaluator.read_bytes()).hexdigest()})
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copyfile(root / "backend" / name, temporary / name)
    files = {str(path.relative_to(temporary)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(temporary.rglob("*")) if path.is_file()}
    manifest = {"schema": "tta-factorial-v1", "created_at": datetime.now(timezone.utc).isoformat(),
        "baseline_revision": revision, "current_head": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip(),
        "groups": GROUPS, "route_modules": list(ROUTE_MODULES), "runtime": runtime(), "files": files,
        "execution_budget": {"worker_timeout_seconds": 180, "threads": 1, "seed": 20261017},
        "scope": "Engineering replay with frozen profiles, selection outputs and finite restaurant pools; not a production benchmark or human study",
        "source": route_source, "restaurant_limit": restaurant_limit, "model_api_calls": 0,
        "data_attribution": "ChinaTravel CC BY 4.0; project-reviewed metadata retains field provenance",
        "baseline_policy": "Six historical route/meal/repair modules; current shared data, experience semantics, selector and final validator",
        "model_selection": "frozen_llm" if model_responses else "rule_control",
        "holdout_policy": "Configured before first run; once inspected, do not reuse for tuning and a new final claim"}
    write_json(temporary / "manifest.json", manifest)
    temporary.rename(output)
    return manifest
