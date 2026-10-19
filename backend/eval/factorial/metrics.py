from __future__ import annotations

from collections import Counter
import itertools
import statistics

from .common import GROUPS, audit_rows


def itinerary_metrics(result: dict, places: dict) -> dict:
    itinerary, profile, report = result["itinerary"], result["profile"], result["report"]
    rides, regions = [], []
    travel_total = walking = duplicate = 0
    visited_interests = set()
    for day in itinerary.days:
        groups = []
        previous_region = None
        for index, item in enumerate(day.items):
            travel_total += item.travel_min
            if index and item.travel_mode not in {"步行", "原地"}:
                rides.append(item.travel_min)
            if item.kind == "poi" and item.ref_id in places:
                place = places[item.ref_id]
                visited_interests.update(place.interests)
                if place.visit_group:
                    groups.append(place.visit_group)
                if previous_region and place.region_id:
                    regions.append(previous_region != place.region_id)
                previous_region = place.region_id or None
        duplicate += len(groups) - len(set(groups))
        travel_total += day.return_travel_min
        walking += day.walking_m
    requested = set(profile.interests)
    return {"internal_max_ride_min": max(rides, default=0), "internal_ride_min": sum(rides),
            "internal_ride_violations": sum(minutes > 25 for minutes in rides), "total_travel_min": travel_total,
            "walking_m": walking, "cross_region_count": sum(regions), "known_region_connections": len(regions),
            "continuous_duplicate_count": duplicate, "poi_count": sum(item.kind == "poi" for day in itinerary.days for item in day.items),
            "interest_covered": len(requested & visited_interests), "interest_requested": len(requested),
            "interest_coverage": len(requested & visited_interests) / len(requested) if requested else None,
            "idle_min": report.totals.get("idle_min"), "cost_total": report.totals.get("total")}


def summarize(inputs: dict, rows: list[dict], split: str | None = None) -> dict:
    audit_rows(inputs, rows)
    selected = [row for row in rows if split is None or row["split"] == split]
    groups = {}
    for name in GROUPS:
        entries = [row for row in selected if row["group"] == name]
        succeeded = [row for row in entries if row["status"] == "ok"]
        denominator = len(entries)
        counts = {"generated": len(succeeded), "constraint_passed": sum(row["report"]["constraint_status"] == "passed" for row in succeeded),
                  "arrangement_complete": sum(row["report"]["arrangement_status"] == "complete" for row in succeeded),
                  "evidence_verified": sum(row["report"]["evidence_status"] == "verified" for row in succeeded),
                  "complete_executable": sum(row["report"]["complete_executable"] for row in succeeded)}
        columns = set().union(*(row["metrics"].keys() for row in succeeded)) if succeeded else set()
        descriptive = {}
        for column in sorted(columns):
            values = [row["metrics"][column] for row in succeeded if row["metrics"][column] is not None]
            descriptive[column] = {"mean": statistics.mean(values) if values else None, "n": len(values)}
        groups[name] = {"requests": denominator, "errors": denominator - len(succeeded),
                       "counts": counts, "rates": {key: value / denominator if denominator else None for key, value in counts.items()},
                       "descriptive_on_generated": descriptive,
                       "hard_codes": dict(Counter(issue["code"] for row in succeeded for issue in row["report"]["violations"] if issue["severity"] == "hard"))}
    paired = {}
    for first, second in itertools.combinations(GROUPS, 2):
        left = {row["case_id"]: row for row in selected if row["group"] == first}
        right = {row["case_id"]: row for row in selected if row["group"] == second}
        eligible = [identifier for identifier in left if left[identifier]["status"] == right[identifier]["status"] == "ok"
                    and left[identifier]["report"]["complete_executable"] and right[identifier]["report"]["complete_executable"]]
        values = [right[identifier]["metrics"]["interest_coverage"] - left[identifier]["metrics"]["interest_coverage"]
                  for identifier in eligible if left[identifier]["metrics"]["interest_coverage"] is not None
                  and right[identifier]["metrics"]["interest_coverage"] is not None]
        paired[f"{first}__{second}"] = {"complete_pairs": eligible, "n": len(eligible),
            "interest_coverage_delta_mean": statistics.mean(values) if values else None, "interest_n": len(values)}
    regressions = []
    by_key = {(row["case_id"], row["group"]): row for row in selected}
    for case in inputs["cases"]:
        if split is not None and case["split"] != split:
            continue
        before, after = by_key[(case["id"], "baseline")], by_key[(case["id"], "full")]
        if before["status"] == "ok" and before["report"]["constraint_status"] == "passed" and (
                after["status"] != "ok" or after["report"]["constraint_status"] != "passed"):
            regressions.append(case["id"])
    return {"split": split or "all", "groups": groups, "paired_preference": paired,
            "hard_regressions_baseline_to_full": regressions,
            "promotion": "not_established_without_verified_navigation_and_real_blind_feedback"}


