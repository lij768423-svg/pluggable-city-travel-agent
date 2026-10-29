"""加载杭州数据快照。LLM 增强标签缺失时退回按 type 的规则标签（并在 tag_source 中注明）。"""
from __future__ import annotations

from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path

import pandas as pd

from .config import CITY_DIR
from .cities import city_config, current_city
from .geo import gcj02_to_bd09, parse_hm
from .matching import norm
from .schemas import POI, Restaurant, FieldEvidence, ExperienceCondition

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

# 辣味菜系：不吃辣时排除，爱吃辣时优先
SPICY_CUISINES: set[str] = {"川菜", "湘菜", "火锅"}
NON_MEAL_CUISINES = {"咖啡店", "面包甜点", "酒吧/酒馆", "茶馆/茶室", ""}


def load_pois() -> dict[int, POI]:
    """当前任务固定目录中的景点（app/poi_catalog.py）；无任务上下文时读进程当前发布版，base_only 即原库。"""
    from .poi_catalog import current_catalog
    return current_catalog().pois


@lru_cache(maxsize=None)
def base_digest(city: str = "hangzhou") -> str:
    """原库（Parquet＋审阅/语义叠加）的内容摘要；补充目录发布和任务目录版本都以它为基准。"""
    # 知名度档位是推荐偏好、不是地点事实，不进原库摘要：否则每次更新档位都要重新发布补充目录，旧行程也无法再修改。
    # 评测由 fair_compare 的设计文件记录 popularity.json 的摘要，保证回放可复现
    normalized = {str(key): value.model_dump(mode="json", exclude={"popularity", "popularity_source"})
                  for key, value in sorted((load_base_pois() if city == "hangzhou" else load_base_pois(city)).items())}
    return hashlib.sha256(json.dumps(normalized, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode()).hexdigest()


@lru_cache(maxsize=None)
def load_base_pois(city: str = "hangzhou") -> dict[int, POI]:
    settings = city_config(city)
    df = pd.read_parquet(settings.data_dir / "attractions.parquet")
    tags_path = settings.data_dir / "attraction_tags.parquet"
    tags = pd.read_parquet(tags_path).set_index("id") if tags_path.exists() else None
    out: dict[int, POI] = {}
    for r in df.itertuples():
        interests, stamina, indoor = TYPE_RULES.get(r.type, ([], 2, False))
        audience: list[str] = []
        summary = ""
        tag_basis = "rule"
        if tags is not None and r.id in tags.index:
            t = tags.loc[r.id]
            interests, stamina, indoor = list(t["interests"]), int(t["stamina"]), bool(t["indoor"])
            audience, summary = list(t["audience"]), str(t["summary"])
            tag_basis = "model_estimate" if t.get("source", "llm") == "llm" else "rule"
        evidence = {field: FieldEvidence(basis="dataset", reference=f"ChinaTravel 2026.08.2 / attractions.parquet / id={int(r.id)}; CC BY 4.0")
                    for field in ("type", "coordinates", "opening_hours", "duration", "price")}
        evidence.update({field: FieldEvidence(basis=tag_basis, reference=f"attraction_tags.parquet / id={int(r.id)}" if tags is not None and r.id in tags.index else "app/data.py:TYPE_RULES")
                         for field in ("interests", "audience", "stamina", "summary")})
        out[int(r.id)] = POI(
            id=int(r.id), name=r.name, type=r.type, lat=r.lat, lon=r.lon,
            open_min=parse_hm(r.opentime), close_min=parse_hm(r.endtime), price=float(r.price),
            min_hours=float(r.recommendmintime), max_hours=float(r.recommendmaxtime),
            interests=interests, stamina=stamina, audience=audience, indoor=indoor, summary=summary,
            field_evidence=evidence,
        )
    review_path = settings.assets_dir / "poi_reviews.json"
    review = json.loads(review_path.read_text()) if review_path.exists() or city == "hangzhou" else {"places": [], "sources": {}}
    for entry in review["places"]:
        poi = out.get(entry["id"])
        if poi is None or norm(entry["name"], city) != norm(poi.name, city):
            raise ValueError(f"POI 审计身份不匹配：{entry['id']} {entry['name']}")
        changes = dict(entry["changes"])
        if "price" in changes and entry["sources"] and review["sources"][entry["sources"][0]].get("verification") == "observed":
            changes["price_verified"] = True
        if "opening_rules" in changes and review["sources"][entry["sources"][0]].get("verification") != "observed":
            changes.pop("opening_rules")
        if "opening_rules" in changes:
            changes["opening_rules"] = {**changes["opening_rules"], "verified_at":review["reviewed_at"],
                "source_url":review["sources"][entry["sources"][0]]["url"]}
        evidence = dict(poi.field_evidence)
        observed = bool(entry["sources"] and review["sources"][entry["sources"][0]].get("verification") == "observed")
        for field in changes:
            key = {"min_hours": "duration", "max_hours": "duration", "duration_source": "duration", "open_min": "opening_hours", "close_min": "opening_hours"}.get(field, field)
            direct = observed and key in {"opening_hours", "price", "opening_rules"}
            source = review["sources"][entry["sources"][0]] if direct else {}
            evidence[key] = FieldEvidence(basis="observed" if direct else "curator_inference",
                reference=source.get("url", f"{settings.asset_reference('poi_reviews.json')}:places[id={poi.id}]; {entry['note']}"),
                source_date=source.get("published_at"), reviewed_at=review["reviewed_at"] if direct else None)
        if "opening_rules" in changes and "booking_required" in changes["opening_rules"]:
            evidence["booking_required"] = evidence["opening_rules"]
        out[poi.id] = POI.model_validate({**poi.model_dump(), **changes, "review_note":entry["note"], "field_evidence": evidence})
    # 用户核对的停留区间（scripts/apply_stay_audit.py 生成）；已在上面审阅过停留时长的景点不在其中
    overrides = settings.assets_dir / "stay_overrides.json"
    if overrides.exists():
        reviewed = {entry["id"] for entry in review["places"] if {"min_hours", "max_hours"} & entry["changes"].keys()}
        for entry in json.loads(overrides.read_text(encoding="utf-8"))["places"]:
            poi = out.get(entry["poi_id"])
            if poi is None or norm(entry["name"], city) != norm(poi.name, city) or poi.id in reviewed:
                raise ValueError(f"停留覆盖身份不匹配或与审阅冲突：{entry['poi_id']} {entry['name']}")
            out[poi.id] = poi.model_copy(update={"min_hours": entry["low_min"] / 60, "max_hours": entry["high_min"] / 60,
                "duration_source": f"停留时长按{entry['reviewed_at']}人工核对结果：{entry['evidence']}",
                "field_evidence": {**poi.field_evidence, "duration": FieldEvidence(basis="manual_review", reference=entry["evidence"], reviewed_at=entry["reviewed_at"])}})
    semantics_path = settings.assets_dir / "poi_semantics.json"
    semantics = json.loads(semantics_path.read_text()) if semantics_path.exists() or city == "hangzhou" else {"places": []}
    for entry in semantics["places"]:
        poi = out.get(entry["id"])
        if poi is None or norm(poi.name, city) != norm(entry["name"], city):
            raise ValueError(f"地点语义身份不匹配：{entry['id']}")
        conditions = [ExperienceCondition.model_validate(condition) for condition in entry["conditions"]]
        out[poi.id] = poi.model_copy(update={"experience_conditions": conditions,
            "field_evidence": {**poi.field_evidence, "experience_conditions": FieldEvidence(basis="curator_inference", reference=f"{settings.asset_reference('poi_semantics.json')}:{semantics['version']}")}})
    # 知名度档位（scripts/build_popularity.py 按城市生成，存在数据目录、不进 git）；没有文件的城市一律"一般"
    popularity_path = settings.data_dir / "popularity.json"
    if popularity_path.exists():
        popularity = json.loads(popularity_path.read_text(encoding="utf-8"))
        for key, entry in popularity["places"].items():
            poi = out.get(int(key))
            if poi is None or norm(entry["name"], city) != norm(poi.name, city):
                raise ValueError(f"知名度身份不匹配：{key} {entry['name']}")
            if entry["tier"] in {"landmark", "known"}:
                out[poi.id] = poi.model_copy(update={"popularity": entry["tier"], "popularity_source": entry["source"]})
    from .poi_semantics import enrich_semantics
    out = {identifier: enrich_semantics(poi, city) for identifier, poi in out.items()}
    return out


def tag_source(city: str | None = None) -> str:
    return "llm" if (city_config(city or current_city().key).data_dir / "attraction_tags.parquet").exists() else "rule"


def load_restaurants(city: str | None = None) -> dict[int, Restaurant]:
    return _load_restaurants(city or current_city().key)


@lru_cache(maxsize=None)
def _load_restaurants(city: str) -> dict[int, Restaurant]:
    df = pd.read_parquet(city_config(city).data_dir / "restaurants.parquet")
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


load_restaurants.cache_clear = _load_restaurants.cache_clear


def spice_ok(cuisine: str, spice: str) -> bool:
    return not (spice == "no" and cuisine in SPICY_CUISINES)
