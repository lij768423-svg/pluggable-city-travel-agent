"""私网 SSH MCP 的一次性网页登录绑定；不冒充 HTTP OAuth 服务。"""
from __future__ import annotations

import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from . import accounts

DEVICE_AGE = 600
GRANT_AGE = 90 * 86400
BROWSER_AGE = 30 * 86400
router = APIRouter()


class MCPLoginStore:
    def __init__(self, path: Path, store, site_url: str):
        self.path, self.store, self.site_url = path, store, site_url.rstrip("/")
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS devices (
                    id TEXT PRIMARY KEY, token_hash TEXT UNIQUE NOT NULL,
                    label TEXT NOT NULL, expires REAL NOT NULL, user_id TEXT);
                CREATE TABLE IF NOT EXISTS grants (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                    label TEXT NOT NULL, expires REAL NOT NULL);
            """)
        path.chmod(0o600)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def user(self, token: str) -> dict | None:
        with self.connect() as db:
            row = db.execute("SELECT user_id FROM grants WHERE token_hash=? AND expires>?",
                             (accounts.hash_token(token), time.time())).fetchone()
        if not row:
            return None
        with self.store.connect() as db:
            user = db.execute("SELECT id,username FROM users WHERE id=?", (row["user_id"],)).fetchone()
        return dict(user) if user else None

    def status(self, token: str, label: str) -> dict:
        user = self.user(token)
        if user:
            return {"status": "connected", "user": user, "message": "账号已绑定，后续无需重复登录 MCP。"}
        digest, now = accounts.hash_token(token), time.time()
        with self.connect() as db:
            db.execute("DELETE FROM devices WHERE expires<?", (now,))
            db.execute("DELETE FROM grants WHERE expires<?", (now,))
            row = db.execute("SELECT id,expires FROM devices WHERE token_hash=?", (digest,)).fetchone()
            if not row:
                # 私有传输仍需运维凭据；此外对待授权数量设置有界上限。
                if db.execute("SELECT count(*) FROM devices").fetchone()[0] >= 128:
                    raise HTTPException(429, "待授权连接过多，请稍后再试。")
                identifier = secrets.token_urlsafe(24)
                expires = now + DEVICE_AGE
                db.execute("INSERT INTO devices VALUES (?,?,?,?,NULL)", (identifier, digest, label, now + DEVICE_AGE))
            else:
                identifier = row["id"]
                expires = row["expires"]
        return {"status": "login_required", "verification_url": f"{self.site_url}/?mcp_connect={identifier}",
                "expires_in": max(0, int(expires - now)), "message": "首次配置：打开链接，在网站登录并确认授权；完成后重试即可。不要在聊天中填写密码。"}

    def device(self, identifier: str, user_id: str | None = None) -> dict:
        with self.connect() as db:
            row = db.execute("SELECT id,label,expires,user_id FROM devices WHERE id=? AND expires>?",
                             (identifier, time.time())).fetchone()
        if not row:
            raise HTTPException(410, "授权链接已过期，请在 MCP 中重新获取登录链接。")
        return {"client_name": row["label"], "approved": row["user_id"] is not None, "account_matches": bool(user_id and row["user_id"] == user_id),
                "expires_in": max(0, int(row["expires"] - time.time()))}

    def approve(self, identifier: str, user_id: str) -> None:
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM devices WHERE id=? AND expires>?", (identifier, time.time())).fetchone()
            if not row:
                raise HTTPException(410, "授权链接已过期，请重新获取。")
            if row["user_id"]:
                # 不允许重复链接切换或重新激活已撤销的授权。
                raise HTTPException(409, "这个授权链接已经使用，请回到 MCP 查看连接状态。")
            db.execute("INSERT INTO grants VALUES (?,?,?,?)", (row["token_hash"], user_id, row["label"], time.time() + GRANT_AGE))
            db.execute("UPDATE devices SET user_id=? WHERE id=?", (user_id, identifier))

    def revoke(self, token: str) -> None:
        with self.connect() as db:
            digest = accounts.hash_token(token)
            db.execute("DELETE FROM grants WHERE token_hash=?", (digest,))
            db.execute("DELETE FROM devices WHERE token_hash=?", (digest,))


def login_store(request: Request) -> MCPLoginStore:
    store = getattr(request.app.state, "mcp_login", None)
    if store is None:
        raise HTTPException(404, "未启用 MCP 账号绑定。")
    return store


class Approval(BaseModel):
    connection_id: str = Field(min_length=24, max_length=64)


@router.get("/api/mcp/connect/{identifier}")
def device_info(identifier: str, request: Request, response: Response):
    response.headers.update({"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})
    user = accounts.current_user(request)
    return login_store(request).device(identifier, user["id"] if user else None)


@router.post("/api/mcp/connect")
def approve(body: Approval, request: Request, response: Response):
    accounts.same_origin(request)
    store = login_store(request)
    user = accounts.current_user(request, True)
    store.approve(body.connection_id, user["id"])
    # 首次网页登录与 MCP 授权在同一站点完成，浏览器保留30天登录。
    accounts.establish(user, request, response, age=BROWSER_AGE)
    return {"status": "connected", "user": user, "browser_days": 30}
