"""E5 探针（docs/研究计划v2-20261024.md E5）：把本系统的一份行程转成 ChinaTravel 的 plan JSON，
用它的 hard_logic_py 校验器和常识检查跑一遍，看哪些约束族能用、哪些要排除、适配缺口在哪。

用法：cd ~/src/probe-chinatravel/ChinaTravel && PYTHONPATH=. ../venv/bin/python ../ct_probe.py <replay.json> <case_id> <method>
只读本仓库之外的数据，不调用模型，不联网。
"""
from __future__ import annotations

import ast
import collections
import csv
import json
import math
import re
import sys
from pathlib import Path

csv.field_size_limit(10**8)
DATA = Path(__file__).resolve().parent / "data"
CITY_CN = {"hangzhou": "杭州", "nanjing": "南京", "suzhou": "苏州", "beijing": "北京", "shanghai": "上海", "guangzhou": "广州",
           "shenzhen": "深圳", "chengdu": "成都", "chongqing": "重庆", "wuhan": "武汉"}
MODE = {"步行": "walk", "打车": "taxi", "公交": "metro", "地铁": "metro", "公交/地铁": "metro"}
FAMILY = [  # 约束族：按 DSL 文本归类，用于统计"哪些族能用"
    ("天数", r"day_count\("), ("人数", r"people_count\("), ("总预算", r"total_cost"),
    ("城际交通", r"intercity_transport|'train'|'airplane'"), ("酒店/房型", r"accommodation|room_"),
    ("必去景点", r"attraction_name_set"), ("景点类型", r"attraction_type\("),
    ("指定餐厅", r"restaurant_name_set"), ("菜系", r"restaurant_type\("),
    ("市内交通方式", r"innercity_transport_type"), ("票数/车数", r"activity_tickets|metro_tickets|taxi_cars"),
    ("距离", r"poi_distance"), ("时间", r"activity_start_time|activity_end_time|activity_time"),
]


def hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def family_of(constraint: str) -> str:
    hits = [name for name, pattern in FAMILY if re.search(pattern, constraint)]
    return "+".join(hits) if hits else "其他"


def to_plan(itinerary: dict, profile: dict, start_city: str, people: int) -> dict:
    """本系统行程 → ChinaTravel plan JSON。酒店、城际交通不在本系统范围内，不生成；票数按人数，打车按 4 人一车。"""
    target = CITY_CN[profile.get("city") or "hangzhou"]
    hotel = (profile.get("hotel") or {}).get("name") or "酒店"
    days = []
    for day in itinerary["days"]:
        activities, previous, previous_end = [], hotel, None
        for item in day["items"]:
            kind, name = item["kind"], item["name"]
            if kind == "poi":
                price = float(item.get("cost") or 0)
                activity = {"type": "attraction", "position": name, "price": price, "tickets": people, "cost": price * people}
            elif kind == "meal":
                price = float(item.get("cost") or 0)
                activity = {"type": item.get("meal") or "lunch", "position": name, "price": price, "cost": price * people}
            else:
                continue
            activity.update({"start_time": hhmm(item["start"]), "end_time": hhmm(item["end"])})
            evidence = item.get("travel_evidence") or {}
            minutes = int(item.get("travel_min") or 0)
            mode = MODE.get(item.get("travel_mode") or "", "walk")
            if previous != name:
                leg = {"start": previous, "end": name, "mode": mode, "start_time": hhmm(item["start"] - minutes), "end_time": hhmm(item["start"]),
                       "price": float(evidence.get("cost") or 0), "cost": float(evidence.get("cost") or 0),
                       "distance": round((evidence.get("walking_m") or 0) / 1000, 2)}
                if mode == "taxi":
                    leg["cars"] = math.ceil(people / 4)
                else:
                    leg["tickets"] = people
                activity["transports"] = [leg]
            else:
                activity["transports"] = []
            activities.append(activity)
            previous, previous_end = name, item["end"]
        days.append({"day": day["day"], "activities": activities})
    return {"people_number": people, "start_city": start_city, "target_city": target, "itinerary": days}


def load_queries(city_cn: str):
    rows = []
    for split in ("easy", "medium", "human"):
        for row in csv.DictReader(open(DATA / f"{split}.csv", encoding="utf-8")):
            if row["target_city"] == city_cn:
                row["split"] = split
                row["hard_logic_py"] = ast.literal_eval(row["hard_logic_py"])  # CSV 里是 Python 字面量列表，单双引号混用，不是 JSON
                rows.append(row)
    return rows


EXCLUDED = ("城际交通", "酒店/房型")  # 本系统不排酒店和城际交通，这两族按协议排除
TRIVIAL = ("天数", "人数", "票数/车数")  # 由转换器直接写入 plan，不构成对规划方法的检验


def evaluate_one(constraint: str, plan: dict) -> tuple[str, str]:
    """跑一条 DSL：pass / fail / error（error = 适配缺口或 DSL 执行异常，不是行程不合格）。"""
    from copy import deepcopy
    from chinatravel.symbol_verification.concept_func import func_dict
    from chinatravel.symbol_verification.dsl import execute_dsl_code
    from chinatravel.symbol_verification.hard_constraint import _normalize_legacy_hard_logic_py
    variables = deepcopy(func_dict)
    variables["plan"] = plan
    try:
        execute_dsl_code(_normalize_legacy_hard_logic_py(constraint), variables, allowed_builtins={"set": set})
        return ("pass" if bool(variables.get("result", False)) else "fail"), ""
    except Exception as error:  # noqa: BLE001
        return "error", f"{type(error).__name__}: {str(error)[:120]}"


