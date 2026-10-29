"""公网访问控制：生成行程要求登录、按账号、IP 与全站计每日次数；一句话需求按账号计每日次数；外部数据接口按 IP 限频；公网域名屏蔽研究工具。

开关全部来自环境变量，公网部署时由 systemd 的 EnvironmentFile 提供（见 docs/公网部署.md），
默认全部关闭，本地开发与测试行为不变。
"""
from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from . import accounts, config
from .plan_queue import PLAN_QUEUE, QueueFull

CHINA = timezone(timedelta(hours=8))
PUBLIC_BLOCKED = ("/stay-audit", "/api/stay-audit")   # 研究用核对页，只在内网使用
# 外部数据接口的限频：(次数, 秒)，按客户端 IP 计
RATE_LIMITS = {"places": (60, 600), "navigation": (60, 600), "details": (120, 600), "media": (240, 600),
               "amap": (1500, 600), "swap": (30, 600), "understand": (30, 600)}
_hits: dict[str, list[float]] = {}
_lock = threading.Lock()


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "local"


def today() -> str:
    return datetime.now(CHINA).date().isoformat()


def blocked(request: Request) -> bool:
    host = request.headers.get("host", "").split(":")[0].lower()
    return host in config.PUBLIC_HOSTS and request.url.path.startswith(PUBLIC_BLOCKED)


def unlimited(user: dict | None) -> bool:
    return bool(user) and user.get("username") in config.UNLIMITED_USERS


def require_login(request: Request) -> dict | None:
    """开启登录要求时未登录返回 401；不扣次数。规划前的需求理解也要调一次模型，不能给未登录的访客白用。"""
    user = accounts.current_user(request)
    if config.REQUIRE_LOGIN_TO_PLAN and user is None:
        raise HTTPException(401, "请先登录，再生成行程。")
    return user


def guard_plan(request: Request) -> tuple[str, list[str]] | None:
    """生成行程前调用：未登录（开启时）返回 401；排队已满或当天次数用完返回 429。计数在开始规划时扣除，
    返回扣掉的（日期, 计数键）供排队时离开退回；不限次的账号返回 None。"""
    user = require_login(request)
    if unlimited(user):
        return None
    try:
        PLAN_QUEUE.check_room()
    except QueueFull:
        raise HTTPException(429, "现在排队生成的人太多了，请过几分钟再试。") from None
    limits = {}
    if user and config.PLAN_LIMIT_USER:
        limits[f"user:{user['id']}"] = config.PLAN_LIMIT_USER
    if config.PLAN_LIMIT_IP:
        limits[f"ip:{client_ip(request)}"] = config.PLAN_LIMIT_IP
    if config.PLAN_LIMIT_SITE:
        limits["site"] = config.PLAN_LIMIT_SITE
    day = today()
    if limits and not accounts.STORE.consume(day, limits):
        raise HTTPException(429, full_message(day, limits))
    return day, list(limits)


def full_message(day: str, limits: dict[str, int]) -> str:
    full = {key for key, limit in limits.items() if accounts.STORE.usage(day, key) >= limit}
    if "site" in full:
        return f"今天的名额已经用完了（全站每天 {config.PLAN_LIMIT_SITE} 份），明天再来吧。"
    if any(key.startswith("ip:") for key in full) and not any(key.startswith("user:") for key in full):
        return f"这个网络今天的生成次数已用完（每个 IP 每天 {config.PLAN_LIMIT_IP} 次），明天再来吧。"
    return f"今天的生成次数已用完（每个账号每天 {config.PLAN_LIMIT_USER or config.PLAN_LIMIT_IP} 次），明天再来吧。"


def refund(charge: tuple[str, list[str]] | None) -> None:
    if charge:
        accounts.STORE.refund(*charge)


_understood: dict[str, set[str]] = {}   # 当天已计过次数的需求文字（账号 → 摘要），改表重算同一段不再计


def guard_understand(user: dict | None, query: str) -> None:
    """一句话需求每段新文字调一次模型，按账号计每天次数；没写文字（只改表）的不计。"""
    text = (query or "").strip()
    if not text or not config.UNDERSTAND_LIMIT_USER or not user or unlimited(user):
        return
    day = today()
    digest = hashlib.sha256(text.encode()).hexdigest()
    with _lock:
        seen = _understood.setdefault(f"{day}:{user['id']}", set())
        if digest in seen:
            return
        if len(_understood) > 5000:
            for stale in [key for key in _understood if not key.startswith(day)]:
                del _understood[stale]
    if not accounts.STORE.consume(day, {f"understand:{user['id']}": config.UNDERSTAND_LIMIT_USER}):
        raise HTTPException(429, f"今天跟 AI 说需求的次数已用完（每个账号每天 {config.UNDERSTAND_LIMIT_USER} 次）。"
                                 "表里的内容仍可直接修改，也可以改用详细填写。")
    with _lock:
        _understood.setdefault(f"{day}:{user['id']}", set()).add(digest)


def quota(user: dict | None) -> dict | None:
    if not user or not config.PLAN_LIMIT_USER or unlimited(user):
        return None
    used = accounts.STORE.usage(today(), f"user:{user['id']}")
    return {"limit": config.PLAN_LIMIT_USER, "used": used, "remaining": max(0, config.PLAN_LIMIT_USER - used)}


def rate_limit(request: Request, bucket: str) -> None:
    if not config.RATE_LIMIT:
        return
    count, window = RATE_LIMITS[bucket]
    now = time.time()
    key = f"{bucket}:{client_ip(request)}"
    with _lock:
        if len(_hits) > 20000:
            for stale in [name for name, stamps in _hits.items() if not stamps or stamps[-1] < now - 3600]:
                del _hits[stale]
        stamps = [stamp for stamp in _hits.get(key, []) if stamp > now - window]
        if len(stamps) >= count:
            _hits[key] = stamps
            raise HTTPException(429, "请求太频繁，请稍后再试。")
        stamps.append(now)
        _hits[key] = stamps
