import pytest

from app.nodes.meals import scheduled_reason


@pytest.mark.parametrize("reason,start,end,expected", [
    ("水上赏湖，傍晚可顺路衔接晚餐，靠近住处。", 9 * 60, 10 * 60, "水上赏湖，靠近住处"),
    ("白堤桃柳相间，傍晚散步回住处方便。", 9 * 60, 11 * 60, "白堤桃柳相间"),
    ("适合人文历史爱好者，晚上可以欣赏夜景", 19 * 60, 20 * 60, "适合人文历史爱好者，晚上可以欣赏夜景"),
])
def test_reason_matches_scheduled_time(reason, start, end, expected):
    assert scheduled_reason(reason, start, end) == expected


def test_incompatible_reason_has_an_honest_fallback():
    reason = scheduled_reason("傍晚观日落", 9 * 60, 10 * 60)
    assert "日落" not in reason
    assert "实际安排时段" in reason
