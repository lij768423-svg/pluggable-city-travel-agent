"""命令行：uv run python -m app.cli "杭州3天，喜欢人文历史，节奏轻松，预算2000，不吃辣" [--rule] [--no-repair]"""
from __future__ import annotations

import argparse

from .geo import hm
from .graph import run_plan


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("--rule", action="store_true", help="选点用纯规则打分（消融）")
    ap.add_argument("--no-repair", action="store_true", help="关闭校验后的修复回路（消融）")
    a = ap.parse_args()
    r = run_plan(a.query, select_mode="rule" if a.rule else "llm", enable_repair=not a.no_repair)
    p = r["profile"]
    print(f"画像：{p.days}天 | {p.pace} | 兴趣 {p.interests} | 口味 {p.spice} | 预算 {p.budget:.0f} | {p.companions}")
    for d in r["itinerary"].days:
        print(f"\n第 {d.day} 天")
        for i in d.items:
            tag = "🍽" if i.kind == "meal" else "📍"
            print(f"  {hm(i.start)}-{hm(i.end)} {tag} {i.name}  [{i.travel_mode} {i.travel_min}min] ¥{i.cost:.0f}  — {i.reason}")
        print(f"  回酒店 {d.return_travel_min} 分钟" + ("；" + "；".join(d.notes) if d.notes else ""))
    rep = r["report"]
    print(f"\n校验：{'通过 ✅' if rep.passed else '未通过 ❌'}  {rep.totals}")
    for v in rep.violations:
        print(f"  [{v.severity}] {v.code} 第{v.day}天 {v.item or ''}：{v.message}")
    print("\n过程：")
    for line in r["log"]:
        print("  -", line)
    print(f"\n耗时 {r['seconds']}s；LLM {r['usage']}")


if __name__ == "__main__":
    main()
