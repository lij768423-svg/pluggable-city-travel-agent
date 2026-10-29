"""20 个画像的完整流程批量评估（LLM 选点＋高德路线＋修复＋夜游），每个画像跑一次。

run：用当前 PYTHONPATH 下的 app 跑指定画像（--only），结果追加写入 --output，可分两份并行；
assess：汇总一份或多份结果，给出逐份指标、总体统计、违例分布和逐日路线，供人工复核。
会调用 MiniMax 与高德；需在补充目录模式下运行（TTA_POI_CATALOG=with_supplement）。
单次运行受模型选点波动影响，只作现象评估，不做显著性结论。
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import statistics
import time
from pathlib import Path

try:
    from area_quality import BINJIANG, HUBIN, WULIN, XIASHA, case_metrics
except ImportError:  # 以 python -m eval.batch20 运行
    from eval.area_quality import BINJIANG, HUBIN, WULIN, XIASHA, case_metrics

XIAOSHAN = {"name": "萧山城区酒店", "lat": 30.1700, "lon": 120.2700}
EAST = {"name": "杭州东站", "lat": 30.2970, "lon": 120.2190}
XIXI = {"name": "西溪附近酒店", "lat": 30.2770, "lon": 120.0750}
QIANJIANG = {"name": "钱江新城酒店", "lat": 30.2500, "lon": 120.2180}
CHENGZHAN = {"name": "杭州城站", "lat": 30.2500, "lon": 120.1890}
ZIJINGANG = {"name": "浙大紫金港附近", "lat": 30.3100, "lon": 120.0900}


def profile(hotel, days, transport, budget, pace, companions, interests=(), **extra):
    return {"hotel": hotel, "days": days, "transport": transport, "budget": budget, "pace": pace,
            "companions": companions, "interests": list(interests), **extra}


PROFILES = [
    ("01-xiasha-2d-transit", profile(XIASHA, 2, "transit", 300, "normal", "solo")),
    ("02-xiasha-1d-photo", profile(XIASHA, 1, "transit", 200, "relaxed", "couple", ["拍照出片"])),
    ("03-xiasha-3d-food-night", profile(XIASHA, 3, "taxi", 1000, "normal", "friends", ["美食探店", "夜景夜游"])),
    ("04-hubin-1d-history", profile(HUBIN, 1, "transit", 500, "intense", "solo", ["人文历史", "博物馆展览"])),
    ("05-hubin-2d-elderly", profile(HUBIN, 2, "transit", 1500, "relaxed", "elderly", ["自然风光", "特色文化"])),
    ("06-hubin-3d-couple-dated", profile(HUBIN, 3, "transit", 2000, "normal", "couple", ["拍照出片", "夜景夜游"], start_date="2026-10-24")),
    ("07-wulin-2d-kids", profile(WULIN, 2, "taxi", 1500, "normal", "family_kids", ["亲子游乐"])),
    ("08-east-1d-must-lingyin", profile(EAST, 1, "transit", 400, "normal", "solo", must_visit=["灵隐寺"])),
    ("09-east-2d-shopping", profile(EAST, 2, "transit", 800, "intense", "friends", ["购物街区", "美食探店"])),
    ("10-binjiang-2d-art", profile(BINJIANG, 2, "taxi", 1200, "normal", "couple", ["艺术文创", "小众安静"])),
    ("11-binjiang-3d-kids", profile(BINJIANG, 3, "transit", 1500, "relaxed", "family_kids", ["亲子游乐", "自然风光"])),
    ("12-xixi-2d-hiking", profile(XIXI, 2, "transit", 1000, "normal", "solo", ["自然风光", "户外徒步"])),
    ("13-xixi-1d-temples", profile(XIXI, 1, "taxi", 600, "relaxed", "elderly", ["宗教寺庙"])),
    ("14-qianjiang-2d-night", profile(QIANJIANG, 2, "transit", 1200, "normal", "couple", ["夜景夜游", "拍照出片"])),
    ("15-chengzhan-1d-nospicy", profile(CHENGZHAN, 1, "transit", 300, "intense", "solo", ["人文历史"], spice="no")),
    ("16-chengzhan-2d-tea-must", profile(CHENGZHAN, 2, "taxi", 2000, "normal", "friends", ["特色文化", "园林古镇"], must_visit=["龙井村"])),
    ("17-xiaoshan-2d-themepark", profile(XIAOSHAN, 2, "transit", 1500, "normal", "family_kids", ["主题乐园"])),
    ("18-zijingang-2d-museum-monday", profile(ZIJINGANG, 2, "transit", 800, "normal", "solo", ["博物馆展览", "艺术文创"], start_date="2026-10-26")),
    ("19-hubin-4d-relaxed", profile(HUBIN, 4, "transit", 3000, "relaxed", "couple", ["人文历史", "自然风光"])),
    ("20-xiasha-2d-taxi", profile(XIASHA, 2, "taxi", 500, "normal", "solo")),
]


def run(output: Path, only: list[str] | None = None) -> None:
    from app.graph import run_plan

    rows = json.loads(output.read_text())["results"] if output.exists() else []
    done = {row["id"] for row in rows}
    for name, form in PROFILES:
        if name in done or (only and not any(name.startswith(prefix) for prefix in only)):
            continue
        started = time.monotonic()
        try:
            out = run_plan("", dict(form), select_mode="llm", enable_repair=True)
        except Exception as error:  # 记录失败，不中断其余画像
            rows.append({"id": name, "form": form, "error": f"{type(error).__name__}: {error}"[:300],
                         "seconds": round(time.monotonic() - started, 1)})
        else:
            rows.append({"id": name, "form": form, "profile": out["profile"].model_dump(mode="json"),
                         "selected": [selection.poi_id for selection in out["selected"]],
                         "dropped": out.get("dropped", []),
                         "initial_selected_ids": out.get("initial_selected_ids", []),
                         "schedule_attempts": out.get("schedule_attempts", []),
                         "repair_removed_ids": out.get("repair_removed_ids", []),
                         "usage": out.get("usage", {}), "poi_catalog": out.get("poi_catalog"),
                         "report": out["report"].model_dump(mode="json"),
                         "itinerary": out["itinerary"].model_dump(mode="json"), "log": out["log"],
                         "travel_stats": out.get("travel_stats"), "seconds": round(time.monotonic() - started, 1)})
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps({"scope": "full pipeline; live MiniMax + Amap; supplement catalog; one run per profile",
                                      "results": rows}, ensure_ascii=False, indent=2))
        print(name, "完成" if "error" not in rows[-1] else "失败", rows[-1]["seconds"], "秒", flush=True)


def hm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def run_metrics(row: dict, index, places) -> dict:
    from app.schemas import UserProfile
    profile = UserProfile.model_validate(row["profile"])
    days = row["itinerary"]["days"]
    visits = [item for day in days for item in day["items"] if item["kind"] == "poi"]
    internal = [item for day in days for item in day["items"][1:] if item["travel_mode"] not in ("步行", "原地")]
    first = [day["items"][0]["travel_min"] for day in days if day["items"]]
    totals = row["report"]["totals"]
    matched = [item for item in visits if item["ref_id"] in places and set(places[item["ref_id"]].interests) & set(profile.interests)]
    must = profile.must_visit
    names = {item["name"] for item in visits}
    return {**case_metrics(row, index),
            "天数": len(days), "每天景点": round(len(visits) / max(1, len(days)), 2),
            "最长日内单段": max((item["travel_min"] for item in internal), default=0),
            "首段均值": round(statistics.mean(first), 1) if first else 0,
            "返程均值": round(statistics.mean(day["return_travel_min"] for day in days if day["items"]), 1) if first else 0,
            "餐次覆盖": f"{int(totals.get('covered_meals', 0))}/{int(totals.get('expected_meals', 0))}",
            "花费/预算": f"{totals.get('total', 0):.0f}/{profile.budget:.0f}",
            "兴趣命中率": round(len(matched) / len(visits), 2) if profile.interests and visits else None,
            "必去满足": all(any(word in name for name in names) for word in must) if must else None,
            "约束": row["report"]["constraint_status"], "安排": row["report"]["arrangement_status"],
            "体验": row["report"]["experience_status"],
            "高德请求": (row.get("travel_stats") or {}).get("live_calls"), "秒": row["seconds"]}


def assess(inputs: list[Path], output: Path) -> None:
    from app.areas import area_index
    from app.data import load_pois

    index, places = area_index(), load_pois()
    rows = sorted((row for path in inputs for row in json.loads(path.read_text())["results"]), key=lambda row: row["id"])
    failed = [row for row in rows if "error" in row]
    ok = [row for row in rows if "error" not in row]
    table = {row["id"]: run_metrics(row, index, places) for row in ok}
    soft = Counter(v["code"] for row in ok for v in row["report"]["violations"] if v["severity"] == "soft")
    hard = Counter(v["code"] for row in ok for v in row["report"]["violations"] if v["severity"] == "hard")

    def mean(key):
        values = [metrics[key] for metrics in table.values() if isinstance(metrics[key], (int, float)) and metrics[key] is not None]
        return round(statistics.mean(values), 2) if values else None
    lines = ["# 20 画像完整流程评估", "",
             f"画像 {len(rows)} 个，成功 {len(ok)}，失败 {len(failed)}。每个画像只跑一次；模型选点有波动，以下是现象统计，不是显著性结论。", "",
             "## 总体", "",
             "| 指标 | 数值 |", "|---|---:|",
             f"| 约束通过 | {sum(m['约束'] == 'passed' for m in table.values())}/{len(ok)} |",
             f"| 安排完整（餐厅已落实） | {sum(m['安排'] == 'complete' for m in table.values())}/{len(ok)} |",
             f"| 体验无待完善项 | {sum(m['体验'] == 'complete' for m in table.values())}/{len(ok)} |",
             f"| 必去满足 | {sum(m['必去满足'] is True for m in table.values())}/{sum(m['必去满足'] is not None for m in table.values())} |"]
    for key in ("每天片区数均值", "跨片区次数", "不顺路跳转次数", "被拆到多天的片区", "日内乘车分钟", "最长日内单段",
                "首段均值", "返程均值", "每天景点", "空档分钟", "兴趣命中率", "高德请求", "秒"):
        lines.append(f"| {key}（每份均值） | {mean(key)} |")
    lines += ["", "## 违例分布", "", "| 代码 | 硬 | 软 |", "|---|---:|---:|"]
    lines += [f"| {code} | {hard.get(code, 0)} | {soft.get(code, 0)} |" for code in sorted(set(hard) | set(soft))]
    keys = ["每天片区数均值", "跨片区次数", "不顺路跳转次数", "日内乘车分钟", "最长日内单段", "首段均值", "每天景点",
            "空档分钟", "餐次覆盖", "花费/预算", "兴趣命中率", "必去满足", "约束", "体验"]
    lines += ["", "## 逐份指标", "", "| 画像 | " + " | ".join(keys) + " |", "|---|" + "---|" * len(keys)]
    lines += [f"| {name} | " + " | ".join("—" if metrics[key] is None else str(metrics[key]) for key in keys) + " |"
              for name, metrics in table.items()]
    if failed:
        lines += ["", "## 失败", ""] + [f"- {row['id']}：{row['error']}" for row in failed]
    lines += ["", "## 逐日路线与提示", ""]
    for row in ok:
        lines.append(f"**{row['id']}**（{row['report']['totals'].get('total', 0):.0f} 元；舍弃：{'、'.join(places[i].name for i in row['dropped'] if i in places) or '无'}）")
        for day in row["itinerary"]["days"]:
            stops = []
            for item in day["items"]:
                ride = f"（{item['travel_mode']}{item['travel_min']}分）" if item["travel_mode"] not in ("步行", "原地") else ""
                if item["kind"] == "poi":
                    stops.append(f"{hm(item['start'])} {item['name']}〔{index.label(index.area_of(item['ref_id']))}〕{ride}")
                else:
                    stops.append(f"{hm(item['start'])} 餐：{item['name']}{ride}")
            lines.append(f"- 第{day['day']}天：" + " → ".join(stops) + f" → 返程{day['return_travel_min']}分")
        notes = [f"{v['severity']}:{v['code']}(D{v['day']}) {v['message']}" for v in row["report"]["violations"]]
        if notes:
            lines.append("  - 提示：" + "；".join(notes))
        lines.append("")
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines[:60]))


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run")
    runner.add_argument("--output", type=Path, required=True)
    runner.add_argument("--only", nargs="*")
    assessor = commands.add_parser("assess")
    assessor.add_argument("inputs", type=Path, nargs="+")
    assessor.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        run(args.output, args.only)
    else:
        assess(args.inputs, args.output)


if __name__ == "__main__":
    main()
