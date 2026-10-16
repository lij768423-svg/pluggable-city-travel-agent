"""加载杭州数据快照。LLM 增强标签缺失时退回按 type 的规则标签（并在 tag_source 中注明）。"""
from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path

import pandas as pd

from .config import CITY_DIR
from .geo import gcj02_to_bd09, parse_hm
from .matching import norm
from .schemas import POI, Restaurant

# 规则兜底：数据集 type → 兴趣标签 / 体力 / 室内
TYPE_RULES: dict[str, tuple[list[str], int, bool]] = {
    "游乐园/体育娱乐": (["亲子游乐", "主题乐园"], 2, False),
    "自然风光": (["自然风光", "拍照出片"], 2, False),
    "人文景观": (["人文历史", "拍照出片"], 1, False),
    "历史古迹": (["人文历史"], 1, False),
    "公园": (["自然风光", "小众安静"], 1, False),
    "博物馆/纪念馆": (["博物馆展览", "人文历史"], 1, True),
    "商业街区": (["购物街区", "美食探店"], 1, False),
    "文化旅游区": (["人文历史", "园林古镇"], 2, False),
    "美术馆/艺术馆": (["艺术文创", "博物馆展览"], 1, True),
    "红色景点": (["人文历史"], 1, False),
    "大学校园": (["小众安静", "拍照出片"], 1, False),
}

# 饮食禁忌 → 需要排除的菜系
DIET_EXCLUDE: dict[str, set[str]] = {
    "不吃辣": {"川菜", "湘菜", "火锅"},
    "只吃中餐": {"西餐", "日本料理", "韩国料理", "东南亚菜", "泰国菜", "意大利菜", "法国菜", "牛排"},
    "清真": set(),
    "不吃海鲜": {"海鲜"},
}
NON_MEAL_CUISINES = {"咖啡店", "面包甜点", "酒吧/酒馆", "茶馆/茶室", ""}


@lru_cache(maxsize=1)
def load_pois() -> dict[int, POI]:
    df = pd.read_parquet(CITY_DIR / "attractions.parquet")
    tags_path = CITY_DIR / "attraction_tags.parquet"
    tags = pd.read_parquet(tags_path).set_index("id") if tags_path.exists() else None
    out: dict[int, POI] = {}
    for r in df.itertuples():
        interests, stamina, indoor = TYPE_RULES.get(r.type, ([], 2, False))
        audience: list[str] = []
        summary = ""
        if tags is not None and r.id in tags.index:
            t = tags.loc[r.id]
            interests, stamina, indoor = list(t["interests"]), int(t["stamina"]), bool(t["indoor"])
            audience, summary = list(t["audience"]), str(t["summary"])
        out[int(r.id)] = POI(
            id=int(r.id), name=r.name, type=r.type, lat=r.lat, lon=r.lon,
            open_min=parse_hm(r.opentime), close_min=parse_hm(r.endtime), price=float(r.price),
            min_hours=float(r.recommendmintime), max_hours=float(r.recommendmaxtime),
            interests=interests, stamina=stamina, audience=audience, indoor=indoor, summary=summary,
        )
    review = json.loads((Path(__file__).parent / "assets" / "poi_reviews.json").read_text())
    for entry in review["places"]:
        poi = out.get(entry["id"])
        if poi is None or norm(entry["name"]) != norm(poi.name):
            raise ValueError(f"POI 审计身份不匹配：{entry['id']} {entry['name']}")
        changes = dict(entry["changes"])
        if "price" in changes and entry["sources"] and review["sources"][entry["sources"][0]].get("verification") == "observed":
            changes["price_verified"] = True
        if "opening_rules" in changes and review["sources"][entry["sources"][0]].get("verification") != "observed":
            changes.pop("opening_rules")
        if "opening_rules" in changes:
            changes["opening_rules"] = {**changes["opening_rules"], "verified_at":review["reviewed_at"],
                "source_url":review["sources"][entry["sources"][0]]["url"]}
        out[poi.id] = POI.model_validate({**poi.model_dump(), **changes, "review_note":entry["note"]})
    # 用户核对的停留区间（scripts/apply_stay_audit.py 生成）；已在上面审阅过停留时长的景点不在其中
    overrides = Path(__file__).parent / "assets" / "stay_overrides.json"
    if overrides.exists():
        reviewed = {entry["id"] for entry in review["places"] if {"min_hours", "max_hours"} & entry["changes"].keys()}
        for entry in json.loads(overrides.read_text(encoding="utf-8"))["places"]:
            poi = out.get(entry["poi_id"])
            if poi is None or norm(entry["name"]) != norm(poi.name) or poi.id in reviewed:
                raise ValueError(f"停留覆盖身份不匹配或与审阅冲突：{entry['poi_id']} {entry['name']}")
            out[poi.id] = poi.model_copy(update={"min_hours": entry["low_min"] / 60, "max_hours": entry["high_min"] / 60,
                "duration_source": f"停留时长按{entry['reviewed_at']}人工核对结果：{entry['evidence']}"})
    return out


def tag_source() -> str:
    return "llm" if (CITY_DIR / "attraction_tags.parquet").exists() else "rule"


@lru_cache(maxsize=1)
def load_restaurants() -> dict[int, Restaurant]:
    df = pd.read_parquet(CITY_DIR / "restaurants.parquet")
    out: dict[int, Restaurant] = {}
    for r in df.itertuples():
        if (r.cuisine or "") in NON_MEAL_CUISINES:
            continue
        lat, lon = gcj02_to_bd09(float(r.lat), float(r.lon))
        out[int(r.id)] = Restaurant(
            id=int(r.id), name=r.name, lat=lat, lon=lon, price=float(r.price), cuisine=r.cuisine,
            open_min=parse_hm(r.opentime), close_min=parse_hm(r.endtime), dishes=r.recommendedfood or "",
        )
    snapshot = os.environ.get("TTA_RESTAURANT_SNAPSHOT")
    if os.environ.get("TTA_OFFLINE") == "1" and snapshot:
        path = Path(snapshot)
        if not path.is_file() or path.stat().st_size > 10_000_000:
            raise ValueError("餐厅快照不存在或超过10MB限制")
        records = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(records, list):
            raise ValueError("餐厅快照须为 Restaurant 对象数组")
        for record in records:
            restaurant = Restaurant.model_validate(record)
            out[restaurant.id] = restaurant
    return out


def diet_ok(cuisine: str, dietary: list[str]) -> bool:
    if "清真" in dietary and cuisine not in {"新疆菜", "西北菜"}:
        return False
    return not any(cuisine in DIET_EXCLUDE.get(d, set()) for d in dietary)
