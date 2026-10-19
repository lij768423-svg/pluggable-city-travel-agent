"""片区为主排程的固定选点对照：分天、排程与餐饮；估算交通，不联网、不调模型、不含修复与夜游。

run：用当前 PYTHONPATH 下的 app 跑全部案例（改前、改后各跑一次）；
compare：用本分支的片区索引给两份结果统一计算片区指标，保证两边是同一把尺子。
需在补充目录模式下运行（TTA_POI_CATALOG=with_supplement），下沙案例才有补充公园。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
from pathlib import Path

try:
    from route_quality import cases as base_cases
except ImportError:  # 以 python -m eval.area_quality 运行
    from eval.route_quality import cases as base_cases

XIASHA = {"name": "杭州电子科技大学(下沙校区)", "lat": 30.32041618813116, "lon": 120.34965381992117}
HUBIN = {"name": "湖滨步行街附近酒店", "lat": 30.2645, "lon": 120.1685}
BINJIANG = {"name": "滨江区政府附近", "lat": 30.2140, "lon": 120.2180}
WULIN = {"name": "武林广场附近酒店", "lat": 30.2770, "lon": 120.1700}


def cases() -> list[dict]:
    extra = [
        # 用户最初反馈：杭电出发、未填日期、公交
        ("xiasha-first", XIASHA, 2, "transit", 300, "normal", [376, 300, 162, 57, 91, 26]),
        ("xiasha-rerun", XIASHA, 2, "transit", 300, "normal", [1000001, 1000000, 376, 26, 232, 20]),
        # 用户截图行程 TrDfEK0NHcNapDrB 的选点：打车、预算 500
        ("xiasha-taxi", XIASHA, 2, "taxi", 500, "normal", [1000000, 376, 149, 94, 300, 162, 26, 91]),
        ("xiasha-taxi-relaxed", XIASHA, 2, "taxi", 500, "relaxed", [1000000, 376, 149, 94, 300, 162, 26, 91]),
        ("hubin-spread", HUBIN, 2, "transit", 1500, "normal", [300, 162, 149, 36, 9, 20, 91, 0]),
        ("binjiang-3d", BINJIANG, 3, "taxi", 1500, "normal", [94, 149, 300, 162, 20, 91, 36, 9, 13]),
        ("wulin-1d", WULIN, 1, "transit", 800, "normal", [15, 46, 20, 91, 36]),
        ("hubin-3d-mix", HUBIN, 3, "transit", 1500, "normal", [0, 2, 9, 13, 36, 20, 57, 71, 5]),
    ]
    result = base_cases()
    for name, hotel, days, transport, budget, pace, selected in extra:
        result.append({"id": name, "selected": selected, "profile": {
            "days": days, "pace": pace, "budget": budget, "transport": transport, "hotel": hotel,
            "companions": "solo", "include_evening": False}})
    return result


def run(output: Path) -> None:
    from app.data import load_pois, load_restaurants
    from app.nodes.meals import build_itinerary
    from app.nodes.schedule import schedule
    from app.nodes.validate import validate_itinerary
    from app.planning_quality import choose_meal_aware_plan
    from app.schemas import Selection, UserProfile
    from app.travel import Travel

    places = load_pois()
    rows = []
    for case in cases():
        started = time.monotonic()
        profile = UserProfile.model_validate(case["profile"])
        route = Travel("estimate", profile.transport)
        required = {identifier for identifier in case["selected"]
                    if any(name in places[identifier].name for name in profile.must_visit)}
        orders, dropped, log = schedule(case["selected"], profile, {identifier: 1.0 for identifier in case["selected"]}, route, must=required)
        selected = [Selection(poi_id=identifier, reason="固定选点对照", source="rule") for identifier in case["selected"]]
        orders, itinerary, messages = choose_meal_aware_plan(orders, profile, selected, route=route, live=False)
        report = validate_itinerary(itinerary, profile, travel_fn=route.leg, restaurants=load_restaurants())
        rows.append({**case, "orders": orders, "dropped": dropped, "report": report.model_dump(mode="json"),
                     "itinerary": itinerary.model_dump(mode="json"), "log": log + messages,
                     "seconds": round(time.monotonic() - started, 3)})
        print(case["id"], "hard", len(report.hard), flush=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"scope": "fixed selection; schedule + meals; estimated transport; supplement catalog; no model/network/repair/evening",
                                  "cases_sha256": hashlib.sha256(json.dumps(cases(), sort_keys=True).encode()).hexdigest(),
                                  "results": rows}, ensure_ascii=False, indent=2))


def case_metrics(row: dict, index) -> dict[str, float]:
    from app.schemas import UserProfile
    hotel = UserProfile.model_validate(row["profile"]).hotel
    home = index.home_areas(hotel.lat, hotel.lon)
    days = row["itinerary"]["days"]
    sequences = [[index.area_of(item["ref_id"]) for item in day["items"] if item["kind"] == "poi"] for day in days]
    day_areas = [list(dict.fromkeys(sequence)) for sequence in sequences]
    changes = [[(a, b) for a, b in zip(sequence, sequence[1:]) if a != b] for sequence in sequences]
    seen: dict[str, set[int]] = {}
    for number, areas in enumerate(day_areas):
        for area in areas:
            seen.setdefault(area, set()).add(number)
    internal = [item for day in days for item in day["items"][1:] if item["travel_mode"] not in ("步行", "原地")]
    active = [areas for areas in day_areas if areas]
    return {
        "每天片区数均值": round(sum(len(areas) for areas in active) / len(active), 3) if active else 0,
        "片区超2个的天数": sum(len(areas) > 2 for areas in day_areas),
        "跨片区次数": sum(len(pairs) for pairs in changes),
        "不顺路跳转次数": sum(not index.compatible(a, b, home) for pairs in changes for a, b in pairs),
        "被拆到多天的片区": sum(len(numbers) > 1 for numbers in seen.values()),
        "日内乘车分钟": sum(item["travel_min"] for item in internal),
        "游览地点数": sum(item["kind"] == "poi" for day in days for item in day["items"]),
        "舍弃地点数": len(row["dropped"]),
        "空档分钟": row["report"]["totals"].get("idle_min", 0),
        "硬约束违例": sum(item["severity"] == "hard" for item in row["report"]["violations"]),
    }


def compare(before: Path, after: Path, output: Path) -> None:
    from app.areas import area_index

    left, right = (json.loads(path.read_text()) for path in (before, after))
    for field in ("cases_sha256", "scope"):
        if left[field] != right[field]:
            raise SystemExit(f"拒绝比较：{field} 不一致")
    index = area_index()
    old = {row["id"]: case_metrics(row, index) for row in left["results"]}
    new = {row["id"]: case_metrics(row, index) for row in right["results"]}
    if old.keys() != new.keys():
        raise SystemExit("拒绝比较：案例不一致")
    keys = list(next(iter(old.values())))
    total = lambda table: {key: round(sum(row[key] for row in table.values()), 3) for key in keys}  # noqa: E731
    first, second = total(old), total(new)
    focus = [identifier for identifier in old if not identifier.endswith(("-normal", "-relaxed", "-intense")) or identifier.startswith("xiasha")]
    lines = ["# 片区为主排程 · 固定选点对照", "",
             f"共 {len(old)} 组固定画像与固定选点（原路线质量 24 组 + 新增 {len(focus)} 组下沙/湖滨/滨江/武林起点）。",
             "仅比较分天、排程与餐饮；估算交通，未联网、未调用模型，不含修复回路、空档补点与夜游。片区指标两侧统一用改后片区索引计算。", "",
             "| 指标（所有案例合计） | 修改前 | 修改后 |", "|---|---:|---:|"]
    lines += [f"| {key} | {first[key]:g} | {second[key]:g} |" for key in keys]
    lines += ["", "## 新增起点案例", "", "| 案例 | " + " | ".join(keys) + " |", "|---|" + "---:|" * len(keys)]
    for identifier in focus:
        lines.append(f"| {identifier} | " + " | ".join(f"{old[identifier][key]:g} → {new[identifier][key]:g}" for key in keys) + " |")
    worse = [identifier for identifier in old if new[identifier]["舍弃地点数"] > old[identifier]["舍弃地点数"]
             or new[identifier]["硬约束违例"] > old[identifier]["硬约束违例"]]
    lines += ["", "## 舍弃或硬违例变多的案例", "", "、".join(worse) if worse else "无"]
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


def main() -> None:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    runner = commands.add_parser("run")
    runner.add_argument("--output", type=Path, required=True)
    comparer = commands.add_parser("compare")
    comparer.add_argument("before", type=Path)
    comparer.add_argument("after", type=Path)
    comparer.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "run":
        run(args.output)
    else:
        compare(args.before, args.after, args.output)


if __name__ == "__main__":
    main()
