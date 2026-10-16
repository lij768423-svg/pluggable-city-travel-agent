"""FastAPI 服务。只绑定 127.0.0.1（AGENTS.md 规则 7）：
uv run uvicorn app.api:app --host 127.0.0.1 --port 18600
构建好的前端（frontend/dist）由同一端口在 / 下提供。
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Literal

import pandas as pd
import httpx
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from . import config
from . import accounts
from . import access, stay_audit
from .data import load_pois, load_restaurants, tag_source
from .geo import bd09_to_gcj02, hm
from .graph import run_plan
from .places import PlaceSearchError, search_places
from .navigation import NavigationRequest, get_navigation
from .place_details import PlaceDetailsRequest, describe_place, get_place_details, remember_restaurants
from .schemas import AUDIENCES, DIETARY, INTERESTS, UserProfile
from .sessions import SESSIONS, SessionNotFound, StaleRevision
from .progress import stream_plan_events

app = FastAPI(title="杭州个性化行程规划 Agent", version="0.1.0")
app.include_router(accounts.router)
app.include_router(stay_audit.router)


@app.middleware("http")
async def private_responses(request: Request, call_next):
    if access.blocked(request):
        return JSONResponse(status_code=404, content={"detail": "Not Found"})
    response = await call_next(request)
    if request.url.path.startswith(("/api/auth", "/api/history", "/api/plan")):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, error: RequestValidationError) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail":[{"loc":list(item["loc"]), "msg":item["msg"], "type":item["type"]}
                                                        for item in error.errors()]})


def gcj(lat: float, lon: float) -> dict[str, float]:
    la, lo = bd09_to_gcj02(lat, lon)
    return {"lat": round(la, 6), "lon": round(lo, 6)}


@lru_cache(maxsize=1)
def hotel_presets() -> list[dict[str, Any]]:
    df = pd.read_parquet(config.CITY_DIR / "accommodations.parquet")
    out = []
    for area in ["湖滨", "西溪", "武林", "钱江新城", "滨江", "灵隐"]:
        hit = df[df.name.str.contains(area)]
        if len(hit):
            r = hit.iloc[0]
            out.append({"area": area, "name": r["name"], "lat": float(r.lat), "lon": float(r.lon), **{"gcj": gcj(r.lat, r.lon)}})
    return out


class PlanRequest(BaseModel):
    query: str = ""
    form: dict[str, Any] = Field(default_factory=dict)
    select_mode: Literal["llm", "rule", "jev"] = "llm"
    enable_repair: bool = True
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
        UserProfile.model_validate({"days":5,"day_start":0,"day_end":1440,**value})
        return value


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"ok": True, "pois": len(load_pois()), "restaurants": len(load_restaurants()), "tag_source": tag_source(),
            "llm_model": config.LLM_MODEL, "llm_thinking": config.LLM_THINKING, "llm_key_set": bool(config.LLM_API_KEY)}


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    return {"interests": INTERESTS, "dietary": DIETARY, "audiences": AUDIENCES, "hotels": hotel_presets(),
            # Web端 key 本来就会出现在浏览器里；安全密钥不下发，由 /_AMapService 代理附加（A8）
            "map": {"provider": "amap" if config.AMAP_JS_KEY and config.AMAP_JS_SECURITY else "leaflet",
                    "amap_js_key": config.AMAP_JS_KEY or None},
            "access": {"login_to_plan": config.REQUIRE_LOGIN_TO_PLAN, "invite_required": bool(config.INVITE_CODES)},
            "paces": {"relaxed": "轻松", "normal": "适中", "intense": "紧凑"},
            "companions": {"solo": "独自", "couple": "情侣", "friends": "朋友", "family_kids": "带孩子", "elderly": "带老人"}}


@app.get("/api/guides/status")
def guides_status() -> dict[str, Any]:
    from .guides import corpus_status
    return corpus_status()


@app.get("/api/pois")
def pois() -> list[dict[str, Any]]:
    return [{**p.model_dump(include={"id", "name", "type", "price", "interests", "stamina", "summary"}),
             **gcj(p.lat, p.lon)} for p in load_pois().values()]


@app.get("/api/places/search")
async def places_search(request: Request, q: str = Query(min_length=2, max_length=80)) -> dict[str, Any]:
    access.rate_limit(request, "places")
    if len(q.strip()) < 2:
        raise HTTPException(status_code=422, detail="请输入至少两个字的地点名称。")
    try:
        return {"places": await search_places(q)}
    except PlaceSearchError as error:
        raise HTTPException(status_code=503, detail=str(error)) from None


@app.post("/api/plan")
def plan(req: PlanRequest, request: Request, response: Response) -> dict[str, Any]:
    accounts.same_origin(request)
    access.guard_plan(request)
    owner = accounts.principal(request, response)
    try:
        r = run_plan(req.query, req.form, select_mode=req.select_mode, enable_repair=req.enable_repair, enable_refine=req.enable_repair,
                     travel_source=req.travel_source, guide_mode=req.guide_mode)
    except ValidationError:
        raise HTTPException(status_code=422, detail="出游条件格式不正确，请检查日期、活动时段、预算和起点。") from None
    except Exception:
        raise HTTPException(status_code=502, detail="规划暂未完成，请保留条件后重试；外部数据或模型可能暂不可用。") from None
    request_meta = {"select_mode": req.select_mode, "enable_repair": req.enable_repair, "guide_mode": req.guide_mode, "input": req.model_dump()}
    session = SESSIONS.create(r, request_meta, owner)
    return deliver_plan(r, session, 0)


@app.post("/api/plan/stream")
async def plan_stream(req: PlanRequest, request: Request):
    accounts.same_origin(request)
    access.guard_plan(request)
    cookie_response = Response()
    owner = accounts.principal(request, cookie_response)
    def generate(progress):
        result = run_plan(req.query, req.form, select_mode=req.select_mode, enable_repair=req.enable_repair, enable_refine=req.enable_repair,
                          travel_source=req.travel_source, guide_mode=req.guide_mode, progress=progress)
        request_meta = {"select_mode": req.select_mode, "enable_repair": req.enable_repair, "guide_mode": req.guide_mode, "input": req.model_dump()}
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
        except (OSError, accounts.sqlite3.Error):
            payload["history_error"] = "行程已生成，但历史记录暂未保存，请点击保存重试。"
    return payload


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


def serialize_plan(r: dict[str, Any], request_meta: dict[str, Any], plan_id: str | None = None,
                   revision: int | None = None, change: dict[str, Any] | None = None) -> dict[str, Any]:
    """把规划结果转成前端格式（新规划、换餐厅、撤销共用）。"""
    P, R = load_pois(), r.get("restaurants", load_restaurants())
    restaurant_ids = {reference for day in r["itinerary"].days for item in day.items if item.kind == "meal"
                      for reference in [item.ref_id, *(alternative.ref_id for alternative in item.alternatives)]}
    remember_restaurants(R[reference] for reference in restaurant_ids if reference in R)
    cand = {c.poi_id: c for c in r["candidates"]}
    src = {s.poi_id: s.source for s in r["selected"]}
    cited = {selection.poi_id: set(selection.guide_ids) for selection in r["selected"]}
    days = []
    for d in r["itinerary"].days:
        items = []
        for i in d.items:
            place = P.get(i.ref_id) if i.kind == "poi" else R.get(i.ref_id)
            alternatives = []
            for alternative in i.alternatives:
                restaurant = R.get(alternative.ref_id)
                alternatives.append({**alternative.model_dump(),
                                     **(gcj(restaurant.lat, restaurant.lon) if restaurant else {}),
                                     "detail": describe_place(restaurant, "meal") if restaurant else None})
            items.append({**i.model_dump(), "start_hm": hm(i.start), "end_hm": hm(i.end),
                          "alternatives": alternatives,
                          **(gcj(place.lat, place.lon) if place else {}),
                          "type": getattr(place, "type", getattr(place, "cuisine", "")),
                          "visit_group": getattr(place, "visit_group", ""),
                          "visit_group_label": getattr(place, "visit_group_label", ""),
                          "visit_role": getattr(place, "visit_role", "attraction"),
                          "source": "rule" if i.evening else src.get(i.ref_id) if i.kind == "poi" else None,
                          "detail": describe_place(place, i.kind) if place else None,
                          "guide_evidence": [entry for entry in r.get("guide_evidence", []) if not i.evening and i.kind == "poi" and entry["id"] in cited.get(i.ref_id, set())],
                          "price_verified": getattr(place, "price_verified", False),
                          "restaurant": place.model_dump(include={"provider", "external_id", "address", "rating", "fetched_at", "hours_known", "opening_text", "price_known"}) if i.kind == "meal" and place else None,
                          "score": cand[i.ref_id].breakdown | {"score": cand[i.ref_id].score} if i.kind == "poi" and i.ref_id in cand else None})
        days.append({**d.model_dump(exclude={"items"}), "items": items})
    prof = r["profile"]
    from .guides import corpus_status
    return jsonable_encoder({
        "profile": prof.model_dump() | {"hotel_gcj": gcj(prof.hotel.lat, prof.hotel.lon)},
        "days": days,
        "report": r["report"].model_dump(),
        "candidates": [{"id": c.poi_id, "name": P[c.poi_id].name, "score": c.score, **c.breakdown,
                        "selected": c.poi_id in src, **gcj(P[c.poi_id].lat, P[c.poi_id].lon)} for c in r["candidates"]],
        "dropped": [P[x].name for x in r.get("dropped", [])],
        "log": r["log"], "usage": r["usage"], "seconds": r["seconds"],
        "plan_id": plan_id, "revision": revision, "change": change,
        "meta": {"travel": r.get("travel_stats", {}), "restaurants": r.get("restaurant_stats", {}), "select_mode": request_meta["select_mode"], "enable_repair": request_meta["enable_repair"], "repair_rounds": r.get("repair_round", 0),
                 "operation": r.get("operation", "plan"),
                 "llm_model": config.LLM_MODEL, "llm_thinking": config.LLM_THINKING, "tag_source": tag_source(),
                 "selection": r.get("selection_stats", {}),
                 "guide_mode": request_meta["guide_mode"], "guide_snapshot": corpus_status() if request_meta["guide_mode"] == "pilot" else None,
                 "guide_status": "experimental_pending_human_evaluation" if request_meta["guide_mode"] == "pilot" else "off"},
    })


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
    """把某一餐换成备选餐厅，并只重新规划这一天（设计原则"可编辑"）。"""
    from .replan import ReplanError, swap_meal
    accounts.same_origin(request)
    access.rate_limit(request, "swap")
    try:
        session = SESSIONS.get(plan_id)
        accounts.authorize_session(session, request)
        if req.revision != session.current:
            raise StaleRevision
        updated, change = swap_meal(session.result(), req.day, req.item_index, req.restaurant_id)
        revision = SESSIONS.append(plan_id, req.revision, updated)
    except HTTPException:
        raise
    except SessionNotFound:
        raise HTTPException(status_code=410, detail=EXPIRED) from None
    except StaleRevision:
        raise HTTPException(status_code=409, detail="行程刚刚被修改过，请刷新后再操作。") from None
    except ReplanError as error:
        raise HTTPException(status_code=error.status, detail=error.message) from None
    except Exception:
        raise HTTPException(status_code=502, detail="重新规划暂未完成，原行程保持不变，请稍后重试。") from None
    return deliver_plan(updated, session, revision, change)


@app.post("/api/plans/{plan_id}/revert")
def revert_endpoint(plan_id: str, req: RevertRequest, request: Request) -> dict[str, Any]:
    """撤销：恢复旧内容，版本号仍递增。"""
    accounts.same_origin(request)
    try:
        session = SESSIONS.get(plan_id)
        accounts.authorize_session(session, request)
        result, revision = SESSIONS.revert(plan_id, req.revision, req.base_revision)
    except SessionNotFound:
        raise HTTPException(status_code=410, detail=EXPIRED) from None
    except StaleRevision:
        raise HTTPException(status_code=409, detail="行程刚刚被修改过，不能撤销旧版本的操作。") from None
    return deliver_plan(result, session, revision)


@app.get("/api/place-media")
def place_media(kind: Literal["poi", "meal"], id: int, http: Request) -> dict[str, Any]:  # noqa: A002 — 与前端字段名一致
    """地点图片（最多 5 张），来源与留存规范见 app/media.py、docs/图片资源规范.md。"""
    access.rate_limit(http, "media")
    from .media import get_place_media
    result = get_place_media(kind, id)
    if result is None:
        raise HTTPException(status_code=404, detail="找不到这个地点")
    return result.model_dump()


@app.post("/api/navigation")
def navigation(request: NavigationRequest, http: Request) -> dict[str, Any]:
    from .amap import AmapError
    access.rate_limit(http, "navigation")
    try:
        return get_navigation(request)
    except AmapError:
        raise HTTPException(status_code=503, detail="导航服务暂不可用，请稍后重试") from None


@app.post("/api/place-details")
def place_details(request: PlaceDetailsRequest, http: Request) -> dict[str, Any]:
    access.rate_limit(http, "details")
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
app.mount("/media/files", StaticFiles(directory=_MEDIA_FILES), name="media-files")  # 人工补充的图片（随仓库管理）

_dist = config.REPO_DIR / "frontend" / "dist"
if _dist.exists():
    app.mount("/", StaticFiles(directory=_dist, html=True), name="frontend")
