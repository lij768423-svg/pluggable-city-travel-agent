"""规划前的“我是这么理解的”：字段来源、相对日期、接入方上下文与预检（2026-10-28 用户决定）。"""
from datetime import date
import json

import pytest

from app.calendar import resolve_date_phrase
from app.llm import MockLLM
from app.nodes.parse import parse_profile
from app.schemas import POI, TripContext
from app.understanding import understand

WEDNESDAY = date(2026, 10, 28)


@pytest.mark.parametrize("phrase, expected", [
    ("周末", date(2026, 10, 31)), ("这周末", date(2026, 10, 31)), ("下周末", date(2026, 11, 7)),
    ("明天", date(2026, 10, 29)), ("后天", date(2026, 10, 30)), ("大后天", date(2026, 10, 31)),
    ("周五", date(2026, 10, 30)), ("星期天", date(2026, 11, 1)), ("下周六", date(2026, 11, 7)),
    ("国庆", None), ("", None)])
def test_relative_date_phrases(phrase, expected):
    assert resolve_date_phrase(phrase, WEDNESDAY) == expected


def test_weekend_on_a_weekend_means_today():
    assert resolve_date_phrase("周末", date(2026, 11, 1)) == date(2026, 11, 1)


def parsed(**fields) -> MockLLM:
    return MockLLM({"parse": json.dumps(fields, ensure_ascii=False)})


@pytest.fixture
def places(monkeypatch):
    lingyin = POI(id=1, name="灵隐寺", type="宗教寺庙", lat=30.24, lon=120.10, open_min=420, close_min=1080, price=45,
                  min_hours=1, max_hours=2)
    monkeypatch.setattr("app.understanding.load_pois", lambda: {1: lingyin})


def sources(result) -> dict[str, str]:
    return {field.key: field.source for field in result.fields}


def test_vague_request_marks_what_was_said_inferred_and_defaulted(places):
    llm = parsed(days=1, companions="couple", date_phrase="周末")
    result = understand("周末一日游，跟女朋友一起去，你给我安排一下", {"city": "hangzhou"}, llm=llm, today=WEDNESDAY)
    assert result.profile.days == 1 and result.profile.start_date == date(2026, 10, 31)
    assert result.profile.budget == 300  # 没说预算按每天人均 300 元估（用户：本地大概人均 300）
    got = sources(result)
    assert got["days"] == got["companions"] == "said"
    assert got["start_date"] == "inferred" and got["city"] == "form"
    assert got["budget"] == got["familiarity"] == got["transport"] == got["hotel"] == "default"
    assert "must_visit" not in got  # 没说必去就不列
    assert result.issues == []


def test_context_infers_local_and_usual_transport_but_never_overrides_what_was_said(places):
    context = TripContext(home_city="杭州", usual_transport="transit")
    result = understand("周末一日游", {"city": "hangzhou"}, context, llm=parsed(days=1), today=WEDNESDAY)
    assert result.profile.familiarity == "local" and result.profile.transport == "transit"
    assert sources(result)["familiarity"] == sources(result)["transport"] == "inferred"
    said = understand("第一次来杭州，打车", {"city": "hangzhou"}, context,
                      llm=parsed(familiarity="first", transport="taxi"), today=WEDNESDAY)
    assert said.profile.familiarity == "first" and said.profile.transport == "taxi"


def test_tight_taxi_budget_offers_transit_or_more_budget(places):
    # 用户 2026-10-28 那份：杭州 4 天、人均 300、打车
    result = understand("", {"city": "hangzhou", "days": 4, "budget": 300, "transport": "taxi"}, llm=parsed(), today=WEDNESDAY)
    issue = next(item for item in result.issues if item.code == "budget_tight")
    assert issue.level == "warning"
    assert [fix.patch for fix in issue.fixes] == [{"transport": "transit"}, {"budget": 600}]


def test_past_date_blocks_and_unknown_must_visit_warns(places):
    form = {"city": "hangzhou", "start_date": "2026-10-01", "must_visit": ["灵隐寺", "不存在的景点"]}
    result = understand("", form, llm=parsed(), today=WEDNESDAY)
    codes = {item.code: item for item in result.issues}
    assert codes["date_past"].level == "blocker" and codes["date_past"].fixes[0].patch == {"start_date": "2026-10-29"}
    assert "不存在的景点" in codes["must_not_found"].message and "灵隐寺" not in codes["must_not_found"].message


def test_driving_is_not_planned_and_long_trips_are_capped(places):
    result = understand("自驾去杭州玩 7 天", {"city": "hangzhou"}, llm=parsed(days=7, transport="taxi"), today=WEDNESDAY)
    codes = {item.code for item in result.issues}
    assert {"no_driving", "days_capped"} <= codes and result.profile.days == 5


def test_parse_profile_resolves_relative_dates_too(monkeypatch):
    monkeypatch.setattr("app.nodes.parse.china_today", lambda: WEDNESDAY)
    assert parse_profile("周末去西湖", parsed(date_phrase="周末")).start_date == date(2026, 10, 31)
    assert parse_profile("周末去西湖", parsed(date_phrase="周末"), {"start_date": "2026-11-02"}).start_date == date(2026, 11, 2)


