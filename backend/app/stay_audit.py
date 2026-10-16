"""停留时长人工核对页的接口：读取 top50.csv 与参考数据，保存用户的选择。

页面是 frontend/public/stay-audit.html（构建后在 /stay-audit.html）。
每次选择都记进 picks.json（完整记录，没有来源的条目也记）；只有满足
load_stay_references 要求（来源 URL、原文、核对人、日期齐全）的结论才写进
top50.csv 的"人工结论"，否则该列写"待核"，评分时仍用模型估计。
"""
from __future__ import annotations

import csv
import io
import json
import os
import re
import tempfile
import threading
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from .accounts import same_origin
from .data import load_pois
from .reasonableness import STAY_AUDIT_PATH, STAY_REFERENCE_PATH, load_stay_references

AUDIT_PATH = STAY_AUDIT_PATH
REFERENCE_PATH = STAY_REFERENCE_PATH
SOURCES_PATH = STAY_AUDIT_PATH.parent / "sources.json"
PICKS_PATH = STAY_AUDIT_PATH.parent / "picks.json"
DECISION_PATH = STAY_AUDIT_PATH.parent / "decision.json"
FIELDS = ["poi_id", "name", "数据集区间", "LLM区间", "来源 URL", "原文摘录", "人工结论", "核对人", "核对日期"]
POLICIES = ("eval_only", "manual_only", "manual_plus_llm", "all_llm")
CONCLUSIONS = {"llm": "采用LLM", "dataset": "采用数据集", "skip": "不确定"}

router = APIRouter()
_lock = threading.Lock()


class Pick(BaseModel):
    choice: Literal["llm", "dataset", "ctrip", "custom", "skip"]
    low: int | None = Field(None, gt=0, le=1440)
    high: int | None = Field(None, gt=0, le=1440)
    url: str = Field("", max_length=500)
    excerpt: str = Field("", max_length=300)
    reviewer: str = Field("", max_length=40)

    @field_validator("url")
    @classmethod
    def public_url(cls, value: str) -> str:
        value = value.strip()
        if value and not value.startswith(("https://", "http://")):
            raise ValueError("来源链接需以 http:// 或 https:// 开头")
        return value


class Settings(BaseModel):
    policy: Literal["eval_only", "manual_only", "manual_plus_llm", "all_llm"] | None = None
    reviewer: str | None = Field(None, max_length=40)


def _read_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(handle, "w", encoding="utf-8", newline="") as stream:
        stream.write(text)
    os.replace(temporary, path)


def _read_rows() -> list[dict]:
    try:
        with AUDIT_PATH.open(encoding="utf-8-sig", newline="") as handle:
            return list(csv.DictReader(handle))
    except FileNotFoundError:
        return []


