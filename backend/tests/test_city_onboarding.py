"""一键接入新城市：推导规则与完整性检查（不联网）。"""
from __future__ import annotations

import pandas as pd
import pytest

from app.schemas import POI
from scripts.onboard_city import (bounds_from_polyline, city_key, default_origin, evening_streets, generic_profiles,
                                  hotel_areas, local_cuisine_from_data, rule_reviews)


def place(identifier, name, **values):
    return POI(id=identifier, name=name, type=values.pop("type", "公园"), lat=values.pop("lat", 31.30), lon=values.pop("lon", 120.60),
               **{"open_min": 480, "close_min": 1260, "price": 0, "min_hours": 1, "max_hours": 2, "interests": ["人文历史"], **values})


def test_city_key_covers_dataset_cities_and_requires_key_otherwise():
    assert city_key("苏州") == "suzhou" and city_key("厦门", "xiamen") == "xiamen"
    with pytest.raises(SystemExit):
        city_key("厦门")


def test_bounds_from_amap_polyline_with_margin():
    assert bounds_from_polyline("120.5,31.2;120.9,31.5|121.0,31.1", margin=0.05) == [31.05, 31.55, 120.45, 121.05]


def test_hotel_areas_keep_compact_frequent_branch_names():
    names = ["全季酒店(苏州观前街店)", "汉庭(苏州观前街平江路店)", "如家(观前街乐桥店)", "亚朵(苏州金鸡湖店)", "希尔顿(金鸡湖东店)",
             "美居(苏州金鸡湖博览中心店)", "A(苏州北站高铁站店)", "B(吴江高铁站店)", "C(昆山高铁站店)"]
    lat = [31.31, 31.311, 31.312, 31.32, 31.321, 31.322, 31.42, 31.16, 31.38]
    lon = [120.62, 120.621, 120.622, 120.71, 120.711, 120.712, 120.60, 120.64, 120.95]
    areas = hotel_areas(pd.DataFrame({"name": names, "lat": lat, "lon": lon}), ("苏州市", "苏州"))
    assert areas[:2] == ["观前街", "金鸡湖"] and not any("铁站" in area for area in areas)


def test_default_origin_is_hotel_with_most_sights_nearby():
    sights = pd.DataFrame({"lat": [31.30, 31.301, 31.302, 31.50], "lon": [120.60, 120.601, 120.602, 120.90]})
    hotels = pd.DataFrame({"id": [1, 2], "name": ["郊区酒店", "市中心酒店"], "lat": [31.49, 31.3005], "lon": [120.89, 120.6005]})
    assert default_origin(sights, hotels, "苏州")["name"].startswith("市中心酒店")


def test_evening_streets_prefer_named_streets_with_night_interest():
    pois = {1: place(1, "平江路历史街区", type="商业街区", interests=["夜景夜游"], max_hours=3),
            2: place(2, "某商场", type="商业街区", interests=["夜景夜游"], max_hours=4),
            3: place(3, "白天公园", interests=["自然风光"])}
    assert list(evening_streets(pois, count=2)) == ["1", "2"]


def test_rule_reviews_only_lengthen_quarantine_outside_and_flag_zero_price():
    raw = pd.DataFrame([{"id": 1, "name": "某某乐园", "type": "游乐园/体育娱乐", "price": 200, "lat": 31.3, "lon": 120.6},
                        {"id": 2, "name": "小亭子", "type": "人文景观", "price": 0, "lat": 31.3, "lon": 120.6},
                        {"id": 3, "name": "邻市景区", "type": "自然风光", "price": 0, "lat": 32.5, "lon": 120.6},
                        {"id": 4, "name": "水上乐园", "type": "游乐园/体育娱乐", "price": 0, "lat": 31.3, "lon": 120.6}])
    pois = {1: place(1, "某某乐园", type="游乐园/体育娱乐", min_hours=0.5, max_hours=1, price=200),
            2: place(2, "小亭子", min_hours=2, max_hours=3), 3: place(3, "邻市景区", lat=32.5), 4: place(4, "水上乐园")}
    references = {1: {"low_min": 300, "high_min": 420, "typical_min": 360, "confidence": "medium"},
                  2: {"low_min": 10, "high_min": 30, "typical_min": 20, "confidence": "high"}}
    changes, pending = rule_reviews(raw, pois, references, [31.0, 32.0, 120.0, 121.0])
    by_id = {entry["id"]: entry["changes"] for entry in changes}
    assert by_id[1] == {"min_hours": 5.0, "max_hours": 7.0, "day_anchor": True, "onsite_dining": True}
    assert 2 not in by_id  # 偏长不自动缩短，只进待核清单
    assert by_id[3] == {"recommendable": False}
    event = pd.DataFrame([{"id": 9, "name": "2024某某电音嘉年华", "type": "其它", "price": 0, "lat": 31.3, "lon": 120.6}])
    assert rule_reviews(event, {9: place(9, "2024某某电音嘉年华")}, {}, [31.0, 32.0, 120.0, 121.0])[0][0]["changes"] == {"recommendable": False}
    issues = {(row["id"], row["issue"][:6]) for row in pending}
    assert any(identifier == 2 for identifier, _ in issues) and any(identifier == 4 for identifier, _ in issues)


def test_local_cuisine_falls_back_to_most_common_regional_dish():
    frame = pd.DataFrame({"cuisine": ["西餐", "湖北菜", "湖北菜", "小吃", "湘菜"]})
    assert local_cuisine_from_data(frame, "武汉") == {"湖北": "湖北菜"}


def test_generic_profiles_cover_twenty_conditions_with_must_visits():
    from app.cities import city_config
    from app.data import load_base_pois
    settings = city_config("nanjing")
    if not (settings.data_dir / "accommodations.parquet").exists():
        pytest.skip("缺南京数据")
    profiles = generic_profiles("nanjing", pd.read_parquet(settings.data_dir / "accommodations.parquet"),
                                settings.default_origin, load_base_pois("nanjing"), (settings.name, settings.province.removesuffix("省")))
    pois = {poi.name for poi in load_base_pois("nanjing").values() if poi.recommendable}
    assert len(profiles) == 20 and len({name for name, _ in profiles}) == 20
    assert all(settings.contains(form["hotel"]["lat"], form["hotel"]["lon"]) for _, form in profiles)
    assert all(name in pois for _, form in profiles for name in form.get("must_visit", []))
    assert profiles[7][1]["must_visit"] == ["南京博物院"]


@pytest.mark.parametrize("city", ["hangzhou", "nanjing"])
def test_enabled_cities_pass_completeness_check(city):
    from app.cities import city_config
    from scripts.check_city import check
    settings = city_config(city)
    if not (settings.data_dir / "attraction_tags.parquet").exists() or not settings.calibration.exists():
        pytest.skip("公开版不带模型标签与标定，跳过")
    assert check(city) == []


def test_nanjing_batch_profiles_unchanged():
    from eval.city_batch20 import profiles
    from app.cities import city_config
    if not (city_config("nanjing").data_dir / "accommodations.parquet").exists():
        pytest.skip("缺南京数据")
    rows = profiles()
    assert len(rows) == 20 and rows[0][0] == "01-south-2d-transit" and rows[16][1]["interests"] == ["主题乐园"]


def test_tag_summary_list_is_joined():
    from scripts.tag_pois import _Out
    out = _Out.model_validate({"items": [{"id": 1, "summary": ["面向亲子", "创意体验"]}, {"id": 2, "summary": "园林"}]})
    assert [item.summary for item in out.items] == ["面向亲子；创意体验", "园林"]