def test_default_budget_scales_with_days():
    from app.schemas import UserProfile
    assert UserProfile(days=1).budget == 300 and UserProfile(days=4).budget == 1200
    assert UserProfile(days=4, budget=500).budget == 500


def test_later_sentences_win_and_no_taxi_counts_as_transit():
    from app.prompts import PARSE_SYSTEM
    assert "以最后一次为准" in PARSE_SYSTEM  # 跟 AI 说模式：几轮的话合在一起解析
    profile = parse_profile("周末和女朋友一日游，预算300\n预算改成500，别打车", parsed(budget=500, transport="transit"))
    assert profile.budget == 500 and profile.transport == "transit"
    # 模型只给了“不允许改打车”、没给出行方式时，也不能再按默认的打车排
    assert parse_profile("周末去西湖，别打车", parsed(allow_taxi_fallback=False)).transport == "transit"


@pytest.fixture
def presets(monkeypatch):
    monkeypatch.setattr("app.understanding.hotel_presets", lambda city: [
        {"area": "武林", "name": "杭州西湖武林Pagoda君亭设计酒店", "lat": 30.27, "lon": 120.17, "gcj": {}}])


def test_origin_said_resolves_to_preset_or_place_and_unknown_warns(places, presets):
    from app.prompts import PARSE_SYSTEM
    assert "origin" in PARSE_SYSTEM
    said = understand("从杭州西湖武林Pagoda君亭设计酒店出发", {"city": "hangzhou"},
                      llm=parsed(origin="杭州西湖武林Pagoda君亭设计酒店"), today=WEDNESDAY)
    assert said.profile.hotel.name == "杭州西湖武林Pagoda君亭设计酒店" and sources(said)["hotel"] == "said"
    near = understand("住在灵隐那边", {"city": "hangzhou"}, llm=parsed(origin="灵隐"), today=WEDNESDAY)
    assert near.profile.hotel.name == "灵隐寺" and sources(near)["hotel"] == "inferred"
    assert "灵隐" in next(field.note for field in near.fields if field.key == "hotel")
    unknown = understand("从火星出发", {"city": "hangzhou"}, llm=parsed(origin="火星"), today=WEDNESDAY)
    assert sources(unknown)["hotel"] == "default" and unknown.issues[0].code == "origin_not_found"
    # 问卷里已经选了起点，就不再提示
    form = {"city": "hangzhou", "hotel": {"name": "某酒店", "lat": 30.25, "lon": 120.16}}
    assert not understand("从火星出发", form, llm=parsed(origin="火星"), today=WEDNESDAY).issues


def test_cleared_lists_in_the_form_override_what_was_said(places):
    result = understand("一定要去灵隐寺，想看自然风光", {"city": "hangzhou", "must_visit": [], "interests": []},
                        llm=parsed(must_visit=["灵隐寺"], interests=["自然风光"]), today=WEDNESDAY)
    assert result.profile.must_visit == [] and result.profile.interests == []
    assert sources(result)["interests"] == "form" and "must_visit" not in sources(result)


def test_budget_reads_as_a_trip_total_with_the_daily_average(places):
    result = understand("", {"city": "hangzhou", "days": 2}, llm=parsed(), today=WEDNESDAY)
    budget = next(field for field in result.fields if field.key == "budget")
    assert budget.display.startswith("人均共 600 元（2 天，平均每天 300 元")
    assert budget.note == "没说预算，按每天 300 元 × 2 天估"


def test_unknown_origin_means_open_days(places):
    result = understand("周末一日游", {"city": "hangzhou"}, llm=parsed(days=1), today=WEDNESDAY)
    assert result.profile.origin_known is False
    hotel = next(field for field in result.fields if field.key == "hotel")
    assert hotel.display.startswith("不定") and hotel.source == "default"
    chosen = understand("", {"city": "hangzhou", "hotel": {"name": "某酒店", "lat": 30.25, "lon": 120.16}}, llm=parsed(), today=WEDNESDAY)
    assert chosen.profile.origin_known is True


def test_open_origin_legs_cost_nothing_and_days_end_at_the_last_stop():
    from app.schemas import UserProfile
    from app.travel import Travel
    profile = UserProfile(days=1, origin_known=False)
    route = Travel("estimate", "taxi")
    route.set_day(profile, 1)
    home, sight = (profile.hotel.lat, profile.hotel.lon), (30.2407, 120.1014)
    assert route.leg(*home, *sight) == (0, "未计入", 0.0) and route.leg(*sight, *home)[0] == 0
    assert route.matrix([home, sight])[0][1] == 0
    assert route.leg(*sight, 30.2588, 120.1411)[0] > 0  # 景点之间照常算
    route.set_day(UserProfile(days=1), 1)
    assert route.leg(*home, *sight)[0] > 0  # 定了起点照旧


def test_a_searched_origin_keeps_its_coordinates_in_the_chat(places):
    picked = [{"name": "杭州某某精品酒店", "lat": 30.2612, "lon": 120.1734}]  # 表上用高德搜到的，不在常用起点和景点库里
    result = understand("从杭州某某精品酒店出发", {"city": "hangzhou"}, llm=parsed(origin="杭州某某精品酒店"),
                        today=WEDNESDAY, places=picked)
    assert result.profile.origin_known and result.profile.hotel.lat == 30.2612
    assert sources(result)["hotel"] == "said"
