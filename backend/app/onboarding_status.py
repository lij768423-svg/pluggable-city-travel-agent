"""城市接入进度页的只读接口（2026-10-21 七城接入时用户要“做一个前端 ui”看进度）。

页面是 frontend/public/onboarding.html（构建后在 /onboarding.html）。只有设置了 TTA_ONBOARDING_STATUS_ROOT
（接入所用 worktree 的根目录）才启用，生产不设，返回 404。只读 data/<key>/onboarding.json、模型资产记录、
评测 capture.json 和 work/onboard-seven 下的日志，不触发任何外部调用。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()

# 与 work/onboard-seven/run.sh 的顺序一致
CITIES = [("北京", "beijing"), ("上海", "shanghai"), ("广州", "guangzhou"), ("深圳", "shenzhen"),
          ("成都", "chengdu"), ("重庆", "chongqing"), ("武汉", "wuhan")]
STAGES = [("config", "城市配置"), ("data", "下载数据"), ("assets", "标签与时长"), ("derive", "推导起点"),
          ("calibration", "交通标定"), ("areas", "片区命名"), ("review", "规则体检"), ("check", "完整性检查"), ("eval", "20 画像评测")]
LOG_DIR = "work/onboard-seven"


def _json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _summary(root: Path) -> dict[str, dict]:
    states: dict[str, dict] = {}
    try:
        lines = (root / LOG_DIR / "summary.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return states
    for line in lines:
        match = re.match(r"(\S+) (start|end) (\S+)(?: attempt=(\d+))?(?: exit=(\d+))?", line)
        if not match:
            continue
        at, kind, name, attempt, code = match.groups()
        state = states.setdefault(name, {})
        if kind == "start":
            state.update(started_at=state.get("started_at", at), attempt=int(attempt or 1), running=True)
        else:
            state.update(ended_at=at, exit_code=int(code), running=False)
    return states


def _activity(log: Path) -> dict:
    """日志里最近一行进度：标签批次、时长参考批次或评测画像。"""
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return {}
    stage = next((line[3:].strip() for line in reversed(lines) if line.startswith("== ")), None)
    eval_start = max((index for index, line in enumerate(lines) if line.strip() == "== eval"), default=None)
    cases = [line for line in lines[eval_start:] if re.match(r"^\d{2}-\S+ (ok|failed) ", line)] if eval_start is not None else []
    detail = None
    for line in reversed(lines):
        if match := re.match(r"tags batch=(\d+)/(\d+)", line):
            detail = f"景点标签 第 {match.group(1)}/{match.group(2)} 批"
        elif match := re.match(r"sample=(\d+)/(\d+) batch=(\d+)/(\d+)", line):
            detail = f"建议游玩时长 第 {match.group(1)}/{match.group(2)} 轮 · 第 {match.group(3)}/{match.group(4)} 批"
        elif re.match(r"^\d{2}-\S+ (ok|failed) ", line):
            detail = f"评测录制 {len(cases)}/20 个画像" if len(cases) <= 20 else f"评测回放 {len(cases) - 20}/20 个画像"
        if detail:
            break
    failed = [line.split()[0] for line in cases if " failed " in line]
    error = next((line for line in reversed(lines[-40:]) if re.match(r"^\w+(Error|Exception)\b", line) or "门槛未通过" in line), None)
    return {"stage": stage, "detail": detail, "eval_cases_done": len(cases),
            "eval_cases_failed": failed, "last_error": error}


def _city(root: Path, name: str, key: str, state: dict) -> dict:
    record = _json(root / "data" / key / "onboarding.json") or {"stages": {}}
    done = record.get("stages", {})
    activity = _activity(root / LOG_DIR / f"{name}.log")
    eval_result = done.get("eval") or {}
    assets = _json(root / "data" / key / "model-assets-run.json") or {}
    tag_calls = sum((session.get("tags") or {}).get("usage", {}).get("calls", 0) for session in assets.get("sessions", []))
    stay = _json(root / "data" / key / "stay-reference-v1" / "checkpoint.json") or {}
    stay_calls = sum(record.get("request_attempts", 0) for record in stay.get("records", []))
    capture = _json(Path(eval_result["root"]) / "capture.json") if eval_result.get("root") else None
    eval_rows = (capture or {}).get("results", [])
    amap_eval = sum((row.get("travel_stats") or {}).get("live_calls", 0) or 0 for row in eval_rows)
    model_eval = sum(row.get("model_request_attempts", 0) or 0 for row in eval_rows)
    data = done.get("data") or {}
    status = ("passed" if eval_result.get("gate_passed") else "gate_failed") if eval_result else \
        "running" if state.get("running") else "error" if state.get("exit_code") else "waiting"
    if status == "waiting" and state:  # 跑过但没跑完、也不在运行（例如手动停掉）
        status = "paused"
    return {
        "name": name, "key": key, "status": status, "started_at": state.get("started_at"), "ended_at": state.get("ended_at"),
        "attempt": state.get("attempt"),
        "stages": [{"key": stage, "label": label, "done": stage in done, "finished_at": (done.get(stage) or {}).get("finished_at")} for stage, label in STAGES],
        "current": activity.get("detail") if status == "running" else None,
        "last_error": activity.get("last_error") if status in {"error", "gate_failed"} else None,
        "counts": {"attractions": (data.get("attractions") or {}).get("rows_city"), "restaurants": (data.get("restaurants") or {}).get("rows_city"),
                   "hotels": (data.get("accommodations") or {}).get("rows_city"),
                   "rule_changes": (done.get("review") or {}).get("rule_changes"), "pending": (done.get("review") or {}).get("pending"),
                   "areas": (done.get("areas") or {}).get("areas"), "areas_named": (done.get("areas") or {}).get("named")},
        "eval": {"constraints_passed": eval_result.get("constraints_passed"), "hard_violations": eval_result.get("hard_violations"),
                 "errors": eval_result.get("errors"), "cases_done": activity.get("eval_cases_done"), "cases_failed": activity.get("eval_cases_failed")},
        "usage": {"model": tag_calls + stay_calls + (done.get("areas") or {}).get("model_requests", 0) + model_eval,
                  "amap": ((done.get("config") and 2) or 0) + (done.get("calibration") or {}).get("normal_http_requests", 0) + amap_eval},
        "defaults": {key_: (done.get("derive") or {}).get(key_) for key_ in ("hotel_areas", "evening_streets", "local_cuisine")}
                    | {"origin": ((done.get("derive") or {}).get("default_origin") or {}).get("name")},
    }


@router.get("/api/onboarding-status")
def onboarding_status() -> dict:
    root = os.environ.get("TTA_ONBOARDING_STATUS_ROOT", "").strip()
    if not root:
        raise HTTPException(404, "未启用")
    base = Path(root)
    states = _summary(base)
    cities = [_city(base, name, key, states.get(name, {})) for name, key in CITIES]
    try:
        summary = (base / LOG_DIR / "summary.txt").read_text(encoding="utf-8")
    except OSError:
        summary = ""
    return {"cities": cities, "all_done": "all done" in summary,
            "updated_at": max((p.stat().st_mtime for p in (base / LOG_DIR).glob("*") if p.is_file()), default=None)}
