"""一键接入新城市（2026-10-20 用户要求“别的城市只要改个名字就能插入”）。

用法（backend 目录）：
  uv run python scripts/onboard_city.py --name 苏州 --dry-run      # 只打印步骤与预计外部调用
  uv run python scripts/onboard_city.py --name 苏州                # 依次跑完全部步骤
  uv run python scripts/onboard_city.py --name 苏州 --stage review # 只重跑某一步

步骤：config 配置（高德行政区）→ data 下载数据 → assets 模型标签与停留参考 → derive 默认起点/酒店区/夜游街
→ calibration 交通标定 → areas 片区命名 → review 规则体检 → check 完整性检查 → eval 20 画像评测。
每步完成记入 data/<key>/onboarding.json，已完成的步骤不重跑（--stage 指定时强制重跑该步）。
自动做到“可在测试实例启用”为止：生产启用仍由人决定（TTA_CITIES）。
规则体检只做南京总结出的通用修正（时长只加不减、越界隔离、大型乐园整日），其余列入待核清单，不冒充人工核验。
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config  # noqa: E402

CITIES_PATH = config.BACKEND_DIR / "app" / "assets" / "cities.json"
# ChinaTravel Sandbox 覆盖的 10 个城市；其他城市需 --key 并自备同格式数据
DATASET_CITIES = {"北京": "beijing", "上海": "shanghai", "南京": "nanjing", "苏州": "suzhou", "杭州": "hangzhou",
                  "深圳": "shenzhen", "成都": "chengdu", "武汉": "wuhan", "广州": "guangzhou", "重庆": "chongqing"}
# 本地菜关键词（匹配高德餐厅分类与店名），项目整理，非官方分类；未列出的城市取数据集中最常见的地方菜系
LOCAL_CUISINE = {
    "suzhou": {"苏帮": "苏帮菜", "苏州菜": "苏州菜", "苏菜": "苏菜", "江浙": "江浙菜"},
    "shanghai": {"本帮": "本帮菜", "上海菜": "上海菜", "沪菜": "上海菜", "江浙": "江浙菜"},
    "beijing": {"北京菜": "北京菜", "京菜": "北京菜", "烤鸭": "北京烤鸭", "宫廷菜": "宫廷菜"},
    "chongqing": {"重庆菜": "重庆菜", "渝菜": "重庆菜", "江湖菜": "江湖菜", "火锅": "重庆火锅", "川菜": "川菜"},
    "chengdu": {"川菜": "川菜", "成都菜": "川菜", "火锅": "川味火锅", "串串": "串串香"},
    "guangzhou": {"粤菜": "粤菜", "广府": "广府菜", "早茶": "广式早茶", "茶餐厅": "茶餐厅"},
    "shenzhen": {"粤菜": "粤菜", "潮汕": "潮汕菜", "客家": "客家菜", "广府": "广府菜"},
    "wuhan": {"湖北菜": "湖北菜", "鄂菜": "湖北菜", "武汉菜": "湖北菜", "楚菜": "湖北菜"},
}
GENERIC_CUISINE = {"西餐", "小吃", "火锅", "日本料理", "面包甜点", "咖啡店", "烧烤", "自助餐", "创意菜", "快餐简餐",
                   "韩国料理", "东南亚菜", "清真菜", "酒吧/酒馆", "素食", "海鲜", "融合菜", "粤菜", "川菜", "湘菜", "江浙菜"}
HOTEL_AREA_STOP = ("酒店", "公寓", "宾馆", "客栈", "民宿", "中心", "广场", "大道", "地铁", "店", "国际", "开发区", "景区", "风景区")
STAGES = ("config", "data", "assets", "derive", "calibration", "areas", "review", "check", "eval")
# 每步预计外部调用（模型 / 高德），dry-run 和开跑前打印
ESTIMATES = {"config": (0, 2), "data": (0, 0), "assets": ("ceil(n/20)+3*ceil(n/12)", 0), "derive": (0, 0),
             "calibration": (0, 48), "areas": (2, 0), "review": (0, 0), "check": (0, 0), "eval": (20, "≈20×85")}


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def city_key(name: str, key: str | None = None) -> str:
    if key:
        return key
    if name in DATASET_CITIES:
        return DATASET_CITIES[name]
    raise SystemExit(f"{name} 不在 ChinaTravel 覆盖的 10 个城市内，请用 --key 指定英文键并自备数据")


def bounds_from_polyline(polyline: str, margin: float = 0.05) -> list[float]:
    """高德行政区边界（GCJ-02）外扩为粗筛矩形 [南, 北, 西, 东]；数据集 BD-09 偏移约 0.006°，外扩足以覆盖。"""
    points = [tuple(map(float, pair.split(","))) for ring in polyline.split("|") for pair in ring.split(";") if pair]
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    return [round(min(lats) - margin, 3), round(max(lats) + margin, 3), round(min(lons) - margin, 3), round(max(lons) + margin, 3)]


def amap_city_meta(name: str) -> dict:
    """行政区与省份：高德行政区查询 1 次＋逆地理 1 次。"""
    from app.amap import get_router
    router = get_router()
    district = router._get("v3/config/district", {"keywords": name, "subdistrict": 0, "extensions": "all"})
    hits = [item for item in district.get("districts") or [] if item.get("level") in ("city", "province")]
    if not hits:
        raise SystemExit(f"高德行政区查不到 {name}")
    item = hits[0]
    lon, lat = map(float, item["center"].split(","))
    regeo = router._get("v3/geocode/regeo", {"location": item["center"]})
    province = (regeo.get("regeocode") or {}).get("addressComponent", {}).get("province") or item["name"]
    return {"name_full": item["name"], "adcode": item["adcode"], "province": province if isinstance(province, str) else item["name"],
            "center": {"lat": lat, "lon": lon}, "bounds": bounds_from_polyline(item["polyline"])}


def draft_config(name: str, key: str, meta: dict) -> dict:
    labels = LOCAL_CUISINE.get(key, {})
    return {"key": key, "name": name, "name_full": meta["name_full"], "province": meta["province"], "amap_city": name,
            "adcode": meta["adcode"], "bounds": meta["bounds"],
            "default_origin": {"name": f"{name}市中心（待推导）", **meta["center"]}, "map_center": dict(meta["center"]),
            "data_dir": f"data/{key}", "assets_dir": f"app/assets/cities/{key}", "supplement_dir": f"data/{key}/poi_supplement",
            "calibration": f"data/{key}/travel_calibration.json",
            "local_cuisine": "|".join(labels) or name, "local_cuisine_labels": labels,
            "evening_streets": {}, "brand": f"{name}行记", "hotel_areas": [],
            "provenance": {"bounds": "高德行政区边界外扩 0.05° 的粗筛矩形，不是行政边界", "generated_by": "scripts/onboard_city.py",
                           "generated_at": now(), "reviewer": "自动生成，未经人工复核"}}


def local_cuisine_from_data(restaurants, name: str) -> dict[str, str]:
    """未收录城市：取数据集中最常见、非通用的菜系作本地菜。"""
    counts = Counter(str(value) for value in restaurants["cuisine"])
    local = [cuisine for cuisine, _ in counts.most_common() if cuisine not in GENERIC_CUISINE][:2]
    return {cuisine.removesuffix("菜"): cuisine for cuisine in local} or {name: f"{name}菜"}


def default_origin(attractions, accommodations, name: str) -> dict:
    """周边 3 公里内景点最多的住宿，作为默认参考起点（数据集历史坐标，不宣称地铁口）。"""
    import numpy as np
    scale = math.cos(math.radians(float(attractions.lat.median())))
    sights = np.c_[attractions.lat.to_numpy() * 111, attractions.lon.to_numpy() * 111 * scale]
    hotels = np.c_[accommodations.lat.to_numpy() * 111, accommodations.lon.to_numpy() * 111 * scale]
    counts = (np.linalg.norm(hotels[:, None, :] - sights[None, :, :], axis=2) <= 3).sum(axis=1)
    best = accommodations.assign(n=counts).sort_values(["n", "id"], ascending=[False, True]).iloc[0]
    return {"name": f"{best['name']}附近（默认参考起点）", "lat": float(best.lat), "lon": float(best.lon)}


def hotel_areas(accommodations, names: tuple[str, ...], count: int = 6, spread_km: float = 2.5) -> list[str]:
    """从住宿店名括号里的分店名提取常见区域词（如“观前街”“金鸡湖”）：每个至少命中 3 家、互不包含，
    且命中的住宿集中在一处（离中心的中位距离不超过 spread_km），以排除“高铁站”“温泉”这类分散的词。"""
    hotels = [str(value) for value in accommodations["name"]]
    lat0 = float(accommodations.lat.median())
    places = [(float(row.lat) * 111, float(row.lon) * 111 * math.cos(math.radians(lat0))) for row in accommodations.itertuples()]

    def compact(token: str) -> bool:
        points = [point for hotel, point in zip(hotels, places) if token in hotel]
        cy, cx = sum(p[0] for p in points) / len(points), sum(p[1] for p in points) / len(points)
        distances = sorted(math.hypot(p[0] - cy, p[1] - cx) for p in points)
        return distances[len(distances) // 2] <= spread_km
    texts = []
    for hotel in hotels:
        match = re.search(r"[（(]([^)）]+)[)）]", hotel)
        if match:
            text = match.group(1)
            for prefix in names:
                text = text.replace(prefix, "")
            # 通用词切开再取片段，避免“博览中心”留下“博览中”这类残词
            texts.append(re.sub("|".join(sorted(HOTEL_AREA_STOP, key=len, reverse=True)), "|", re.sub(r"(分店|店)$", "", text)))
    tokens = Counter()
    for text in texts:
        seen = {text[i:i + size] for size in (2, 3, 4) for i in range(len(text) - size + 1)}
        tokens.update(token for token in seen if "|" not in token and not re.search(r"[A-Za-z0-9]", token))
    hits = {token: sum(token in hotel for hotel in hotels) for token in tokens}
    chosen: list[str] = []
    for token in sorted(hits, key=lambda token: (-hits[token], -len(token), token)):
        if hits[token] < 3 or any(token in other or other in token for other in chosen) or not compact(token):
            continue
        # 词的片段（如“西湖湖滨”里的“西湖湖”）：命中的住宿大半已被选中的词覆盖，就跳过
        matched = [hotel for hotel in hotels if token in hotel]
        if sum(any(other in hotel for other in chosen) for hotel in matched) * 2 >= len(matched):
            continue
        chosen.append(token)
        if len(chosen) == count:
            break
    return chosen


def evening_streets(pois: dict, count: int = 4) -> dict[str, str]:
    """夜游街：带“夜景夜游”兴趣的商业街区/步行街，按建议时长取前几处。"""
    streets = [poi for poi in pois.values() if poi.recommendable and "夜景夜游" in poi.interests
               and (poi.type == "商业街区" or re.search(r"街|夜市|里$", poi.name))]
    streets.sort(key=lambda poi: (not re.search(r"街|夜市|巷", poi.name), -poi.max_hours, poi.id))
    return {str(poi.id): poi.name for poi in streets[:count]}


def rule_reviews(raw, pois: dict, references: dict, bounds: list[float]) -> tuple[list[dict], list[dict]]:
    """南京总结的通用规则：能定的直接改（只加时长不减、越界隔离、大型乐园整日），其余列待核。返回 (修订, 待核)。"""
    south, north, west, east = bounds
    changes, pending = [], []
    for row in raw.itertuples():
        poi, entry, flags = pois[row.id], {}, []
        if not (south <= row.lat <= north and west <= row.lon <= east):
            entry.update(recommendable=False)
            flags.append("坐标在城市范围外，已暂停推荐")
        if re.search(r"20\d\d|嘉年华|音乐节|演唱会", str(row.name)):
            entry.update(recommendable=False)
            flags.append("名称是限时活动（如“2024……嘉年华”），已暂停推荐")
        reference = references.get(row.id)
        if reference and reference.get("confidence") != "low" and "recommendable" not in entry:
            low, high = reference["low_min"] / 60, reference["high_min"] / 60
            # 只加不减：原表上限不足模型典型值一半时，按模型区间调长（四舍五入到半小时）
            if poi.max_hours * 60 < reference["typical_min"] / 2:
                new_low, new_high = max(poi.min_hours, round(low * 2) / 2), max(poi.max_hours, round(high * 2) / 2)
                if new_high > poi.max_hours:
                    entry.update(min_hours=new_low, max_hours=new_high)
                    flags.append(f"原表 {poi.min_hours}–{poi.max_hours} 小时明显偏短，按模型估计调为 {new_low}–{new_high} 小时")
            if poi.max_hours * 60 > reference["typical_min"] * 2.5 and reference["typical_min"] <= 60:
                pending.append({"id": int(row.id), "name": row.name, "issue": f"原表 {poi.max_hours} 小时可能偏长（模型典型 {reference['typical_min']} 分），未自动缩短"})
            big_park = (row.type == "游乐园/体育娱乐" and row.price >= 150 and reference["typical_min"] >= 300
                        and re.search(r"乐园|欢乐谷|方特|世界|迪士尼|海洋", row.name))
            if big_park and not poi.day_anchor:
                entry.update(day_anchor=True, onsite_dining=True, min_hours=max(entry.get("min_hours", poi.min_hours), 5.0),
                             max_hours=max(entry.get("max_hours", poi.max_hours), 7.0))
                flags.append("大型主题乐园，参照杭州乐园按整日游玩、园内用餐")
        if row.price == 0 and re.search(r"乐园|游船|画舫|水世界|欢乐|影视|动物园|海洋|漂流", row.name):
            pending.append({"id": int(row.id), "name": row.name, "issue": "原表门票 0 元，疑似收费项目，预算可能偏低"})
        if re.search(r"大学|学院", row.name) and row.type != "博物馆/纪念馆":
            pending.append({"id": int(row.id), "name": row.name, "issue": "校园类地点，可能需预约或不对社会开放"})
        if entry:
            changes.append({"id": int(row.id), "name": row.name, "sources": ["onboard_rules"], "changes": entry,
                            "note": "；".join(flags)})
    names = Counter(re.sub(r"[（(].*?[)）]|景区|风景区|公园", "", str(name)) for name in raw.name)
    for row in raw.itertuples():
        if names[re.sub(r"[（(].*?[)）]|景区|风景区|公园", "", str(row.name))] > 1:
            pending.append({"id": int(row.id), "name": row.name, "issue": "名称与其他条目相近，可能重复"})
    return changes, pending


HOTEL_SLOTS = ("central", "north", "east", "south", "west", "outer")
TEMPLATES = [  # 与南京 20 画像同一组条件（天数/交通/预算/节奏/同行/兴趣），起点按酒店分布自动取
    ("01", "outer", 2, "transit", 300, "normal", "solo", (), {}),
    ("02", "outer", 1, "transit", 200, "relaxed", "couple", ("拍照出片",), {}),
    ("03", "outer", 3, "taxi", 1000, "normal", "friends", ("美食探店", "夜景夜游"), {}),
    ("04", "central", 1, "transit", 500, "intense", "solo", ("人文历史", "博物馆展览"), {}),
    ("05", "central", 2, "transit", 1500, "relaxed", "elderly", ("自然风光", "园林古镇"), {}),
    ("06", "central", 3, "transit", 2000, "normal", "couple", ("拍照出片", "夜景夜游"), {"start_date": "2026-10-24"}),
    ("07", "north", 2, "taxi", 1500, "normal", "family_kids", ("亲子游乐",), {}),
    ("08", "outer", 1, "transit", 400, "normal", "solo", (), {"must_visit": "museum"}),
    ("09", "north", 2, "transit", 800, "intense", "friends", ("购物街区", "美食探店"), {}),
    ("10", "west", 2, "taxi", 1200, "normal", "couple", ("艺术文创", "小众安静"), {}),
    ("11", "west", 3, "transit", 1500, "relaxed", "family_kids", ("亲子游乐", "自然风光"), {}),
    ("12", "south", 2, "transit", 1000, "normal", "solo", ("自然风光", "户外徒步"), {}),
    ("13", "south", 1, "taxi", 600, "relaxed", "elderly", ("宗教寺庙",), {}),
    ("14", "north", 2, "transit", 1200, "normal", "couple", ("夜景夜游", "拍照出片"), {}),
    ("15", "east", 1, "transit", 300, "intense", "solo", ("人文历史",), {"spice": "no"}),
    ("16", "east", 2, "taxi", 2000, "normal", "friends", ("人文历史", "园林古镇"), {"must_visit": "heritage"}),
    ("17", "south", 2, "transit", 1500, "normal", "family_kids", ("主题乐园",), {}),
    ("18", "central", 2, "transit", 800, "normal", "solo", ("博物馆展览", "艺术文创"), {"start_date": "2026-10-26"}),
    ("19", "central", 4, "transit", 3000, "relaxed", "couple", ("人文历史", "自然风光"), {}),
    ("20", "outer", 2, "taxi", 500, "normal", "solo", (), {}),
]


def hotel_slots(accommodations, origin: dict) -> dict[str, dict]:
    """中心＝默认起点；东南西北取离起点 3–12 公里、方位最接近的住宿；outer 取 10–25 公里内距离居 3/4 分位的住宿
    （不取最远：苏州试点里最远的是太湖岛上民宿，公交往返 6 小时，测不出常规郊区起点）。"""
    lat0, lon0 = origin["lat"], origin["lon"]
    scale = math.cos(math.radians(lat0))
    rows = []
    for row in accommodations.itertuples():
        dy, dx = (row.lat - lat0) * 111, (row.lon - lon0) * 111 * scale
        rows.append((math.hypot(dx, dy), math.degrees(math.atan2(dy, dx)) % 360, int(row.id), row))
    slots = {"central": {"name": origin["name"], "lat": origin["lat"], "lon": origin["lon"]}}
    for slot, angle in (("east", 0), ("north", 90), ("west", 180), ("south", 270)):
        near = [item for item in rows if 3 <= item[0] <= 12] or rows
        best = min(near, key=lambda item: (min(abs(item[1] - angle), 360 - abs(item[1] - angle)), item[2]))
        slots[slot] = {"name": best[3].name + "附近（数据集参考起点）", "lat": float(best[3].lat), "lon": float(best[3].lon)}
    far = sorted([item for item in rows if 10 <= item[0] <= 25] or rows, key=lambda item: (item[0], item[2]))
    best = far[(len(far) * 3) // 4 if len(far) > 1 else 0]
    slots["outer"] = {"name": best[3].name + "附近（数据集参考起点）", "lat": float(best[3].lat), "lon": float(best[3].lon)}
    return slots


def must_visit_names(pois: dict, names: tuple[str, ...] = (), origin: dict | None = None) -> dict[str, str]:
    """必去画像用的代表景点：博物馆优先“<城市/省>博物馆/院”；古迹取起点 15 公里内门票最高者（名胜多收费），再比时长。"""
    usable = [poi for poi in pois.values() if poi.recommendable]
    if origin:
        near = [poi for poi in usable if math.hypot(poi.lat - origin["lat"], (poi.lon - origin["lon"]) * math.cos(math.radians(origin["lat"]))) * 111 <= 15]
        usable_near = near or usable
    else:
        usable_near = usable
    museums = [poi for poi in usable if poi.type == "博物馆/纪念馆" and re.search("博物院|博物馆", poi.name)]
    flagship = [poi for poi in museums for name in names if name and re.fullmatch(f"{name}(市|省)?博物(院|馆)", poi.name)]
    museum = min(flagship, key=lambda poi: poi.id) if flagship else max(museums or [p for p in usable if p.type == "博物馆/纪念馆"],
                                                                          key=lambda poi: (poi.max_hours, -poi.id))
    heritage = max([poi for poi in usable_near if poi.type in ("历史古迹", "红色景点", "园林")], key=lambda poi: (poi.price, poi.max_hours, -poi.id))
    return {"museum": museum.name, "heritage": heritage.name}


def generic_profiles(key: str, accommodations, origin: dict, pois: dict, names: tuple[str, ...] = ()) -> list[tuple[str, dict]]:
    slots, musts = hotel_slots(accommodations, origin), must_visit_names(pois, names, origin)
    out = []
    for number, slot, days, transport, budget, pace, companions, interests, extra in TEMPLATES:
        form = {"city": key, "hotel": slots[slot], "days": days, "transport": transport, "budget": budget, "pace": pace,
                "companions": companions, "interests": list(interests), **{k: v for k, v in extra.items() if k != "must_visit"}}
        if "must_visit" in extra:
            form["must_visit"] = [musts[extra["must_visit"]]]
        tag = "-".join(filter(None, [slot, f"{days}d", transport if not interests else interests[0]]))
        out.append((f"{number}-{tag}", form))
    return out


# ---------------- 各步骤 ----------------

def load_definitions() -> dict:
    return json.loads(CITIES_PATH.read_text(encoding="utf-8"))


def save_definitions(definitions: dict) -> None:
    CITIES_PATH.write_text(json.dumps(definitions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    reset_caches()


def reset_caches() -> None:
    """前一步改了 cities.json 或审阅文件后，同一进程里的后续步骤要读新内容。"""
    from app import cities, data
    for cached in (cities._definitions, data.load_base_pois, data.base_digest, data._load_restaurants):
        cached.cache_clear()


def stage_config(name: str, key: str) -> dict:
    definitions = load_definitions()
    if key in definitions:
        return {"skipped": "cities.json 已有该城市"}
    definitions[key] = draft_config(name, key, amap_city_meta(name))
    save_definitions(definitions)
    return {"bounds": definitions[key]["bounds"], "adcode": definitions[key]["adcode"]}


def stage_data(key: str, mirror: str) -> dict:
    from scripts import fetch_data
    sys.argv = ["fetch_data.py", "--city", key, "--mirror", mirror]
    fetch_data.main()
    from app.cities import city_config
    return json.loads((city_config(key).data_dir / "meta.json").read_text())["tables"]


def stage_assets(key: str) -> dict:
    from scripts import city_model_assets
    sys.argv = ["city_model_assets.py", "--city", key, "--stage", "both"]
    status = city_model_assets.main()
    if status:
        raise SystemExit(f"模型资产生成失败（退出码 {status}），检查点已保存，可修复后重跑 --stage assets")
    return {"exit_code": status}


def stage_derive(key: str) -> dict:
    import pandas as pd
    from app.cities import city_config
    from app.data import load_base_pois
    settings = city_config(key)
    attractions = pd.read_parquet(settings.data_dir / "attractions.parquet")
    accommodations = pd.read_parquet(settings.data_dir / "accommodations.parquet")
    restaurants = pd.read_parquet(settings.data_dir / "restaurants.parquet")
    definitions = load_definitions()
    entry = definitions[key]
    origin = default_origin(attractions, accommodations, entry["name"])
    entry.update(default_origin=origin, map_center={"lat": origin["lat"], "lon": origin["lon"]},
                 hotel_areas=hotel_areas(accommodations, (entry["name_full"], entry["name"], entry["province"], entry["province"].removesuffix("省"))),
                 evening_streets=evening_streets(load_base_pois(key)))
    if key not in LOCAL_CUISINE:
        labels = local_cuisine_from_data(restaurants, entry["name"])
        entry.update(local_cuisine="|".join(labels), local_cuisine_labels=labels)
    entry["provenance"].update(default_origin="景点分布中位点最近的住宿（数据集历史坐标）", hotel_areas="住宿店名中常见的分店区域词",
                               night_streets="带夜景夜游兴趣的商业街区，未核实夜间营业", derived_at=now())
    save_definitions(definitions)
    return {key: entry[key] for key in ("default_origin", "hotel_areas", "evening_streets", "local_cuisine")}


def stage_calibration(key: str) -> dict:
    from scripts import collect_city_calibration
    sys.argv = ["collect_city_calibration.py", "--city", key]
    collect_city_calibration.main()
    from app.cities import city_config
    calibration = json.loads(city_config(key).calibration.read_text())
    return {name: calibration[name] for name in ("drive_min", "transit_min", "taxi_cost")}


def stage_areas(key: str) -> dict:
    """片区自动聚类；成员 ≥3 的片区请模型起草展示名，其余用“<成员名>一带”。"""
    from pydantic import BaseModel
    from app.areas import build_area_index
    from app.cities import city_config
    from app.data import load_base_pois
    from app.llm import OpenAICompatLLM, chat_json
    places = {identifier: poi for identifier, poi in load_base_pois(key).items() if poi.recommendable}
    index = build_area_index(places, [])
    groups: dict[str, list[int]] = {}
    for identifier in places:
        groups.setdefault(index.area_of(identifier), []).append(identifier)
    big = sorted((members for members in groups.values() if len(members) >= 3), key=lambda members: (-len(members), min(members)))

    class Name(BaseModel):
        anchor_id: int
        name: str

    class Names(BaseModel):
        areas: list[Name]

    lines = [f"anchor_id={min(members)} | " + "、".join(places[i].name for i in sorted(members)[:12]) for members in big]
    llm = OpenAICompatLLM(retry_attempts=1, sdk_max_retries=0)
    named: dict[int, str] = {}
    for start in range(0, len(lines), 40):
        out = chat_json(llm, [{"role": "system", "content": f"你给{city_config(key).name}的旅游片区起展示名。每行是一个片区的若干景点。"
                               "名字用游客熟悉的地名或代表景点，2–10 个字，可用“·”连接两个地名，不编造不存在的地名。"
                               '只输出JSON：{"areas":[{"anchor_id":0,"name":"片区名"}]}，逐条返回所有 anchor_id。'},
                              {"role": "user", "content": "\n".join(lines[start:start + 40])}], Names, tag="area_names",
                        temperature=0.0, max_tokens=4000, retries=0)
        anchors = {min(members) for members in big}
        named.update({item.anchor_id: item.name.strip()[:12] for item in out.areas if item.anchor_id in anchors and item.name.strip()})
    payload = {"version": f"{now()[:10]}-onboard-v1", "scope": "自动聚类＋模型起草，待用户审阅；只影响展示名称，不是行政边界。",
               "areas": [{"anchor_id": anchor, "name": name} for anchor, name in sorted(named.items())]}
    path = city_config(key).assets_dir / "area_names.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"areas": len(groups), "named": len(named), "model_requests": llm.request_attempts}


def stage_review(key: str) -> dict:
    import csv
    import pandas as pd
    from app.cities import city_config
    from app.data import load_base_pois
    settings = city_config(key)
    raw = pd.read_parquet(settings.data_dir / "attractions.parquet")
    reference_path = settings.assets_dir / "stay_reference.json"
    references = {entry["poi_id"]: entry for entry in json.loads(reference_path.read_text())["places"]} if reference_path.exists() else {}
    review_path = settings.assets_dir / "poi_reviews.json"
    if review_path.exists():
        review = json.loads(review_path.read_text())
        review["places"] = [entry for entry in review["places"] if entry["sources"] != ["onboard_rules"]]
        review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        reset_caches()  # 规则按去掉旧规则修订后的数据重新判断
    else:
        review = {"version": f"{now()[:10]}-onboard-v1", "reviewed_at": now()[:10],
                  "scope": "scripts/onboard_city.py 通用规则修正，未做人工或现场核验。", "sources": {}, "places": []}
    review["sources"]["onboard_rules"] = {"url": "scripts/onboard_city.py", "published_at": None, "verification": "curator_inference",
                                          "facts": "接入脚本通用规则：越界隔离、偏短时长按模型估计调长（只加不减）、大型乐园整日；时长为估计，不是现场事实。"}
    changes, pending = rule_reviews(raw, load_base_pois(key), references, settings.bounds)
    review["places"].extend(changes)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    out = settings.data_dir / "onboarding-review"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "pending.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "name", "issue"])
        writer.writeheader()
        writer.writerows(pending)
    return {"rule_changes": len(changes), "pending": len(pending), "pending_csv": str(out / "pending.csv")}


def stage_check(key: str) -> dict:
    from scripts.check_city import check
    problems = check(key)
    if problems:
        raise SystemExit("完整性检查未通过：\n" + "\n".join(problems))
    return {"problems": 0}


def stage_eval(key: str) -> dict:
    from eval import city_batch20
    # 规划入口只接受启用城市；评测时把待接入城市临时加进本进程的 TTA_CITIES（2026-10-21 北京评测全部 CityDisabled），不影响生产与测试实例
    enabled = [item for item in os.environ.get("TTA_CITIES", "hangzhou").split(",") if item.strip()]
    if key not in enabled:
        os.environ["TTA_CITIES"] = ",".join([*enabled, key])
    root = config.BACKEND_DIR.parent / "data" / f"onboard-{key}-{now()[:10].replace('-', '')}" / "batch20"
    for mode in ("capture", "replay", "assess"):
        sys.argv = ["city_batch20.py", mode, "--root", str(root), "--city", key]
        city_batch20.main()
    results = json.loads((root / "replay.json").read_text())["results"]
    errors = [row["id"] for row in results if "error" in row]
    passed = sum(row.get("report", {}).get("passed", False) for row in results)
    hard = sum(len([v for v in row["report"]["violations"] if v["severity"] == "hard"]) for row in results if "report" in row)
    gate = not errors and passed >= len(results) - 2
    return {"root": str(root), "errors": errors, "constraints_passed": f"{passed}/{len(results)}", "hard_violations": hard,
            "gate_passed": gate, "assess": str(root / "assess.md")}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--name", required=True, help="城市中文名，如 苏州")
    parser.add_argument("--key", help="英文键；ChinaTravel 覆盖的 10 个城市可省略")
    parser.add_argument("--stage", choices=STAGES, help="只跑（并强制重跑）这一步")
    parser.add_argument("--mirror", default="https://huggingface.co")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    key = city_key(args.name, args.key)
    record_path = config.BACKEND_DIR.parent / "data" / key / "onboarding.json"
    record = json.loads(record_path.read_text()) if record_path.exists() else {"city": key, "name": args.name, "stages": {}}
    todo = [args.stage] if args.stage else [stage for stage in STAGES if stage not in record["stages"]]
    print(json.dumps({"city": key, "todo": todo, "estimates(模型, 高德)": {stage: ESTIMATES[stage] for stage in todo}}, ensure_ascii=False))
    if args.dry_run:
        return 0
    runners = {"config": lambda: stage_config(args.name, key), "data": lambda: stage_data(key, args.mirror),
               "assets": lambda: stage_assets(key), "derive": lambda: stage_derive(key),
               "calibration": lambda: stage_calibration(key), "areas": lambda: stage_areas(key),
               "review": lambda: stage_review(key), "check": lambda: stage_check(key), "eval": lambda: stage_eval(key)}
    for stage in todo:
        print(f"== {stage}", flush=True)
        reset_caches()
        result = runners[stage]()
        record["stages"][stage] = {"finished_at": now(), **result}
        record_path.parent.mkdir(parents=True, exist_ok=True)
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
        if stage == "eval" and not result["gate_passed"]:
            print("评测门槛未通过：不建议启用，先看 assess.md", flush=True)
            return 1
    if "eval" in record["stages"]:
        print(f"完成：可在测试实例 TTA_CITIES 加入 {key}；片区名见 app/assets/cities/{key}/area_names.json，"
              f"待核清单见 data/{key}/onboarding-review/pending.csv", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
