"""同进程双回环监听。默认网页进程不启用 MCP；本入口需显式配置后运行。"""
from __future__ import annotations

import argparse
import asyncio
import os
import socket
from contextlib import AsyncExitStack, asynccontextmanager

import uvicorn
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse

from . import accounts
from .api import app as web_app
from .mcp_service import MCPService
from .mcp_transport import create_mcp_app
from .mcp_login import MCPLoginStore


class PortDispatcher:
    def __init__(self, web, mcp, web_port: int, mcp_port: int, *, close=None):
        self.web, self.mcp = web, mcp
        self.web_port, self.mcp_port = web_port, mcp_port

        @asynccontextmanager
        async def lifespan(app):
            try:
                async with AsyncExitStack() as stack:
                    await stack.enter_async_context(web.router.lifespan_context(web))
                    await stack.enter_async_context(mcp.router.lifespan_context(mcp))
                    yield
            finally:
                if close:
                    await asyncio.to_thread(close)
        self.lifecycle = Starlette(lifespan=lifespan)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            return await self.lifecycle(scope, receive, send)
        port = (scope.get("server") or (None, None))[1]
        if port == self.web_port:
            # 即使未来 SPA catchall 扩展，也不让 /mcp 回落到页面或协议。
            if scope.get("path", "").rstrip("/") == "/mcp":
                return await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)
            return await self.web(scope, receive, send)
        if port == self.mcp_port:
            return await self.mcp(scope, receive, send)
        return await PlainTextResponse("Not Found", status_code=404)(scope, receive, send)


def bind(port: int) -> socket.socket:
    if not 18600 <= port <= 18699:
        raise ValueError("端口必须在18600–18699")
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))
        sock.listen(128)
        sock.setblocking(False)
        return sock
    except BaseException:
        sock.close()
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--web-port", type=int, default=18600)
    parser.add_argument("--mcp-port", type=int, default=18602)
    args = parser.parse_args()
    if args.web_port == args.mcp_port:
        parser.error("两个入口必须使用不同端口")
    credential = os.environ.get("TTA_MCP_TOKEN", "")
    username = os.environ.get("TTA_MCP_USERNAME", "")
    site_url = os.environ.get("TTA_MCP_SITE_URL", "")
    if len(credential) < 24 or not username or not site_url:
        parser.error("需要在backend/.env中配置专用MCP凭据、本人账号及网站基址；不回显配置值")
    with accounts.STORE.connect() as db:
        row = db.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    if row is None:
        parser.error("绑定的网站账号不存在")
    service = MCPService(site_url=site_url, daily_limit=int(os.environ.get("MCP_DAILY_PLAN_LIMIT", "0")),
                         allow_unverified_draft=os.environ.get("TTA_MCP_ALLOW_UNVERIFIED_DRAFT", "0") == "1")
    sockets = []
    try:
        sockets.append(bind(args.web_port))
        sockets.append(bind(args.mcp_port))
        login = MCPLoginStore(accounts.STORE.path.with_name("mcp-logins.sqlite3"), accounts.STORE, site_url)
        web_app.state.mcp_login = login
        mcp_app = create_mcp_app(service, f"user:{row['id']}", credential, port=args.mcp_port, login=login)
        app = PortDispatcher(web_app, mcp_app, args.web_port, args.mcp_port, close=service.close)
        # 只信任回环代理；按实际server端口分发，不能以Host/X-Forwarded-*切换入口。
        server = uvicorn.Server(uvicorn.Config(app, proxy_headers=True, forwarded_allow_ips="127.0.0.1",
                                               access_log=False, timeout_graceful_shutdown=30))
        asyncio.run(server.serve(sockets=sockets))
    finally:
        for sock in sockets:
            sock.close()
        service.close()


if __name__ == "__main__":
    main()
