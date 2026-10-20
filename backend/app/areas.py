"""游览片区：按距离自动聚类的派生索引（2026-10-19 用户决定“片区为主”）。

片区不写进景点字段：原库摘要按景点全字段计算，补充目录发布时要核对该摘要，改字段会让已发布目录失效。
因此片区按目录内容现算并缓存，同一目录版本结果固定、可复现。

- 聚类：全链接层次聚类，片区内任意两点直线不超过 AREA_DIAMETER_KM；同一连续游览组（visit_group）不拆开。
- 相邻：两片区最近的两个成员直线不超过 ADJACENT_KM。
- 起点附近：有成员离起点不超过 ADJACENT_KM 的片区，与任何片区都算顺路（可放在一天开头或结尾）。
- 名称：app/assets/area_names.json 按锚点景点人工命名；没有命名的取“编号最小成员名＋一带”。
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import threading

import numpy as np
from scipy.cluster.hierarchy import fcluster, linkage

from .cities import current_city
from .geo import haversine_km
from .schemas import Itinerary, POI

AREA_DIAMETER_KM = 3.0
ADJACENT_KM = 4.0
NAMES_PATH = Path(__file__).parent / "assets" / "area_names.json"
KM_PER_DEGREE = 111.0


@dataclass(frozen=True)
class AreaIndex:
    of: dict[int, str]
    members: dict[str, tuple[int, ...]]
    labels: dict[str, str]
    neighbors: dict[str, frozenset[str]]
    positions: dict[int, tuple[float, float]]

    def area_of(self, poi_id: int) -> str:
        return self.of.get(poi_id, f"area:{poi_id}")

    def label(self, area_id: str) -> str:
        return self.labels.get(area_id, area_id)

    def adjacent(self, first: str, second: str) -> bool:
        """同一片区或相邻片区。"""
        return first == second or second in self.neighbors.get(first, frozenset())

    def home_areas(self, lat: float, lon: float) -> frozenset[str]:
        """起点附近的片区。"""
        return frozenset(self.of[identifier] for identifier, (place_lat, place_lon) in self.positions.items()
                         if haversine_km(lat, lon, place_lat, place_lon) <= ADJACENT_KM)

    def compatible(self, first: str, second: str, home: frozenset[str] = frozenset()) -> bool:
        """能放在同一天：同片区、相邻片区，或其中一个在起点附近。"""
        return self.adjacent(first, second) or first in home or second in home

    def areas_of(self, identifiers: list[int]) -> list[str]:
        """按出现顺序去重的片区序列。"""
        return list(dict.fromkeys(self.area_of(identifier) for identifier in identifiers))


def area_fill_conflicts(itinerary: Itinerary, places: dict[int, POI]) -> list[int]:
    """实际游览顺序超过两个片区或返回已离开的片区时，补点不得采纳。

    只看 POI，忽略餐厅与起点首尾段；连续同片区合并，不能全局去重而丢掉 A→B→A。
    索引仍为派生数据，不改写 POI。显式必去的既有冲突继续交统一校验。
    """
    zones = area_index(places)
    conflicts = []
    for day in itinerary.days:
        sequence = []
        for item in day.items:
            if item.kind == "poi" and item.ref_id in places:
                zone = zones.area_of(item.ref_id)
                if not sequence or sequence[-1] != zone:
                    sequence.append(zone)
        if len(set(sequence)) > 2 or len(sequence) > len(set(sequence)):
            conflicts.append(day.day)
    return conflicts


def _names() -> list[dict]:
    path = current_city().assets_dir / "area_names.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["areas"]


def build_area_index(places: dict[int, POI], names: list[dict] | None = None) -> AreaIndex:
    identifiers = sorted(places)
    if not identifiers:
        return AreaIndex({}, {}, {}, {}, {})
    scale = math.cos(math.radians(sum(places[identifier].lat for identifier in identifiers) / len(identifiers)))
    points = np.array([[places[identifier].lat * KM_PER_DEGREE, places[identifier].lon * KM_PER_DEGREE * scale]
                       for identifier in identifiers])
    clusters = (fcluster(linkage(points, "complete"), AREA_DIAMETER_KM, "distance")
                if len(identifiers) > 1 else np.array([1]))
    # 连续游览组强制同片区：并查集合并
    parent = {int(cluster): int(cluster) for cluster in clusters}

    def find(cluster: int) -> int:
        while parent[cluster] != cluster:
            parent[cluster] = parent[parent[cluster]]
            cluster = parent[cluster]
        return cluster

    group_cluster: dict[str, int] = {}
    for identifier, cluster in zip(identifiers, clusters):
        group = places[identifier].visit_group
        if group:
            if group in group_cluster:
                parent[find(int(cluster))] = find(group_cluster[group])
            else:
                group_cluster[group] = int(cluster)
    grouped: dict[int, list[int]] = {}
    for identifier, cluster in zip(identifiers, clusters):
        grouped.setdefault(find(int(cluster)), []).append(identifier)
    members = {f"area:{min(group)}": tuple(group) for group in grouped.values()}
    of = {identifier: area for area, group in members.items() for identifier in group}
    labels: dict[str, str] = {}
    for entry in names if names is not None else _names():
        area = of.get(entry["anchor_id"])
        if area is not None and area not in labels:
            labels[area] = entry["name"]
    for area, group in members.items():
        labels.setdefault(area, f"{places[min(group)].name}一带")
    position = {identifier: index for index, identifier in enumerate(identifiers)}
    areas = sorted(members)
    neighbors: dict[str, set[str]] = {area: set() for area in areas}
    for index, first in enumerate(areas):
        left = points[[position[identifier] for identifier in members[first]]]
        for second in areas[index + 1:]:
            right = points[[position[identifier] for identifier in members[second]]]
            nearest = float(np.sqrt(((left[:, None, :] - right[None, :, :]) ** 2).sum(axis=2)).min())
            if nearest <= ADJACENT_KM:
                neighbors[first].add(second)
                neighbors[second].add(first)
    return AreaIndex(of=of, members=members, labels=labels,
                     neighbors={area: frozenset(values) for area, values in neighbors.items()},
                     positions={identifier: (places[identifier].lat, places[identifier].lon) for identifier in identifiers})


_CACHE: dict[object, AreaIndex] = {}
_LOCK = threading.Lock()


def area_index(places: dict[int, POI] | None = None) -> AreaIndex:
    """当前目录（或给定景点集合）的片区索引。当前目录按版本缓存；其他集合按坐标与游览组指纹缓存。"""
    from .poi_catalog import current_catalog
    catalog = current_catalog()
    if places is None or places is catalog.pois:
        places, key = catalog.pois, (catalog.city, catalog.version)
    else:
        key = (catalog.city, "places", tuple(sorted((identifier, place.lat, place.lon, place.visit_group, place.name)
                                      for identifier, place in places.items())))
    with _LOCK:
        found = _CACHE.get(key)
    if found is not None:
        return found
    index = build_area_index(places)
    with _LOCK:
        if len(_CACHE) > 64:
            _CACHE.clear()
        _CACHE[key] = index
    return index


# 排程成本（片区为主；首尾段不加）。取值为实现取舍，经固定选点对照校准，见 docs/片区排程实施与验收20261019.md
CROSS_AREA_COST = 40      # 日内弧：相邻片区之间
FAR_AREA_COST = 400       # 日内弧：不相邻片区之间（高于普通地点的舍弃代价，宁可舍弃也不跨远区）
AREA_EXTRA_COST = 300     # 分天：当天每多一个片区
AREA_FAR_COST = 3000      # 分天：当天有一对不相邻片区
AREA_SPLIT_COST = 1500    # 分天：一天装得下的片区被拆到多天，每多拆一天


def area_transition_cost(first: str, second: str, index: AreaIndex, home: frozenset[str] = frozenset()) -> int:
    if first == second:
        return 0
    return CROSS_AREA_COST if index.compatible(first, second, home) else FAR_AREA_COST
