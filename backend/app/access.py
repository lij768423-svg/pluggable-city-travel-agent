"""公网访问控制：生成行程要求登录、按账号与 IP 计每日次数；外部数据接口按 IP 限频；公网域名屏蔽研究工具。

开关全部来自环境变量，公网部署时由 systemd 的 EnvironmentFile 提供（见 docs/公网部署.md），
默认全部关闭，本地开发与测试行为不变。
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, Request

from . import accounts, config

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


def guard_plan(request: Request) -> None:
    """生成行程前调用：未登录（开启时）返回 401；当天次数用完返回 429。计数在开始规划时扣除。"""
    user = require_login(request)
    if unlimited(user):
        return
    limits = {}
    if user and config.PLAN_LIMIT_USER:
        limits[f"user:{user['id']}"] = config.PLAN_LIMIT_USER
    if config.PLAN_LIMIT_IP:
        limits[f"ip:{client_ip(request)}"] = config.PLAN_LIMIT_IP
    if limits and not accounts.STORE.consume(today(), limits):
        raise HTTPException(429, f"今天的生成次数已用完（每个账号每天 {config.PLAN_LIMIT_USER or config.PLAN_LIMIT_IP} 次），明天再来吧。")


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
