"""对已保存的行程结果计算合理性分数（不重新规划、不联网、不调用模型）。

用法：
  uv run python eval/reasonableness_eval.py ../data/quality-20261016/before.json ../data/quality-20261016/after-v3.json
输入是 eval/route_quality.py 的输出（含 profile 与 itinerary）；结果打印为 Markdown。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.data import load_pois, load_restaurants
from app.reasonableness import LABELS, WEIGHTS, STAY_REFERENCE_PATH, STAY_AUDIT_PATH, load_stay_references, score_itinerary
from app.schemas import Itinerary, UserProfile


def evaluate(path: Path, stay_reference: Path | None = None, stay_audit: Path | None = STAY_AUDIT_PATH) -> list[dict]:
    pois, restaurants = load_pois(), load_restaurants()
    references = load_stay_references(stay_reference, stay_audit, pois) if stay_reference is not None else None
    rows = []
    for case in json.loads(path.read_text())["results"]:
        result = score_itinerary(Itinerary.model_validate(case["itinerary"]), UserProfile.model_validate(case["profile"]),
                                 pois, restaurants, references=references)
        rows.append({"id": case["id"], **result})
    return rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--json", type=Path, help="另存逐例明细")
    parser.add_argument("--stay-reference", nargs="?", const=STAY_REFERENCE_PATH, type=Path,
                        help="启用独立停留参考；可指定JSON路径，省略路径用默认文件")
    parser.add_argument("--stay-audit", type=Path, default=STAY_AUDIT_PATH, help="人工核对CSV，完成核对的结论优先")
    args = parser.parse_args()
    runs = {path.stem: evaluate(path, args.stay_reference, args.stay_audit) for path in args.files}
    names = list(runs)
    print("| 维度（权重） | " + " | ".join(names) + " |")
    print("|---|" + "---:|" * len(names))
    for key in ["overall", *WEIGHTS]:
        label = "综合" if key == "overall" else f"{LABELS[key]}（{WEIGHTS[key]:.0%}）"
        cells = []
        for rows in runs.values():
            values = [row["overall"] if key == "overall" else row["scores"][key] for row in rows]
            values = [value for value in values if value is not None]
            cells.append(f"{sum(values) / len(values):.1f}" if values else "—")
        print(f"| {label} | " + " | ".join(cells) + " |")
    print("| 严重问题条数 | " + " | ".join(str(sum(row["severe"] for row in rows)) for rows in runs.values()) + " |")
    for name, rows in runs.items():
        print(f"\n### {name}：问题分布")
        counts = Counter(LABELS[issue["dimension"]] for row in rows for issue in row["issues"])
        print("，".join(f"{label} {number}" for label, number in counts.most_common()))
        coverage = Counter()
        for row in rows:
            coverage.update(row["coverage"])
        print("覆盖：", dict(coverage))
        print("\n| 案例 | 综合 | " + " | ".join(LABELS[key] for key in WEIGHTS) + " | 主要问题 |")
        print("|---|---:|" + "---:|" * len(WEIGHTS) + "---|")
        for row in rows:
            top = "；".join(f"D{issue['day'] or '-'} {issue['item'] or ''} {issue['message']}".replace("  ", " ") for issue in row["issues"][:2])
            print(f"| {row['id']} | {row['overall']} | " + " | ".join("—" if row["scores"][key] is None else f"{row['scores'][key]:.0f}" for key in WEIGHTS) + f" | {top} |")
    if args.json:
        args.json.write_text(json.dumps(runs, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
