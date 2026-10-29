"""知名度分档（研究计划 3.3；2026-10-25 用户决定：第一次来 / 来过 / 本地人，小众安静反向，首访地标保证，必去优先）。"""
import pytest

from app.nodes.retrieve import needs_landmark, popularity_bonus, score_poi
from app.schemas import POI, Candidate, Selection, UserProfile
from tests.conftest import needs_data


def place(identifier, popularity="", lat=30.25, lon=120.15, **values):
    return POI(id=identifier, name=f"景点{identifier}", type="公园", lat=lat, lon=lon, open_min=480, close_min=1080, price=0,
               min_hours=1, max_hours=2, interests=["人文历史"], popularity=popularity, **values)


def test_bonus_follows_familiarity_and_niche_reverses():
    landmark, known, plain = place(1, "landmark"), place(2, "known"), place(3)
    first, visited, local = (UserProfile(familiarity=level) for level in ("first", "visited", "local"))
    assert popularity_bonus(landmark, first) > popularity_bonus(landmark, visited) > popularity_bonus(landmark, local) > 0
    assert popularity_bonus(landmark, first) > popularity_bonus(known, first) > popularity_bonus(plain, first) == 0
    niche = UserProfile(familiarity="first", interests=["小众安静"])
    assert popularity_bonus(landmark, niche) < popularity_bonus(known, niche) < 0 == popularity_bonus(plain, niche)


def test_default_familiarity_is_first_and_landmark_rule():
    assert UserProfile().familiarity == "first"
    assert needs_landmark(UserProfile(days=2)) and not needs_landmark(UserProfile(days=1))
    assert not needs_landmark(UserProfile(days=3, familiarity="visited")) and not needs_landmark(UserProfile(days=3, familiarity="local"))


@needs_data
def test_score_records_popularity_and_must_visit_still_dominates():
    profile = UserProfile(familiarity="first", interests=["人文历史"])
    plain, landmark = score_poi(place(3), profile, set()), score_poi(place(1, "landmark"), profile, set())
    assert landmark.breakdown["popularity"] == 0.3 and plain.breakdown["popularity"] == 0
    assert landmark.score - plain.score == pytest.approx(0.3, abs=1e-3)
    must_plain = score_poi(place(3), profile, {3})
    assert must_plain.score > landmark.score  # 用户点名的必去始终优先


def test_selection_keeps_one_landmark_for_first_visits(monkeypatch):
    from app.nodes import select
    places = {1: place(1, "landmark"), 2: place(2), 3: place(3), 4: place(4), 5: place(5, "landmark")}
    monkeypatch.setattr(select, "load_pois", lambda: places)
    cands = [Candidate(poi_id=i, score=s, breakdown={"must_visit": float(i == 2)}) for i, s in ((1, 0.9), (2, 1.5), (3, 0.5), (4, 0.4), (5, 0.7))]
    picks = [Selection(poi_id=i, reason="", source="llm") for i in (2, 3, 4)]
    profile = UserProfile(days=2)
    kept, log = select.ensure_landmark(picks, cands, profile)
    assert {s.poi_id for s in kept} == {2, 3, 1} and "替换「景点4」" in log  # 换掉分数最低的非必去，补分数最高的地标
    assert select.ensure_landmark(kept, cands, profile) == (kept, None)  # 已有地标不再动
    assert select.ensure_landmark(picks, cands, UserProfile(days=1)) == (picks, None)  # 一日游不保证
    only_must = [Selection(poi_id=2, reason="", source="rule")]
    kept, _ = select.ensure_landmark(only_must, cands, profile)
    assert [s.poi_id for s in kept] == [2, 1]  # 必去不被替换，地标另外补上


@needs_data
def test_retrieval_puts_a_landmark_into_candidates_even_for_quiet_tastes():
    from app.data import load_pois
    from app.nodes.retrieve import retrieve_with_report
    if not any(poi.popularity == "landmark" for poi in load_pois().values()):
        pytest.skip("本机没有 data/<city>/popularity.json")
    profile = UserProfile(days=2, familiarity="first", interests=["小众安静"])  # 小众安静会压低地标分数
    candidates, report = retrieve_with_report(profile)
    places = load_pois()
    assert report["landmark_guarantee"]["needed"] and any(places[c.poi_id].popularity == "landmark" for c in candidates)
    local, _ = retrieve_with_report(profile.model_copy(update={"familiarity": "local"}))
    assert not _["landmark_guarantee"]["needed"]


def test_parse_reads_familiarity_and_drops_unknown_values():
    from app.llm import MockLLM
    from app.nodes.parse import parse_profile
    assert parse_profile("杭州两天，本地人", MockLLM({"parse": '{"days": 2, "familiarity": "local"}'})).familiarity == "local"
    assert parse_profile("杭州两天", MockLLM({"parse": '{"days": 2, "familiarity": "tourist"}'})).familiarity == "first"
    assert parse_profile("", None, {"familiarity": "visited"}).familiarity == "visited"


@needs_data
def test_candidate_line_and_profile_text_show_popularity():
    from app.nodes.select import _candidate_line, candidate_facts
    from app.prompts import profile_to_text
    facts = candidate_facts(Candidate(poi_id=1, score=1, breakdown={"distance_km": 2}), {1: place(1, "landmark")}, "西湖")
    assert facts["popularity"] == "城市地标" and "知名度:城市地标" in _candidate_line(facts)
    assert "对这座城市：本地人" in profile_to_text(UserProfile(familiarity="local"))


def test_j2_reports_share_of_known_places():
    from app.schemas import DayPlan, Hotel, Item, Itinerary, Restaurant
    from eval.objective_metrics import objective_metrics
    pois = {1: place(1, "landmark"), 2: place(2, "known", lat=30.26), 3: place(3, lat=30.27)}
    restaurants = {10: Restaurant(id=10, name="餐厅", lat=30.252, lon=120.152, price=50, cuisine="杭帮菜", open_min=600, close_min=1260)}
    plan = Itinerary(days=[DayPlan(day=1, items=[Item(kind="poi", ref_id=1, name="景点1", start=570, end=660),
        Item(kind="poi", ref_id=2, name="景点2", start=680, end=770), Item(kind="meal", ref_id=10, name="餐厅", start=780, end=840, meal="lunch"),
        Item(kind="poi", ref_id=3, name="景点3", start=860, end=950)])])
    walk = lambda *c, **k: {"walk": {"m": 600, "min": 8}}  # noqa: E731
    result = objective_metrics(plan, UserProfile(days=1, hotel=Hotel(lat=30.24, lon=120.14)), walk, pois, restaurants)
    assert result["O13_known_share"] == round(2 / 3, 3) and result["O13_landmark_visits"] == 1
