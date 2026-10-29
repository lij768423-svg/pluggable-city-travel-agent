"""口味（辣度）端到端：不吃辣时一顿辣味菜系都不排；旧的 dietary 字段仍能读，含"不吃辣"即为不吃辣。"""
from app.data import SPICY_CUISINES, load_restaurants
from app.graph import run_plan
from app.schemas import UserProfile


def test_no_spicy_never_schedules_spicy_cuisines(mock_llm):
    r = run_plan("", {"days": 2, "interests": ["人文历史"], "spice": "no", "budget": 2000}, llm=mock_llm, select_mode="rule")
    restaurants = {**load_restaurants(), **(r.get("restaurants") or {})}
    meals = [item for day in r["itinerary"].days for item in day.items if item.kind == "meal"]
    assert meals and not [meal.name for meal in meals if restaurants[meal.ref_id].cuisine in SPICY_CUISINES]
    assert "diet_conflict" not in {v.code for v in r["report"].violations}


def test_legacy_dietary_is_read_as_spice():
    assert UserProfile(dietary=["不吃辣", "清真"]).spice == "no"
    assert UserProfile(dietary=["清真", "不吃海鲜"]).spice == "any"  # 其余忌口不再支持，直接丢弃
    assert UserProfile(dietary=["不吃辣"], spice="love").spice == "love"  # 显式给了口味时以口味为准
    assert "dietary" not in UserProfile(dietary=["不吃辣"]).model_dump()
