"""FastAPI 服务。只绑定 127.0.0.1（AGENTS.md 规则 7）：
uv run uvicorn app.api:app --host 127.0.0.1 --port 18600
构建好的前端（frontend/dist）由同一端口在 / 下提供。
"""
from __future__ import annotations


import logging
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.gzip import GZipMiddleware
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from .cities import current_city
from . import config
from . import accounts
from . import access, onboarding_status, stay_audit
from .data import load_pois, load_restaurants, tag_source
from .geo import bd09_to_gcj02, hm
from .graph import run_plan
from .understanding import understand
from .places import PlaceSearchError, search_places
from .navigation import NavigationRequest, NavigationBoundsError, get_navigation
from .place_details import PlaceDetailsRequest, get_place_details
from .schemas import (AUDIENCES, COMPANION_LABELS, FAMILIARITY_LEVELS, INTERESTS, PACE_LABELS, SPICE_LEVELS, Hotel, TripContext, UserProfile,
                      MealEditPreviewRequest, MealEditCommitRequest)
from .sessions import SESSIONS, SessionNotFound, StaleRevision, PreviewNotFound
from .mcp_login import router as mcp_login_router
from .progress import stream_plan_events
from .presentation import serialize_plan, city_entry, hotel_presets
from .cities import CityDisabled, city_config, enabled_cities, ensure_enabled, reference_city
from .poi_catalog import current_catalog, published_catalog, result_catalog, use_catalog

# uvicorn 只配置它自己的 logger；应用日志（模型调用耗时等）另挂一个输出到标准错误，由 systemd 收进用户日志
_app_log = logging.getLogger("app")
if not _app_log.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
    _app_log.addHandler(_handler)
    _app_log.setLevel(logging.INFO)

app = FastAPI(title=f"{current_city().name}个性化行程规划 Agent", version="0.1.0")
app.include_router(accounts.router)
app.include_router(mcp_login_router)
app.include_router(stay_audit.router)
app.include_router(onboarding_status.router)  # 只在设置 TTA_ONBOARDING_STATUS_ROOT 的测试实例开放


@app.middleware("http")
async def private_responses(request: Request, call_next):
    if access.blocked(request):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    response = await call_next(request)
    if request.url.path.startswith(("/api/auth", "/api/history", "/api/plan", "/api/trips")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail":[{"loc":list(item["loc"]), "msg":item["msg"], "type":item["type"]}
                                                        for item in error.errors()]})


def gcj(lat: float, lon: float) -> dict[str, float]:
    la, lo = bd09_to_gcj02(lat, lon)
    return {"lat": round(la, 6), "lon": round(lo, 6)}



class PlanRequest(BaseModel):
    query: str = ""
    form: dict[str, Any] = Field(default_factory=dict)
    select_mode: Literal["llm", "rule", "jev"] = "llm"
    enable_repair: bool = True
    # 合理性精修（app/refine.py）。不传时跟随 enable_repair：网页规划默认开启精修是用户 2026-10-16 看过效果后的决定；
    # 评测与论文配置必须显式传值（论文“本系统 B”为 False，B+ 为 True）。
    enable_refine: bool | None = None
    guide_mode: Literal["off", "pilot"] = "off"
    travel_source: Literal["amap", "estimate"] | None = None  # 实验开关；默认有 Web服务 key 就用高德

    @model_validator(mode="after")
    def validate_experiments(self):
        if self.select_mode == "jev" and self.guide_mode == "pilot":
            raise ValueError("Jev 选点试验暂不与攻略试验混用")
        return self

    @field_validator("form")
    @classmethod
    def validate_form(cls, value):
        profile = UserProfile.model_validate({"days":5,"day_start":0,"day_end":1440,**value})
        ensure_enabled(profile.city)
        return value


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "pois": len(load_pois()), "restaurants": len(load_restaurants()), "tag_source": tag_source(),
            "llm_model": config.LLM_MODEL, "llm_thinking": config.LLM_THINKING, "llm_key_set": bool(config.LLM_API_KEY)}


