"""把用户在 /stay-audit.html 的核对结论生成为排程用的停留时长覆盖表。

用法：uv run python scripts/apply_stay_audit.py
输入：backend/eval/stay_audit/picks.json、decision.json；输出：backend/app/assets/stay_overrides.json。
规则（用户 2026-10-16 确认）：方案须为 manual_only；跳过的不换；poi_reviews.json 里已经手工改过
停留时长的景点保持现值（宋城整天、湖边步行组只算驻足时间等）；没有公开来源的结论也换，但注明。
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import load_pois  # noqa: E402

BACKEND = Path(__file__).resolve().parent.parent
AUDIT = BACKEND / "eval" / "stay_audit"
REVIEWS = BACKEND / "app" / "assets" / "poi_reviews.json"
OUTPUT = BACKEND / "app" / "assets" / "stay_overrides.json"
LABELS = {"llm": "大模型估计", "dataset": "ChinaTravel 原数据", "ctrip": "携程攻略", "custom": "核对人自定"}


def build() -> dict:
    decision = json.loads((AUDIT / "decision.json").read_text(encoding="utf-8"))
    if decision.get("policy") != "manual_only":
        raise SystemExit(f"排程方案是 {decision.get('policy')!r}，本脚本只实现 manual_only")
    picks = json.loads((AUDIT / "picks.json").read_text(encoding="utf-8"))
    reviewed = {entry["id"]: entry["name"] for entry in json.loads(REVIEWS.read_text(encoding="utf-8"))["places"]
                if {"min_hours", "max_hours"} & entry["changes"].keys()}
    pois = load_pois()
    with (AUDIT / "top50.csv").open(encoding="utf-8-sig", newline="") as handle:
        rows = {int(row["poi_id"]): row for row in csv.DictReader(handle)}
    places, kept = [], []
    for key, pick in sorted(picks.items(), key=lambda item: int(item[0])):
        identifier = int(key)
        if pick["choice"] == "skip":
            continue
        if identifier in reviewed:
            kept.append({"poi_id": identifier, "name": reviewed[identifier], "picked": [pick["low"], pick["high"]]})
            continue
        evidence = LABELS[pick["choice"]]
        url, excerpt = rows[identifier]["来源 URL"].strip(), rows[identifier]["原文摘录"].strip()
        if url:
            evidence += f"；来源 {url}" + (f"「{excerpt}」" if excerpt and len(excerpt) <= 20 else "")
        elif pick["choice"] != "dataset":
            evidence += "；未找到公开来源，按核对人判断"
        places.append({"poi_id": identifier, "name": pois[identifier].name, "low_min": pick["low"], "high_min": pick["high"],
                       "choice": pick["choice"], "counted_as_manual": bool(pick.get("counted")),
                       "reviewer": pick.get("reviewer", ""), "reviewed_at": pick["date"], "evidence": evidence})
    return {"policy": "manual_only", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "rule": "用户核对结论覆盖排程停留区间；poi_reviews.json 已有停留修订的景点保持不变；跳过的不换",
            "places": places, "kept_reviewed": kept}


def main() -> None:
    artifact = build()
    OUTPUT.write_text(json.dumps(artifact, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"覆盖 {len(artifact['places'])} 个，保持已审阅 {len(artifact['kept_reviewed'])} 个 → {OUTPUT}")


if __name__ == "__main__":
    main()
