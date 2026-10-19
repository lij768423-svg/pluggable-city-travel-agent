"""隔离浏览器验收：临时测试账号/估算行程，绝不使用生产账号库。"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "backend/eval/runs/mcp-onboarding-20261018"
os.environ.update(TTA_ACCOUNT_DB=str(RUN / "onboarding-ui-accounts.sqlite3"), TTA_FRONTEND_DIST=str(RUN / "frontend-dist"),
                  TTA_REQUIRE_LOGIN="1", TTA_INVITE_CODES="", TTA_RATE_LIMIT="0", AMAP_WEB_KEY="", AMAP_JS_KEY="", AMAP_JS_SECURITY="")
from app import accounts
from app.api import app
from app.graph import run_plan
from app.llm import MockLLM
from app.presentation import serialize_plan
from app.sessions import SESSIONS
import uvicorn

with accounts.STORE.connect() as db:
    row = db.execute("SELECT id FROM users WHERE username=?", ("onboarding_ui",)).fetchone()
user = {"id": row["id"]} if row else accounts.STORE.register("onboarding_ui", "fixture-password-only")
result = run_plan(form={"days": 1, "budget": 1500, "start_date": "2026-10-22", "include_evening": False},
                  llm=MockLLM({}), select_mode="rule", travel_source="estimate", use_solver=False)
meta = {"source": "mcp", "select_mode": "external_ai", "enable_repair": True, "guide_mode": "off", "input": {"query": "", "form": {"days": 1}, "select_mode": "llm", "enable_repair": True}}
session = SESSIONS.create(result, meta, f"user:{user['id']}")
payload = serialize_plan(result, meta, session.plan_id, 0)
identifier = accounts.STORE.save(user["id"], session.plan_id, meta["input"], payload)
SESSIONS.bind_history(session.plan_id, identifier)
(RUN / "ui-fixture.json").write_text(__import__('json').dumps({"history_id": identifier, "plan_id": session.plan_id}))
from app.mcp_login import MCPLoginStore
import secrets
login = MCPLoginStore(RUN / "onboarding-ui-grants.sqlite3", accounts.STORE, "http://127.0.0.1:18684")
app.state.mcp_login = login
connection = login.status(secrets.token_urlsafe(32), "codex-browser-test")
(RUN / "onboarding-ui.json").write_text(__import__('json').dumps({"verification_url": connection["verification_url"], "history_id": identifier}))
uvicorn.run(app, host="127.0.0.1", port=18684, log_level="warning", access_log=False)