def enabled_city(key: str) -> str:
    try:
        ensure_enabled(key)
    except CityDisabled as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except ValueError:
        raise HTTPException(status_code=404, detail="未知城市") from None
    return key


def ensure_editable(result: dict) -> None:
    """停用城市的历史行程只读：可查看，不能换餐、撤销或提交修改。"""
    try:
        ensure_enabled(reference_city(result.get("poi_catalog")))
    except CityDisabled as error:
        raise HTTPException(status_code=409, detail=str(error)) from None


@app.get("/api/meta")
def meta(city: str | None = Query(None, max_length=40)) -> dict[str, Any]:
    city = enabled_city(city or ("hangzhou" if "hangzhou" in enabled_cities() else enabled_cities()[0]))
    return {"city": city, "cities": [city_entry(key) for key in enabled_cities()],
            "interests": INTERESTS, "spice_levels": SPICE_LEVELS, "familiarity_levels": FAMILIARITY_LEVELS, "audiences": AUDIENCES, "hotels": hotel_presets(city),
            # Web端 key 本来就会出现在浏览器里；安全密钥不下发，由 /_AMapService 代理附加（A8）
            "map": {"provider": "amap" if config.AMAP_JS_KEY and config.AMAP_JS_SECURITY else "leaflet",
                    "amap_js_key": config.AMAP_JS_KEY or None},
            "access": {"login_to_plan": config.REQUIRE_LOGIN_TO_PLAN, "invite_required": bool(config.INVITE_CODES)},
            "paces": PACE_LABELS, "companions": COMPANION_LABELS}


@app.get("/api/guides/status")
def guides_status() -> dict[str, Any]:
    from .guides import corpus_status
    return corpus_status()


@app.get("/api/pois")
def pois(city: str = Query("hangzhou", max_length=40)) -> list[dict[str, Any]]:
    with use_catalog(published_catalog(enabled_city(city))):
        return [{**p.model_dump(include={"id", "name", "type", "price", "interests", "stamina", "summary"}),
                 **gcj(p.lat, p.lon)} for p in load_pois().values()]


@app.get("/api/places/search")
async def places_search(request: Request, q: str = Query(min_length=2, max_length=80),
                        city: str = Query("hangzhou", max_length=40)) -> dict[str, Any]:
    access.rate_limit(request, "places")
    city = enabled_city(city)
    if len(q.strip()) < 2:
        raise HTTPException(status_code=422, detail="请输入至少两个字的地点名称。")
    try:
        return {"places": await search_places(q, city)}
    except PlaceSearchError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


def refine_enabled(req: PlanRequest) -> bool:
    return req.enable_repair if req.enable_refine is None else req.enable_refine


class UnderstandRequest(BaseModel):
    query: str = Field("", max_length=3000)  # 跟 AI 说模式把几轮的话按行合在一起传
    form: dict[str, Any] = Field(default_factory=dict)
    context: TripContext | None = None
    places: list[Hotel] = Field(default_factory=list, max_length=10)  # 跟 AI 说：表上搜过选过的起点，带坐标


@app.post("/api/plan/understand")
def understand_request(req: UnderstandRequest, request: Request) -> dict[str, Any]:
    """规划前的“我是这么理解的”：解析一句话、标明每项来源并预检；不扣每天的生成次数。"""
    accounts.same_origin(request)
    access.require_login(request)
    access.rate_limit(request, "understand")
    city = enabled_city(req.form.get("city") or "hangzhou")
    try:
        with use_catalog(published_catalog(city)):
            result = understand(req.query, {**req.form, "city": city}, req.context,
                                places=[place.model_dump() for place in req.places])
    except ValidationError:
        raise HTTPException(status_code=422, detail="出游条件格式不正确，请检查日期、活动时段、预算和起点。") from None
    except Exception:
        raise HTTPException(status_code=502, detail="暂时没能理解这段需求，请稍后重试，或改用详细填写。") from None
    return result.model_dump(mode="json")


