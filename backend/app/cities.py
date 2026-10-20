"""城市配置与当前任务的城市。读取配置不触发原库加载，避免名称归一化造成循环依赖。"""
from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
from functools import lru_cache
import json
import math
import os
from pathlib import Path

from . import config
from .planning_context import poi_catalog

DEFAULT_CITY = "hangzhou"
CITIES_PATH = Path(__file__).parent / "assets" / "cities.json"


@dataclass(frozen=True)
class CityConfig:
    key: str
    name: str
    name_full: str
    province: str
    amap_city: str
    adcode: str
    bounds: tuple[float, float, float, float]
    default_origin: dict
    map_center: dict
    data_dir: Path
    assets_dir: Path
    supplement_dir: Path
    calibration: Path
    local_cuisine: str
    evening_streets: dict[int, str]
    brand: str
    hotel_areas: tuple[str, ...] = ()
    local_cuisine_labels: dict[str, str] = field(default_factory=dict)

    def contains(self, lat: float, lon: float) -> bool:
        south, north, west, east = self.bounds
        return math.isfinite(lat) and math.isfinite(lon) and south <= lat <= north and west <= lon <= east

    @property
    def prefixes(self) -> tuple[str, ...]:
        return self.name_full, self.name, self.province, self.province.removesuffix("省")

    def asset_reference(self, filename: str) -> str:
        # 引用始终使用 backend 相对路径；杭州既有字符串逐字保留。
        return (self.assets_dir / filename).relative_to(config.BACKEND_DIR).as_posix()


@lru_cache(maxsize=1)
def _definitions() -> dict:
    return json.loads(CITIES_PATH.read_text(encoding="utf-8"))


_CONFIGS: dict[tuple, tuple[dict, CityConfig]] = {}


def city_config(key: str = DEFAULT_CITY) -> CityConfig:
    raw = _definitions().get(key)
    if raw is None:
        raise ValueError(f"未知城市：{key}")
    # 名称匹配和坐标筛选是热路径。路径覆盖与配置内容都纳入检查，避免测试/隔离目录串用。
    cache_key = (key, config.DATA_DIR, config.CITY_DIR, config.BACKEND_DIR, config.POI_SUPPLEMENT_DIR)
    cached = _CONFIGS.get(cache_key)
    if cached is not None and cached[0] == raw:
        return cached[1]
    def data_path(value: str) -> Path:
        return config.DATA_DIR / Path(value).relative_to("data")
    settings = CityConfig(
        **{field: raw[field] for field in ("key", "name", "name_full", "province", "amap_city", "adcode", "local_cuisine", "brand")},
        bounds=tuple(raw["bounds"]), default_origin=dict(raw["default_origin"]), map_center=dict(raw["map_center"]),
        data_dir=config.CITY_DIR if key == DEFAULT_CITY else data_path(raw["data_dir"]),
        assets_dir=config.BACKEND_DIR / raw["assets_dir"],
        supplement_dir=config.POI_SUPPLEMENT_DIR if key == DEFAULT_CITY else data_path(raw["supplement_dir"]),
        calibration=data_path(raw["calibration"]),
        evening_streets={int(identifier): name for identifier, name in raw["evening_streets"].items()},
        hotel_areas=tuple(raw.get("hotel_areas", [])),
        local_cuisine_labels=dict(raw.get("local_cuisine_labels", {})),
    )
    if len(_CONFIGS) >= 64:
        _CONFIGS.clear()
    _CONFIGS[cache_key] = (deepcopy(raw), settings)
    return settings


def enabled_cities() -> tuple[str, ...]:
    keys = tuple(dict.fromkeys(key.strip() for key in os.environ.get("TTA_CITIES", DEFAULT_CITY).split(",") if key.strip()))
    if not keys:
        raise ValueError("TTA_CITIES 至少启用一个城市")
    for key in keys:
        city_config(key)
    return keys


def current_city() -> CityConfig:
    # 无任务上下文时与 current_catalog 的缺省城市一致；不能为此加载目录本身。
    catalog = poi_catalog.get()
    return city_config(catalog.city if catalog is not None else DEFAULT_CITY)


class CityDisabled(ValueError):
    """城市不在启用列表：历史行程可查看，不能新建规划、修改或换餐。"""


def ensure_enabled(key: str) -> CityConfig:
    settings = city_config(key)
    if key not in enabled_cities():
        raise CityDisabled(f"{settings.name}暂未开放，相关历史行程只能查看")
    return settings


def reference_city(reference: dict | None) -> str:
    """规划结果目录引用里的城市；C2 之前的旧记录没有城市，按杭州。"""
    return (reference or {}).get("city") or DEFAULT_CITY
