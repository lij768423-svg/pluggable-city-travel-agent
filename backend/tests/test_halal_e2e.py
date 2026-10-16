"""清真端到端回归：以前四顿全落到预留时段，现在应排到餐厅并写明须到店确认。"""
from app.graph import run_plan


def test_halal_gets_restaurants_with_a_confirmation_notice(mock_llm):
    form = {"days": 2, "interests": ["人文历史"], "dietary": ["清真"], "budget": 2000}
    r = run_plan("", form, llm=mock_llm, select_mode="rule")
    meals = [item for day in r["itinerary"].days for item in day.items if item.kind == "meal"]
    assert meals, "清真不应该整趟都排不出餐厅"
    assert all(any("须到店确认" in notice for notice in meal.notices) for meal in meals)
    assert any("清真" in unknown for unknown in r["report"].unknowns)
    # 放宽的餐厅只算待确认，不能让整份行程被判为行不通
    assert "diet_unverified" in {v.code for v in r["report"].violations}
    assert not [v for v in r["report"].violations if v.severity == "hard" and v.code.startswith("diet")]
