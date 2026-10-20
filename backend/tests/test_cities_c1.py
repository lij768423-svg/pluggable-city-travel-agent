"""C1：杭州参数保真、旧引用兼容，以及城市配置/目录缓存的边界。"""
import copy
import json
from pathlib import Path

import pandas as pd
import pytest
from pydantic import ValidationError

from app import cities, config, data
from app.areas import area_index
from app.cities import city_config, current_city, enabled_cities
from app.poi_catalog import PlanningCatalog, base_catalog, register_catalog, resolve_catalog, use_catalog
from app.schemas import UserProfile
from tests.conftest import needs_data


def test_hangzhou_configuration_preserves_existing_parameters(monkeypatch):
    city = city_config()
    assert (city.key, city.name, city.name_full, city.province) == ("hangzhou", "杭州", "杭州市", "浙江省")
    assert (city.amap_city, city.adcode, city.bounds) == ("杭州", "330100", (29, 31, 118, 121))
    assert city.default_origin == {"name": "西湖湖滨（默认）", "lat": 30.254433, "lon": 120.169771}
    assert city.map_center == {"lat": 30.254433, "lon": 120.169771}
    assert city.data_dir == config.CITY_DIR
    assert city.assets_dir == config.BACKEND_DIR / "app/assets"
    assert city.supplement_dir == config.POI_SUPPLEMENT_DIR
    assert city.calibration == config.DATA_DIR / "travel_calibration.json"
    assert city.local_cuisine == r"杭帮|浙江菜|江浙"
    assert city.evening_streets == {20: "清河坊街", 69: "武林夜市", 91: "南宋御街", 208: "湖滨路步行街"}
    assert city.brand == "杭州行记"
    assert city.asset_reference("poi_reviews.json") == "app/assets/poi_reviews.json"
    monkeypatch.delenv("TTA_CITIES", raising=False)
    assert enabled_cities() == ("hangzhou",)
    assert city.contains(29, 118) and city.contains(31, 121)
    assert not city.contains(float("nan"), 120) and not city.contains(32, 120)


def test_unknown_and_empty_city_settings_fail_explicitly(monkeypatch):
    with pytest.raises(ValueError, match="未知城市"):
        city_config("missing")
    monkeypatch.setenv("TTA_CITIES", "missing")
    with pytest.raises(ValueError, match="未知城市"):
        enabled_cities()
    monkeypatch.setenv("TTA_CITIES", "")
    with pytest.raises(ValueError, match="至少启用"):
        enabled_cities()


@needs_data
def test_old_base_and_supplement_references_resolve_without_city(monkeypatch):
    from app.poi_catalog import published_catalog
    for catalog in (base_catalog(), published_catalog()):
        old = {key: value for key, value in catalog.reference().items() if key != "city"}
        assert resolve_catalog(old) is catalog
    assert resolve_catalog(None).city == "hangzhou"
    reference = {**base_catalog().reference(), "city": "missing"}
    with pytest.raises(ValueError, match="未知城市"):
        resolve_catalog(reference)


@pytest.fixture
def synthetic_cities(tmp_path, monkeypatch):
    definitions = copy.deepcopy(cities._definitions())
    for key in ("testcity", "othercity"):
        raw = copy.deepcopy(definitions["hangzhou"])
        raw.update(key=key, name="测试城", name_full="测试城市", province="测试省", amap_city="测试城", adcode="999999",
                   bounds=[31, 33, 118, 120], default_origin={"name": "测试起点", "lat": 31.9, "lon": 118.8},
                   data_dir=f"data/{key}", assets_dir=f"app/assets/cities/{key}",
                   supplement_dir=f"data/{key}/poi_supplement", calibration=f"data/{key}/travel_calibration.json",
                   evening_streets={}, local_cuisine="测试菜", hotel_areas=[])
        definitions[key] = raw
        directory = tmp_path / "data" / key
        directory.mkdir(parents=True)
        pd.DataFrame([{"id": 0, "name": "测试景点", "type": "自然风光", "lat": 31.9, "lon": 118.8,
                       "opentime": "08:00", "endtime": "18:00", "price": 10,
                       "recommendmintime": 1, "recommendmaxtime": 2}]).to_parquet(directory / "attractions.parquet")
        pd.DataFrame([{"id": 0, "name": "测试餐厅", "cuisine": "测试菜", "lat": 31.9, "lon": 118.8,
                       "opentime": "08:00", "endtime": "22:00", "price": 30, "recommendedfood": "测试菜"}]).to_parquet(directory / "restaurants.parquet")
    monkeypatch.setattr(cities, "_definitions", lambda: definitions)
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(config, "BACKEND_DIR", tmp_path / "backend")
    monkeypatch.delenv("TTA_RESTAURANT_SNAPSHOT", raising=False)
    data.load_base_pois.cache_clear()
    data.base_digest.cache_clear()
    data.load_restaurants.cache_clear()
    yield
    data.load_base_pois.cache_clear()
    data.base_digest.cache_clear()
    data.load_restaurants.cache_clear()
    from app import poi_catalog, areas, travel
    poi_catalog._CATALOGS.clear()
    areas._CACHE.clear()
    travel.calibration.cache_clear()


