from app.nodes.meals import meal_suitability, menu_suggestions, pick_restaurant
from app.schemas import Restaurant, UserProfile
from .conftest import needs_data


def restaurant(place_id, name, cuisine="江浙菜", price=60, dishes="", lat=30.25, lon=120.16):
    return Restaurant(id=place_id, name=name, cuisine=cuisine, price=price, dishes=dishes,
                      lat=lat, lon=lon, open_min=600, close_min=1260)


def test_snack_shop_and_bar_are_not_lunch():
    profile = UserProfile(companions="family_kids")
    assert meal_suitability(restaurant(1, "某点心店", "小吃", dishes="葱煎包,定胜糕"), profile) is None
    assert meal_suitability(restaurant(2, "烧肉酒场", "日本料理"), profile) is None
    assert meal_suitability(restaurant(3, "某面馆", "小吃", dishes="片儿川"), profile) is not None


def test_proper_meal_beats_nearer_snack_and_expensive_option(monkeypatch):
    options = [restaurant(1, "某点心店", "小吃", 17, "葱煎包"),
               restaurant(2, "杭帮菜馆", price=68, lat=30.253),
               restaurant(3, "高价餐厅", price=149)]
    monkeypatch.setattr("app.nodes.meals.load_restaurants", lambda: {place.id: place for place in options})
    picked = pick_restaurant((30.25, 120.16), None, 720, UserProfile(), set(), 150)
    assert picked and picked[0].id == 2


def test_restaurant_cannot_start_after_meal_window(monkeypatch):
    late = restaurant(1, "晚开门菜馆")
    late.open_min = 900
    monkeypatch.setattr("app.nodes.meals.load_restaurants", lambda: {late.id: late})
    assert pick_restaurant((30.25, 120.16), None, 720, UserProfile(), set(), 100, latest_start=810) is None


def test_menu_skips_spicy_dishes_only_for_no_spicy():
    place = restaurant(1, "餐厅", dishes="辣椒炒肉,龙井虾仁,油焖春笋,清蒸鱼,番茄炒蛋")
    assert menu_suggestions(place, UserProfile(spice="no")) == ["龙井虾仁", "油焖春笋", "清蒸鱼"]
    assert menu_suggestions(place, UserProfile(spice="love")) == ["辣椒炒肉", "龙井虾仁", "油焖春笋"]


@needs_data
def test_relaxed_days_include_lunch_dinner_and_afternoon(mock_llm):
    from app.graph import run_plan

    result = run_plan("", {"days": 2, "pace": "relaxed", "interests": ["人文历史"], "budget": 1500},
                      llm=mock_llm, select_mode="rule")
    assert result["report"].passed
    for day in result["itinerary"].days:
        assert {item.meal for item in day.items if item.kind == "meal"} == {"lunch", "dinner"}
        assert max(item.end for item in day.items if item.kind == "poi") >= 16 * 60
        assert day.items[-1].end + day.return_travel_min >= 18 * 60


@needs_data
def test_no_repair_ablation_does_not_fill_gaps(mock_llm):
    from app.graph import run_plan

    result = run_plan("杭州两天", llm=mock_llm, enable_repair=False)
    assert all(selection.source == "llm" for selection in result["selected"])
    assert not any("空档修复" in line for line in result["log"])


def test_dinner_allows_actual_short_return_trip(monkeypatch):
    profile = UserProfile(pace="relaxed")
    place = restaurant(1, "酒店附近菜馆", lat=profile.hotel.lat, lon=profile.hotel.lon)
    monkeypatch.setattr("app.nodes.meals.load_restaurants", lambda: {place.id: place})
    picked = pick_restaurant((place.lat, place.lon), None, 18 * 60 + 15, profile, set(), 100,
                             latest_start=18 * 60 + 30)
    assert picked is not None


@needs_data
def test_three_day_gap_repair_preserves_meals(mock_llm):
    from app.graph import run_plan

    result = run_plan("", {"days": 3, "pace": "relaxed", "interests": ["人文历史"], "budget": 1500},
                      llm=mock_llm, select_mode="rule")
    assert result["report"].passed
    for day in result["itinerary"].days:
        assert {item.meal for item in day.items if item.kind == "meal"} == {"lunch", "dinner"}
