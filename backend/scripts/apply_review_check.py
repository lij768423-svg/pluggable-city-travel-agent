"""把待核清单的核对结果并入城市的 poi_reviews.json（2026-10-21，七城接入后交给 Codex 上网核对）。

用法（backend 目录）：uv run python scripts/apply_review_check.py --city beijing
输入：data/<key>/onboarding-review/check.csv（列见 docs/七城待核清单核对说明.md）
清单外补核（2026-10-21 起，0 元门票排查）：--check check-zero-price.csv --reviewer Claude，列同 check.csv
输出：app/assets/cities/<key>/poi_reviews.json 增补或合并条目；同一条结论重复运行不会重复写入。
只接受白名单字段；observed 只用于亲眼看到的官网或政府公告，其余一律不算核实票价。
注意：之后不要再跑 onboard_city.py --stage review，它会按规则重新生成 poi_reviews.json，覆盖这里的结论。
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

FIELDS = {"price": float, "min_hours": float, "max_hours": float, "open_min": int, "close_min": int,
          "recommendable": bool, "day_anchor": bool, "onsite_dining": bool}
VERIFICATION = {"observed", "secondary", "inference"}
VERDICTS = {"修改", "保持", "无法核实"}


def parse_changes(row: dict) -> dict:
    text = (row.get("changes") or "").strip()
    if not text:
        return {}
    changes = json.loads(text)
    unknown = set(changes) - FIELDS.keys()
    if unknown:
        raise ValueError(f"id={row['id']} 不支持的字段：{sorted(unknown)}")
    for field, kind in FIELDS.items():
        if field in changes and not isinstance(changes[field], bool if kind is bool else (int, float)):
            raise ValueError(f"id={row['id']} {field} 类型应为 {kind.__name__}")
    low, high = changes.get("min_hours"), changes.get("max_hours")
    if (low is None) != (high is None) or (low is not None and not 0 < low <= high <= 12):
        raise ValueError(f"id={row['id']} 时长需同时给 min_hours ≤ max_hours，且在 0–12 小时内")
    for field in ("open_min", "close_min"):
        if field in changes and not 0 <= changes[field] <= 1440:
            raise ValueError(f"id={row['id']} {field} 应为 0–1440 分钟")
    if ("open_min" in changes) != ("close_min" in changes):
        raise ValueError(f"id={row['id']} 开放时间需同时给 open_min 与 close_min")
    if changes.get("price", 0) < 0:
        raise ValueError(f"id={row['id']} 票价不能为负")
    return changes


def apply(key: str, check_name: str = "check.csv", reviewer: str = "Codex") -> dict:
    from app.cities import city_config
    from app.data import load_base_pois
    from app.matching import norm
    settings = city_config(key)
    check_path = settings.data_dir / "onboarding-review" / check_name
    review_path = settings.assets_dir / "poi_reviews.json"
    review = json.loads(review_path.read_text(encoding="utf-8"))
    pois = load_base_pois(key)
    entries = {entry["id"]: entry for entry in review["places"]}
    with check_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    counts = {"rows": len(rows), "applied": 0, "kept": 0, "unverified": 0, "skipped_duplicate": 0}
    for row in rows:
        identifier, verdict = int(row["id"]), row["verdict"].strip()
        if verdict not in VERDICTS:
            raise ValueError(f"id={identifier} verdict 只能是 {sorted(VERDICTS)}")
        poi = pois.get(identifier)
        if poi is None or norm(poi.name, key) != norm(row["name"], key):
            raise ValueError(f"id={identifier} {row['name']} 与数据集名称不符")
        changes = parse_changes(row)
        if verdict != "修改":
            if changes:
                raise ValueError(f"id={identifier} verdict={verdict} 时 changes 应为空")
            counts["kept" if verdict == "保持" else "unverified"] += 1
            continue
        if not changes:
            raise ValueError(f"id={identifier} verdict=修改 但没有 changes")
        verification = row["verification"].strip()
        if verification not in VERIFICATION:
            raise ValueError(f"id={identifier} verification 只能是 {sorted(VERIFICATION)}")
        url = row["source_url"].strip()
        if verification != "inference" and not url.startswith("http"):
            raise ValueError(f"id={identifier} {verification} 需要 http 来源链接")
        note = f"{row.get('checked_at') or review.get('reviewed_at')} AI核对（{reviewer}）：{row['note'].strip()}"
        source_key = f"check-{identifier}" if check_name == "check.csv" else f"{Path(check_name).stem}-{identifier}"
        review["sources"][source_key] = {"url": url or f"data/{key}/onboarding-review/check.csv", "published_at": row["source_date"].strip() or None,
                                         "verification": "observed" if verification == "observed" else "curator_inference",
                                         "facts": row["note"].strip()}
        entry = entries.get(identifier)
        if entry is None:
            entry = {"id": identifier, "name": poi.name, "sources": [source_key], "changes": changes, "note": note}
            review["places"].append(entry)
            entries[identifier] = entry
        elif note in entry["note"]:
            counts["skipped_duplicate"] += 1
            continue
        else:
            entry["changes"].update(changes)
            entry["sources"] = [source_key] + [source for source in entry["sources"] if source != source_key]
            entry["note"] = f"{entry['note']}；{note}"
        counts["applied"] += 1
    if counts["applied"] and check_name == "check.csv" and "Codex" not in review["scope"]:
        review["scope"] += "；待核清单经 Codex 上网核对（AI 核对，未做人工或现场核验）。"
    if counts["applied"] and check_name != "check.csv" and check_name not in review["scope"]:
        review["scope"] += f"；清单外条目经 {reviewer} 上网补核（{check_name}，AI 核对，未做人工或现场核验）。"
    review["places"].sort(key=lambda entry: entry["id"])
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    load_base_pois.cache_clear()
    load_base_pois(key)  # 按运行时同一套逻辑重新载入，名称不符或字段不合法会在这里报错
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--city", required=True)
    parser.add_argument("--check", default="check.csv", help="onboarding-review 下的核对表文件名")
    parser.add_argument("--reviewer", default="Codex", help="写进 note 的核对者")
    args = parser.parse_args()
    print(json.dumps(apply(args.city, args.check, args.reviewer), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