@app.post("/api/plan")
def plan(req: PlanRequest, request: Request, response: Response) -> dict[str, Any]:
    accounts.same_origin(request)
    access.guard_plan(request)
    owner = accounts.principal(request, response)
    try:
        r = run_plan(req.query, req.form, select_mode=req.select_mode, enable_repair=req.enable_repair, enable_refine=refine_enabled(req),
                     travel_source=req.travel_source, guide_mode=req.guide_mode)
    except ValidationError:
        raise HTTPException(status_code=422, detail="出游条件格式不正确，请检查日期、活动时段、预算和起点。") from None
    except CityDisabled as error:
        raise HTTPException(status_code=409, detail=str(error)) from None
    except Exception:
        raise HTTPException(status_code=502, detail="规划暂未完成，请保留条件后重试；外部数据或模型可能暂不可用。") from None
    request_meta = {"select_mode": req.select_mode, "enable_repair": req.enable_repair, "enable_refine": refine_enabled(req), "guide_mode": req.guide_mode, "input": req.model_dump()}
    session = SESSIONS.create(r, request_meta, owner)
    return deliver_plan(r, session, 0)


@app.post("/api/plan/stream")
async def plan_stream(req: PlanRequest, request: Request):
    accounts.same_origin(request)
    access.guard_plan(request)
    cookie_response = Response()
    owner = accounts.principal(request, cookie_response)
    def generate(progress):
        result = run_plan(req.query, req.form, select_mode=req.select_mode, enable_repair=req.enable_repair, enable_refine=refine_enabled(req),
                          travel_source=req.travel_source, guide_mode=req.guide_mode, progress=progress)
        request_meta = {"select_mode": req.select_mode, "enable_repair": req.enable_repair, "enable_refine": refine_enabled(req), "guide_mode": req.guide_mode, "input": req.model_dump()}
        session = SESSIONS.create(result, request_meta, owner)
        return deliver_plan(result, session, 0)

    response = StreamingResponse(stream_plan_events(generate, request.is_disconnected),
                             media_type="application/x-ndjson",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})
    response.raw_headers.extend(header for header in cookie_response.raw_headers if header[0] == b"set-cookie")
    return response


def deliver_plan(result, session, revision, change=None):
    payload = serialize_plan(result, session.request, session.plan_id, revision, change)
    if session.owner and session.owner.startswith("user:"):
        try:
            payload["history_id"] = accounts.STORE.save(session.owner[5:], session.plan_id,
                                                       session.request.get("input", {}), payload)
            SESSIONS.bind_history(session.plan_id, payload["history_id"])
        except (OSError, accounts.sqlite3.Error):
            payload["history_error"] = "行程已生成，但历史记录暂未保存，请点击保存重试。"
    return payload


@app.get("/api/trips/{history_id}")
def linked_trip(history_id: str, request: Request):
    user = accounts.current_user(request, True)
    saved = accounts.STORE.get(user["id"], history_id)
    session = SESSIONS.find_history(f"user:{user['id']}", history_id)
    if session is not None:
        result, meta, revision, owner = SESSIONS.snapshot(session.plan_id)
        saved["plan"] = {**serialize_plan(result, meta, session.plan_id, revision),
                         "history_id": history_id, "history_readonly": False}
    return saved


