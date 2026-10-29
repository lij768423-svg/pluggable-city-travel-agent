"""E1 公平对比（docs/评测协议v2.md 第 2–7 节）：先采集，再离线回放打分，最后汇总。

  capture  联网采集。每个用例只解析一次画像；每种方法各自录下模型响应（SnapshotLLM）和生成阶段的高德请求
           （RecordedRouter，按顺序）。裁判要用的路段另存为键值快照 judge_amap.jsonl。采集结果只用于核对，不报告分数。
           只改了部分方法时，加 --reuse-from <旧目录> --reuse <方法…>：其余方法的录制从旧目录复制过来、离线重跑，
           并与旧采集核对行程是否仍然一致（capture.json 里的 reused.itinerary_equal）。
  replay   禁止联网，按录下的响应重跑全部方法，用 J1（本系统校验器）和 J2（客观路线指标）打分，并与采集逐字段比对。
  rescore  只改了裁判（JUDGE_CODE 里的文件）时，从录制离线重算分数：生成代码、数据、用例必须与冻结设计一致，
           裁判代码可以不同；结果写入 rescore-<裁判指纹>.json，不改 replay.json。
           新裁判要查采集时没查过的路段时，先加 --judge-online：生成照样按录制离线重跑，只有裁判可以联网，
           把缺的路段补进各用例的 judge_amap.jsonl（结果写入 judgefill-<裁判指纹>.json，不作为分数）；再不加该参数重算一次。
  assess   读 replay.json（或 --source 指定的结果文件）生成 results.md（描述统计；配对检验由 eval/report.py 做）。

路线快照（2026-10-26 起）：裁判查一段路时，先读该方法生成时查到的高德记录（系统排程用的就是这些），再读本用例的
judge_amap.jsonl，都没有才由裁判补查并写进 judge_amap.jsonl。没填出游日期的用例，公交按采集时冻结的参考日
（design.json 的 reference_date，经环境变量 TTA_REFERENCE_DATE 传给系统与裁判）查，回放与重算沿用同一天。

用法（在 backend 目录）：
  uv run python eval/fair_compare.py capture --root eval/runs/e1-dev12-r1 [--methods A Ap R B D] [--only c01 c02]
  uv run python eval/fair_compare.py capture --root eval/runs/<新目录> --methods A Ap R B D --reuse-from eval/runs/<旧目录> --reuse A Ap R
  uv run python eval/fair_compare.py replay  --root eval/runs/e1-dev12-r1
  uv run python eval/fair_compare.py rescore --root eval/runs/e1-dev12-r1 [--judge-online]
  uv run python eval/fair_compare.py assess  --root eval/runs/e1-dev12-r1 [--source rescore-1a2b3c4d]
重复运行用不同的 --root。采集时模型不读本地缓存，所以每次重复都是独立的模型输出。
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import statistics
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent

METHODS = {
    "A": "A 纯大模型（只给画像）",
    "Ap": "A′ 候选感知纯大模型",
    "R": "R ReAct 工具调用",
    "B": "B 本系统（修复开、精修关）",
    "Bplus": "B+ 本系统加精修（线上网页默认）",
    "C": "C 去掉修复回路",
    "D": "D 规则选点",
    "E": "E 估算路程排程",
}
DEFAULT_METHODS = ["A", "Ap", "R", "B", "D"]
PLAN_KWARGS = {
    "B": dict(select_mode="llm", enable_repair=True, enable_refine=False, travel_source="amap"),
    "Bplus": dict(select_mode="llm", enable_repair=True, enable_refine=True, travel_source="amap"),
    "C": dict(select_mode="llm", enable_repair=False, enable_refine=False, travel_source="amap"),
    "D": dict(select_mode="rule", enable_repair=True, enable_refine=False, travel_source="amap"),
    "E": dict(select_mode="llm", enable_repair=True, enable_refine=False, travel_source="estimate"),
}
VOLATILE = {"fetched_at", "fetched_epoch", "created_at", "captured_at"}
# 只负责打分、不参与生成的代码：改动它们不必重新采集，用 rescore 从录制重算即可。
# J1（app/nodes/validate.py）不在其中：它也驱动 B 的修复回路，改了就会改变生成结果。
JUDGE_CODE = ("backend/eval/objective_metrics.py", "backend/eval/fair_compare.py")
GENERATION_FIELDS = ("itinerary", "error")
SCORE_FIELDS = ("j1", "j2", "j2_sys_walk")
# 采集时所有方法共用的模型调用设置。SDK 不再自行重试，只保留应用层最多 3 次，避免一次卡住拖上数小时。
# v3 r1（2026-10-27）中 A、A′ 的非流式请求连续六次等满 1200 秒没有任何返回，同时短请求正常；
# 改为流式后连接上持续有数据，超时也变成"多久没收到新片段"（思考过程同样逐段下发），
# 不再需要为两万多 token 的长回答留出十几分钟，300 秒没有新片段就判定卡住。
E1_LLM_TIMEOUT_S = 300
E1_SDK_MAX_RETRIES = 0
E1_STREAM = True


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()).hexdigest()


def clean(value):
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items() if key not in VOLATILE}
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


def pick(record: dict, fields) -> dict:
    return clean({key: record.get(key) for key in fields})


def load_cases(directory: Path, only: list[str] | None) -> list[dict]:
    cases = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(directory.glob("*.json"))]
    return [case for case in cases if not only or case["id"] in only]


def file_hashes(paths) -> dict[str, str]:
    return {str(path.relative_to(REPO)): hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(paths) if path.is_file()}


def design_of(cases: list[dict], methods: list[str], cases_dir: Path, reuse: dict | None = None) -> dict:
    from app import config
    from app.cities import city_config
    from app.poi_catalog import published_catalog
    cities = sorted({(case.get("form") or {}).get("city") or "hangzhou" for case in cases})
    code = [*(BACKEND / "app").rglob("*.py"), *(BACKEND / "app" / "assets").rglob("*.json"), *(BACKEND / "baselines").rglob("*.py"),
            BACKEND / "eval" / "fair_compare.py", BACKEND / "eval" / "objective_metrics.py"]
    data = {city: {name: hashlib.sha256((city_config(city).data_dir / name).read_bytes()).hexdigest()
                   for name in ("attractions.parquet", "restaurants.parquet", "accommodations.parquet", "attraction_tags.parquet",
                                "popularity.json")  # 知名度档位不进目录摘要，这里单独记录
                   if (city_config(city).data_dir / name).is_file()} for city in cities}
    design = {"protocol": "docs/评测协议v2.md", "experiment": "E1", "methods": {m: METHODS[m] for m in methods},
              "plan_kwargs": {m: PLAN_KWARGS[m] for m in methods if m in PLAN_KWARGS},
              "cases_dir": str(cases_dir.relative_to(REPO)) if cases_dir.is_relative_to(REPO) else str(cases_dir),
              "cases": cases, "cases_sha256": digest(cases), "code_sha256": file_hashes(code), "city_data_sha256": data,
              "poi_catalogs": {city: published_catalog(city).reference() for city in cities},
              "model": config.LLM_MODEL, "thinking": config.LLM_THINKING, "online_restaurants": config.ONLINE_RESTAURANTS,
              "llm_call": {"timeout_seconds": E1_LLM_TIMEOUT_S, "sdk_max_retries": E1_SDK_MAX_RETRIES, "app_retry_attempts": 3,
                           "stream": E1_STREAM},
              "judges": {"J1": "app.nodes.validate.validate_itinerary（快照路程，按画像出行方式）",
                         "J2": "eval.objective_metrics.objective_metrics（AmapRouter.leg_detail 原始结果）"}}
    if reuse:
        design["reuse"] = reuse
    if os.environ.get("TTA_REFERENCE_DATE"):
        design["reference_date"] = os.environ["TTA_REFERENCE_DATE"]  # 旧设计没有这一项，不写空值，免得重算时误判设计改变
    return design


def pinned_reference(root: Path, mode: str) -> str | None:
    """本次运行查公交用的参考日：已有设计沿用设计里的；首次采集用环境变量 TTA_REFERENCE_DATE（显式给定时），
    否则取两天后起的第一个工作日（中国时间），保证一整轮采集期间它都还在未来。"""
    design_path = root / "design.json"
    if design_path.exists():
        return json.loads(design_path.read_text(encoding="utf-8")).get("reference_date")
    if mode != "capture":
        return None
    if os.environ.get("TTA_REFERENCE_DATE"):  # 多次重复采集显式给同一天，保证各次可比
        return os.environ["TTA_REFERENCE_DATE"]
    day = datetime.now(ZoneInfo("Asia/Shanghai")).date() + timedelta(days=2)
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return day.isoformat()


def generation_design(design: dict) -> dict:
    """冻结设计里决定生成结果的部分：去掉裁判代码的指纹和裁判说明。"""
    out = clean(json.loads(json.dumps(design, default=str)))
    out["code_sha256"] = {path: sha for path, sha in out["code_sha256"].items() if path not in JUDGE_CODE}
    out.pop("judges", None)
    return out


def judge_fingerprint(design: dict) -> str:
    return digest({path: design["code_sha256"].get(path) for path in JUDGE_CODE})[:8]


def frozen_design(root: Path, design: dict, mode: str) -> dict:
    """capture 首次运行写入 design.json，之后每次都与它比对：replay 要求完全一致，rescore 只要求生成部分一致。"""
    design_path = root / "design.json"
    current = json.loads(json.dumps(design, default=str))
    if not design_path.exists():
        if mode != "capture":
            raise SystemExit("没有采集记录（design.json），不能回放或重算")
        root.mkdir(parents=True, exist_ok=True)
        design_path.write_text(json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return current
    frozen = json.loads(design_path.read_text(encoding="utf-8"))
    before, after = (generation_design(frozen), generation_design(current)) if mode == "rescore" else (clean(frozen), clean(current))
    if before != after:
        changed = sorted(key for key in set(before) | set(after) if before.get(key) != after.get(key))
        if "code_sha256" in changed:
            files = set(before["code_sha256"]) | set(after["code_sha256"])
            changed += sorted(f"code:{path}" for path in files if before["code_sha256"].get(path) != after["code_sha256"].get(path))
        raise SystemExit(f"{'生成' if mode == 'rescore' else ''}代码、数据、用例或方法与冻结设计不一致：{changed}")
    return frozen


def reuse_root(reuse: dict) -> Path:
    path = Path(reuse["from"])
    return path if path.is_absolute() else REPO / path


def reuse_plan(old_root: Path, reuse_methods: list[str], methods: list[str], cases: list[dict]) -> dict:
    """核对旧目录能否复用：用例、模型与思考设置相同，要复用的方法在旧目录里每个用例都采集过。"""
    from app import config
    if not reuse_methods:
        raise SystemExit("--reuse-from 要同时给出 --reuse 方法")
    if not (old_root / "design.json").exists() or not (old_root / "capture.json").exists():
        raise SystemExit(f"{old_root} 不是完整的采集目录（缺 design.json 或 capture.json）")
    old = json.loads((old_root / "design.json").read_text(encoding="utf-8"))
    missing = [m for m in reuse_methods if m not in old["methods"]]
    if missing:
        raise SystemExit(f"旧目录没有采集过方法 {missing}")
    if set(reuse_methods) - set(methods):
        raise SystemExit("--reuse 的方法必须包含在 --methods 里")
    if old["cases_sha256"] != digest(cases) or (old["model"], old["thinking"]) != (config.LLM_MODEL, config.LLM_THINKING):
        raise SystemExit("旧目录的用例或模型设置与本次不同，不能复用")
    absent = [f"{case['id']}/{name}" for case in cases for name in ("parse", *reuse_methods)
              if not (old_root / case["id"] / name).exists()]  # judge_amap.jsonl 可以没有：裁判路段不够会联网补
    if absent:
        raise SystemExit(f"旧目录缺少录制：{absent[:10]}")
    rel = old_root.relative_to(REPO) if old_root.is_relative_to(REPO) else old_root
    return {"from": str(rel), "from_design_sha256": digest(old), "methods": list(reuse_methods)}


def copy_recordings(source: Path, target: Path, methods: list[str]) -> None:
    """把旧目录里某个用例的画像解析、裁判路段快照和指定方法的录制复制到新目录。"""
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source / "parse", target / "parse", dirs_exist_ok=True)
    if (source / "judge_amap.jsonl").exists():
        shutil.copy2(source / "judge_amap.jsonl", target / "judge_amap.jsonl")
    for method in methods:
        shutil.copytree(source / method, target / method, dirs_exist_ok=True)


def run_config(mode: str, started: str) -> dict:
    from app import config
    git = lambda *args: subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True).stdout.strip()  # noqa: E731
    return {"mode": mode, "started_at": started, "finished_at": datetime.now(timezone.utc).isoformat(),
            "git_commit": git("rev-parse", "HEAD"), "git_dirty_files": [line for line in git("status", "--porcelain").splitlines() if line],
            "python": platform.python_version(), "model": config.LLM_MODEL, "thinking": config.LLM_THINKING,
            "llm_host": config.LLM_BASE_URL.split("//")[-1].split("/")[0] if config.LLM_BASE_URL else None,
            "offline": os.environ.get("TTA_OFFLINE") == "1", "data_dir": str(config.DATA_DIR)}


def stated_taxi_pairs(itinerary, profile, pois, restaurants) -> set[tuple]:
    """行程标明打车的有向路段（坐标与 Travel.pair 一样取 6 位）。起点随已知地点推进，与校验器一致；
    紧跟库外地点的一站不采用（那段路的起点不同）。"""
    pairs: set[tuple] = set()
    pair = lambda a, b: (round(a[0], 6), round(a[1], 6), round(b[0], 6), round(b[1], 6))  # noqa: E731
    home = (profile.hotel.lat, profile.hotel.lon)
    for day in itinerary.days:
        position, after_unknown = home, False
        for item in day.items:
            place = pois.get(item.ref_id) if item.kind == "poi" else restaurants.get(item.ref_id)
            if place is None:
                after_unknown = True
                continue
            if item.travel_mode == "打车" and not after_unknown:
                pairs.add(pair(position, (place.lat, place.lon)))
            position, after_unknown = (place.lat, place.lon), False
        if day.return_leg and day.return_leg.mode == "打车" and not after_unknown and position != home:
            pairs.add(pair(position, home))
    return pairs


def judge_view(judge, router):
    """裁判看到的路线：先是该方法生成时查到的高德记录（系统排程用的同一份），再是本用例裁判快照。
    裁判与测试替身没有缓存时原样返回。"""
    if not hasattr(judge, "cache") or not hasattr(router, "cache"):
        return judge
    view = copy.copy(judge)
    view.cache = {**judge.cache, **router.cache}
    view.records = {**judge.records, **router.records}
    return view


def merge_judge(judge, view, router) -> None:
    """裁判这次补查到的路段并回本用例裁判快照，供后面的方法复用；不把该方法自己的生成记录带给别的方法。"""
    if view is judge:
        return
    for key, value in view.cache.items():
        if key not in judge.cache and key not in router.cache:
            judge.cache[key] = value
            judge.records[key] = view.records.get(key, {})


def route_legs(itinerary) -> dict:
    """定稿行程的路段来源：系统方法每段都有证据；纯大模型等没有逐段证据的方法 total 为 0。"""
    legs = [item.travel_evidence for day in itinerary.days for item in day.items if item.travel_evidence]
    legs += [day.return_leg for day in itinerary.days if day.return_leg]
    return {"total": len(legs), "estimated": sum(leg.source == "estimate" for leg in legs)}


def own_validator(itinerary, profile, judge, restaurants) -> tuple[dict, list]:
    """J1：本系统校验器（与 B 的优化目标同源，只说明违反了哪些系统已知约束）。

    行程标明打车的路段按打车重算（2026-10-26 起）：生成时修复环节把步行过多的短段改成打车，
    校验也按改后的方式；裁判重跑时若不知道这些路段，会把它们算回步行，误报步行超限和出发过早。
    """
    from collections import Counter
    from app.data import load_pois
    from app.nodes.validate import FEASIBILITY, validate_itinerary
    leg = judge.travel_fn(profile.transport)
    route = getattr(leg, "__self__", None)
    if hasattr(route, "taxi_overrides"):
        route.taxi_overrides = stated_taxi_pairs(itinerary, profile, load_pois(), restaurants)
    report = validate_itinerary(itinerary, profile, travel_fn=leg, restaurants=restaurants)
    items = [item for day in itinerary.days for item in day.items]
    hard = [v for v in report.violations if v.severity == "hard"]
    return {"passed": report.passed, "constraint_status": report.constraint_status,
            "feasible": not any(v.code in FEASIBILITY for v in report.violations),
            "feasible_excl_unknown": not any(v.code in FEASIBILITY - {"unknown_place"} for v in report.violations),
            "hard": len(hard), "hard_excl_unknown": sum(v.code != "unknown_place" for v in hard),
            "soft": len(report.violations) - len(hard), "codes": dict(Counter(v.code for v in report.violations)),
            "unknown_rate": sum(item.ref_id is None for item in items) / len(items) if items else 0.0,
            "cost_total": report.totals.get("total", 0)}, [v.model_dump(mode="json") for v in report.violations]


def run_method(method: str, case_dir: Path, profile, candidates, judge, replay: bool, delegate) -> dict:
    from app import amap
    from app.data import load_pois, load_restaurants
    from app.graph import run_plan
    from app.llm import SnapshotLLM
    from eval.city_batch20 import RecordedRouter
    from eval.objective_metrics import objective_metrics, snapshot_leg, system_walk_limit_m
    directory = case_dir / method
    directory.mkdir(parents=True, exist_ok=True)
    router = RecordedRouter(directory / "amap.jsonl", replay)
    previous, amap._router = amap._router, router  # 生成阶段的高德请求全部经过本方法的录制器；结束后恢复
    llm = SnapshotLLM(directory / "llm", delegate=delegate)
    started = time.monotonic()
    row: dict = {"method": method}
    try:
        restaurants = dict(load_restaurants())
        if method == "A":
            from baselines.pure_llm import run_pure_llm
            itinerary = run_pure_llm(profile, llm)
        elif method == "Ap":
            from baselines.candidate_llm import run_candidate_llm
            itinerary = run_candidate_llm(profile, candidates, llm)
        elif method == "R":
            from app.travel import make_travel
            from baselines.react_agent import run_react
            itinerary, row["trace"] = run_react(profile, candidates, make_travel("amap", profile.transport), llm)
        else:
            out = run_plan("", profile.model_dump(mode="json"), llm=llm, **PLAN_KWARGS[method])
            itinerary = out["itinerary"]
            restaurants.update(out.get("restaurants") or {})
            row["plan"] = {"repair_rounds": out.get("repair_round", 0), "dropped": out.get("dropped", []),
                           "selected": [item.poi_id for item in out["selected"]], "travel_stats": out.get("travel_stats", {})}
        row["generation_seconds"] = round(time.monotonic() - started, 2)
        view = judge_view(judge, router)
        # J2、J2′ 先于 J1：J1 给修复改打车的路段算车费时，裁判缓存里已有同一路段、同一出发时刻的公交记录就用其中的高德打车估价，
        # 没有就用标定公式。J1 在前时，采集时这些记录要等随后的 J2 才查到，回放时快照里已全部载入，两边车费不同
        # （dev-12 v3 r1 的 B、D 有 22 处 cost_total 不一致）。J2 对标明乘车的每一段都按同一出发时刻查了公交，先跑它两边就一样。
        row["j2"] = objective_metrics(itinerary, profile, snapshot_leg(view), load_pois(), restaurants)
        # J2′ 敏感性口径：步行上限按系统规则（成人 2.5 km、亲子与老人 1.5 km），其余与 J2 相同
        row["j2_sys_walk"] = objective_metrics(itinerary, profile, snapshot_leg(view, system_walk_limit_m(profile)),
                                               load_pois(), restaurants)
        row["j1"], row["violations"] = own_validator(itinerary, profile, view, restaurants)
        merge_judge(judge, view, router)
        row["itinerary"] = itinerary.model_dump(mode="json")
        row["route_legs"] = route_legs(itinerary)
    except Exception as error:  # noqa: BLE001 —— 方法失败也是结果，如实记录
        row["error"] = f"{type(error).__name__}: {str(error)[:300]}"
    finally:
        amap._router = previous
    if not replay:
        (directory / "amap.jsonl").touch()  # 没有高德请求的方法也留一个空记录，回放才能按"零请求"核对
    row["llm_calls"] = len(llm.records)
    row["tokens"] = sum(record.get("usage", {}).get("total_tokens", 0) for record in llm.records)
    row["amap_generation_requests"] = len(router.entries)
    if replay:
        row["all_http_records_consumed"] = router.cursor[0] == len(router.entries)
    return row


def run(root: Path, mode: str, methods: list[str], cases_dir: Path, only: list[str] | None,
        reuse_from: Path | None = None, reuse_methods: list[str] | None = None, judge_online: bool = False) -> None:
    """采集、回放或重算。模型调用超时只在本次运行内改为 E1_LLM_TIMEOUT_S，结束后恢复。"""
    from app import config
    if judge_online and mode != "rescore":
        raise SystemExit("--judge-online 只用于 rescore")
    started = datetime.now(timezone.utc).isoformat()
    previous_timeout, config.LLM_TIMEOUT = config.LLM_TIMEOUT, E1_LLM_TIMEOUT_S
    previous_reference = os.environ.get("TTA_REFERENCE_DATE")
    reference = pinned_reference(root, mode)
    if reference:
        os.environ["TTA_REFERENCE_DATE"] = reference
    try:
        _run(root, mode, methods, cases_dir, only, started, reuse_from, reuse_methods or [], judge_online)
    finally:
        config.LLM_TIMEOUT = previous_timeout
        if previous_reference is None:
            os.environ.pop("TTA_REFERENCE_DATE", None)
        else:
            os.environ["TTA_REFERENCE_DATE"] = previous_reference


def _run(root: Path, mode: str, methods: list[str], cases_dir: Path, only: list[str] | None, started: str,
         reuse_from: Path | None, reuse_methods: list[str], judge_online: bool = False) -> None:
    from app.amap import AmapRouter
    from app.llm import OpenAICompatLLM, SnapshotLLM
    from app.nodes.parse import parse_profile
    from app.nodes.retrieve import retrieve
    from app.poi_catalog import published_catalog, use_catalog
    from eval.factorial.common import block_network
    offline = mode in ("replay", "rescore")
    design_path = root / "design.json"
    cases = load_cases(cases_dir, None)
    reuse = None
    if offline:
        if not design_path.exists():
            raise SystemExit("没有采集记录（design.json），不能回放或重算")
        previous = json.loads(design_path.read_text(encoding="utf-8"))
        methods, reuse = list(previous["methods"]), previous.get("reuse")  # 回放沿用采集时的方法与复用来源
    elif reuse_from:
        reuse = reuse_plan(reuse_from, reuse_methods, methods, cases)
    design = design_of(cases, methods, cases_dir, reuse)
    frozen = frozen_design(root, design, mode)
    from scripts.freeze_manifest import create_manifest
    (root / f"manifest-{mode}.json").write_text(json.dumps(create_manifest(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    output = root / (f"{'judgefill' if judge_online else 'rescore'}-{judge_fingerprint(design)}.json" if mode == "rescore"
                     else f"{mode}.json")
    rows = json.loads(output.read_text(encoding="utf-8"))["results"] if output.exists() else []
    done = {row["id"] for row in rows}
    captured = {row["id"]: row for row in json.loads((root / "capture.json").read_text(encoding="utf-8"))["results"]} if offline else {}
    reused_rows = ({row["id"]: row for row in json.loads((reuse_root(reuse) / "capture.json").read_text(encoding="utf-8"))["results"]}
                   if reuse and not offline else {})
    reuse_set = set(reuse["methods"]) if reuse and not offline else set()
    reruns_path = root / "reruns.json"
    for case in cases:
        if case["id"] in done or (only and case["id"] not in only):
            continue
        case_dir = root / case["id"]
        if not offline and case_dir.exists():  # 上次采集中途失败：原样挪开并记录，再重新采集
            moved = case_dir.with_name(f"{case['id']}.incomplete-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}")
            case_dir.rename(moved)
            log = json.loads(reruns_path.read_text(encoding="utf-8")) if reruns_path.exists() else []
            log.append({"case": case["id"], "moved_to": moved.name, "reason": "上次采集未完成", "at": datetime.now(timezone.utc).isoformat()})
            reruns_path.write_text(json.dumps(log, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        if reuse_set:
            copy_recordings(reuse_root(reuse) / case["id"], case_dir, sorted(reuse_set))
        delegate = None if offline else OpenAICompatLLM(cache=False, sdk_max_retries=E1_SDK_MAX_RETRIES, stream=E1_STREAM)
        city = (case.get("form") or {}).get("city") or "hangzhou"
        start = time.monotonic()
        row: dict = {"id": case["id"], "query": case["query"], "results": {}}
        judge_file = case_dir / "judge_amap.jsonl"
        judge_lines = len(judge_file.read_text(encoding="utf-8").splitlines()) if judge_file.exists() else 0
        with block_network() if offline and not judge_online else nullcontext(), use_catalog(published_catalog(city)):
            try:
                profile = parse_profile(case["query"], SnapshotLLM(case_dir / "parse" / "llm", delegate=delegate), case.get("form"))
                row["profile"] = profile.model_dump(mode="json")
                candidates = retrieve(profile) if {"Ap", "R"} & set(methods) else []
                row["candidate_ids"] = [candidate.poi_id for candidate in candidates]
                judge = AmapRouter(cache_path=judge_file)
                # 一个用例要跑 15–30 分钟，默认 15 分钟缓存会让后打分的方法重查同一路段、快照里留下两条不同的值，
                # 回放只认最后一条，采集与回放就会差几分钟（dev-12 v2 r1、r2）。同一用例内同一路段只查一次，各方法用同一个值。
                judge.ttl = float("inf")
                if judge_online:  # 只有裁判联网补路段并写回快照；生成仍按录制离线重跑（TTA_OFFLINE 不变）
                    if getattr(judge, "key", None) == "offline-placeholder":
                        raise SystemExit("--judge-online 需要 backend/.env 里的 AMAP_WEB_KEY")
                    judge.offline, judge.persist = False, True
                for method in methods:
                    if method in reuse_set:  # 复用的录制离线重跑；裁判路段不够时仍可联网补
                        result = run_method(method, case_dir, profile, candidates, judge, True, None)
                        before = reused_rows.get(case["id"], {}).get("results", {}).get(method, {})
                        result["reused"] = {"from": reuse["from"],
                                            "itinerary_equal": pick(result, GENERATION_FIELDS) == pick(before, GENERATION_FIELDS)}
                    else:
                        result = run_method(method, case_dir, profile, candidates, judge, offline, delegate)
                    row["results"][method] = result
            except Exception as error:  # noqa: BLE001
                row["error"] = f"{type(error).__name__}: {str(error)[:300]}"
        if offline and case["id"] in captured:
            before = captured[case["id"]]["results"]
            fields = GENERATION_FIELDS + SCORE_FIELDS
            row["capture_equal"] = {method: pick(result, fields) == pick(before.get(method, {}), fields)
                                    for method, result in row["results"].items()}
            if mode == "rescore":  # 生成结果必须与采集一致；分数允许因裁判改动而变化，变了哪些如实记录
                row["generation_equal"] = {method: pick(result, GENERATION_FIELDS) == pick(before.get(method, {}), GENERATION_FIELDS)
                                           for method, result in row["results"].items()}
                row["score_changed"] = {method: [f for f in SCORE_FIELDS if pick(result, (f,)) != pick(before.get(method, {}), (f,))]
                                        for method, result in row["results"].items()}
        if judge_online:
            row["judge_legs_added"] = (len(judge_file.read_text(encoding="utf-8").splitlines()) if judge_file.exists() else 0) - judge_lines
        row["seconds"] = round(time.monotonic() - start, 2)
        rows.append(row)
        scope = {"capture": "live capture; not evaluation scores",
                 "replay": "offline replay with network blocked; evaluation scores",
                 "rescore": "offline rescore with network blocked; judge code differs from design.json, generation code equal"}[mode]
        if judge_online:
            scope = "judge route fill: generation replayed offline, judge fetched missing legs online; not evaluation scores"
        payload = {"scope": scope, "design_sha256": digest(frozen), "results": rows}
        if mode == "rescore":
            payload["judge_code_sha256"] = {path: design["code_sha256"].get(path) for path in JUDGE_CODE}
            payload["frozen_judge_code_sha256"] = {path: frozen["code_sha256"].get(path) for path in JUDGE_CODE}
        output.write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        status = "失败 " + row["error"] if "error" in row else " ".join(
            f"{m}:{'✗' if 'error' in r else r['j2'].get('objective_feasible')}{'' if r.get('reused', {}).get('itinerary_equal', True) else '(复用不一致)'}"
            for m, r in row["results"].items())
        print(f"[{case['id']}] {row['seconds']}s {status}" + (f" 补路段 {row['judge_legs_added']}" if judge_online else ""), flush=True)
    label = "judgefill" if judge_online else mode
    (root / f"config-{label}.json").write_text(json.dumps(run_config(label, started), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"结果写入 {output.name}", flush=True)


FEASIBILITY_VIEWS = (("objective_feasible", "客观可行"), ("objective_feasible_no_onsite", "客观可行·不计园内用餐"),
                     ("constraint_feasible", "约束可行"), ("constraint_feasible_no_onsite", "约束可行·不计园内用餐"))
CONSTRAINT_CHECKS = (("U1_must_missing", "U1 必去缺失"), ("U2_avoid_visited", "U2 去了回避"), ("U3_spice_conflicts", "U3 不吃辣冲突"),
                     ("U4_window_violations", "U4 超出时段"), ("U5_walk_days_over", "U5 步行超限"))


def constraint_section(rows: list[dict], methods: list[str], design: dict, table: dict) -> list[str]:
    """J2 用户约束 U1–U5 与园内用餐两种口径（2026-10-26 起的裁判才有这些字段；旧结果文件不输出本节）。"""
    if not any("constraint_feasible" in (r.get("j2") or {}) for row in rows for r in row["results"].values()):
        return []
    lines = ["", "## 约束口径（J2 补充用户约束 U1–U5；园内用餐计入 / 不计入两种口径）", "",
             "| 方法 | " + " | ".join(f"J2 {label}" for _, label in FEASIBILITY_VIEWS) + " | "
             + " | ".join(f"J2′ {label}" for _, label in FEASIBILITY_VIEWS) + " | " + " | ".join(label for _, label in CONSTRAINT_CHECKS) + " |",
             "|" + "---|" * (1 + 2 * len(FEASIBILITY_VIEWS) + len(CONSTRAINT_CHECKS))]
    for method in methods:
        ok = [row["results"][method] for row in rows if method in row["results"] and "error" not in row["results"][method]]
        views = {}
        cells = []
        for judge in ("j2", "j2_sys_walk"):
            for key, _ in FEASIBILITY_VIEWS:
                judged = [r[judge][key] for r in ok if r[judge].get(key) is not None]
                views[f"{judge}.{key}"] = [sum(bool(v) for v in judged), len(judged)]
                cells.append(f"{views[f'{judge}.{key}'][0]}/{len(judged)}")
        failing = {key: sum((r["j2"].get(key) or 0) > 0 for r in ok) for key, _ in CONSTRAINT_CHECKS}
        cells += [str(failing[key]) for key, _ in CONSTRAINT_CHECKS]
        table[method].update({"views": views, "constraint_failures": failing})
        lines.append(f"| {design['methods'][method]} | " + " | ".join(cells) + " |")
    lines += ["", "U 列为该项未通过的用例数（J2 主口径）。约束可行 = 客观可行且 U1–U5 全部通过。",
              "", "### 未通过的用户约束（J2 主口径）", ""]
    for row in rows:
        for method in methods:
            r = row["results"].get(method, {})
            if "error" not in r and (r.get("j2") or {}).get("constraint_details"):
                lines.append(f"- {row['id']} {design['methods'][method]}：" + "；".join(r["j2"]["constraint_details"]))
    return lines


def assess(root: Path, source: str = "replay") -> None:
    design = json.loads((root / "design.json").read_text(encoding="utf-8"))
    payload = json.loads((root / f"{source}.json").read_text(encoding="utf-8"))
    rows = payload["results"]
    captured = {row["id"]: row for row in json.loads((root / "capture.json").read_text(encoding="utf-8"))["results"]}
    methods = list(design["methods"])
    pct = lambda x: "—" if x is None else f"{x * 100:.0f}%"  # noqa: E731

    def summary(method: str) -> dict:
        results = [row["results"].get(method, {"error": "case failed"}) for row in rows]
        ok = [r for r in results if "error" not in r]
        judged = [r for r in ok if r["j2"]["objective_feasible"] is not None]
        judged_sys = [r for r in ok if r["j2_sys_walk"]["objective_feasible"] is not None]
        mean = lambda f: statistics.mean(f(r) for r in ok) if ok else None  # noqa: E731
        usage = [captured.get(row["id"], {}).get("results", {}).get(method, {}) for row in rows]
        return {"n": len(results), "errors": len(results) - len(ok), "judged": len(judged),
                "objective_feasible": sum(r["j2"]["objective_feasible"] for r in judged) / len(judged) if judged else None,
                "judged_sys": len(judged_sys),
                "objective_feasible_sys": sum(r["j2_sys_walk"]["objective_feasible"] for r in judged_sys) / len(judged_sys) if judged_sys else None,
                "O3_sys": mean(lambda r: r["j2_sys_walk"]["O3_time_conflicts"]),
                "j1_pass": sum(r["j1"]["passed"] for r in ok) / len(results) if results else None,
                "hard_excl": mean(lambda r: r["j1"]["hard_excl_unknown"]), "unknown": mean(lambda r: r["j1"]["unknown_rate"]),
                **{key: mean(lambda r, key=key: r["j2"][key]) for key in (
                    "O1_travel_min_per_day", "O2_max_leg_min", "O3_time_conflicts", "O4_opening_conflicts", "O5_idle_min_per_day",
                    "O6_diameter_km_per_day", "O8_short_visit_rate", "O9_meal_coverage", "O10_budget_deviation", "O11_pois_per_day")},
                "llm_calls": statistics.mean(u.get("llm_calls", 0) for u in usage) if usage else None,
                "tokens": statistics.mean(u.get("tokens", 0) for u in usage) if usage else None}

    table = {method: summary(method) for method in methods}
    num = lambda x, f="{:.1f}": "—" if x is None else f.format(x)  # noqa: E731
    short = lambda sha: (sha or "")[:8]  # noqa: E731
    lines = ["# E1 公平对比（描述统计）", "",
             f"- 协议：`docs/评测协议v2.md`；用例 `{design['cases_dir']}` 共 {len(rows)} 条；模型 {design['model']}（思考 {design['thinking']}）",
             "- 分数来自离线回放（禁止联网），裁判路段来自采集时的高德快照；J1 与 B 的优化目标同源，结论以 J2 为准。",
             "- J2 主口径单段步行上限 1.5 km；J2′ 为敏感性口径，步行上限按系统规则（成人 2.5 km、亲子与老人 1.5 km），两者并列报告。",
             "- 配对检验（McNemar、Wilcoxon、bootstrap 区间）由 `eval/report.py` 计算，本表只做描述。"]
    if source != "replay":
        current, frozen = payload.get("judge_code_sha256", {}), payload.get("frozen_judge_code_sha256", {})
        lines.append(f"- 本表来自离线重算 `{source}.json`：生成代码、数据与用例与冻结设计一致，裁判代码为 "
                     + "、".join(f"`{Path(p).name}` {short(current.get(p))}（采集时 {short(frozen.get(p))}）" for p in JUDGE_CODE) + "。")
    if design.get("reuse"):
        reuse = design["reuse"]
        bad = [f"{row['id']}:{m}" for row in captured.values() for m, r in row.get("results", {}).items()
               if "reused" in r and not r["reused"]["itinerary_equal"]]
        lines.append(f"- 方法 {'、'.join(design['methods'][m] for m in reuse['methods'])} 的录制复用自 `{reuse['from']}`"
                     f"（设计 {short(reuse['from_design_sha256'])}），采集时已离线重跑并与旧采集核对行程：{'不一致 ' + '、'.join(bad) if bad else '全部一致'}。")
    lines += ["",
              "| 方法 | 出错 | J2 客观可行率（可判定数） | J2′ 客观可行率（可判定数） | J2′ O3 时间冲突 | J1 通过率 | J1 硬违例（不计库外） | 库外占比 | O1 日均交通分 | O2 最长单段 | O3 时间冲突 | O4 开放冲突 | O5 日均长空档 | O6 日均直径 km | O8 游玩不足 | O9 正餐覆盖 | O10 预算偏差 | O11 日均景点 | 模型调用 | tokens |",
              "|" + "---|" * 20]
    for method in methods:
        s = table[method]
        lines.append(f"| {design['methods'][method]} | {s['errors']}/{s['n']} | {pct(s['objective_feasible'])}（{s['judged']}） | "
                     f"{pct(s['objective_feasible_sys'])}（{s['judged_sys']}） | {num(s['O3_sys'], '{:.2f}')} | {pct(s['j1_pass'])} | "
                     f"{num(s['hard_excl'], '{:.2f}')} | {pct(s['unknown'])} | {num(s['O1_travel_min_per_day'])} | {num(s['O2_max_leg_min'])} | "
                     f"{num(s['O3_time_conflicts'], '{:.2f}')} | {num(s['O4_opening_conflicts'], '{:.2f}')} | {num(s['O5_idle_min_per_day'])} | "
                     f"{num(s['O6_diameter_km_per_day'], '{:.2f}')} | {pct(s['O8_short_visit_rate'])} | {pct(s['O9_meal_coverage'])} | "
                     f"{num(s['O10_budget_deviation'], '{:+.2f}')} | {num(s['O11_pois_per_day'], '{:.2f}')} | {num(s['llm_calls'])} | {num(s['tokens'], '{:.0f}')} |")
    lines += constraint_section(rows, methods, design, table)
    legs = {method: [row["results"][method]["route_legs"] for row in rows
                     if "route_legs" in row["results"].get(method, {})] for method in methods}
    if any(legs.values()):
        lines += ["", "定稿路段中用了估算的（只有系统方法有逐段证据；按协议应为 0）："
                  + "；".join(f"{design['methods'][m]} {sum(x['estimated'] for x in v)}/{sum(x['total'] for x in v)}"
                             for m, v in legs.items() if v and sum(x["total"] for x in v)) + "。"]
        for method, values in legs.items():
            table[method]["route_legs"] = {"estimated": sum(x["estimated"] for x in values), "total": sum(x["total"] for x in values)}
    if source == "replay":
        mismatched = [f"{row['id']}:{m}" for row in rows for m, same in row.get("capture_equal", {}).items() if not same]
        lines += ["", f"回放与采集不一致：{'、'.join(mismatched) or '无'}。"]
    else:
        mismatched = [f"{row['id']}:{m}" for row in rows for m, same in row.get("generation_equal", {}).items() if not same]
        changed = [f"{row['id']}:{m}({','.join(f)})" for row in rows for m, f in row.get("score_changed", {}).items() if f]
        lines += ["", f"重算的生成结果与采集不一致：{'、'.join(mismatched) or '无'}。",
                  f"分数因裁判改动而变化：{len(changed)} 项{'（' + '、'.join(changed) + '）' if changed else ''}。"]
    lines += ["", "## 逐条（J2 客观可行 / J2′ 客观可行 / J1 硬违例）", "",
              "| 用例 | " + " | ".join(design["methods"][m] for m in methods) + " |", "|---|" + "---|" * len(methods)]
    for row in rows:
        cells = []
        for method in methods:
            r = row["results"].get(method, {"error": row.get("error", "case failed")})
            mark = {True: "✓", False: "✗", None: "?"}
            cells.append("出错" if "error" in r else
                         f"{mark[r['j2']['objective_feasible']]} / {mark[r['j2_sys_walk']['objective_feasible']]} / {r['j1']['hard']}")
        lines.append(f"| {row['id']} | " + " | ".join(cells) + " |")
    stem = "results" if source == "replay" else f"results-{source}"
    (root / f"{stem}.json").write_text(json.dumps(table, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (root / f"{stem}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("mode", choices=("capture", "replay", "rescore", "assess"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--methods", nargs="+", choices=list(METHODS), default=DEFAULT_METHODS)
    parser.add_argument("--cases-dir", type=Path, default=BACKEND / "eval" / "cases")
    parser.add_argument("--only", nargs="*", help="只跑指定用例 id")
    parser.add_argument("--reuse-from", type=Path, help="capture：从这个 run 目录复用 --reuse 所列方法的录制")
    parser.add_argument("--reuse", nargs="+", choices=list(METHODS), default=[], help="capture：要复用录制的方法")
    parser.add_argument("--source", default="replay", help="assess：读哪个结果文件（不含 .json），如 rescore-1a2b3c4d")
    parser.add_argument("--judge-online", action="store_true", help="rescore：只让裁判联网补齐缺的路段（结果不作为分数）")
    args = parser.parse_args()
    if bool(args.reuse) != bool(args.reuse_from) or (args.reuse and args.mode != "capture"):
        parser.error("--reuse-from 与 --reuse 要一起给出，且只用于 capture")
    if args.mode == "assess":
        return assess(args.root, args.source)
    if args.mode in ("replay", "rescore"):
        os.environ["TTA_OFFLINE"] = "1"
        os.environ.pop("TTA_ROUTE_SNAPSHOT", None)  # 裁判只读本次采集的 judge_amap.jsonl
    else:
        os.environ.pop("TTA_OFFLINE", None)
    run(args.root.resolve(), args.mode, args.methods, args.cases_dir.resolve(), args.only,
        args.reuse_from.resolve() if args.reuse_from else None, args.reuse, args.judge_online)


if __name__ == "__main__":
    main()