def test_same_digest_and_overlapping_ids_remain_city_scoped(synthetic_cities):
    first, second = base_catalog("testcity"), base_catalog("othercity")
    assert first.version == second.version
    assert first is not second and first.pois is not second.pois
    assert resolve_catalog(first.reference()) is first
    assert resolve_catalog(second.reference()) is second
    with use_catalog(first):
        a = area_index()
        a_custom = area_index(dict(first.pois))
        restaurants = data.load_restaurants()
        assert current_city().key == "testcity"
        with use_catalog(second):
            assert current_city().key == "othercity"
            assert area_index() is not a
            assert area_index(dict(second.pois)) is not a_custom
            assert data.load_restaurants() is not restaurants
        assert current_city().key == "testcity"
    assert current_city().key == "hangzhou"


def test_defaults_search_and_assets_use_task_city(synthetic_cities, caplog):
    from app.guides import load_corpus
    from app.reasonableness import load_stay_references
    from app.media import load_registry
    from app.places import normalize_place
    from app.matching import norm
    from app.prompts import profile_to_text, select_system, baseline_system
    from app.travel import calibration, DEFAULT_CAL
    with use_catalog(base_catalog("testcity")):
        profile = UserProfile()
        assert profile.hotel.name == "测试起点" and profile.hotel.lat == 31.9
        with pytest.raises(ValidationError, match="起点须在测试城"):
            UserProfile(hotel={"name": "异地", "lat": 30.2, "lon": 118.8})
        assert load_corpus().samples == [] and load_registry().places == {}
        assert load_stay_references() == {}
        assert calibration() == DEFAULT_CAL and "testcity" in caplog.text
        assert norm("测试城市景点") == "景点"
        assert "城市：测试城" in profile_to_text(profile)
        assert "测试城旅行规划师" in select_system() and "测试城景点" in baseline_system()
        raw = {"name": "起点", "location": "118.8,31.9", "cityname": "杭州市"}
        assert normalize_place(raw) is None
        assert normalize_place({**raw, "cityname": "测试城市"}) is not None
    assert normalize_place({"name": "西湖", "location": "120.1,30.2", "cityname": "杭州市"}) is not None


def test_current_city_does_not_load_base_catalog(monkeypatch):
    # 原库读取会调用名称归一化，配置读取若触发目录加载便会递归。
    import app.poi_catalog as catalogs
    monkeypatch.setattr(catalogs, "current_catalog", lambda: pytest.fail("不应加载目录"))
    assert current_city().key == "hangzhou"


def test_config_changes_and_directory_overrides_do_not_use_stale_values(tmp_path, monkeypatch):
    original = city_config()
    monkeypatch.setattr(config, "POI_SUPPLEMENT_DIR", tmp_path / "supplement")
    assert city_config().supplement_dir == tmp_path / "supplement"
    raw = cities._definitions()["hangzhou"]
    monkeypatch.setitem(raw, "adcode", "changed-for-isolation-test")
    assert city_config().adcode == "changed-for-isolation-test"
    assert original.adcode == "330100"


def test_stay_estimation_keeps_city_in_cache_identity(synthetic_cities):
    from scripts.estimate_stay import messages_for
    rows = [{"poi_id": 0, "name": "同名公园"}]
    first = json.loads(messages_for(rows, 1, "testcity")[1]["content"])
    second = json.loads(messages_for(rows, 1, "othercity")[1]["content"])
    assert first["city"] == "testcity" and second["city"] == "othercity"
    assert first != second
    # 杭州旧检查点与模型缓存的提示词格式保持兼容。
    old = json.loads(messages_for(rows, 1)[1]["content"])
    assert old == {"prompt_version": "hangzhou-stay-v1", "independent_sample": 1, "places": rows}
