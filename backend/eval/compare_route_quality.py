from __future__ import annotations

import argparse
import json
from pathlib import Path


def metrics(rows: list[dict]) -> dict[str, float]:
    return {
        "硬约束违例": sum(item["severity"] == "hard" for row in rows for item in row["report"]["violations"]),
        "空白天数": sum(item["code"] == "empty_day" for row in rows for item in row["report"]["violations"]),
        "已落实餐次": sum(row["report"]["totals"]["covered_meals"] for row in rows),
        "游览地点数": sum(row["visits"] for row in rows),
        "交通分钟": sum(row["travel_min"] for row in rows),
        "空档分钟": sum(row["report"]["totals"]["idle_min"] for row in rows),
        "运行秒数": round(sum(row["seconds"] for row in rows), 3),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    before, after = [json.loads(path.read_text()) for path in (args.before, args.after)]
    for document in (before, after):
        normalized = {}
        for path, digest in document["data_sha256"].items():
            label = path
            for prefix in ("data/hangzhou/", "backend/app/assets/"):
                if prefix in path:
                    label = prefix + path.split(prefix, 1)[1]
                    break
            if label in normalized:
                raise SystemExit("拒绝比较：数据文件标签重复")
            normalized[label] = digest
        document["data_sha256"] = normalized
    for field in ("cases_sha256", "data_sha256", "scope"):
        if before[field] != after[field]:
            raise SystemExit(f"拒绝比较：{field} 不一致")
    old = {row["id"]: row for row in before["results"]}
    new = {row["id"]: row for row in after["results"]}
    if old.keys() != new.keys() or len(old) != len(before["results"]) or len(new) != len(after["results"]):
        raise SystemExit("拒绝比较：案例缺失或重复")
    first, second = metrics(list(old.values())), metrics(list(new.values()))
    lines = ["# 路线质量固定选点评测 · 2026-10-16", "",
             f"共 {len(old)} 组固定画像与固定选点；两侧数据文件哈希与案例哈希一致。",
             "仅比较分天、排程与餐饮。使用本地地点数据及相同交通估算模型，未联网、未调用模型；不包含修复回路、夜游补点或用户盲评。不是完整产品满意度或真实公交耗时结论。", "",
             "| 指标（所有案例合计） | 修改前 | 修改后 |", "|---|---:|---:|"]
    for key in first:
        lines.append(f"| {key} | {first[key]:g} | {second[key]:g} |")
    lines += ["", "## 逐案例", "", "| 案例 | 交通分钟 前→后 | 游览数 前→后 | 餐次 前→后 | 硬违例 前→后 |",
              "|---|---:|---:|---:|---:|"]
    for identifier, left in old.items():
        right = new[identifier]
        left_metrics, right_metrics = metrics([left]), metrics([right])
        cells = [f"{left_metrics[key]:g} → {right_metrics[key]:g}" for key in ("交通分钟", "游览地点数", "已落实餐次", "硬约束违例")]
        lines.append(f"| {identifier} | " + " | ".join(cells) + " |")
    regressions = [identifier for identifier in old if new[identifier]["travel_min"] > old[identifier]["travel_min"]]
    lines += ["", "## 边界与待验收", "",
              "- 启发式权重尚未用用户偏好数据标定；交通减少可能伴随删点，应同时查看地点数与用餐覆盖。",
              "- 部分方案为了保留较长的核心游览或填补短日行程，会增加交通；不能概括为每个案例都改善。",
              "- 交通增加的案例：" + ("、".join(regressions) or "无") + "。",
              "- 园内预留餐次仍不计作已核验餐厅；开放、排队、内部步行等数据不完整的限制保持不变。",
              "- 实际导航与真实用户评价需独立验证；本报告不替代 P4 盲评。", ""]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("\n".join(lines))
    print(json.dumps({"before": first, "after": second, "travel_increased_cases": regressions}, ensure_ascii=False))


if __name__ == "__main__":
    main()
