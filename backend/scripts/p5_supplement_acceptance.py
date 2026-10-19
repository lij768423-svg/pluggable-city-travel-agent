"""P5 私网真实小额验收：隔离数据目录＋叠加已发布补充目录，经 MCP 业务层走真实高德路线；服务端模型 0。

在主仓库 backend 目录运行（读取 backend/.env 中的高德 Web 服务 key，不回显）：
  TTA_DATA_DIR=<隔离数据> TTA_POI_CATALOG=with_supplement TTA_POI_SUPPLEMENT_DIR=<补充目录副本> \
  .venv-mcp/bin/python -B <本脚本> <输出目录> [用例名,用例名]
只保存摘要（状态、景点与时段、请求数、提示），不保存提供商原始响应。
"""
from __future__ import annotations

import json
import secrets
import sys
import time
from pathlib import Path

sys.path.insert(0, ".")
from app import accounts, config  # noqa: E402
from app.geo import gcj02_to_bd09  # noqa: E402
from app.mcp_service import MCPService  # noqa: E402
from app.poi_catalog import current_catalog  # noqa: E402

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
SUPPLEMENT_ID = 1_000_000
assert config.POI_CATALOG_MODE == "with_supplement" and config.AMAP_WEB_KEY, "需要隔离补充目录模式和高德Web服务key"
catalog = current_catalog()
assert SUPPLEMENT_ID in catalog.pois, "补充目录未包含金沙湖公园"
SUPPLEMENT_IDS = sorted(i for i in catalog.pois if i >= SUPPLEMENT_ID)

store = accounts.AccountStore(OUT / "accounts.sqlite3")
user = store.register(f"p5tester{secrets.token_hex(3)}", secrets.token_urlsafe(16))
owner = f"user:{user['id']}"
service = MCPService(store=store, ledger_path=OUT / "requests.sqlite3", site_url="", allow_unverified_draft=True)

lat, lon = gcj02_to_bd09(30.3089, 120.3262)  # 金沙湖地铁站附近的测试起点（GCJ-02 换算为 BD-09）
CASES = {
    "jinshahu_must_visit": {"days": 1, "budget": 500, "start_date": "2026-10-20", "include_evening": False,
                            "interests": ["自然风光", "拍照出片"], "must_visit": ["金沙湖"], "preferred_regions": ["下沙"],
                            "hotel": {"name": "下沙金沙湖附近（测试起点）", "lat": round(lat, 6), "lon": round(lon, 6)}},
    "base_regression": {"days": 1, "budget": 800, "start_date": "2026-10-21", "include_evening": False,
                        "interests": ["人文历史"],
                        "hotel": {"name": "西湖湖滨（测试起点）", "lat": 30.254433, "lon": 120.169771}},
    "xiasha_parks_day": {"days": 1, "budget": 500, "start_date": "2026-10-20", "include_evening": False,
                         "interests": ["自然风光"], "preferred_regions": ["下沙", "白杨"],
                         "hotel": {"name": "下沙金沙湖附近（测试起点）", "lat": round(lat, 6), "lon": round(lon, 6)}},
}
# 只在该半径内选点的用例：模拟宿主AI优先挑近处，不把一天拆成跨城往返
NEARBY_KM = {"xiasha_parks_day": 10}
SELECTED = set(sys.argv[2].split(",")) if len(sys.argv) > 2 else set(CASES)


def choose(candidates: list[dict], required: set[int], limit: int = 4, within_km: float | None = None) -> list[dict]:
    """代替宿主AI的确定性选点：必去在前，其余按候选分数取前几个；可限定离起点的半径。"""
    if within_km is not None:
        candidates = [c for c in candidates if c["poi_id"] in required or c["breakdown"]["distance_km"] <= within_km]
    ordered = sorted(candidates, key=lambda c: (c["poi_id"] not in required, -c["score"]))
    return [{"poi_id": c["poi_id"], "reason": "符合出行兴趣与距离" if c["poi_id"] not in required else "用户指定必去"}
            for c in ordered[:limit]]


summary = {"catalog": catalog.reference(), "server_llm_calls": 0, "cases": {}}
for name, profile in CASES.items():
    if name not in SELECTED:
        continue
    prepared = service.prepare(owner, profile, f"p5-prepare-{name}")
    entry = {"prepare_status": prepared["status"], "prepare_amap": prepared.get("amap_http_calls"),
             "must_visit": prepared.get("retrieval_report", {}).get("must_visit"),
             "supplement_candidate": next((c for c in prepared.get("candidates", []) if c["poi_id"] == SUPPLEMENT_ID), None),
             "supplement_candidates": [c["poi_id"] for c in prepared.get("candidates", []) if c["poi_id"] in SUPPLEMENT_IDS],
             "region_coverage": prepared.get("retrieval_report", {}).get("region_coverage")}
    if prepared["status"] != "prepared":
        entry["prepare"] = {k: prepared.get(k) for k in ("code", "message", "required_gaps", "missing_fields")}
        summary["cases"][name] = entry
        continue
    required = {e["poi_id"] for e in entry["must_visit"] or [] if e.get("poi_id") is not None}
    selections = choose(prepared["candidates"], required, within_km=NEARBY_KM.get(name))
    entry["selected"] = [s["poi_id"] for s in selections]
    queued = service.generate(owner, prepared["job_id"], prepared["preparation_revision"], prepared["candidate_set_id"],
                              selections, f"p5-generate-{name}")
    entry["generate"] = queued.get("status")
    started = time.monotonic()
    job = service.get_job(owner, prepared["job_id"])
    while job["status"] in {"queued", "running"} and time.monotonic() - started < 600:
        time.sleep(3)
        job = service.get_job(owner, prepared["job_id"])
    entry.update(status=job["status"], code=job.get("code"), amap_http_calls=job["amap_http_calls"],
                 stage_http_calls=job.get("stage_http_calls"), seconds=round(time.monotonic() - started, 1))
    plan = job.get("plan")
    if plan:
        entry["delivery_status"] = plan.get("delivery_status")
        entry["verification_notice"] = plan.get("verification_notice")
        entry["poi_catalog"] = plan.get("poi_catalog")
        entry["days"] = [[{"kind": i["kind"], "ref_id": i["ref_id"], "name": i["name"], "start": i["start_hm"], "end": i["end_hm"],
                           "travel_min": i.get("travel_min"), "travel_mode": i.get("travel_mode"),
                           "evidence_source": (i.get("travel_evidence") or {}).get("source")}
                          for i in day["items"]] for day in plan["days"]]
        report = plan["report"]
        entry["report"] = {k: report.get(k) for k in ("passed", "constraint_status", "arrangement_status", "evidence_status", "complete_executable", "totals")}
        entry["hard"] = [v.get("message") for v in report.get("hard", [])]
        entry["unknowns"] = report.get("unknowns")
        entry["history_saved"] = bool(job.get("history_id"))
    else:
        entry["outcome"] = {k: job.get(k) for k in ("message", "gaps", "conflicts", "unknowns", "options")}
    summary["cases"][name] = entry

service.close()
(OUT / "p5-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1, default=str))
print(json.dumps({n: {k: c.get(k) for k in ("prepare_status", "status", "delivery_status", "amap_http_calls", "selected")}
                  for n, c in summary["cases"].items()}, ensure_ascii=False))
