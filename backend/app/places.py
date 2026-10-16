from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
from typing import Any

import httpx

from . import config
from .geo import gcj02_to_bd09
from .amap_limits import amap_limiter


class PlaceSearchError(RuntimeError):
    pass


_cache: OrderedDict[str, tuple[float, list[dict[str, Any]]]] = OrderedDict()
_lock = asyncio.Lock()
_last_request = 0.0


def normalize_place(place: dict[str, Any]) -> dict[str, Any] | None:
    if place.get("cityname") != "杭州市":
        return None
    try:
        lon, lat = map(float, place["location"].split(","))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if not (math.isfinite(lat) and math.isfinite(lon) and 29 <= lat <= 31 and 118 <= lon <= 121):
        return None
    if not isinstance(place.get("name"), str) or not place["name"].strip():
        return None
    internal_lat, internal_lon = gcj02_to_bd09(lat, lon)
    return {
        "id": str(place.get("id") or ""), "name": place["name"],
        "address": place.get("address") if isinstance(place.get("address"), str) else "",
        "area": place.get("adname") if isinstance(place.get("adname"), str) else "杭州市",
        "lat": internal_lat, "lon": internal_lon, "gcj": {"lat": lat, "lon": lon},
        "source": "amap", "coordinate_system": "BD-09",
    }


async def search_places(query: str) -> list[dict[str, Any]]:
    global _last_request
    query = query.strip()
    if not config.AMAP_WEB_KEY:
        raise PlaceSearchError("地点搜索暂不可用，请选择预设起点。")
    async with _lock:
        cached = _cache.get(query)
        if cached and time.monotonic() - cached[0] < 300:
            _cache.move_to_end(query)
            return cached[1]
        await asyncio.to_thread(amap_limiter.wait)
        _last_request = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get("https://restapi.amap.com/v3/place/text", params={
                    "key": config.AMAP_WEB_KEY, "keywords": query, "city": "330100",
                    "citylimit": "true", "offset": 10, "page": 1,
                })
                response.raise_for_status()
                data = response.json()
        except (httpx.HTTPError, ValueError):
            raise PlaceSearchError("地点搜索暂时连接失败，请稍后重试或选择预设起点。") from None
        if not isinstance(data, dict) or data.get("status") != "1":
            raise PlaceSearchError("地点搜索暂不可用，请稍后重试或选择预设起点。")
        raw_places = data.get("pois")
        if not isinstance(raw_places, list):
            raise PlaceSearchError("地点搜索返回异常，请稍后重试。")
        places = [result for item in raw_places if isinstance(item, dict)
                  if (result := normalize_place(item)) is not None]
        _cache[query] = (time.monotonic(), places)
        _cache.move_to_end(query)
        while len(_cache) > 128:
            _cache.popitem(last=False)
        return places
