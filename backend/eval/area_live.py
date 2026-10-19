"""片区为主的真实小样本：完整流程（LLM 选点＋高德路线＋修复＋夜游），改前、改后各跑一次。

run：用当前 PYTHONPATH 下的 app 跑 6 个画像（会调用 MiniMax 与高德，单次约 1 次模型调用、60–150 次高德请求）；
compare：用本分支的片区索引统一计算片区指标。模型选点每次可能不同，单次结果只作现象记录，不做统计结论。
需在补充目录模式下运行（TTA_POI_CATALOG=with_supplement）。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

try:
    from area_quality import BINJIANG, HUBIN, WULIN, XIASHA, case_metrics
except ImportError:  # 以 python -m eval.area_live 运行
    from eval.area_quality import BINJIANG, HUBIN, WULIN, XIASHA, case_metrics

XIAOSHAN = {"name": "萧山城区酒店", "lat": 30.1700, "lon": 120.2700}
PROFILES = [
    ("xiasha-transit", {"days": 2, "budget": 300, "pace": "normal", "transport": "transit", "hotel": XIASHA}),
    ("xiasha-taxi", {"days": 2, "budget": 500, "pace": "normal", "transport": "taxi", "hotel": XIASHA}),
    ("hubin-2d", {"days": 2, "budget": 1500, "pace": "normal", "transport": "transit", "hotel": HUBIN}),
    ("binjiang-3d", {"days": 3, "budget": 1500, "pace": "normal", "transport": "taxi", "hotel": BINJIANG}),
    ("wulin-1d", {"days": 1, "budget": 800, "pace": "normal", "transport": "transit", "hotel": WULIN}),
    ("xiaoshan-2d", {"days": 2, "budget": 1500, "pace": "normal", "transport": "transit", "hotel": XIAOSHAN}),
]


def run(output: Path, only: list[str] | None = None) -> None:
    from app.graph import run_plan

    rows = json.loads(output.read_text())["results"] if output.exists() else []
    done = {row["id"] for row in rows}
    for name, form in PROFILES:
        if name in done or (only and name not in only):
            continue
        started = time.monotonic()
        out = run_plan("", dict(form), select_mode="llm", enable_repair=True)
        itinerary, report = out["itinerary"], out["report"]
        rows.append({"id": name, "profile": out["profile"].model_dump(mode="json"),
                     "selected": [selection.poi_id for selection in out["selected"]],
                     "initial_selected_ids": out.get("initial_selected_ids"),
                     "dropped": out.get("dropped", []), "report": report.model_dump(mode="json"),
                     "itinerary": itinerary.model_dump(mode="json"), "log": out["log"],
                     "travel_stats": out.get("travel_stats"), "usage": str(out.get("usage")),
                     "seconds": round(time.monotonic() - started, 1)})
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"scope": "full pipeline; live MiniMax + Amap; supplement catalog; single run per profile",
                                      "results": rows}, ensure_ascii=False, indent=2))
        print(name, "完成", rows[-1]["seconds"], "秒", flush=True)


def compare(before: Path, after: Path, output: Path) -> None:
    from app.areas import area_index

    index = area_index()
    left, right = ({row["id"]: row for row in json.loads(path.read_text())["results"]} for path in (before, after))
    keys = None
    lines = ["# 片区为主 · 真实小样本（完整流程，单次）", "",
             "每个画像改前、改后各跑一次；模型选点每次可能不同，只记录现象，不做统计结论。片区指标两侧统一用改后片区索引计算。", ""]
    for name, _ in PROFILES:
        if name not in left or name not in right:
            continue
        old, new = case_metrics(left[name], index), case_metrics(right[name], index)
        if keys is None:
            keys = list(old)
            lines += ["| 画像 | " + " | ".join(keys) + " |", "|---|" + "---:|" * len(keys)]
        lines.append(f"| {name} | " + " | ".join(f"{old[key]:g} → {new[key]:g}" for key in keys) + " |")
    lines += ["", "## 逐日路线", ""]
    for name, _ in PROFILES:
        for side, table in (("改前", left), ("改后", right)):
            if name not in table:
                continue
            row = table[name]
            total = row["report"]["totals"].get("total")
            lines.append(f"**{name} · {side}**（合计约 {total} 元）")
            for day in row["itinerary"]["days"]:
                stops = [f"{item['name']}〔{index.label(index.area_of(item['ref_id']))}〕"
                         + (f"（{item['travel_mode']}{item['travel_min']}分）" if item["travel_mode"] not in ("步行", "原地") else "")
                         for item in day["items"] if item["kind"] == "poi"]
                lines.append(f"- 第{day['day']}天：" + " → ".join(stops))
            lines.append("")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run")
    runner.add_argument("--output", type=Path, required=True)
    runner.add_argument("--only", nargs="*")
    comparer = commands.add_parser("compare")
    comparer.add_argument("before", type=Path)
    comparer.add_argument("after", type=Path)
    comparer.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        run(args.output, args.only)
    else:
        compare(args.before, args.after, args.output)


if __name__ == "__main__":
    main()
