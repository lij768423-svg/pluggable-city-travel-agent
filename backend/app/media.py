"""地点图片（景点 + 餐厅），每个地点最多 5 张。完整规范见 docs/图片资源规范.md。

来源按以下顺序合并，去重后截取前 5 张：
1. manual：人工补充。登记在 assets/media/registry.json，文件放在 assets/media/files/，随仓库管理；
   每张都必须写明作者、许可证和来源，由 scripts/media.py check 校验。
2. amap：按名称 + 坐标匹配到的高德 POI 自带图片（每个 POI 最多 3 张）。
   高德餐厅有 external_id 时直接用详情接口，不做名称匹配。
3. amap_related：仅景点。同一响应里与本地点同源（名称包含或公共前缀 ≥ 2 字）、距离 ≤ 0.8 km、
   属于风景名胜类（typecode 11 开头）的关联 POI，用来补足 4–5 张；会标注关联地点名称。

高德返回的数据只放在 AmapRouter 的内存缓存里（默认 15 分钟，与路线缓存一致；高德服务协议对留存有约束），
不下载、不落盘，图片由浏览器直接从高德图片服务器加载。不合适的高德图片在注册表 hide 列表里按 URL 指纹屏蔽。
图片不足 3 张时如实返回，不用无关图片凑数。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, field_validator

from . import config
from .amap import AmapError, MissingSnapshotError, get_router, pt, straight_km
from .amap_limits import RequestBudget

MAX_PHOTOS = 5
MIN_TARGET = 3
RELATED_MAX_KM = 0.8
MEDIA_DIR = config.BACKEND_DIR / "app" / "assets" / "media"
FILES_DIR = MEDIA_DIR / "files"
REGISTRY_PATH = MEDIA_DIR / "registry.json"
PUBLIC_PREFIX = "/media/files/"
ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp"}
MAX_FILE_BYTES = 2 * 1024 * 1024
AMAP_HOSTS = ("store.is.autonavi.com", "aos-comment.amap.com")
POLICY = "高德图片仅内存缓存 15 分钟、浏览器直连加载；人工图片随仓库管理并登记许可证"

Kind = Literal["poi", "meal"]


# ── 数据结构 ─────────────────────────────────────────────
class Photo(BaseModel):
    url: str  # 放大查看用（评论原图会限制到 1600 宽）
    thumb_url: str  # 列表/滑动用的缩略图（约 720 宽）
    caption: str = ""
    source: Literal["manual", "amap", "amap_related"]
    source_label: str
    attribution: str = ""
    license: str = ""
    source_url: str = ""
    fingerprint: str


class PlaceMedia(BaseModel):
    kind: Kind
    id: int
    name: str
    photos: list[Photo] = Field(default_factory=list)
    status: Literal["ready", "partial", "empty", "unavailable"]
    fetched_at: str | None = None
    policy: str = POLICY


class ManualPhoto(BaseModel):
    """注册表里的一张人工图片。author / license / source_url 为必填，保证来源可追溯。"""
    file: str = Field(min_length=1)
    caption: str = ""
    author: str = Field(min_length=1)
    license: str = Field(min_length=1)
    source_url: str = Field(min_length=1)
    added_at: date

    @field_validator("file")
    @classmethod
    def _plain_name(cls, value: str) -> str:
        if "/" in value or "\\" in value or value.startswith("."):
            raise ValueError("file 只能是 files/ 目录下的文件名")
        if Path(value).suffix.lower() not in ALLOWED_EXT:
            raise ValueError(f"只支持 {sorted(ALLOWED_EXT)}")
        return value


class RegistryEntry(BaseModel):
    name: str = Field(min_length=1, description="地点名称，仅用于人工核对，不参与匹配")
    photos: list[ManualPhoto] = Field(default_factory=list, max_length=MAX_PHOTOS)
    hide: list[str] = Field(default_factory=list, description="要屏蔽的高德图片 URL 指纹（scripts/media.py hide 写入）")


class Registry(BaseModel):
    version: Literal[1] = 1
    places: dict[str, RegistryEntry] = Field(default_factory=dict)

    @field_validator("places")
    @classmethod
    def _keys(cls, value: dict[str, RegistryEntry]) -> dict[str, RegistryEntry]:
        for key in value:
            if not re.fullmatch(r"(poi|meal):-?\d+", key):
                raise ValueError(f"注册表键必须形如 poi:12 或 meal:-123，收到 {key!r}")
        return value


# ── 工具函数 ─────────────────────────────────────────────
def place_key(kind: str, identifier: int) -> str:
    return f"{kind}:{identifier}"


def normalize_url(url: str) -> str:
    url = str(url or "").strip()
    if url.startswith("http://") and url.split("/")[2] in AMAP_HOSTS:
        url = "https://" + url[len("http://"):]  # 两个图片域名都支持 https，避免将来上 https 时出现混合内容
    return url


# aos-comment（用户评论图，原图常为 2048 px、1–1.4 MB）支持阿里云 OSS 服务端缩放：720 宽 + q80 约 180 KB。
# 2026-10-15 实测；store.is.autonavi.com 的商户图本身约 100 KB，不支持该参数，原样使用。
OSS_THUMB = "?x-oss-process=image/resize,w_720/quality,q_80"
OSS_FULL = "?x-oss-process=image/resize,w_1600/quality,q_85"


def sized(url: str) -> tuple[str, str]:
    """返回 (放大图, 缩略图)。"""
    if url.startswith("https://aos-comment.amap.com/") and "?" not in url:
        return url + OSS_FULL, url + OSS_THUMB
    return url, url


def fingerprint(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode()).hexdigest()[:12]


def _registry_mtime() -> float:
    return REGISTRY_PATH.stat().st_mtime if REGISTRY_PATH.exists() else 0.0


@lru_cache(maxsize=4)
def _load_registry(_mtime: float) -> Registry:
    if not REGISTRY_PATH.exists():
        return Registry()
    return Registry.model_validate(json.loads(REGISTRY_PATH.read_text(encoding="utf-8")))


def load_registry() -> Registry:
    """注册表按修改时间缓存：改完文件无需重启服务。"""
    return _load_registry(_registry_mtime())


def save_registry(registry: Registry) -> None:
    MEDIA_DIR.mkdir(parents=True, exist_ok=True)
    data = registry.model_dump(mode="json")
    data["places"] = dict(sorted(data["places"].items()))
    tmp = REGISTRY_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, REGISTRY_PATH)


def manual_photos(entry: RegistryEntry | None) -> list[Photo]:
    if entry is None:
        return []
    out = []
    for item in entry.photos:
        url = PUBLIC_PREFIX + item.file
        out.append(Photo(url=url, thumb_url=url, caption=item.caption, source="manual", source_label="人工补充",
                         attribution=f"{item.author} · {item.license}", license=item.license,
                         source_url=item.source_url, fingerprint=fingerprint(url)))
    return out


def _normalized_name(value: str) -> str:
    from .place_details import normalized_name
    return normalized_name(value)


def _photos_of(poi: dict, source: str, label: str) -> list[Photo]:
    out = []
    for raw in poi.get("photos") or []:
        if not isinstance(raw, dict) or not raw.get("url"):
            continue
        url = normalize_url(raw["url"])
        try:
            parsed = urlsplit(url)
            if parsed.scheme != "https" or parsed.netloc not in AMAP_HOSTS:
                continue
        except ValueError:
            continue
        title = str(raw.get("title") or "").strip()
        full, thumb = sized(url)
        out.append(Photo(url=full, thumb_url=thumb, caption=title if isinstance(raw.get("title"), str) else "", source=source,
                         source_label=label, attribution="高德地图（商户或用户上传）", fingerprint=fingerprint(url)))
    return out


def related_pois(place, primary: dict | None, pois: list[dict]) -> list[dict]:
    """同一景区的关联 POI：风景名胜类、名称同源、距离不超过 0.8 km（规则见模块说明）。"""
    own = _normalized_name(place.name)
    origin = pt(place.lat, place.lon)
    out = []
    for candidate in pois:
        if primary is not None and candidate.get("id") == primary.get("id"):
            continue
        if not str(candidate.get("typecode") or "").startswith("11"):
            continue
        other = _normalized_name(str(candidate.get("name") or ""))
        if not other or not (own in other or other in own or len(os.path.commonprefix([own, other])) >= 2):
            continue
        try:
            if straight_km(origin, str(candidate.get("location") or "")) > RELATED_MAX_KM:
                continue
        except (ValueError, TypeError):
            continue
        out.append(candidate)
    return out


def amap_photos(place, kind: str, router) -> tuple[list[Photo], str | None]:
    """返回 (高德图片, 查询时间)。结果放进 router 的内存缓存（键含地点名，地点改名会自然失效）。"""
    from .place_details import match_place
    key = f"media|{kind}|{place.id}|{place.name}|{pt(place.lat, place.lon)}"
    if router.has(key):
        cached = router.get(key)
        return [Photo.model_validate(item) for item in cached], router.records.get(key, {}).get("fetched_at")
    photos: list[Photo] = []
    external = getattr(place, "external_id", "") if kind == "meal" else ""
    if external:
        data = router._get("v5/place/detail", {"id": external, "show_fields": "photos"})
        poi = (data.get("pois") or [None])[0]
        if isinstance(poi, dict):
            photos = _photos_of(poi, "amap", "高德地图 · 本店")
    else:
        data = router._get("v5/place/around", {"location": pt(place.lat, place.lon), "keywords": place.name,
                                               "radius": 1000, "page_size": 10, "show_fields": "photos",
                                               "sortrule": "distance"})
        pois = [item for item in data.get("pois") or [] if isinstance(item, dict)]
        primary = match_place(place, pois)
        if primary is not None:
            photos = _photos_of(primary, "amap", "高德地图 · 本地点" if kind == "poi" else "高德地图 · 本店")
        if kind == "poi":
            for candidate in related_pois(place, primary, pois):
                photos += _photos_of(candidate, "amap_related", f"高德地图 · 关联地点「{candidate.get('name')}」")
    router._put(key, [photo.model_dump() for photo in photos])
    return photos, router.records.get(key, {}).get("fetched_at")


def merge(manual: list[Photo], amap: list[Photo], hidden: set[str]) -> list[Photo]:
    seen: set[str] = set()
    out: list[Photo] = []
    for photo in [*manual, *amap]:
        if photo.fingerprint in seen or (photo.source != "manual" and photo.fingerprint in hidden):
            continue
        seen.add(photo.fingerprint)
        out.append(photo)
        if len(out) >= MAX_PHOTOS:
            break
    return out


def get_place_media(kind: str, identifier: int, router=None) -> PlaceMedia | None:
    """单个地点的图片。找不到地点返回 None；高德不可用时仍返回人工图片并标 unavailable。"""
    from .place_details import resolve_place
    from .schemas import PlaceReference
    place = resolve_place(PlaceReference(kind=kind, id=identifier))
    if place is None:
        return None
    entry = load_registry().places.get(place_key(kind, identifier))
    manual = manual_photos(entry)
    hidden = set(entry.hide) if entry else set()
    fetched_at = None
    try:
        router = (router or get_router()).fork(RequestBudget(2))
        amap, fetched_at = amap_photos(place, kind, router)
        unavailable = False
    except (AmapError, MissingSnapshotError):
        amap, unavailable = [], True
    photos = merge(manual, amap, hidden)
    if unavailable and not photos:
        status = "unavailable"
    elif not photos:
        status = "empty"
    else:
        status = "ready" if len(photos) >= MIN_TARGET else "partial"
    return PlaceMedia(kind=kind, id=identifier, name=place.name, photos=photos, status=status, fetched_at=fetched_at)


# ── 人工图片管理（scripts/media.py 调用） ─────────────────────
def add_manual_photo(kind: str, identifier: int, name: str, source_file: Path, *, caption: str, author: str,
                     license: str, source_url: str, today: date | None = None) -> ManualPhoto:
    source_file = Path(source_file)
    ext = source_file.suffix.lower()
    if ext not in ALLOWED_EXT:
        raise ValueError(f"只支持 {sorted(ALLOWED_EXT)}")
    if source_file.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("单张图片不能超过 2 MB，请先压缩")
    registry = load_registry()
    key = place_key(kind, identifier)
    entry = registry.places.get(key) or RegistryEntry(name=name)
    if len(entry.photos) >= MAX_PHOTOS:
        raise ValueError(f"{key} 已有 {MAX_PHOTOS} 张人工图片")
    digest = hashlib.sha1(source_file.read_bytes()).hexdigest()[:8]
    filename = f"{kind}{identifier}-{digest}{ext}"
    FILES_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_file, FILES_DIR / filename)
    photo = ManualPhoto(file=filename, caption=caption, author=author, license=license, source_url=source_url,
                        added_at=today or date.today())
    entry.photos.append(photo)
    registry.places[key] = entry
    save_registry(registry)
    return photo


def remove_manual_photo(kind: str, identifier: int, filename: str) -> bool:
    registry = load_registry()
    entry = registry.places.get(place_key(kind, identifier))
    if entry is None or not any(photo.file == filename for photo in entry.photos):
        return False
    entry.photos = [photo for photo in entry.photos if photo.file != filename]
    if not entry.photos and not entry.hide:
        registry.places.pop(place_key(kind, identifier))
    save_registry(registry)
    if not any(photo.file == filename for item in registry.places.values() for photo in item.photos):
        (FILES_DIR / filename).unlink(missing_ok=True)
    return True


def set_hidden(kind: str, identifier: int, name: str, url_or_fingerprint: str, hidden: bool = True) -> str:
    value = url_or_fingerprint if re.fullmatch(r"[0-9a-f]{12}", url_or_fingerprint) else fingerprint(url_or_fingerprint)
    registry = load_registry()
    key = place_key(kind, identifier)
    entry = registry.places.get(key) or RegistryEntry(name=name)
    entry.hide = sorted(set(entry.hide) | {value}) if hidden else [item for item in entry.hide if item != value]
    if entry.photos or entry.hide:
        registry.places[key] = entry
    else:
        registry.places.pop(key, None)
    save_registry(registry)
    return value


def check_registry() -> list[str]:
    """返回问题清单（空表示通过）：文件缺失、超限、格式、孤儿文件、名称与数据不符。"""
    from .data import load_pois, load_restaurants
    problems = []
    try:
        registry = load_registry()
    except Exception as error:  # noqa: BLE001 — 校验工具需要把格式错误原样报告出来
        return [f"registry.json 无法解析：{error}"]
    referenced = set()
    for key, entry in registry.places.items():
        kind, identifier = key.split(":")
        known = (load_pois() if kind == "poi" else load_restaurants()).get(int(identifier))
        if known is not None and _normalized_name(known.name) != _normalized_name(entry.name):
            problems.append(f"{key}: 名称「{entry.name}」与数据中的「{known.name}」不一致")
        for photo in entry.photos:
            referenced.add(photo.file)
            path = FILES_DIR / photo.file
            if not path.exists():
                problems.append(f"{key}: 缺少文件 files/{photo.file}")
            elif path.stat().st_size > MAX_FILE_BYTES:
                problems.append(f"{key}: files/{photo.file} 超过 2 MB")
    if FILES_DIR.exists():
        for path in FILES_DIR.iterdir():
            if path.is_file() and not path.name.startswith(".") and path.name not in referenced:
                problems.append(f"孤儿文件 files/{path.name}：注册表中没有登记")
    return problems
