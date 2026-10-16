"""从冻结的独立参考重建数据集对比报告与50点人工核对表，不调用LLM。"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config
from app.data import load_pois
from app.reasonableness import load_stay_references
from app.schemas import StayReference
from eval.reasonableness_eval import evaluate


def interval(low: float, high: float) -> str:
    return f"{low:g}–{high:g}"


def safe(value: object) -> str:
    return str(value).replace("|", "／").replace("\n", " ")


def comparison_rows(artifact: dict) -> list[dict]:
    original = pd.read_parquet(config.CITY_DIR / "attractions.parquet").set_index("id")
    current = load_pois()
    rows = []
    for item in artifact["places"]:
        reference = StayReference.model_validate(item)
        raw = original.loc[reference.poi_id]
        low, high = float(raw.recommendmintime) * 60, float(raw.recommendmaxtime) * 60
        direction = "偏长" if low > reference.high_min else "偏短" if high < reference.low_min else "重叠"
        gap = max(0, low - reference.high_min, reference.low_min - high)
        midpoint_delta = (low + high) / 2 - reference.typical_min
        rows.append({**reference.model_dump(), "dataset_low": low, "dataset_high": high,
                     "current_low": current[reference.poi_id].min_hours * 60,
                     "current_high": current[reference.poi_id].max_hours * 60,
                     "direction": direction, "gap": gap, "midpoint_delta": midpoint_delta,
                     "large_spread": reference.spread["max_min"] >= max(60, reference.typical_min * .5)})
    if set(original.index) != {row["poi_id"] for row in rows}:
        raise ValueError("参考与原始数据集ID未完整对应")
    return rows


def audit_selection() -> list[dict]:
    reviews = json.loads((config.BACKEND_DIR / "app/assets/poi_reviews.json").read_text(encoding="utf-8"))["places"]
    selected = [{"id": row["id"], "name": row["name"]} for row in reviews]
    pois = load_pois()
    identifiers = {row["id"] for row in selected}
    if 73 not in identifiers:
        selected.append({"id": 73, "name": pois[73].name})
        identifiers.add(73)
    counts = Counter()
    cases = json.loads((config.DATA_DIR / "quality-20261016/before.json").read_text(encoding="utf-8"))["results"]
    for case in cases:
        counts.update({item["ref_id"] for day in case["itinerary"]["days"] for item in day["items"] if item["kind"] == "poi"})
    guides = json.loads((config.BACKEND_DIR / "app/assets/guides/pilot-v1.json").read_text(encoding="utf-8"))["samples"]
    for sample in guides:
        counts.update({place["poi_id"] for place in sample["places"] if place.get("poi_id") is not None})
    for identifier, count in sorted(counts.items(), key=lambda item: (-item[1], item[0])):
        if identifier not in identifiers:
            selected.append({"id": identifier, "name": pois[identifier].name, "frequency": count})
            identifiers.add(identifier)
        if len(selected) == 50:
            break
    return selected


def build_audit(rows: list[dict], path: Path, source_path: Path) -> dict:
    reviews_path = config.BACKEND_DIR / "app/assets/poi_reviews.json"
    selected = audit_selection()
    references = {row["poi_id"]: row for row in rows}
    sources = {row["poi_id"]: row for row in json.loads(source_path.read_text(encoding="utf-8"))}
    previous = {}
    if path.exists():
        with path.open(encoding="utf-8-sig", newline="") as handle:
            previous = {int(row["poi_id"]): row for row in csv.DictReader(handle)}
    fields = ["poi_id", "name", "数据集区间", "LLM区间", "来源 URL", "原文摘录", "人工结论", "核对人", "核对日期"]
    output = []
    for entry in selected:
        identifier = entry["id"]
        row = references[identifier]
        source = sources.get(identifier, {})
        if source and source["name"] != entry["name"]:
            raise ValueError(f"来源与POI身份不匹配：{identifier}")
        old = previous.get(identifier, {})
        output.append({"poi_id": identifier, "name": entry["name"],
            "数据集区间": f"{row['dataset_low']:g}-{row['dataset_high']:g}分钟",
            "LLM区间": f"{row['low_min']}-{row['high_min']}分钟",
            "来源 URL": old.get("来源 URL") or source.get("url", ""),
            "原文摘录": old.get("原文摘录") or source.get("excerpt", ""),
            **{key: old.get(key, "") for key in ("人工结论", "核对人", "核对日期")}})
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(output)
    return {"selected": len(output), "sources": sum(bool(row["来源 URL"]) for row in output),
            "manual": sum(bool(row["人工结论"]) for row in output),
            "extra": [{"poi_id": row["id"], "name": row["name"], "frequency": row.get("frequency")}
                      for row in selected[44:]],
            "review_sha256": hashlib.sha256(reviews_path.read_bytes()).hexdigest()}


def build_report(artifact: dict, rows: list[dict], audit: dict, sources: list[dict]) -> str:
    counts = Counter(row["direction"] for row in rows)
    disjoint = counts["偏长"] + counts["偏短"]
    stats = artifact["run_stats"]
    confidence = Counter(row["confidence"] for row in rows)
    lines = ["# 停留时长独立参考与原始数据集对比（2026-10-16）", "",
        "> **LLM估计不是事实，只是独立于数据集数值的第二意见。** 下文的“偏长/偏短”指相对该估计区间完全不重叠，不能当作已证实的数据集错误率。未经过用户人工核对。", "",
        "## 数据与独立性", "",
        "- 原始数据：ChinaTravel Sandbox 2026.08.2，CC BY 4.0，杭州377条。直接读取`data/hangzhou/attractions.parquet`的`recommendmintime/recommendmaxtime`，换算分钟；该来源论文只称public information，具体时长出处未验证。",
        "- 估计输入仅有poi_id、名称、类型、已有简介、票价、开放时间；从原始景点表和已有标签读取，不经过带人工时长修订的load_pois，不给模型任何数据集时长字段，也不提供排程时长。",
        "- 已有简介本身是早期LLM标签，生成时可能受数据集影响，因此只能声称没有直接提供原时长、形成第二意见，不能声称统计或知识来源完全独立。三次来自同一模型，也不构成三个独立事实来源。",
        f"- 提示词版本：`{artifact['prompt_version']}`；SHA-256：`{artifact['prompt_sha256']}`；完整提示词和输入哈希保存在`backend/app/assets/stay_reference.json`顶部。",
        "- 每点3次，每批10点，共38×3=114批；temperature=0.7。请求包含不同independent_sample，使本地缓存键不同；仍保留LLM_CACHE=1以支持复现。",
        "- low/high/typical分别取三个样本中位数；scope/reason取与中位数数值距离最近的一次，原始三次保留在samples。confidence取三次最低等级，typical分歧达到max(60分钟,中位数的50%)再降为low。",
        "- spread记录low/high/typical各字段的max-min，max_min取三者最大值。报告“大分歧”定义为max_min≥60分钟且≥typical中位数的50%。",
        f"- 实际API请求尝试 **{stats['actual_calls']}次**，本地缓存命中 **{stats['cache_hits']}次**，有效响应批数 **{len(artifact['requests'])}**，耗时 **{stats['elapsed_seconds']:.3f}秒（{stats['elapsed_seconds']/60:.1f}分钟）**。",
        f"- 实际响应模型ID：{', '.join('`' + value + '`' for value in stats['response_models'])}；配置thinking=`{artifact['thinking']}`；生成时间`{artifact['generated_at']}`（UTC）。不更改模型配置。",
        f"- 失败记录：{len(stats['failures'])}条；脚本SDK重试=0、封装尝试=1、JSON重试=0；HTTP401/402/403/429立即停止，其他连续两错停止。检查点在`data/stay-reference-v1/checkpoint.json`；数据不完整时不发布参考。",
        "- 历史交接来源：Hindsight shared `c3858a89-37e8-412b-870f-4fb662534b1c`，用户工作约定有效日2026-10-14；homeserve `54f90e72-9a79-46a2-be35-bc9489ba717d`，记录时间2026-10-16T03:19:49Z，独立有效日期与整理日期未注明。本轮2026-10-16现场复核main起点e32e8b9及数据，不把记忆当当前机器状态。", "",
        "## 区间比较", "",
        "判定严格使用原始区间：原始下限>LLM上限为偏长；原始上限<LLM下限为偏短；边界接触算重叠。偏差排名先按不重叠的间距，再按原区间中点与LLM typical的绝对差，均为分钟。", "",
        "| 指标 | 数值 |", "|---|---:|",
        f"| 区间完全不重叠 | {disjoint}/{len(rows)}（{disjoint/len(rows):.1%}） |",
        f"| 原始数据集偏长 | {counts['偏长']} |", f"| 原始数据集偏短 | {counts['偏短']} |",
        f"| 区间重叠（含边界接触） | {counts['重叠']} |",
        f"| confidence=high / medium / low | {confidence['high']} / {confidence['medium']} / {confidence['low']} |",
        f"| 三次估计大分歧 | {sum(row['large_spread'] for row in rows)} |", "",
        "### 偏差最大前30条", "",
        "| ID | 景点 | 原始数据集 | LLM区间 / typical | 方向 / 间距 | 范围 | 模型依据 |",
        "|---:|---|---|---|---|---|---|"]
    ranked = sorted(rows, key=lambda row: (row["gap"], abs(row["midpoint_delta"])), reverse=True)
    for row in ranked[:30]:
        lines.append(f"| {row['poi_id']} | {safe(row['name'])} | {interval(row['dataset_low'],row['dataset_high'])} | {interval(row['low_min'],row['high_min'])} / {row['typical_min']} | {row['direction']} / {row['gap']:g} | {safe(row['scope'])} | {safe(row['reason'])} |")
    lines.extend(["", "### 重点例子与公开第二来源", "",
        "公开攻略也不是时间测量真值。灵隐寺本体/含飞来峰、宋城半日/整日、西溪选段/完整游览都可能造成差异；钱王祠攻略时长与LLM存在分歧，不能只凭模型认定谁对。", "",
        "| ID | 景点 | 原始数据集 | 当前排程 | LLM / typical | LLM范围 | 公开原文（未人工核对） |",
        "|---:|---|---|---|---|---|---|"])
    source_by_id = {source["poi_id"]: source for source in sources}
    row_by_id = {row["poi_id"]: row for row in rows}
    for identifier in (73, 5, 0, 36, 1, 225):
        row = row_by_id[identifier]
        source = source_by_id.get(identifier)
        quote = f"[{safe(source['excerpt'])}]({source['url']})" if source else "未找到可读原文"
        lines.append(f"| {identifier} | {safe(row['name'])} | {interval(row['dataset_low'],row['dataset_high'])} | {interval(row['current_low'],row['current_high'])} | {interval(row['low_min'],row['high_min'])} / {row['typical_min']} | {safe(row['scope'])} | {quote} |")
    lines.extend(["", "### 原始数据与当前排程审阅值", "",
        "比较报告使用原始数据，不把已有项目审阅修订误记为原表。下面列出当前排程区间已与原表不同的条目；这些审阅值也不自动视为事实，本任务未修改它们。", "",
        "| ID | 景点 | 原始数据集 | 当前排程审阅值 | LLM区间 |", "|---:|---|---|---|---|"])
    for row in rows:
        if (row["dataset_low"], row["dataset_high"]) != (row["current_low"], row["current_high"]):
            lines.append(f"| {row['poi_id']} | {safe(row['name'])} | {interval(row['dataset_low'],row['dataset_high'])} | {interval(row['current_low'],row['current_high'])} | {interval(row['low_min'],row['high_min'])} |")
    lines.extend(["", "## 大分歧或低置信度条目", "",
        "此表完整列出confidence=low或大分歧的条目，供优先人工核对。数值接近也不保证scope一致，应查看JSON中的三次scope。", "",
        "| ID | 景点 | LLM区间 / typical | low/high/typical spread | confidence | 大分歧 | 范围及依据 |",
        "|---:|---|---|---|---|---|---|"])
    for row in rows:
        if row["confidence"] == "low" or row["large_spread"]:
            spread = row["spread"]
            lines.append(f"| {row['poi_id']} | {safe(row['name'])} | {interval(row['low_min'],row['high_min'])} / {row['typical_min']} | {spread['low_min']}/{spread['high_min']}/{spread['typical_min']} | {row['confidence']} | {'是' if row['large_spread'] else '否'} | {safe(row['scope'])}；{safe(row['reason'])} |")
    lines.extend(["", "## 热门景点人工核对表", "",
        f"- 共{audit['selected']}点：`poi_reviews.json`全部44点，加用户本任务点名的钱王祠，再补`before.json`24组及`pilot-v1.json`攻略样本中出现频次最高的5个未入选点；每例/每攻略内去重，按合计频次降序、ID升序决胜。未声称是客观旅游热度排名。poi_reviews选择依据文件SHA-256：`{audit['review_sha256']}`。",
        f"- 额外6点（frequency为上述去重频次；钱王祠因任务点名加入，不按频次）：`{audit['extra']}`。",
        f"- 表：`backend/eval/stay_audit/top50.csv`，所有区间单位为分钟。本轮{audit['sources']}条有实际读取的公开页面及原文，{audit['selected']-audit['sources']}条来源空缺；已有人类结论{audit['manual']}条。",
        "- 原文优先提取攻略页的“建议游玩时间”，不拿营业时间冒充停留时间。本轮来源中29条为攻略时长，1条为中国茶叶博物馆龙井馆区官网范围介绍、无停留时长，不能据此推导数值。",
        "- 来源摘录由Codex读取，不属于人工核对。来源发布时间/有效日期无法确定时记为null，读取时间与SHA-256另见`backend/eval/stay_audit/sources.json`；历史页面不保证出行日有效。",
        "- 人工结论、核对人、核对日期均留空。用户须打开来源，检查同名/分馆、是否含周边或游船/用餐、是否只覆盖演出，再填写结论。不能把“半天/一天”机械换算为24小时制。",
        "- 可机器读取的结论：`采用LLM`、`采用数据集`、或直接填`30-60分钟`。有结论时必须同时有URL、摘录、核对人、ISO日期（YYYY-MM-DD）；不确定填`待核`或`不确定`，保持LLM参考。加载器拒绝不完整或含糊的已确认结论。",
        "- 人工参考优先，仅限显式启用独立参考的评估，不改变load_pois、service_min和poi_reviews.json。", "",
        "## 复现", "", "```sh", "cd backend",
        "uv run python scripts/estimate_stay.py --dry-run",
        "uv run python scripts/estimate_stay.py",
        "uv run python scripts/report_stay.py",
        "uv run python eval/reasonableness_eval.py ../data/quality-20261016/before.json ../data/quality-20261016/after-v3.json --stay-reference --json ../work/stay-reason-20261016.json",
        "uv run pytest -q", "```", "",
        "estimate_stay使用已有同版本检查点时不重复调用；换提示词或输入必须新指定检查点。report_stay只读取冻结的参考与来源，保留CSV中用户填写的人工结论。", "",
        "## 适用边界与后续选择", "",
        "- LLM区间有语义范围风险、知识偏差与同一供应商相关性；此结果只能诊断，不能用于论文宣称真实错误率或质量提升。独立参考目前也没有人工标定。",
        "- 选项一：仅评估（本次实现），排程结果保持当前规则；能发现疑似过长/过短，但不自动修复。",
        "- 选项二：只把用户人工核实的点用于排程；影响范围可控，仍需处理scope以及补点/餐饮/时间窗并重跑完整对照。",
        "- 选项三：人工优先、高置信LLM兜底，用于更大范围排程；覆盖更广，也更可能把模型估计错误传播给计划。",
        "- 选项四：全量替换排程为LLM参考；变化最大，可能同时改变选点、分天、交通、餐饮及违例数，必须新冻结输入并重测。若再用同一份参考评分，会重新产生同源偏差，须保留另一份独立/人工评估集。",
        "- 是否替换排程时长待用户决定。本次只在设计决策记录追加此边界，不替用户选择。", ""])
    return "\n".join(lines)


def score_report(files: list[Path], reference: Path, audit: Path) -> tuple[str, dict]:
    runs = {path.stem: {"without": evaluate(path), "with": evaluate(path, reference, audit)} for path in files}
    names = list(runs)
    lines = ["## 独立停留参考复算（2026-10-16）", "",
        "在上面的同一组24例固定选点快照上，仅给评分器传入独立参考，不重排、不联网、不调用模型。原来的调度前后比较与本次“加入维度导致的分数变化”是两种比较，分列报告。LLM估计是第二意见，未人工标定。", "",
        "```sh", "cd backend",
        "uv run python eval/reasonableness_eval.py ../data/quality-20261016/before.json ../data/quality-20261016/after-v3.json --stay-reference --json ../work/stay-reason-20261016.json",
        "uv run python scripts/report_stay.py", "```", "",
        "| 快照 | 案例数 | 停留分 | 综合不含停留 | 综合含停留 | 启用维度变化 | 停留问题 / 红线 |",
        "|---|---:|---:|---:|---:|---:|---:|"]
    for name, run in runs.items():
        enabled, baseline = run["with"], run["without"]
        stay = sum(row["scores"]["stay"] for row in enabled) / len(enabled)
        before = sum(row["overall"] for row in baseline) / len(baseline)
        after = sum(row["overall"] for row in enabled) / len(enabled)
        issues = [issue for row in enabled for issue in row["issues"] if issue["dimension"] == "stay"]
        lines.append(f"| {name} | {len(enabled)} | {stay:.1f} | {before:.1f} | {after:.1f} | {after-before:+.1f} | {len(issues)} / {sum(issue['severe'] for issue in issues)} |")
        run["summary"] = {"cases": len(enabled), "stay": stay, "overall_without": before, "overall_with": after,
                          "stay_issues": len(issues), "stay_severe": sum(issue["severe"] for issue in issues)}
    lines.extend(["", "### 24组逐例", "",
        "| 案例 | before停留 | after-v3停留 | before综合（无→有） | after-v3综合（无→有） |",
        "|---|---:|---:|---|---|"])
    original, revised = runs[names[0]], runs[names[1]]
    for before, after, base_before, base_after in zip(original["with"], revised["with"], original["without"], revised["without"], strict=True):
        if len({before["id"], after["id"], base_before["id"], base_after["id"]}) != 1:
            raise ValueError("评分案例未对齐")
        lines.append(f"| {before['id']} | {before['scores']['stay']:.1f} | {after['scores']['stay']:.1f} | {base_before['overall']:.1f}→{before['overall']:.1f} | {base_after['overall']:.1f}→{after['overall']:.1f} |")
    lines.extend(["", "### 停留扣分最多的景点", "",
        "按各次访问的停留分损失累计排序；这是诊断频次权重，会受景点出现次数影响，同时列平均每次损失。区间单位为分钟，人工结论为空时source均为llm_estimate。", ""])
    for name, run in runs.items():
        grouped = {}
        for case in run["with"]:
            for detail in case["stay_details"]:
                group = grouped.setdefault(detail["poi_id"], {"name": detail["name"], "penalties": [],
                    "actual": [], "low": detail["low_min"], "high": detail["high_min"], "source": detail["source"]})
                group["penalties"].append(detail["penalty"])
                group["actual"].append(detail["actual_min"])
        ranked = sorted(grouped.items(), key=lambda entry: sum(entry[1]["penalties"]), reverse=True)
        run["top_penalties"] = [{"poi_id": identifier, **group, "sum_penalty": sum(group["penalties"]),
                                 "mean_penalty": sum(group["penalties"])/len(group["penalties"])}
                                for identifier, group in ranked if sum(group["penalties"]) > 0]
        lines.extend([f"#### {name}", "", "| ID | 景点 | 访问次数 | 实际停留范围 | 独立参考 | 累计损失 | 平均损失 |",
                      "|---:|---|---:|---|---|---:|---:|"])
        for row in run["top_penalties"][:10]:
            lines.append(f"| {row['poi_id']} | {safe(row['name'])} | {len(row['actual'])} | {interval(min(row['actual']),max(row['actual']))} | {interval(row['low'],row['high'])} | {row['sum_penalty']:.1f} | {row['mean_penalty']:.1f} |")
    lines.extend(["", "### 覆盖、来源和解释边界", ""])
    for path, name in zip(files, names, strict=True):
        coverage = Counter()
        for row in runs[name]["with"]:
            coverage.update(row["coverage"])
        lines.append(f"- {name}快照SHA-256：`{hashlib.sha256(path.read_bytes()).hexdigest()}`；停留来源覆盖：`{dict(coverage)}`。")
    lines.extend(["- 本次新增维度权重20%，其余维度相对权重恢复为原设定；综合分下降表示检测到了偏离参考的停留，不表示调度实现回退。",
        "- scope要人工核对：例如灵隐寺本体不等于含飞来峰，宋城整日游与只看主要演出不同。低置信度与大分歧列表见`停留时长参考对比.md`，本次按用户要求全量纳入第二意见，未把不确定条目升级为事实。",
        "- before与after-v3只比较调度、分天和餐饮，固定选点且没有补点/夜游/修复回路，不能外推为完整产品质量；本任务没有修改排程用时长。",
        "- 逐次访问、逐案例、总分及来源的冻结明细保存在`data/stay-reference-v1/reasonableness.json`；该数据不进git，可用本节命令复现。", ""])
    return "\n".join(lines), runs


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, default=config.BACKEND_DIR / "app/assets/stay_reference.json")
    parser.add_argument("--sources", type=Path, default=config.BACKEND_DIR / "eval/stay_audit/sources.json")
    parser.add_argument("--audit", type=Path, default=config.BACKEND_DIR / "eval/stay_audit/top50.csv")
    parser.add_argument("--output", type=Path, default=config.REPO_DIR / "docs/停留时长参考对比.md")
    parser.add_argument("--skip-scores", action="store_true", help="只生成时长对比与核对表")
    args = parser.parse_args()
    artifact = json.loads(args.reference.read_text(encoding="utf-8"))
    load_stay_references(args.reference, None, load_pois())
    rows = comparison_rows(artifact)
    audit = build_audit(rows, args.audit, args.sources)
    sources = json.loads(args.sources.read_text(encoding="utf-8"))
    args.output.write_text(build_report(artifact, rows, audit, sources), encoding="utf-8")
    if not args.skip_scores:
        files = [config.DATA_DIR / "quality-20261016" / name for name in ("before.json", "after-v3.json")]
        section, scores = score_report(files, args.reference, args.audit)
        score_path = config.DATA_DIR / "stay-reference-v1/reasonableness.json"
        score_path.parent.mkdir(parents=True, exist_ok=True)
        score_path.write_text(json.dumps(scores, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        document = config.REPO_DIR / "docs/计划合理性指标.md"
        text = document.read_text(encoding="utf-8")
        begin, end = "<!-- stay-reference-results:start -->", "<!-- stay-reference-results:end -->"
        block = f"{begin}\n{section}\n{end}\n\n"
        if begin in text and end in text:
            prefix, rest = text.split(begin, 1)
            _, suffix = rest.split(end, 1)
            text = prefix + block.rstrip() + suffix
        else:
            text = text.replace("## 局限\n", block + "## 局限\n", 1)
        document.write_text(text, encoding="utf-8")
    print(json.dumps({"pois": len(rows), "directions": dict(Counter(row['direction'] for row in rows)),
                      "audit": audit, "report": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
