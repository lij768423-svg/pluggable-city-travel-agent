from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator

from . import config

AUTH_COOKIE = "tta_session"
GUEST_COOKIE = "tta_guest"
SESSION_AGE = 7 * 86400


class AccountStore:
    def __init__(self, path: Path):
        self.path = path
        self._ready = False
        self._lock = threading.Lock()
        self._quota_lock = threading.Lock()

    @contextmanager
    def connect(self):
        with self._lock:
            if not self._ready:
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                with sqlite3.connect(self.path) as database:
                    database.executescript("""
                        PRAGMA journal_mode=WAL;
                        CREATE TABLE IF NOT EXISTS users (
                            id TEXT PRIMARY KEY, username TEXT UNIQUE NOT NULL,
                            password TEXT NOT NULL, created REAL NOT NULL);
                        CREATE TABLE IF NOT EXISTS logins (
                            token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL,
                            expires REAL NOT NULL);
                        CREATE TABLE IF NOT EXISTS trips (
                            id TEXT PRIMARY KEY, user_id TEXT NOT NULL, source_id TEXT NOT NULL,
                            title TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL,
                            request_json TEXT NOT NULL, plan_json TEXT NOT NULL,
                            UNIQUE(user_id, source_id));
                        CREATE INDEX IF NOT EXISTS trip_owner ON trips(user_id, updated DESC);
                        CREATE TABLE IF NOT EXISTS plan_quota (
                            day TEXT NOT NULL, key TEXT NOT NULL, count INTEGER NOT NULL,
                            PRIMARY KEY(day, key));
                    """)
                os.chmod(self.path, 0o600)
                self._ready = True
        database = sqlite3.connect(self.path, timeout=10)
        database.row_factory = sqlite3.Row
        try:
            with database:
                yield database
        finally:
            database.close()

    @staticmethod
    def password_hash(password: str, salt: str | None = None) -> str:
        salt = salt or secrets.token_hex(16)
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=32768, r=8, p=3, maxmem=67108864)
        return f"scrypt${salt}${digest.hex()}"

    def register(self, username: str, password: str) -> dict:
        user_id = secrets.token_hex(16)
        password_hash = self.password_hash(password)
        try:
            with self.connect() as database:
                database.execute("INSERT INTO users VALUES (?,?,?,?)", (user_id, username, password_hash, time.time()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "这个用户名已被使用，请换一个。") from None
        return {"id": user_id, "username": username}

    def login(self, username: str, password: str) -> dict:
        with self.connect() as database:
            row = database.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        stored = row["password"] if row else f"scrypt${'00' * 16}${'00' * 64}"
        candidate = self.password_hash(password, stored.split("$")[1])
        if not hmac.compare_digest(candidate, stored) or row is None:
            raise HTTPException(401, "用户名或密码不正确。")
        return {"id": row["id"], "username": row["username"]}

    def issue(self, user_id: str) -> str:
        token = secrets.token_urlsafe(32)
        with self.connect() as database:
            database.execute("DELETE FROM logins WHERE expires < ?", (time.time(),))
            database.execute("INSERT INTO logins VALUES (?,?,?)", (hash_token(token), user_id, time.time() + SESSION_AGE))
        return token

    def user(self, token: str | None) -> dict | None:
        if not token:
            return None
        with self.connect() as database:
            row = database.execute("SELECT users.id, users.username FROM users JOIN logins ON users.id=logins.user_id WHERE token_hash=? AND expires>?",
                                   (hash_token(token), time.time())).fetchone()
        return dict(row) if row else None

    def revoke(self, token: str | None) -> None:
        if token:
            with self.connect() as database:
                database.execute("DELETE FROM logins WHERE token_hash=?", (hash_token(token),))

    def save(self, user_id: str, source_id: str, request_data: dict, plan: dict) -> str:
        saved = copy.deepcopy(plan)
        saved.update(plan_id=None, revision=None, change=None, history_readonly=True)
        saved["log"] = []
        saved["candidates"] = []
        for day in saved["days"]:
            day.pop("return_leg", None)
            for item in day["items"]:
                for key in ("detail", "restaurant", "leg", "guide_evidence", "travel_evidence"):
                    item.pop(key, None)
                item["alternatives"] = []
        title = f"杭州 {saved['profile']['days']} 日 · " + "、".join(saved["profile"].get("interests", [])[:2] or ["随心旅行"])
        now = time.time()
        with self.connect() as database:
            previous = database.execute("SELECT id FROM trips WHERE user_id=? AND source_id=?", (user_id, source_id)).fetchone()
            identifier = previous["id"] if previous else secrets.token_urlsafe(12)
            saved["history_id"] = identifier
            database.execute("""INSERT INTO trips VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(user_id,source_id) DO UPDATE SET title=excluded.title, updated=excluded.updated, request_json=excluded.request_json, plan_json=excluded.plan_json""",
                (identifier, user_id, source_id, title, now, now, json.dumps(request_data, ensure_ascii=False), json.dumps(saved, ensure_ascii=False)))
        return identifier

    def usage(self, day: str, key: str) -> int:
        with self.connect() as database:
            row = database.execute("SELECT count FROM plan_quota WHERE day=? AND key=?", (day, key)).fetchone()
        return row["count"] if row else 0

    def consume(self, day: str, limits: dict[str, int]) -> bool:
        """所有计数都未达上限时各加一并返回 True；任一已满则不扣并返回 False。"""
        with self._quota_lock, self.connect() as database:
            for key, limit in limits.items():
                row = database.execute("SELECT count FROM plan_quota WHERE day=? AND key=?", (day, key)).fetchone()
                if row and row["count"] >= limit:
                    return False
            for key in limits:
                database.execute("INSERT INTO plan_quota VALUES (?,?,1) ON CONFLICT(day,key) DO UPDATE SET count=count+1", (day, key))
            database.execute("DELETE FROM plan_quota WHERE day < date(?, '-30 day')", (day,))
        return True

    def list(self, user_id: str, offset: int = 0) -> dict:
        with self.connect() as database:
            rows = database.execute("SELECT id,title,created,updated,json_extract(plan_json,'$.profile.days') AS days,json_extract(plan_json,'$.profile.start_date') AS start_date FROM trips WHERE user_id=? ORDER BY updated DESC,id DESC LIMIT 21 OFFSET ?", (user_id, offset)).fetchall()
        return {"items": [dict(row) for row in rows[:20]], "has_more": len(rows) > 20}

    def get(self, user_id: str, identifier: str) -> dict:
        with self.connect() as database:
            row = database.execute("SELECT * FROM trips WHERE id=? AND user_id=?", (identifier, user_id)).fetchone()
        if row is None:
            raise HTTPException(404, "这份历史行程不存在。")
        return {"id": identifier, "title": row["title"], "created": row["created"], "updated": row["updated"],
                "request": json.loads(row["request_json"]), "plan": json.loads(row["plan_json"])}

    def delete(self, user_id: str, identifier: str) -> None:
        with self.connect() as database:
            removed = database.execute("DELETE FROM trips WHERE id=? AND user_id=?", (identifier, user_id)).rowcount
        if not removed:
            raise HTTPException(404, "这份历史行程不存在。")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


STORE = AccountStore(Path(os.environ.get("TTA_ACCOUNT_DB", config.DATA_DIR / "accounts" / "accounts.sqlite3")))
router = APIRouter()
_attempts: dict[str, list[float]] = {}
_attempt_lock = threading.Lock()


def same_origin(request: Request) -> None:
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or origin and origin.rstrip("/") != str(request.base_url).rstrip("/"):
        raise HTTPException(403, "请从本站页面发起操作。")


def throttle(request: Request, username: str) -> None:
    now = time.time()
    keys = [f"ip:{request.client.host if request.client else 'local'}", f"name:{username}"]
    with _attempt_lock:
        for key in list(_attempts):
            _attempts[key] = [stamp for stamp in _attempts[key] if stamp > now - 300]
            if not _attempts[key]:
                del _attempts[key]
        if len(_attempts) > 10000 or any(len(_attempts.get(key, [])) >= 12 for key in keys):
            raise HTTPException(429, "尝试次数较多，请五分钟后再试。")
        for key in keys:
            _attempts.setdefault(key, []).append(now)


def current_user(request: Request, required: bool = False) -> dict | None:
    user = STORE.user(request.cookies.get(AUTH_COOKIE))
    if required and user is None:
        raise HTTPException(401, "请先登录，再查看或保存历史行程。")
    return user


def principal(request: Request, response: Response | None = None) -> str:
    user = current_user(request)
    if user:
        return f"user:{user['id']}"
    guest = request.cookies.get(GUEST_COOKIE)
    if not guest and response is not None:
        guest = secrets.token_urlsafe(32)
        response.set_cookie(GUEST_COOKIE, guest, httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=SESSION_AGE)
    return f"guest:{hash_token(guest)}" if guest else "anonymous"


def authorize_session(session, request: Request, allow_guest_claim: bool = False) -> None:
    owner = principal(request)
    guest = request.cookies.get(GUEST_COOKIE)
    if session.owner is not None and session.owner != owner and not (
        allow_guest_claim and guest and session.owner == f"guest:{hash_token(guest)}"
    ):
        raise HTTPException(404, "这份行程不存在或不属于当前账号。")


class Credentials(BaseModel):
    username: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=10, max_length=128)
    invite_code: str | None = Field(None, max_length=64)

    @field_validator("username")
    @classmethod
    def valid_username(cls, value: str) -> str:
        if not re.fullmatch(r"[a-zA-Z0-9_]{3,32}", value):
            raise ValueError("用户名请使用3–32位英文字母、数字或下划线")
        return value.casefold()