@app.post("/api/plans/{plan_id}/save")
def save_current_plan(plan_id: str, request: Request):
    accounts.same_origin(request)
    user = accounts.current_user(request, True)
    try:
        session = SESSIONS.get(plan_id)
        accounts.authorize_session(session, request, allow_guest_claim=True)
        guest = request.cookies.get(accounts.GUEST_COOKIE, "")
        session = SESSIONS.claim(plan_id, f"guest:{accounts.hash_token(guest)}", f"user:{user['id']}")
    except SessionNotFound:
        raise HTTPException(410, "行程会话已过期，请重新规划后保存。") from None
    except PermissionError:
        raise HTTPException(404, "这份行程不存在或不属于当前账号。") from None
    return deliver_plan(session.result(), session, session.current)


class SwapMealRequest(BaseModel):
    revision: int = Field(ge=0)
    day: int = Field(ge=1, le=5)
    item_index: int = Field(ge=0, le=60)
    restaurant_id: int


class RevertRequest(BaseModel):
    revision: int = Field(ge=0)
    base_revision: int = Field(ge=0)


EXPIRED = "这份行程已过期（服务重启或超过 6 小时未操作），请重新生成后再修改。"


@app.post("/api/plans/{plan_id}/swap-meal")
def swap_meal_endpoint(plan_id: str, req: SwapMealRequest, request: Request) -> dict[str, Any]:
    """旧入口保留版本及所有权检查，禁止跳过预览直接提交。"""
    accounts.same_origin(request)
    try:
        session = SESSIONS.get(plan_id)
        accounts.authorize_session(session, request)
        if session.request.get("source") == "mcp":
            raise HTTPException(409, "MCP行程换餐需要先预览并确认。")
        if req.revision != session.current:
            raise StaleRevision
    except SessionNotFound:
        raise HTTPException(status_code=410, detail=EXPIRED) from None
    except StaleRevision:
        raise HTTPException(status_code=409, detail="行程刚刚被修改过，请刷新后再操作。") from None
    raise HTTPException(status_code=428, detail="请先查看更换预览，再确认提交；原行程保持不变。")


@app.post("/api/plans/{plan_id}/meal-preview")
def preview_meal_endpoint(plan_id: str, req: MealEditPreviewRequest, request: Request) -> dict[str, Any]:
    from copy import deepcopy
    from .meal_edits import build_preview
    from .replan import ReplanError
    accounts.same_origin(request)
    access.rate_limit(request, "swap")
    try:
        session = SESSIONS.get(plan_id)
        accounts.authorize_session(session, request)
        ensure_editable(session.result())
        if req.revision != session.current:
            raise StaleRevision()
        updated, change = build_preview(deepcopy(session.result(req.revision)), req)
        if session.request.get("source") == "mcp":
            from .mcp_domain import delivery_status
            if delivery_status(updated, allow_unverified_draft=session.request.get("allow_unverified_draft", False))["status"] not in {"ready", "draft"}:
                raise HTTPException(422, "预览未达到MCP真实路线与证据门槛，原行程保持不变。")
        payload = serialize_plan(updated, session.request, plan_id, req.revision, change)
        if session.request.get("source") == "mcp":
            updated["delivery_status"] = delivery_status(updated, allow_unverified_draft=session.request.get("allow_unverified_draft", False))["status"]
        preview = SESSIONS.add_preview(plan_id, req.revision, updated, change)
    except SessionNotFound:
        raise HTTPException(410, EXPIRED) from None
    except StaleRevision:
        raise HTTPException(409, "行程已经变化，请基于当前版本重新计算预览。") from None
    except ReplanError as error:
        raise HTTPException(status_code=error.status, detail=error.message) from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "预览暂未完成，原行程保持不变，请稍后重试。") from None
    return {"preview_id": preview.preview_id, "base_revision": preview.base_revision, "expires_at": preview.expires_at,
            "can_commit": updated["report"].passed, "change": change, "plan": payload}