def markdown(summary: dict, manifest: dict) -> str:
    lines = ["# 阶段四：冻结二因素离线回放", "", f"路线来源：{manifest['source']}；模型选点：{manifest['model_selection']}。",
        f"餐厅候选预算：每案例 {manifest['restaurant_limit']} 家；基线策略版本：{manifest['baseline_revision']}。",
        "这是工程对照，不是生产满意度、真实交通准确率或用户盲评结论。固定数据、共同分数与选点器、最终校验器；候选与餐厅入口均锁池。",
        "历史基线仅指冻结的六个路线/餐饮/修复模块，其他公共输入使用同一当前版本，不称为过去网页的逐字节复现。", "",
        "| 组别 | 全部请求 | 生成失败 | 硬约束通过 | 安排完整 | 证据核验 | 完整可执行 |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for name, group in summary["groups"].items():
        counts = group["counts"]
        lines.append(f"| {name} | {group['requests']} | {group['errors']} | {counts['constraint_passed']} | {counts['arrangement_complete']} | {counts['evidence_verified']} | {counts['complete_executable']} |")
    lines += ["", "所有比例的分母均为该组全部请求，包括生成失败。下表仅描述生成成功的案例，各均值附有效数量，不能跨组只挑成功案例证明收益。", "",
              "| 组别 | 内部乘车分钟均值（n） | 内部最长乘车均值（n） | 景点数均值（n） | 兴趣覆盖均值（n） |", "|---|---:|---:|---:|---:|"]
    for name, group in summary["groups"].items():
        cells = []
        for key in ("internal_ride_min", "internal_max_ride_min", "poi_count", "interest_coverage"):
            value = group["descriptive_on_generated"].get(key, {"mean": None, "n": 0})
            cells.append("缺失（0）" if value["mean"] is None else f"{value['mean']:.3f}（{value['n']}）")
        lines.append(f"| {name} | " + " | ".join(cells) + " |")
    pair = summary["paired_preference"]["baseline__full"]
    lines += ["", f"基线与完整改进组均达到三维完整状态的配对数：**{pair['n']}**。",
              "没有完整配对时不输出偏好改进分；估算路线不算真实路线已核验。兴趣覆盖不是独立用户满意度。",
              "新硬失败案例（同一评价器，baseline → full）：" + ("、".join(summary["hard_regressions_baseline_to_full"]) or "无") + "。", "",
              "## 尚不能下的结论", "",
              "- 没有真实用户反馈，不填写确认率、盲评胜率、满意度或 SUS。",
              "- 回放耗时不包含原始模型推理时间，不能用来宣布模型提速。",
              "- 规则选点回放不是 LLM 选点实验；历史模型输出需要按画像和候选哈希另行导入。",
              "- 交通减少可能来自删点，需同时检查景点数、餐饮与失败原因。",
              "- 停留时长不纳入独立质量主指标，避免用排程输入自证；独立模型参考仍只属第二意见。",
              "- 已查看的留出案例不能再作为反复调参后的全新验证集。", ""]
    return "\n".join(lines)
