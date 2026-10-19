"""实际双监听与官方 SDK 工具链；非客户端模型模拟。"""
import asyncio
import threading
import time

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from app.mcp_server import PortDispatcher, bind, web_app
from app.mcp_transport import create_mcp_app
from app.mcp_service import MCPService
from app.mcp_domain import run_selected
from app.travel import Travel
from app.restaurants import RestaurantCatalog
from .test_mcp import OWNER, PROFILE

CREDENTIAL = "test-only-credential-not-a-real-secret"


def test_dual_listener_auth_origin_tools_and_shutdown(tmp_path, monkeypatch):
    service = MCPService(ledger_path=tmp_path / "requests.sqlite3", site_url="http://127.0.0.1")
    def offline_run(profile, candidates, selections, report, **kwargs):
        return run_selected(profile, candidates, selections, report, route=Travel("estimate"),
                            catalog=RestaurantCatalog(online=False), use_solver=False, **kwargs)
    monkeypatch.setattr("app.mcp_service.run_selected", offline_run)
    socks = []
    for port in range(18670, 18700):
        try:
            socks.append(bind(port))
        except OSError:
            continue
        if len(socks) == 2:
            break
    assert len(socks) == 2
    web_port, mcp_port = [s.getsockname()[1] for s in socks]
    mcp = create_mcp_app(service, OWNER, CREDENTIAL, port=mcp_port)
    dispatch = PortDispatcher(web_app, mcp, web_port, mcp_port, close=service.close)
    server = uvicorn.Server(uvicorn.Config(dispatch, log_level="error", access_log=False))
    thread = threading.Thread(target=server.run, kwargs={"sockets": socks})
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(.02)
        assert server.started
        url = f"http://127.0.0.1:{mcp_port}/mcp"
        assert httpx.post(url, json={}).status_code == 401
        headers = {"Authorization": f"Bearer {CREDENTIAL}"}
        assert httpx.post(url, headers={**headers, "Origin": "https://evil.invalid"}, json={}).status_code == 403
        for extra in ({}, {"Host": f"127.0.0.1:{mcp_port}"}, {"X-Forwarded-Host": f"127.0.0.1:{mcp_port}", "X-Forwarded-Port": str(mcp_port)}):
            assert httpx.post(f"http://127.0.0.1:{web_port}/mcp", headers={**headers, **extra}, json={}).status_code == 404
        assert httpx.get(f"http://127.0.0.1:{web_port}/api/meta").status_code == 200

        async def exercise():
            async with httpx.AsyncClient(headers=headers) as http:
                async with streamable_http_client(url, http_client=http) as (read, write, _):
                    async with ClientSession(read, write) as client:
                        initialized = await client.initialize()
                        assert initialized.serverInfo.name == "Hangzhou Travel"
                        tools = await client.list_tools()
                        assert {t.name for t in tools.tools} == {"prepare_trip", "generate_trip", "get_job", "get_trip", "preview_meal_change", "confirm_meal_change", "cancel_job"}
                        response = await client.call_tool("prepare_trip", {"profile": PROFILE, "request_id": "protocol-prepare"})
                        assert response.structuredContent is not None, response
                        prep = response.structuredContent
                        assert prep["status"] == "prepared"
                        body = {k: prep[k] for k in ("job_id", "preparation_revision", "candidate_set_id")}
                        body.update(request_id="protocol-generate", selections=[{"poi_id": c["poi_id"], "reason": "符合兴趣"} for c in prep["candidates"][:2]])
                        queued = (await client.call_tool("generate_trip", body)).structuredContent
                        assert queued["status"] == "queued"
                        for _ in range(100):
                            state = (await client.call_tool("get_job", {"job_id": prep["job_id"]})).structuredContent
                            if state["status"] not in {"queued", "running"}:
                                break
                            await asyncio.sleep(.05)
                        assert state["status"] == "data_unavailable", state
                        assert state["server_llm_calls"] == 0 and state["amap_http_calls"] == 0
                        assert "site_url" not in state
                        again = (await client.call_tool("generate_trip", body)).structuredContent
                        assert again["job_id"] == queued["job_id"]
                        other = (await client.call_tool("get_trip", {"trip_id": "other-owner-id"})).structuredContent
                        assert other["code"] == "not_found"
        asyncio.run(exercise())
    finally:
        server.should_exit = True
        thread.join(timeout=15)
        for sock in socks:
            sock.close()
        assert not thread.is_alive()
    # 两个套接字和统一生命周期均已关闭，可立即重新绑定。
    for port in (web_port, mcp_port):
        fresh = bind(port); fresh.close()