def survey_all(plan: dict) -> None:
    """全部 604 条查询：各约束族能否在本系统的 plan 上执行（只看是否报错），以及排除两族后每城还剩多少条有实质约束的查询。"""
    from chinatravel.symbol_verification.concept_func import set_concept_func_lang
    set_concept_func_lang("zh")
    status = collections.defaultdict(collections.Counter)
    errors = collections.Counter()
    meaningful = collections.Counter()
    total = collections.Counter()
    for split in ("easy", "medium", "human"):
        for row in csv.DictReader(open(DATA / f"{split}.csv", encoding="utf-8")):
            constraints = ast.literal_eval(row["hard_logic_py"])
            total[row["target_city"]] += 1
            plan["target_city"] = row["target_city"]
            families = [family_of(c) for c in constraints]
            for constraint, family in zip(constraints, families):
                kind, message = evaluate_one(constraint, plan)
                status[family][kind] += 1
                if kind == "error":
                    errors[f"[{family}] {message}"] += 1
            # 有实质检验力的约束：预算、景点、餐厅、距离、时间，或单独的市内交通方式；票数模板（市内交通方式+票数/车数）不算
            kept = [f for f in families if not any(x in f for x in EXCLUDED)
                    and (f == "市内交通方式" or any(x in f for x in ("总预算", "景点类型", "必去景点", "菜系", "指定餐厅", "距离", "时间")))]
            if kept:
                meaningful[row["target_city"]] += 1
    print("\n== 全部查询各约束族在本系统 plan 上的执行情况（通过/未过只说明与 c01 行程是否吻合，关键看 error）")
    for family, counter in sorted(status.items(), key=lambda kv: -sum(kv[1].values())):
        print(f"   {sum(counter.values()):4d}  {family:28s} pass {counter['pass']:4d}  fail {counter['fail']:4d}  error {counter['error']:4d}")
    print("== 执行异常（适配缺口）：")
    for message, n in errors.most_common(10):
        print(f"   {n:4d}  {message}")
    print("== 排除城际交通、酒店/房型两族，且去掉只剩天数/人数/票数的查询后，各城还有实质约束的查询数：")
    for city in CITY_CN.values():
        print(f"   {city} {meaningful[city]:3d} / {total[city]:3d}")
    print(f"   合计 {sum(meaningful.values())} / {sum(total.values())}")


def main() -> None:
    from chinatravel.symbol_verification.commonsense_constraint import func_commonsense_constraints
    from chinatravel.symbol_verification.hard_constraint import evaluate_constraints_py
    replay, case_id, method = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    if len(sys.argv) > 4 and sys.argv[4] == "--all":
        row = next(r for r in json.loads(replay.read_text(encoding="utf-8"))["results"] if r["id"] == case_id)
        survey_all(to_plan(row["results"][method]["itinerary"], row["profile"], "上海", 1))
        return
    row = next(r for r in json.loads(replay.read_text(encoding="utf-8"))["results"] if r["id"] == case_id)
    profile, result = row["profile"], row["results"][method]
    city_cn = CITY_CN[profile.get("city") or "hangzhou"]
    queries = load_queries(city_cn)
    print(f"== {city_cn} 的 ChinaTravel 查询：{len(queries)} 条（easy/medium/human）")
    families = collections.Counter(family_of(c) for q in queries for c in q["hard_logic_py"])
    print("约束族分布（全部杭州查询）：")
    for name, n in families.most_common():
        print(f"   {n:4d}  {name}")
    # 挑最接近本用例的查询：天数、人数相同，预算最接近
    people = 1 if profile.get("companions") in (None, "solo") else 2
    same = [q for q in queries if int(q["days"]) == profile["days"] and int(q["people_number"]) == people]
    same.sort(key=lambda q: abs(float(re.search(r"<=\s*([\d.]+)", " ".join(q["hard_logic_py"])).group(1)) - profile["budget"])
              if re.search(r"<=\s*([\d.]+)", " ".join(q["hard_logic_py"])) else 1e9)
    print(f"\n== 本用例 {case_id} {method}：{profile['days']} 天 {people} 人 预算 {profile['budget']}；"
          f"同天数同人数的查询 {len(same)} 条，取预算最接近的 3 条做校验")
    plan = to_plan(result["itinerary"], profile, "上海", people)
    out = Path(__file__).resolve().parent / f"plan_{case_id}_{method}.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"   plan JSON 写到 {out.name}：{sum(len(d['activities']) for d in plan['itinerary'])} 项活动")
    for q in same[:3]:
        symbolic = {"uid": q["uid"], "start_city": q["start_city"], "target_city": q["target_city"], "days": int(q["days"]),
                    "people_number": int(q["people_number"]), "hard_logic_py": q["hard_logic_py"]}
        plan["start_city"] = q["start_city"]
        print(f"\n-- 查询 {q['uid']}（{q['split']}）：{q['nature_language'][:80]}")
        results = evaluate_constraints_py(q["hard_logic_py"], plan, verbose=True)
        for constraint, ok in zip(q["hard_logic_py"], results):
            print(f"   {'通过' if ok else '未过'}  [{family_of(constraint)}]  {constraint.strip().splitlines()[-1][:90]}")
        print("   常识检查：", end="")
        passed = func_commonsense_constraints(symbolic, plan, verbose=False)
        print("全部通过" if passed else "有未通过项（见上）")


if __name__ == "__main__":
    main()
