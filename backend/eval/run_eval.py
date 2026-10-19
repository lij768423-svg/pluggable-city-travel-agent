"""MVP 对比评测：同一画像下比较 4 种方法，全部用同一个校验器打分。

  A 纯 LLM（baselines/pure_llm）
  B 本系统完整版（LLM 选点 + 调度 + 校验修复）
  C 本系统去掉修复回路（消融）
  D 本系统规则选点（消融：去掉 LLM 选点）

公平性：每条用例只解析一次画像，四种方法拿到同一个 UserProfile；LLM 设置相同（模型、思考模式、温度）。
用法：
  TTA_ROUTE_SNAPSHOT=/absolute/snapshot.json uv run python eval/run_eval.py
  uv run python eval/run_eval.py --no-amap    # 明确使用估算对照，仍禁止所有在线调用
输出：eval/results.md（汇总表）、eval/results.json（逐条明细）
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

EVAL_DIR = Path(__file__).resolve().parent


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", action="store_true", help="复用 LLM 缓存")
    ap.add_argument("--only", nargs="*", help="只跑指定用例 id")
    ap.add_argument("--no-amap", action="store_true", help="跳过高德真实路程的独立校验")
    ap.add_argument("--output", type=Path, help="保存到独立目录，默认按本次时间创建，不覆盖旧实验")
    ap.add_argument("--historic-restaurants", action="store_true", help="明确只评测历史餐厅库，不代表在线餐饮提供器")
    a = ap.parse_args()
    os.environ["TTA_OFFLINE"] = "1"
    os.environ["LLM_CACHE"] = "1"
    output = a.output or EVAL_DIR / "runs" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if output.exists():
        ap.error("输出目录已存在，请换一个目录以保留旧实验")
    if not a.no_amap and not Path(os.environ.get("TTA_ROUTE_SNAPSHOT", "missing-route-snapshot")).is_file():
        ap.error("离线评测需通过 TTA_ROUTE_SNAPSHOT 指定已授权保存的路线快照；缺失时不会联网或退回估算")
    if not a.historic_restaurants and not Path(os.environ.get("TTA_RESTAURANT_SNAPSHOT", "missing-restaurant-snapshot")).is_file():
        ap.error("需指定 TTA_RESTAURANT_SNAPSHOT，或显式使用 --historic-restaurants 仅评历史库")

    from app import config
    from app.data import load_pois, tag_source
    from app.graph import run_plan
    from app.llm import get_llm
    from app.nodes.parse import parse_profile
    from app.amap import get_router
    from app.data import load_restaurants
    from app.geo import travel
    from app.nodes.validate import FEASIBILITY, validate_itinerary
    from baselines.pure_llm import run_pure_llm

    router = None if a.no_amap else get_router()
    rests = load_restaurants()
    legs: dict[tuple, dict] = {}  # 去重后的路段：估算 vs 高德真实

    def judge(it, prof):
        """主口径：所有方法一律用高德真实路线、按该用例画像的出行方式校验；无高德时退回估算模型。"""
        return validate_itinerary(it, prof, travel_fn=router.travel_fn(prof.transport) if router else None, restaurants=rests)

    def est_view(it, prof):
        rep = validate_itinerary(it, prof)  # 旧口径：估算模型（仅供对照，含同源偏差）
        return {"passed": rep.passed, "hard_excl_unknown": sum(1 for v in rep.violations if v.severity == "hard" and v.code != "unknown_place")}

    def independent(it, prof):
        """交叉检查：同一行程分别按打车 / 公交两种出行方式的真实路线校验。"""
        if router is None:
            return {}
        out = {}
        for sc in ("taxi", "transit"):
            try:
                rep = validate_itinerary(it, prof, travel_fn=router.travel_fn(sc))
                out[sc] = {"passed": rep.passed,
                           "feasible_excl_unknown": not any(v.code in FEASIBILITY - {"unknown_place"} for v in rep.violations),
                           "hard_excl_unknown": sum(1 for v in rep.violations if v.severity == "hard" and v.code != "unknown_place"),
                           "codes": dict(Counter(v.code for v in rep.violations if v.severity == "hard"))}
            except Exception as e:  # 配额/网络问题如实记录
                out[sc] = {"error": str(e)[:200]}
        # 记录每段路的估算与真实值（用于标定估算模型）
        for d in it.days:
            pos = (prof.hotel.lat, prof.hotel.lon)
            stops = []
            for i in d.items:
                place = pois.get(i.ref_id) if i.kind == "poi" else rests.get(i.ref_id)
                if place is not None:
                    stops.append((place.lat, place.lon))
            for nxt in stops + ([(prof.hotel.lat, prof.hotel.lon)] if stops else []):
                key = (round(pos[0], 5), round(pos[1], 5), round(nxt[0], 5), round(nxt[1], 5))
                if key not in legs and key[:2] != key[2:]:
                    try:
                        legs[key] = {"est": travel(*pos, *nxt), "real": router.leg_detail(*pos, *nxt)}
                    except Exception:
                        pass
                pos = nxt
        return out

    pois = load_pois()
    cases = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((EVAL_DIR / "cases").glob("*.json"))]
    if a.only:
        cases = [c for c in cases if c["id"] in a.only]

    def metrics(it, prof, report, seconds, usage):
        poi_items = [i for d in it.days for i in d.items if i.kind == "poi"]
        known = [i for i in poi_items if i.ref_id in pois]
        hit = [i for i in known if set(pois[i.ref_id].interests) & set(prof.interests)] if prof.interests else known
        all_items = [i for d in it.days for i in d.items]
        return {
            "passed": report.passed,
            "constraint_status": report.constraint_status,
            "arrangement_status": report.arrangement_status,
            "evidence_status": report.evidence_status,
            "complete_executable": report.complete_executable,
            "feasible": not any(v.code in FEASIBILITY for v in report.violations),
            # 更公平的口径：不计"库外地点"（数据集未收录不等于不存在），只看时间/路程/重复等问题
            "feasible_excl_unknown": not any(v.code in FEASIBILITY - {"unknown_place"} for v in report.violations),
            "hard_excl_unknown": sum(1 for v in report.violations if v.severity == "hard" and v.code != "unknown_place"),
            "hard": len(report.hard),
            "soft": len(report.violations) - len(report.hard),
            "codes": dict(Counter(v.code for v in report.violations)),
            "items": len(all_items), "pois": len(poi_items),
            "unknown_rate": (sum(1 for i in all_items if i.ref_id is None) / len(all_items)) if all_items else 0.0,
            "interest_hit": (len(hit) / len(known)) if known else 0.0,
            "cost_total": report.totals.get("total", 0), "seconds": round(seconds, 2),
            "llm_calls": usage.get("calls", 0), "cached_calls": usage.get("cached_calls", 0),
            "tokens": usage.get("total_tokens", 0),
        }

    rows = []
    for c in cases:
        base = get_llm().fork()
        prof = parse_profile(c["query"], base)
        form = prof.model_dump()
        rec = {"id": c["id"], "query": c["query"], "note": c["note"], "profile": form, "results": {}}
        print(f"[{c['id']}] {c['query']}")

        llm = get_llm().fork()
        t0 = time.time()
        try:
            it = run_pure_llm(prof, llm)
            u = llm.usage.as_dict()
            rep = judge(it, prof)
            rec["results"]["A_pure_llm"] = metrics(it, prof, rep, time.time() - t0, u) | {
                "est": est_view(it, prof), "real": independent(it, prof),
                "violations": [v.model_dump() for v in rep.violations],
                "itinerary": it.model_dump()}
        except Exception as e:  # 输出无法解析也算一次失败，如实记录
            rec["results"]["A_pure_llm"] = {"error": str(e)[:300], "passed": False, "feasible": False}

        src = "amap" if router else "estimate"
        for key, kw in [("B_full", dict(select_mode="llm", enable_repair=True, travel_source=src)),
                        ("C_no_repair", dict(select_mode="llm", enable_repair=False, travel_source=src)),
                        ("D_rule_select", dict(select_mode="rule", enable_repair=True, travel_source=src)),
                        ("E_estimate_plan", dict(select_mode="llm", enable_repair=True, travel_source="estimate"))]:
            try:
                r = run_plan("", form, **kw)
                rep = judge(r["itinerary"], prof)
                rec["results"][key] = metrics(r["itinerary"], prof, rep, r["seconds"], r["usage"]) | {
                    "experiment_config": kw,
                    "retrieval": r.get("retrieval_report", {}), "candidate_metrics": r.get("candidate_metrics", {}),
                    "meals": r.get("meal_metrics", {}),
                    "repair_rounds": r.get("repair_round", 0), "travel": r.get("travel_stats", {}),
                    "est": est_view(r["itinerary"], prof), "real": independent(r["itinerary"], prof),
                    "violations": [v.model_dump() for v in rep.violations],
                    "itinerary": r["itinerary"].model_dump()}
            except Exception as e:
                rec["results"][key] = {"error": str(e)[:300], "passed": False, "feasible": False,
                                        "experiment_config": kw}
        for k, v in rec["results"].items():
            rl = v.get("real", {})
            print(f"   {k:14s} passed={v.get('passed')} feasible={v.get('feasible')} hard={v.get('hard')} "
                  f"unknown={v.get('unknown_rate', 0):.2f} hit={v.get('interest_hit', 0):.2f} {v.get('seconds')}s "
                  f"| 高德打车 {rl.get('taxi', {}).get('passed')} 公交 {rl.get('transit', {}).get('passed')} {v.get('error', '')}")
        rows.append(rec)

    methods = ["A_pure_llm", "B_full", "C_no_repair", "D_rule_select", "E_estimate_plan"]
    names = {"A_pure_llm": "A 纯 LLM", "B_full": "B 本系统（完整，真实路线排程）", "C_no_repair": "C 去掉修复回路",
             "D_rule_select": "D 规则选点", "E_estimate_plan": "E 估算模型排程（旧版 B）"}

    def agg(m):
        rs = [r["results"][m] for r in rows]
        ok = [x for x in rs if "error" not in x]
        mean = lambda k: statistics.mean(x[k] for x in ok) if ok else float("nan")  # noqa: E731
        codes = Counter()
        for x in ok:
            codes.update(x["codes"])
        return {"n": len(rs), "errors": len(rs) - len(ok),
                "pass_rate": sum(1 for x in rs if x.get("passed")) / len(rs),
                "constraint_pass_rate": sum(x.get("constraint_status") == "passed" for x in rs) / len(rs),
                "arrangement_complete_rate": sum(x.get("arrangement_status") == "complete" for x in rs) / len(rs),
                "evidence_verified_rate": sum(x.get("evidence_status") == "verified" for x in rs) / len(rs),
                "complete_executable_rate": sum(x.get("complete_executable") for x in rs) / len(rs),
                "feasible_rate": sum(1 for x in rs if x.get("feasible")) / len(rs),
                "feasible_excl_rate": sum(1 for x in rs if x.get("feasible_excl_unknown")) / len(rs),
                "hard_excl_mean": mean("hard_excl_unknown"),
                "hard_mean": mean("hard"), "unknown_rate": mean("unknown_rate"), "interest_hit": mean("interest_hit"),
                "pois_mean": mean("pois"), "seconds_mean": mean("seconds"), "tokens_mean": mean("tokens"),
                "cached_calls": sum(x["cached_calls"] for x in ok), "codes": dict(codes.most_common()),
                "real": {sc: real_agg(rs, sc) for sc in ("taxi", "transit")},
                "est_pass_rate": sum(1 for x in ok if x.get("est", {}).get("passed")) / len(rs),
                "live_calls_mean": statistics.mean(x.get("travel", {}).get("live_calls", 0) for x in ok) if ok else 0,
                "approx_legs_total": sum(x.get("travel", {}).get("approx_legs", 0) for x in ok)}

    def real_agg(rs, sc):
        xs = [x.get("real", {}).get(sc) for x in rs]
        xs = [x for x in xs if x and "error" not in x]
        if not xs:
            return {}
        codes = Counter()
        for x in xs:
            codes.update(x["codes"])
        return {"n": len(xs), "pass_rate": sum(x["passed"] for x in xs) / len(rs),
                "feasible_excl_rate": sum(x["feasible_excl_unknown"] for x in xs) / len(rs),
                "hard_excl_mean": statistics.mean(x["hard_excl_unknown"] for x in xs), "codes": dict(codes.most_common())}

    def calibration():
        """估算模型 vs 高德真实路程（按估算模型的方式分组）。"""
        out = {}
        for mode in ("步行", "公交/打车"):
            g = [v for v in legs.values() if v["est"][1] == mode]
            if not g:
                continue
            est = [v["est"][0] for v in g]
            if mode == "步行":
                g = [v for v in g if v["real"]["walk"]["min"] is not None]  # 实际不可步行（如隔河）的另计
                if not g:
                    continue
                est = [v["est"][0] for v in g]
                real = [v["real"]["walk"]["min"] for v in g]
                out[mode] = {"n": len(g), "est_mean": statistics.mean(est), "real_walk_mean": statistics.mean(real),
                             "mae": statistics.mean(abs(e - r) for e, r in zip(est, real)),
                             "real_slower_rate": sum(r > e for e, r in zip(est, real)) / len(g)}
            else:
                g2 = [v for v in g if v["real"].get("drive")]
                taxi = [v["real"]["drive"]["min"] + 5 for v in g2]
                tr = [v["real"]["transit"]["min"] for v in g2 if v["real"].get("transit")]  # 无公交方案的不计
                e2 = [v["est"][0] for v in g2]
                e3 = [v["est"][0] for v in g2 if v["real"].get("transit")]
                out[mode] = {"n": len(g), "n_ride": len(g2), "est_mean": statistics.mean(e2) if e2 else None,
                             "taxi_mean": statistics.mean(taxi) if taxi else None,
                             "transit_mean": statistics.mean(tr) if tr else None,
                             "mae_taxi": statistics.mean(abs(e - r) for e, r in zip(e2, taxi)) if taxi else None,
                             "mae_transit": statistics.mean(abs(e - r) for e, r in zip(e3, tr)) if tr else None,
                             "transit_slower_rate": sum(r > e for e, r in zip(e3, tr)) / len(tr) if tr else None,
                             "n_walk_in_reality": len(g) - len(g2)}
        return out

    summary = {m: agg(m) for m in methods}
    calib = calibration() if router else {}
    meta = {"run_at": datetime.now(timezone.utc).isoformat(), "model": config.LLM_MODEL, "thinking": config.LLM_THINKING,
            "cache": True, "offline": True, "route_snapshot": os.environ.get("TTA_ROUTE_SNAPSHOT"),
            "restaurant_snapshot": os.environ.get("TTA_RESTAURANT_SNAPSHOT"), "historic_restaurants_only":a.historic_restaurants,
            "cases": len(rows), "tag_source": tag_source(), "data": "ChinaTravel Sandbox 2026.08.2 杭州 + 2026-10-14点位审计",
            "amap": {"calls": router.calls, "cache_hits": router.cache_hits, "legs": len(legs)} if router else None,
            "calibration": calib}
    output.mkdir(parents=True)
    (output / "results.json").write_text(json.dumps({"meta": meta, "summary": summary, "cases": rows},
                                                      ensure_ascii=False, indent=1, default=str), encoding="utf-8")

    pct = lambda x: f"{x * 100:.0f}%"  # noqa: E731
    L = ["# MVP 对比评测结果", "",
         f"- 运行时间：{meta['run_at']}；模型：{meta['model']}（思考 {meta['thinking']}）；严格离线回放；缓存耗时不代表在线性能",
         f"- 用例：{len(rows)} 条自建用例（`eval/cases/`），每条只解析一次画像，各方法共用同一画像与同一校验器",
         ("- **主口径：所有方法一律按高德真实路线、用该用例画像的出行方式（默认打车）校验**；\"估算口径\"列是旧的同源口径，仅作对照"
          if router else "- 未启用高德：主口径为估算模型（含同源偏差）"),
         f"- 景点标签来源：{meta['tag_source']}；数据：{meta['data']}", "",
         "| 方法 | 硬约束通过率 | 可行性通过率 | 可行性（不计库外） | 平均硬违例 | 平均硬违例（不计库外） | 估算口径通过率 | 库外地点占比 | 兴趣命中率* | 平均景点数 | 平均耗时(s) | 平均 tokens | 高德联网/兜底 | 出错 |",
         "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for m in methods:
        s = summary[m]
        L.append(f"| {names[m]} | {pct(s['pass_rate'])} | {pct(s['feasible_rate'])} | {pct(s['feasible_excl_rate'])} | "
                 f"{s['hard_mean']:.2f} | {s['hard_excl_mean']:.2f} | {pct(s['est_pass_rate'])} | "
                 f"{pct(s['unknown_rate'])} | {pct(s['interest_hit'])} | {s['pois_mean']:.1f} | {s['seconds_mean']:.1f} | "
                 f"{s['tokens_mean']:.0f} | {s['live_calls_mean']:.0f} 次 / {s['approx_legs_total']} 段 | {s['errors']} |")
    if router:
        L += ["", "## 交叉检查：两种出行方式的真实路线", "",
              "同一份行程，分别按\"估算模型\"\"高德打车（驾车时长 + 5 分钟叫车）\"\"高德公交/地铁\"重新校验；真实步行距离 ≤ 1.5 km 的路段一律按高德步行时间。按打车规划的行程在公交下不通过属于预期。",
              "", "| 方法 | 估算模型 通过率 | 高德打车 通过率 | 高德公交 通过率 | 打车 可行（不计库外） | 公交 可行（不计库外） | 打车 平均硬违例 | 公交 平均硬违例 |",
              "|---|---|---|---|---|---|---|---|"]
        for m in methods:
            s0, rt, rr = summary[m], summary[m]["real"].get("taxi", {}), summary[m]["real"].get("transit", {})
            f = lambda d, k, fmt=pct: fmt(d[k]) if d.get(k) is not None else "—"  # noqa: E731
            L.append(f"| {names[m]} | {pct(s0['est_pass_rate'])} | {f(rt, 'pass_rate')} | {f(rr, 'pass_rate')} | "
                     f"{f(rt, 'feasible_excl_rate')} | {f(rr, 'feasible_excl_rate')} | "
                     f"{f(rt, 'hard_excl_mean', lambda x: f'{x:.2f}')} | {f(rr, 'hard_excl_mean', lambda x: f'{x:.2f}')} |")
        L += ["", "真实路程下的硬违例类型：", ""]
        for m in methods:
            for sc, nm in (("taxi", "打车"), ("transit", "公交")):
                c = summary[m]["real"].get(sc, {}).get("codes", {})
                L.append(f"- **{names[m]} · {nm}**：" + ("、".join(f"{k}×{v}" for k, v in c.items()) or "无"))
        w, r = calib.get("步行", {}), calib.get("公交/打车", {})
        L += ["", "### 估算模型标定（去重后的全部路段）", "",
              "| 估算方式 | 路段数 | 估算均值(分) | 高德真实均值(分) | 平均绝对误差(分) | 真实比估算慢的比例 |", "|---|---|---|---|---|---|"]
        if w:
            L.append(f"| 步行 | {w['n']} | {w['est_mean']:.1f} | 步行 {w['real_walk_mean']:.1f} | {w['mae']:.1f} | {pct(w['real_slower_rate'])} |")
        if r:
            L.append(f"| 公交/打车 | {r['n']}（其中 {r['n_walk_in_reality']} 段实际步行可达） | {r['est_mean']:.1f} | "
                     f"打车 {r['taxi_mean']:.1f} / 公交 {r['transit_mean']:.1f} | 打车 {r['mae_taxi']:.1f} / 公交 {r['mae_transit']:.1f} | "
                     f"公交比估算慢 {pct(r['transit_slower_rate'])} |")
        L.append(f"\n高德在线调用 {router.calls} 次；已指定快照，不会补拉缺失路段。")

    L += ["", "## 违例类型分布（全部用例合计）", ""]
    for m in methods:
        L.append(f"- **{names[m]}**：" + ("、".join(f"{k}×{v}" for k, v in summary[m]["codes"].items()) or "无"))
    L += ["", "## 逐条结果（硬违例数 / 是否通过）", "", "| 用例 | 需求 | " + " | ".join(names[m] for m in methods) + " |",
          "|---|---|" + "---|" * len(methods)]
    for r in rows:
        cells = []
        for m in methods:
            x = r["results"][m]
            cells.append("出错" if "error" in x else f"{x['hard']} / {'✓' if x['passed'] else '✗'}")
        L.append(f"| {r['id']} | {r['query']} | " + " | ".join(cells) + " |")
    L += ["", "## 解读须知（写论文时必须保留）", "",
          "- 样本只有 10 条自建用例，属于 MVP 冒烟级评测，**不足以支撑统计显著性结论**；正式实验按开题计划扩充到 30–50 条并加入 ChinaTravel 改写用例。",
          "- \"可行性（不计库外）\"把库外地点视为可行，只看营业时间、交通时间、时间顺序、重复等问题，是对基线更宽松的口径。",
          "- \"库外地点\"指名称匹配不到 ChinaTravel 杭州数据的地点（匹配规则从宽），**不等于幻觉**：很多是真实存在但数据集未收录的店。基线因此在\"可行性\"上天然吃亏，论文中需单独讨论这一点。",
          "- *兴趣命中率用本项目的 LLM 增强标签计算，而本系统检索也使用同一套标签，存在**循环评估偏差**；只能作参考，个性化效果应以后续人工盲评与 LLM 成对比较为准。",
          "- 主口径用高德真实路线校验；B/C/D 排程时也使用高德真实路线（打车模式下求解矩阵全为真实值；公交模式下求解矩阵部分用标定模型近似、时间线逐段用真实值）。E 用旧估算模型排程，用来量化\"改用真实路线\"本身的作用。",
          "- 高德联网/兜底：单次规划平均联网调用次数 / 定稿行程中因超额或失败而改用标定模型的路段总数（应为 0 或很小）。",
          "- 高德真实路程是查询时刻的路线规划结果（未指定出发时间，不含实时拥堵与排队），打车按驾车时长 + 5 分钟叫车估计。",
          "- 关闭缓存时各方法独立调用 LLM（温度 0.2），重复运行结果会有波动。",
          "- 画像解析（每条用例 1 次 LLM 调用）在四种方法之外单独进行，**不计入**表中各方法的耗时与 tokens；B/C 的 tokens 来自选点调用，A 来自整份行程生成，D 不调用 LLM。",
          "- 本系统耗时中包含 OR-Tools 求解时限（每天 1 秒，GLS 会用满时限），所以 D 的耗时约等于天数 × 1 秒。"]
    (output / "results.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:14]))


if __name__ == "__main__":
    main()
