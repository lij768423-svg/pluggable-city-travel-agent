import pandas as pd
import pytest

from app.amap import pt
from app.data import load_restaurants
from app.geo import bd09_to_gcj02, haversine_km
from app.restaurants import normalize_restaurant
from tests.conftest import needs_data


@needs_data
@pytest.mark.parametrize("identifier,expected_lat,expected_lon", [
    (241, 30.2626682, 120.1608968),
    (342, 30.2622181, 120.1607605),
])
def test_historical_restaurant_map_and_navigation_keep_source_gcj(identifier, expected_lat, expected_lon):
    place = load_restaurants()[identifier]
    display = bd09_to_gcj02(place.lat, place.lon)
    assert haversine_km(*display, expected_lat, expected_lon) * 1000 < 1
    request_lon, request_lat = map(float, pt(place.lat, place.lon).split(","))
    assert haversine_km(request_lat, request_lon, expected_lat, expected_lon) * 1000 < 1
    assert haversine_km(place.lat, place.lon, expected_lat, expected_lon) * 1000 > 700


@needs_data
def test_online_and_historical_restaurant_use_same_internal_coordinate_system():
    historical = load_restaurants()[342]
    online = normalize_restaurant({"id": "B0HRR5TGA2", "name": "金猪", "cityname": "杭州市",
                                   "location": "120.160761,30.262217", "type": "餐饮服务;中餐厅;江浙菜",
                                   "business": {"cost": "62"}})
    assert online is not None
    assert haversine_km(historical.lat, historical.lon, online.lat, online.lon) * 1000 < 1


@needs_data
def test_alternative_api_coordinates_match_the_restaurant_source(mock_llm):
    from app.api import serialize_plan
    from app.graph import run_plan
    from app.schemas import MealAlternative
    result = run_plan(form={"days": 1, "budget": 800}, llm=mock_llm, select_mode="rule", enable_repair=False)
    restaurant = load_restaurants()[241]
    result["restaurants"][restaurant.id] = restaurant
    meal = next(item for day in result["itinerary"].days for item in day.items if item.kind == "meal")
    meal.alternatives = [MealAlternative(ref_id=restaurant.id, name=restaurant.name, cuisine=restaurant.cuisine,
                                        price=restaurant.price, detour_min=0, travel_source="estimate",
                                        provider=restaurant.provider, start=meal.start, end=meal.end)]
    response = serialize_plan(result, {"select_mode": "rule", "enable_repair": False, "guide_mode": "off"})
    alternative = next(alt for day in response["days"] for item in day["items"] for alt in item["alternatives"])
    assert alternative["lon"] == pytest.approx(120.1608968, abs=0.000002)
    assert alternative["lat"] == pytest.approx(30.2626682, abs=0.000002)


def test_offline_normalized_snapshot_is_not_converted_twice(tmp_path, monkeypatch):
    import json
    from app.schemas import Restaurant
    record = Restaurant(id=342, name="金猪", lat=30.26813, lon=120.16729, price=62,
                        cuisine="江浙菜", open_min=630, close_min=1200)
    snapshot = tmp_path / "restaurants.json"
    snapshot.write_text(json.dumps([record.model_dump()]))
    monkeypatch.setenv("TTA_OFFLINE", "1")
    monkeypatch.setenv("TTA_RESTAURANT_SNAPSHOT", str(snapshot))
    monkeypatch.setattr(pd, "read_parquet", lambda path: pd.DataFrame())
    load_restaurants.cache_clear()
    try:
        assert load_restaurants()[342].model_dump() == record.model_dump()
    finally:
        load_restaurants.cache_clear()
