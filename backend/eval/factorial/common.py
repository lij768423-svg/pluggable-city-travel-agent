from __future__ import annotations

from contextlib import contextmanager
import hashlib
import gzip
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import socket


GROUPS = {
    "baseline": {"coverage": False, "route": False},
    "coverage_only": {"coverage": True, "route": False},
    "route_only": {"coverage": False, "route": True},
    "full": {"coverage": True, "route": True},
}
ROUTE_MODULES = (
    "app.nodes.schedule", "app.nodes.meals", "app.nodes.repair",
    "app.planning_quality", "app.meal_ranking", "app.evening",
)
DEPENDENCIES = ("pydantic", "ortools", "langgraph", "pandas", "scikit-learn", "numpy")


class SnapshotError(ValueError):
    pass


class NetworkForbidden(RuntimeError):
    pass


@contextmanager
def offline_environment():
    names = ("TTA_OFFLINE", "TTA_ROUTE_SNAPSHOT", "TTA_ONLINE_RESTAURANTS")
    previous = {name: os.environ.get(name) for name in names}
    os.environ["TTA_OFFLINE"] = "1"
    os.environ["TTA_ONLINE_RESTAURANTS"] = "0"
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def read_json(path: Path) -> dict | list:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as stream:
            return json.load(stream)
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix == ".gz":
        with path.open("xb") as stream:
            stream.write(gzip.compress(canonical(value), mtime=0))
        return
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")


def runtime() -> dict:
    return {"python": platform.python_version(), "dependencies": {
        name: importlib.metadata.version(name) for name in DEPENDENCIES}}


def verify_snapshot(root: Path, check_runtime: bool = True) -> dict:
    root = root.resolve()
    manifest = read_json(root / "manifest.json")
    if manifest.get("schema") != "tta-factorial-v1" or manifest.get("groups") != GROUPS:
        raise SnapshotError("不支持的快照或四组定义")
    if not manifest.get("files"):
        raise SnapshotError("快照缺少文件摘要")
    for name, expected in manifest["files"].items():
        path = root / name
        if not path.resolve().is_relative_to(root) or path.is_symlink() or not path.is_file():
            raise SnapshotError(f"快照路径非法或缺失：{name}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise SnapshotError(f"快照摘要不一致：{name}")
    actual_files = {str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()}
    if actual_files != set(manifest["files"]) | {"manifest.json"}:
        raise SnapshotError("快照出现未登记文件，拒绝额外代码、缓存或数据")
    if check_runtime and manifest["runtime"] != runtime():
        raise SnapshotError("Python 或依赖版本与冻结环境不同")
    inputs = read_json(root / "inputs.json")
    identifiers = [case["id"] for case in inputs["cases"]]
    if not identifiers or len(set(identifiers)) != len(identifiers):
        raise SnapshotError("案例为空或编号重复")
    return manifest


@contextmanager
def block_network():
    original = socket.socket.connect, socket.socket.connect_ex, socket.create_connection
    attempts = {"blocked": 0}
    def refuse(*args, **kwargs):
        attempts["blocked"] += 1
        raise NetworkForbidden("离线评测禁止联网")
    socket.socket.connect = refuse
    socket.socket.connect_ex = refuse
    socket.create_connection = refuse
    try:
        yield attempts
    finally:
        socket.socket.connect, socket.socket.connect_ex, socket.create_connection = original


def selection_contract(case: dict, coverage: bool) -> dict:
    pool = case["pools"]["coverage" if coverage else "baseline"]
    return {"profile": digest(case["profile"]), "candidates": digest(pool["candidates"]),
            "selection": digest(pool["selected"]), "restaurants": digest(case["restaurant_ids"]),
            "routes": case["route_file"], "flags": digest(case["flags"])}


def audit_rows(inputs: dict, rows: list[dict], expected_snapshot: str | None = None) -> None:
    expected = {(case["id"], group) for case in inputs["cases"] for group in GROUPS}
    actual = [(row["case_id"], row["group"]) for row in rows]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise SnapshotError("结果缺组、缺案例或重复；拒绝比较")
    cases = {case["id"]: case for case in inputs["cases"]}
    snapshots = {row.get("snapshot_sha256") for row in rows}
    if None in snapshots or len(snapshots) != 1 or expected_snapshot is not None and snapshots != {expected_snapshot}:
        raise SnapshotError("结果未绑定同一个不可变快照")
    for row in rows:
        expected_contract = selection_contract(cases[row["case_id"]], GROUPS[row["group"]]["coverage"])
        if row.get("input_contract") != expected_contract:
            raise SnapshotError("同池候选、分数、选点、餐厅或路线契约不一致")
        if row.get("flags") != cases[row["case_id"]]["flags"] or row.get("route_snapshot_misses", 0):
            raise SnapshotError("运行开关改变或路线快照不完整")
        if row.get("network_attempts", 0) or row.get("llm_live_calls", 0):
            raise SnapshotError("结果包含网络尝试或真实模型调用")
        if row.get("evaluator_sha256") != inputs["evaluator_sha256"]:
            raise SnapshotError("四组使用了不同最终评价器")
