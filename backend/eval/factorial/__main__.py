from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import subprocess
import sys

from .common import GROUPS, SnapshotError, audit_rows, read_json, selection_contract, verify_snapshot, write_json


def run(snapshot: Path, output: Path, timeout: int | None = None) -> dict:
    snapshot = snapshot.resolve()
    manifest = verify_snapshot(snapshot)
    frozen_timeout = manifest.get("execution_budget", {}).get("worker_timeout_seconds")
    if frozen_timeout is None or timeout is not None and timeout != frozen_timeout:
        raise SnapshotError("运行预算缺失或与冻结配置不同，请使用相同预算或建立新快照")
    timeout = frozen_timeout
    inputs = read_json(snapshot / "inputs.json")
    snapshot_digest = hashlib.sha256((snapshot / "manifest.json").read_bytes()).hexdigest()
    if output.resolve().is_relative_to(snapshot):
        raise SnapshotError("结果不能写入不可变输入快照")
    if output.exists():
        raise SnapshotError("结果目录已存在，不覆盖既有结果")
    output.mkdir(parents=True)
    output = output.resolve()
    rows = []
    environment = dict(os.environ, PYTHONPATH=str(snapshot / "code"), PYTHONNOUSERSITE="1", PYTHONDONTWRITEBYTECODE="1",
        OMP_NUM_THREADS="1", OPENBLAS_NUM_THREADS="1", MKL_NUM_THREADS="1", PYTHONHASHSEED="20261017",
        TTA_OFFLINE="1", TTA_ONLINE_RESTAURANTS="0")
    for name in ("LLM_API_KEY", "AMAP_WEB_KEY", "AMAP_JS_KEY", "AMAP_JS_SECURITY", "TYPESAFE_API_KEY"):
        environment[name] = ""
    for case in inputs["cases"]:
        for group in GROUPS:
            target = output / f"{case['id']}--{group}.json"
            try:
                process = subprocess.run([sys.executable, "-m", "eval.factorial.worker", str(snapshot), group,
                    case["id"], str(target)], cwd=snapshot / "code", env=environment, capture_output=True, text=True, timeout=timeout)
                if process.returncode != 0 or not target.is_file():
                    raise RuntimeError(f"isolated_worker_exit_{process.returncode}")
                row = read_json(target)
            except (subprocess.TimeoutExpired, RuntimeError) as error:
                row = {"case_id": case["id"], "group": group, "split": case["split"], "flags": case["flags"],
                    "snapshot_sha256": snapshot_digest,
                    "input_contract": selection_contract(case, GROUPS[group]["coverage"]),
                    "evaluator_sha256": inputs["evaluator_sha256"], "llm_live_calls": 0, "network_attempts": 0,
                    "status": "error", "error_type": type(error).__name__, "error": str(error), "replay_seconds": None}
                if target.exists():
                    raise SnapshotError("子进程异常且已经写出结果，需检查后使用新结果目录") from error
                write_json(target, row)
            rows.append(row)
            print(f"{case['id']} {group}: {row['status']} {row.get('error_type', '')}", flush=True)
    verify_snapshot(snapshot)
    audit_rows(inputs, rows, snapshot_digest)
    from .metrics import markdown, summarize
    from .study import export_study
    summaries = {split: summarize(inputs, rows, None if split == "all" else split) for split in ("all", "regression", "holdout")}
    result = {"schema": "tta-factorial-results-v1", "created_at": datetime.now(timezone.utc).isoformat(),
              "snapshot": str(snapshot), "snapshot_sha256": snapshot_digest, "execution_budget": manifest["execution_budget"],
              "summaries": summaries, "results": rows}
    write_json(output / "results.json", result)
    (output / "report.md").write_text(markdown(summaries["all"], manifest), encoding="utf-8")
    export_study(inputs, rows, output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="严格四组冻结评测；不会调用真实模型或联网补齐")
    commands = parser.add_subparsers(dest="command", required=True)
    freeze = commands.add_parser("freeze")
    freeze.add_argument("--output", type=Path, required=True)
    freeze.add_argument("--baseline", required=True, help="本地可验证的历史提交，不隐式猜测基线")
    freeze.add_argument("--cases", type=Path)
    freeze.add_argument("--restaurant-limit", type=int, default=64)
    freeze.add_argument("--use-solver", action="store_true")
    freeze.add_argument("--routes", choices=("estimate", "amap"), default="estimate")
    freeze.add_argument("--route-snapshot", type=Path)
    freeze.add_argument("--selections", type=Path)
    execute = commands.add_parser("run")
    execute.add_argument("snapshot", type=Path)
    execute.add_argument("--output", type=Path, required=True)
    execute.add_argument("--timeout", type=int, help="仅允许与冻结预算相同，不可临时扩大单组预算")
    inspect = commands.add_parser("verify")
    inspect.add_argument("snapshot", type=Path)
    replay = commands.add_parser("compare")
    replay.add_argument("first", type=Path)
    replay.add_argument("second", type=Path)
    replay.add_argument("--output", type=Path, required=True)
    study = commands.add_parser("study-import")
    study.add_argument("directory", type=Path)
    study.add_argument("responses", nargs="+", type=Path)
    study.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "freeze":
            from .snapshot import create_snapshot
            root = Path(__file__).resolve().parents[3]
            create_snapshot(root, args.output.resolve(), baseline=args.baseline,
                cases=read_json(args.cases) if args.cases else None, restaurant_limit=args.restaurant_limit,
                use_solver=args.use_solver, route_source=args.routes, route_snapshot=args.route_snapshot, selections=args.selections)
            print(args.output)
        elif args.command == "run":
            run(args.snapshot, args.output, args.timeout)
        elif args.command == "verify":
            manifest = verify_snapshot(args.snapshot)
            print(f"verified {len(manifest['files'])} files")
        elif args.command == "compare":
            from .compare import compare
            result = compare(args.first, args.second)
            write_json(args.output, result)
            if result["differences"]:
                parser.exit(1, "回放存在语义差异，已记录，不视为可重复通过\n")
            print(f"semantic replay: {result['semantic_matches']}/{result['rows']}")
        else:
            from .study import import_responses
            write_json(args.output, import_responses(read_json(args.directory / "blind-public.json"),
                read_json(args.directory / "blind-key-private.json"), args.responses))
    except (SnapshotError, FileNotFoundError, KeyError, ValueError, RuntimeError) as error:
        parser.exit(2, f"拒绝执行：{error}\n")


if __name__ == "__main__":
    main()