def establish(user: dict, request: Request, response: Response) -> dict:
    STORE.revoke(request.cookies.get(AUTH_COOKIE))
    response.set_cookie(AUTH_COOKIE, STORE.issue(user["id"]), httponly=True, secure=request.url.scheme == "https", samesite="strict", max_age=SESSION_AGE)
    response.headers["Cache-Control"] = "no-store"
    return {"user": user}


@router.post("/api/auth/register")
def register(body: Credentials, request: Request, response: Response):
    same_origin(request)
    throttle(request, body.username)
    if config.INVITE_CODES and not any(hmac.compare_digest((body.invite_code or "").strip(), code) for code in config.INVITE_CODES):
        raise HTTPException(403, "邀请码不正确，请向网站管理员索取。")
    return establish(STORE.register(body.username, body.password), request, response)


@router.post("/api/auth/login")
def login(body: Credentials, request: Request, response: Response):
    same_origin(request)
    throttle(request, body.username)
    return establish(STORE.login(body.username, body.password), request, response)


@router.get("/api/auth/me")
def me(request: Request, response: Response):
    response.headers["Cache-Control"] = "no-store"
    from .access import quota
    user = current_user(request)
    return {"user": user, "quota": quota(user)}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    same_origin(request)
    STORE.revoke(request.cookies.get(AUTH_COOKIE))
    response.delete_cookie(AUTH_COOKIE, httponly=True, samesite="strict")
    return {"ok": True}


@router.get("/api/history")
def history(request: Request, response: Response, offset: int = 0):
    user = current_user(request, True)
    if offset < 0 or offset > 100000:
        raise HTTPException(422, "分页范围无效。")
    response.headers["Cache-Control"] = "no-store"
    return STORE.list(user["id"], offset)


@router.get("/api/history/{identifier}")
def history_detail(identifier: str, request: Request, response: Response):
    user = current_user(request, True)
    response.headers["Cache-Control"] = "no-store"
    return STORE.get(user["id"], identifier)


@router.delete("/api/history/{identifier}")
def history_delete(identifier: str, request: Request):
    same_origin(request)
    user = current_user(request, True)
    STORE.delete(user["id"], identifier)
    return {"ok": True}