@app.post("/api/plans/{plan_id}/meal-commit")
def commit_meal_endpoint(plan_id: str, req: MealEditCommitRequest, request: Request) -> dict[str, Any]:
    from .meal_edits import validate_preview
    from .replan import ReplanError
    accounts.same_origin(request)
    access.rate_limit(request, "swap")
    try:
        session = SESSIONS.get(plan_id)
        accounts.authorize_session(session, request)
        ensure_editable(session.result())
        preview = SESSIONS.get_preview(plan_id, req.revision, req.preview_id)
        if not preview.result["report"].passed or preview.result["report"].hard:
            raise HTTPException(422, "这份预览含有硬约束冲突，原行程保持不变，请重新计算预览。")
        report = validate_preview(preview.result)
        if not report.passed or report.hard:
            raise HTTPException(422, "新方案未通过校验，原行程保持不变：" + "；".join(issue.message for issue in report.hard))
        if any(abs(report.totals.get(key, 0) - preview.result["report"].totals.get(key, 0)) > 0.01
               for key in ("tickets", "meals", "transport", "total")):
            raise HTTPException(422, "费用依据已变化，请重新计算预览后再确认；原行程保持不变。")
        if session.request.get("source") == "mcp":
            from .mcp_domain import delivery_status
            checked = {**preview.result, "report": report}
            if delivery_status(checked, allow_unverified_draft=session.request.get("allow_unverified_draft", False))["status"] not in {"ready", "draft"}:
                raise HTTPException(422, "预览证据已过期或不完整，请重新预览。")
        updated, change, revision = SESSIONS.commit_preview(plan_id, req.revision, req.preview_id, report)
    except SessionNotFound:
        raise HTTPException(410, EXPIRED) from None
    except PreviewNotFound:
        raise HTTPException(410, "这份预览已过期，请重新计算；原行程保持不变。") from None
    except StaleRevision:
        raise HTTPException(409, "行程已经变化，请重新计算预览后再确认。") from None
    except ReplanError as error:
        raise HTTPException(error.status, error.message) from None
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(502, "提交暂未完成，原行程保持不变，请稍后重试。") from None
    return deliver_plan(updated, session, revision, change)


@app.get("/api/plans/{plan_id}")
def current_plan_endpoint(plan_id: str, request: Request) -> dict[str, Any]:
    try:
        session = SESSIONS.get(plan_id)
        accounts.authorize_session(session, request)
        return serialize_plan(session.result(), session.request, plan_id, session.current)
    except SessionNotFound:
        raise HTTPException(410, EXPIRED) from None


@app.post("/api/plans/{plan_id}/revert")
def revert_endpoint(plan_id: str, req: RevertRequest, request: Request) -> dict[str, Any]:
    """撤销：恢复旧内容，版本号仍递增。"""
    accounts.same_origin(request)
    try:
        session = SESSIONS.get(plan_id)
        accounts.authorize_session(session, request)
        ensure_editable(session.result())
        if session.request.get("source") == "mcp":
            from .mcp_domain import delivery_status
            if delivery_status(session.result(req.revision), allow_unverified_draft=session.request.get("allow_unverified_draft", False))["status"] not in {"ready", "draft"}:
                raise HTTPException(422, "旧版本未达到MCP证据门槛，不能恢复。")
        result, revision = SESSIONS.revert(plan_id, req.revision, req.base_revision)
    except SessionNotFound:
        raise HTTPException(status_code=410, detail=EXPIRED) from None
    except StaleRevision:
        raise HTTPException(status_code=409, detail="行程刚刚被修改过，不能撤销旧版本的操作。") from None
    return deliver_plan(result, session, revision)


def plan_catalog(plan_id: str | None, request: Request, city: str | None = None):
    """详情/图片按所属行程的目录版本解析；行程过期或不属于当前访问者时读当前发布版。"""
    if plan_id:
        try:
            session = SESSIONS.get(plan_id)
            accounts.authorize_session(session, request)
            return result_catalog(session.result())
        except (SessionNotFound, HTTPException):
            pass
    return use_catalog(published_catalog(city) if city else current_catalog())