def _rows_text(rows: list[dict]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    return "﻿" + buffer.getvalue()


def _interval(text: str) -> tuple[int, int] | None:
    """携程原文 → 分钟区间；以"天"计的说法换算有歧义，返回 None。"""
    matched = re.search(r"(\d+(?:\.\d+)?)\s*(?:[-–~至]\s*(\d+(?:\.\d+)?))?\s*(分钟|小时)", text)
    if not matched:
        return None
    factor = 60 if matched.group(3) == "小时" else 1
    low = float(matched.group(1))
    high = float(matched.group(2) or low)
    return round(low * factor), round(high * factor)


def _apply(row: dict, pick: dict, own_source: bool) -> None:
    """把一条选择写进 CSV 行；证据不全时只标"待核"。own_source：该行没有 Codex 抓取的来源，可由用户补充或改写。"""
    if pick["choice"] == "skip":
        conclusion = CONCLUSIONS["skip"]
    elif pick["choice"] in CONCLUSIONS:
        conclusion = CONCLUSIONS[pick["choice"]]
    else:
        conclusion = f"{pick['low']}-{pick['high']}分钟"
    if own_source:
        row["来源 URL"], row["原文摘录"] = pick.get("url", ""), pick.get("excerpt", "").strip()
    complete = all(row[key].strip() for key in ("来源 URL", "原文摘录")) and pick.get("reviewer")
    pick["counted"] = bool(complete) and pick["choice"] != "skip"
    row["人工结论"] = conclusion if complete or pick["choice"] == "skip" else "待核"
    row["核对人"] = pick.get("reviewer", "")
    row["核对日期"] = pick["date"]


def _sourced() -> set[int]:
    return {source["poi_id"] for source in _read_json(SOURCES_PATH, [])}


def _commit(rows: list[dict], picks: dict) -> None:
    """先写 CSV 再用评分加载器验一遍，不通过就回滚，避免把表写坏。"""
    previous = AUDIT_PATH.read_text(encoding="utf-8-sig") if AUDIT_PATH.exists() else None
    _write_text(AUDIT_PATH, _rows_text(rows))
    try:
        load_stay_references(REFERENCE_PATH, AUDIT_PATH, load_pois())
    except ValueError as error:
        if previous is None:
            AUDIT_PATH.unlink(missing_ok=True)
        else:
            _write_text(AUDIT_PATH, "﻿" + previous)
        raise HTTPException(400, f"核对表校验未通过：{error}") from error
    _write_text(PICKS_PATH, json.dumps(picks, ensure_ascii=False, indent=2) + "\n")


@router.get("/api/stay-audit")
def audit_state() -> dict:
    pois = load_pois()
    references = {place["poi_id"]: place for place in _read_json(REFERENCE_PATH, {"places": []})["places"]}
    sources = {source["poi_id"]: source for source in _read_json(SOURCES_PATH, [])}
    items = []
    for row in _read_rows():
        identifier = int(row["poi_id"])
        place, reference = pois[identifier], references[identifier]
        source = sources.get(identifier, {})
        dataset = [int(float(value)) for value in re.findall(r"\d+(?:\.\d+)?", row["数据集区间"])[:2]]
        items.append({"id": identifier, "name": row["name"], "type": place.type, "summary": place.summary,
                      "dataset": dataset, "llm": [reference["low_min"], reference["high_min"], reference["typical_min"]],
                      "scope": reference["scope"], "reason": reference["reason"], "confidence": reference["confidence"],
                      "spread": reference["spread"]["max_min"], "url": row["来源 URL"], "excerpt": row["原文摘录"],
                      "source": source.get("source") or ("用户补充" if row["来源 URL"] else ""),
                      "user_source": bool(row["来源 URL"]) and not source,
                      "ctrip": _interval(row["原文摘录"]) if source.get("source", "").startswith("携程") else None,
                      "ctrip_days": "天" in row["原文摘录"] and bool(source)})
    return {"items": items, "picks": _read_json(PICKS_PATH, {}), "settings": _read_json(DECISION_PATH, {})}


@router.put("/api/stay-audit/picks/{poi_id}")
def save_pick(poi_id: int, body: Pick, request: Request) -> dict:
    same_origin(request)
    with _lock:
        rows = _read_rows()
        row = next((row for row in rows if int(row["poi_id"]) == poi_id), None)
        if row is None:
            raise HTTPException(404, "这个景点不在核对表里。")
        reference = next(place for place in _read_json(REFERENCE_PATH, {"places": []})["places"] if place["poi_id"] == poi_id)
        pick = body.model_dump()
        pick["reviewer"] = pick["reviewer"].strip()
        pick["date"] = date.today().isoformat()
        if body.choice == "llm":
            pick["low"], pick["high"] = reference["low_min"], reference["high_min"]
        elif body.choice == "dataset":
            pick["low"], pick["high"] = (int(float(value)) for value in re.findall(r"\d+(?:\.\d+)?", row["数据集区间"])[:2])
        elif body.choice == "ctrip":
            interval = _interval(row["原文摘录"])
            if interval is None:
                raise HTTPException(400, "携程原文没有可直接换算的分钟或小时区间，请用自定义填写。")
            pick["low"], pick["high"] = interval
        elif body.choice == "custom" and (body.low is None or body.high is None or body.low > body.high):
            raise HTTPException(400, "自定义需要填最短和最长分钟数，最短不能大于最长。")
        picks = _read_json(PICKS_PATH, {})
        _apply(row, pick, poi_id not in _sourced())
        picks[str(poi_id)] = pick
        _commit(rows, picks)
        return pick


@router.put("/api/stay-audit/settings")
def save_settings(body: Settings, request: Request) -> dict:
    same_origin(request)
    with _lock:
        settings = _read_json(DECISION_PATH, {})
        if body.policy is not None:
            settings["policy"] = body.policy
        if body.reviewer is not None and body.reviewer.strip() != settings.get("reviewer"):
            settings["reviewer"] = body.reviewer.strip()
            rows, picks, sourced = _read_rows(), _read_json(PICKS_PATH, {}), _sourced()
            for row in rows:
                pick = picks.get(row["poi_id"])
                if pick:
                    pick["reviewer"] = settings["reviewer"]
                    _apply(row, pick, int(row["poi_id"]) not in sourced)
            _commit(rows, picks)
        settings["updated"] = date.today().isoformat()
        _write_text(DECISION_PATH, json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
        return settings
