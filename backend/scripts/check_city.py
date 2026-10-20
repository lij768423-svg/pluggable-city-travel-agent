"""城市完整性检查：配置、数据、模型资产、标定与片区名是否齐全一致。返回问题列表，空表示可启用。

用法（backend 目录）：uv run python scripts/check_city.py --city suzhou
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

REQUIRED = ("key", "name", "name_full", "province", "amap_city", "adcode", "bounds", "default_origin", "map_center",
            "data_dir", "assets_dir", "supplement_dir", "calibration", "local_cuisine", "evening_streets", "brand")
MIN_RECOMMENDABLE = 100


def check(key: str) -> list[str]:
    import pandas as pd
    from app.cities import _definitions, city_config
    from app.data import load_base_pois
    raw = _definitions().get(key)
    if raw is None:
        return [f"cities.json 没有 {key}"]
    problems = [f"配置缺字段 {field}" for field in REQUIRED if field not in raw]
    if problems:
        return problems
    settings = city_config(key)
    for name in ("default_origin", "map_center"):
        point = raw[name]
        if not settings.contains(point["lat"], point["lon"]):
            problems.append(f"{name} 不在城市范围内")
    for table in ("attractions", "restaurants", "accommodations"):
        if not (settings.data_dir / f"{table}.parquet").exists():
            problems.append(f"缺数据 {table}.parquet（先跑 fetch_data.py --city {key}）")
    if problems:
        return problems
    if not (settings.data_dir / "attraction_tags.parquet").exists():
        problems.append("缺模型标签 attraction_tags.parquet（city_model_assets.py --stage tags）")
    if not settings.calibration.exists():
        problems.append("缺交通标定（collect_city_calibration.py）")
    if not (settings.assets_dir / "stay_reference.json").exists():
        problems.append("缺停留参考 stay_reference.json（city_model_assets.py --stage stay）")
    hotels = [str(name) for name in pd.read_parquet(settings.data_dir / "accommodations.parquet")["name"]]
    problems += [f"酒店区“{area}”匹配不到住宿" for area in settings.hotel_areas if not any(area in hotel for hotel in hotels)]
    try:
        pois = load_base_pois(key)
    except Exception as error:  # 审阅文件格式错误等
        return problems + [f"景点库加载失败：{type(error).__name__}: {str(error)[:120]}"]
    recommendable = sum(poi.recommendable for poi in pois.values())
    if recommendable < MIN_RECOMMENDABLE:
        problems.append(f"可推荐景点只有 {recommendable} 个（至少 {MIN_RECOMMENDABLE}）")
    outside = [poi.name for poi in pois.values() if poi.recommendable and not settings.contains(poi.lat, poi.lon)]
    if outside:
        problems.append(f"{len(outside)} 个可推荐景点在城市范围外：{'、'.join(outside[:5])}")
    for identifier, name in settings.evening_streets.items():
        if identifier not in pois or not pois[identifier].recommendable:
            problems.append(f"夜游街 {identifier} {name} 不存在或不可推荐")
    names_path = settings.assets_dir / "area_names.json"
    if not names_path.exists():
        problems.append("缺片区名 area_names.json（onboard_city.py --stage areas）")
    else:
        from app.poi_catalog import published_digest, supplement_catalog
        digest = published_digest(settings.supplement_dir)  # 片区名锚点可以是已发布的补充地点，不论当前进程是否启用补充目录
        catalog = supplement_catalog(digest, city=key).pois if digest else pois
        anchors = [entry["anchor_id"] for entry in json.loads(names_path.read_text(encoding="utf-8"))["areas"]]
        missing = [anchor for anchor in anchors if anchor not in catalog]
        if missing:
            problems.append(f"片区名锚点不存在：{missing[:5]}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", required=True)
    problems = check(parser.parse_args().city)
    print("\n".join(problems) if problems else "通过")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
