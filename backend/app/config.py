"""全局配置：路径与 LLM 环境变量。"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BACKEND_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = BACKEND_DIR.parent
DATA_DIR = Path(os.environ.get("TTA_DATA_DIR", REPO_DIR / "data"))
CITY_DIR = DATA_DIR / "hangzhou"  # 旧脚本的杭州别名；按城市读取使用 app.cities
LLM_CACHE_DIR = DATA_DIR / "llm_cache"

load_dotenv(BACKEND_DIR / ".env")

LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "https://api.minimaxi.com/v1")
LLM_MODEL = os.environ.get("LLM_MODEL", "MiniMax-M3.1-Flash-Preview")
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
# 评测可复现 + 节省共用额度：默认缓存 LLM 响应（相同请求直接复用）
LLM_CACHE = os.environ.get("LLM_CACHE", "1") == "1"
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "180"))
# MiniMax-M3 思考模式：disabled | adaptive | ""（不传参数）
LLM_THINKING = os.environ.get("LLM_THINKING", "adaptive")
TYPESAFE_API_KEY = os.environ.get("TYPESAFE_API_KEY", "")
TYPESAFE_MODEL = os.environ.get("TYPESAFE_MODEL", "jev-1.13.0")
TYPESAFE_TIMEOUT = float(os.environ.get("TYPESAFE_TIMEOUT", "12"))
TYPESAFE_PROXY_URL = os.environ.get("TYPESAFE_PROXY_URL", "").strip()

# 高德：Web服务 key 用于服务端 REST（独立校验的真实路程）；Web端 key + 安全密钥用于前端 JS API 地图
AMAP_WEB_KEY = os.environ.get("AMAP_WEB_KEY", "")
AMAP_JS_KEY = os.environ.get("AMAP_JS_KEY", "")
AMAP_JS_SECURITY = os.environ.get("AMAP_JS_SECURITY", "")
ONLINE_RESTAURANTS = os.environ.get("TTA_ONLINE_RESTAURANTS", "1") == "1"

# ── 公网部署（docs/公网部署.md）：由 systemd 的 EnvironmentFile 提供，不写进仓库；默认全部关闭 ──
REQUIRE_LOGIN_TO_PLAN = os.environ.get("TTA_REQUIRE_LOGIN", "0") == "1"
PLAN_LIMIT_USER = int(os.environ.get("TTA_PLAN_LIMIT_USER", "0") or 0)   # 每个账号每天可生成次数，0 = 不限
# 不受每日次数限制的账号（用户名，逗号分隔），如站长自己的账号；仍然要求登录，也不占所在 IP 的额度
UNLIMITED_USERS = frozenset(name.strip() for name in os.environ.get("TTA_UNLIMITED_USERS", "").split(",") if name.strip())
PLAN_LIMIT_IP = int(os.environ.get("TTA_PLAN_LIMIT_IP", "0") or 0)       # 每个 IP 每天可生成次数，0 = 不限
PLAN_LIMIT_SITE = int(os.environ.get("TTA_PLAN_LIMIT_SITE", "0") or 0)   # 全站每天可生成次数（护住模型与高德额度），0 = 不限
# 每个账号每天“一句话说需求”/跟 AI 说的次数（每段新文字算一次，同一段改表重算不算），0 = 不限
UNDERSTAND_LIMIT_USER = int(os.environ.get("TTA_UNDERSTAND_LIMIT_USER", "0") or 0)
PLAN_CONCURRENCY = int(os.environ.get("TTA_PLAN_CONCURRENCY", "0") or 0)  # 同时生成几份，其余排队；0 = 不排队
PLAN_QUEUE_MAX = int(os.environ.get("TTA_PLAN_QUEUE_MAX", "30") or 0)     # 最多几人排队，满了请稍后再来；0 = 不限
RATE_LIMIT = os.environ.get("TTA_RATE_LIMIT", "0") == "1"                # 外部数据接口按 IP 限频
INVITE_CODES = frozenset(code.strip() for code in os.environ.get("TTA_INVITE_CODES", "").split(",") if code.strip())
PUBLIC_HOSTS = frozenset(host.strip().lower() for host in os.environ.get("TTA_PUBLIC_HOSTS", "").split(",") if host.strip())

# 景点目录：base_only（缺省，只读原库）| with_supplement（隔离实例显式开启，叠加已发布补充目录）
POI_CATALOG_MODE = os.environ.get("TTA_POI_CATALOG", "base_only")
POI_SUPPLEMENT_DIR = Path(os.environ.get("TTA_POI_SUPPLEMENT_DIR", DATA_DIR / "poi_supplement"))

# 隔离验证可指定独立静态目录；默认保持既有网页构建产物。
FRONTEND_DIST = Path(os.environ.get("TTA_FRONTEND_DIST", REPO_DIR / "frontend" / "dist"))
