from __future__ import annotations

import asyncio
import math
import time
from collections import OrderedDict
from typing import Any

import httpx

from . import config
from .cities import city_config, current_city
from .geo import gcj02_to_bd09
from .amap_limits import amap_limiter


class PlaceSearchError(RuntimeError):
    pass


_cache: OrderedDict[tuple[str, str], tuple[float, list[dict[str, Any]]]] = OrderedDict()
_lock = asyncio.Lock()
_last_request = 0.0


def normalize_place(place: dict[str, Any], city: str | None = None) -> dict[str, Any] | None:
    settings = city_config(city) if city else current_city()
    if place.get("cityname") != settings.name_full:
        return None
    try:
        lon, lat = map(float, place["location"].split(","))
    except (KeyError, TypeError, ValueError, AttributeError):
        return None
    if not (settings.contains(lat, lon)):
        return None
    if not isinstance(place.get("name"), str) or not place["name"].strip():
        return None
    internal_lat, internal_lon = gcj02_to_bd09(lat, lon)
    return {
        "id": str(place.get("id") or ""), "name": place["name"],
        "address": place.get("address") if isinstance(place.get("address"), str) else "",
        "area": place.get("adname") if isinstance(place.get("adname"), str) else settings.name_full,
        "lat": internal_lat, "lon": internal_lon, "gcj": {"lat": lat, "lon": lon},
        "source": "amap", "coordinate_system": "BD-09",
    }


async def search_places(query: str, city: str | None = None) -> list[dict[str, Any]]:
    global _last_request
    settings = city_config(city) if city else current_city()
    query = query.strip()
    cache_key = (settings.key, query)
    if not config.AMAP_WEB_KEY:
        raise PlaceSearchError("地点搜索暂不可用，请选择预设起点。")
    async with _lock:
        cached = _cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < 300:
            _cache.move_to_end(cache_key)
            return cached[1]
        await asyncio.to_thread(amap_limiter.wait)
        _last_request = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.get("https://restapi.amap.com/v3/place/text", params={
                    "key": config.AMAP_WEB_KEY, "keywords": query, "city": settings.adcode,
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
                  if (result := normalize_place(item, settings.key)) is not None]
        _cache[cache_key] = (time.monotonic(), places)
        _cache.move_to_end(cache_key)
        while len(_cache) > 128:
            _cache.popitem(last=False)
        return places