@app.get("/api/place-media")
def place_media(kind: Literal["poi", "meal"], id: int, http: Request,  # noqa: A002 — 与前端字段名一致
                plan_id: str | None = Query(None, max_length=64)) -> dict[str, Any]:
    """地点图片（最多 5 张），来源与留存规范见 app/media.py、docs/图片资源规范.md。"""
    access.rate_limit(http, "media")
    from .media import get_place_media
    with plan_catalog(plan_id, http):
        result = get_place_media(kind, id)
    if result is None:
        raise HTTPException(status_code=404, detail="找不到这个地点")
    return result.model_dump()


@app.post("/api/navigation")
def navigation(request: NavigationRequest, http: Request) -> dict[str, Any]:
    from .amap import AmapError
    access.rate_limit(http, "navigation")
    try:
        with plan_catalog(request.plan_id, http, request.city):  # 有权访问的行程优先；过期历史按显式城市兜底
            return get_navigation(request)
    except NavigationBoundsError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    except AmapError:
        raise HTTPException(status_code=503, detail="导航服务暂不可用，请稍后重试") from None


@app.post("/api/place-details")
def place_details(request: PlaceDetailsRequest, http: Request) -> dict[str, Any]:
    access.rate_limit(http, "details")
    with plan_catalog(request.plan_id, http):
        return get_place_details(request)


# 高德 JS API 安全密钥代理（官方推荐的 serviceHost 方式）：前端只设置 serviceHost，jscode 由后端附加
_AMAP_UPSTREAM = [("v4/map/styles", "https://webapi.amap.com/v4/map/styles"),
                  ("v3/vectormap", "https://fmap01.amap.com/v3/vectormap"),
                  ("", "https://restapi.amap.com/")]


@app.get("/_AMapService/{path:path}")
async def amap_proxy(path: str, request: Request) -> Response:
    if not config.AMAP_JS_SECURITY:
        raise HTTPException(status_code=404)
    access.rate_limit(request, "amap")
    prefix, base = next((p, u) for p, u in _AMAP_UPSTREAM if path.startswith(p))
    url = base + path[len(prefix):]
    params = [(k, v) for k, v in request.query_params.multi_items() if k != "jscode"] + [("jscode", config.AMAP_JS_SECURITY)]
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(url, params=params)
    return Response(content=r.content, status_code=r.status_code,
                    media_type=r.headers.get("content-type", "application/json"))


from .media import FILES_DIR as _MEDIA_FILES  # noqa: E402

_MEDIA_FILES.mkdir(parents=True, exist_ok=True)
# 后续城市图片使用独立 URL；杭州既有 URL 和目录保持不变。
from .cities import _definitions, city_config  # noqa: E402
for _city_key in _definitions():
    if _city_key != "hangzhou":
        _city_files = city_config(_city_key).assets_dir / "media" / "files"
        if _city_files.is_dir():
            app.mount(f"/media/files/{_city_key}", StaticFiles(directory=_city_files), name=f"media-{_city_key}")
app.mount("/media/files", StaticFiles(directory=_MEDIA_FILES), name="media-files")  # 人工补充的图片（随仓库管理）

class FrontendFiles(StaticFiles):
    """前端构建产物：文件名带哈希的 assets/ 长期缓存，index.html 等每次校验。"""

    async def get_response(self, path: str, scope) -> Response:
        response = await super().get_response(path, scope)
        if path.startswith("assets/") and response.status_code in (200, 304):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def frontend_app(directory) -> GZipMiddleware:
    # 只压缩前端静态文件；规划进度流不经过这里，避免压缩缓冲拖慢逐条推送。
    # 无网关直连（如经 SSH 隧道访问测试实例）时，脚本和样式约缩小七成
    return GZipMiddleware(FrontendFiles(directory=directory, html=True), minimum_size=1024)


_dist = config.FRONTEND_DIST
if _dist.exists():
    app.mount("/", frontend_app(_dist), name="frontend")
