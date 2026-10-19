"""两份有界真实路线冒烟；仅手动运行，不属于可复现离线评测。"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import accounts, config
from app.api import hotel_presets
from app.mcp_service import MCPService


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--username", default="123")
    args = parser.parse_args()
    if not config.AMAP_WEB_KEY:
        parser.error("未配置高德Key；未发请求")
    with accounts.STORE.connect() as db:
        user = db.execute("SELECT id FROM users WHERE username=?", (args.username,)).fetchone()
    if user is None:
        parser.error("本人测试账号不存在；未发请求")
    owner = f"user:{user['id']}"
    def forbidden(*a, **k):
        raise AssertionError("MCP服务器模型入口被调用")
    import app.graph, app.llm, app.nodes.select, app.nodes.parse
    app.graph.get_llm = app.llm.get_llm = app.llm.chat_json = forbidden
    app.nodes.select.evaluate_jev = app.nodes.parse.get_llm = app.nodes.parse.chat_json = forbidden
    args.output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    service = MCPService(ledger_path=args.output.parent / "live-request-ledger.sqlite3",
                         site_url="http://127.0.0.1:18600")
    try:
        hotel = {key: hotel_presets()[0][key] for key in ("name", "lat", "lon")}
        for n, transport in enumerate(("taxi", "transit")):
            profile = {"days": 1, "budget": 1500, "start_date": "2026-10-22", "hotel": hotel,
                       "pace": "normal", "interests": ["人文历史", "自然风光"],
                       "transport": transport, "include_evening": False}
            request = f"live-{time.time_ns()}-{n}"
            prep = service.prepare(owner, profile, request + "-prepare")
            if prep.get("status") != "prepared":
                rows.append({"transport": transport, "preparation_status": prep.get("status"), "code": prep.get("code")})
                continue
            chosen = [{"poi_id": c["poi_id"], "reason": "新建冒烟需求：匹配人文与自然偏好"}
                      for c in prep["candidates"][:2]]
            queued = service.generate(owner, prep["job_id"], prep["preparation_revision"], prep["candidate_set_id"], chosen,
                                      request + "-generate")
            start = time.monotonic()
            state = queued
            while state.get("status") in {"queued", "running"}:
                time.sleep(.2)
                state = service.get_job(owner, prep["job_id"])
                if time.monotonic() - start > 300:
                    service.cancel(owner, prep["job_id"])
                    state = service.get_job(owner, prep["job_id"])
                    break
            row = {"transport": transport, "status": state.get("status"), "code": state.get("code"),
                   "seconds": round(time.monotonic() - start, 2), "amap_http_calls": state.get("amap_http_calls", 0),
                   "server_llm_calls": state.get("server_llm_calls", 0), "stage_http_calls": state.get("stage_http_calls", {}),
                   "unknowns": state.get("unknowns", state.get("report", {}).get("unknowns", [])),
                   "report": state.get("report"), "gaps": state.get("gaps", []),
                   "history_saved": bool(state.get("history_id"))}
            assert row["amap_http_calls"] <= 120
            assert row["server_llm_calls"] == 0
            rows.append(row)
            args.output.write_text(json.dumps({"live_verification_date": "2026-10-18", "cases": rows,
                "max_amap_http": 240, "actual_amap_http": sum(r.get("amap_http_calls", 0) for r in rows),
                "note": "有界实机冒烟，非冻结质量评测；未读取账单，不报告实付金额。"}, ensure_ascii=False, indent=2))
            print(json.dumps({k: row[k] for k in ("transport", "status", "code", "seconds", "amap_http_calls", "history_saved")}), flush=True)
    finally:
        service.close()


if __name__ == "__main__":
    main()
