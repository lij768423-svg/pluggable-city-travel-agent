"""片区为主：片区索引、分天、校验、修复与选点展示。"""
from app.areas import ADJACENT_KM, build_area_index
from app.schemas import (POI, Candidate, DayPlan, Hotel, Item, Itinerary, Selection, UserProfile, ValidationReport,
                         Violation)
from tests.conftest import needs_data


def place(identifier, lat, lon, name=None, **changes):
    return POI(id=identifier, name=name or f"地点{identifier}", type="公园", lat=lat, lon=lon,
               open_min=480, close_min=1260, price=0, min_hours=1, max_hours=1.5, **changes)


FAR_HOTEL = Hotel(name="远处起点", lat=30.36, lon=120.30)  # 不靠近任何合成片区，避免“起点附近”放宽

# 两组相距约 1 公里的地点，组间约 8 公里；另有一个离第一组约 3.5 公里的相邻小片区
SYNTHETIC = {1: place(1, 30.250, 120.150), 2: place(2, 30.255, 120.155), 3: place(3, 30.252, 120.158),
             4: place(4, 30.300, 120.220), 5: place(5, 30.305, 120.225),
             6: place(6, 30.275, 120.165)}


def test_clusters_are_deterministic_and_named_by_anchor_or_lowest_member():
    index = build_area_index(SYNTHETIC, names=[{"anchor_id": 2, "name": "甲片区"}, {"anchor_id": 1, "name": "冲突名"}])
    again = build_area_index(SYNTHETIC, names=[{"anchor_id": 2, "name": "甲片区"}, {"anchor_id": 1, "name": "冲突名"}])
    assert index == again
    assert index.area_of(1) == index.area_of(2) == index.area_of(3) == "area:1"
    assert index.area_of(4) == index.area_of(5) == "area:4" != index.area_of(1)
    assert index.label("area:1") == "甲片区"            # 同一片区以先出现的命名为准
    assert index.label("area:4") == "地点4一带"         # 没有命名时取编号最小成员
    assert index.adjacent("area:1", index.area_of(6)) and not index.adjacent("area:1", "area:4")


def test_walk_group_is_never_split_across_areas():
    far = {**SYNTHETIC, 7: place(7, 30.250, 120.150, visit_group="堤"), 8: place(8, 30.300, 120.220, visit_group="堤")}
    index = build_area_index(far, names=[])
    assert index.area_of(7) == index.area_of(8)


def test_home_area_is_compatible_with_any_area():
    index = build_area_index(SYNTHETIC, names=[])
    home = index.home_areas(30.301, 120.221)
    assert home == {"area:4"} and ADJACENT_KM >= 1
    assert index.compatible("area:1", "area:4", home) and not index.compatible("area:1", "area:4")


def test_areas_are_not_written_into_place_fields():
    # 原库摘要按景点全字段计算，片区写进字段会让已发布补充目录失效
    assert not {"area_id", "area_label", "area"} & set(POI.model_fields)


def test_validator_flags_scattered_days_but_not_home_or_adjacent(monkeypatch):
    from app.nodes.validate import validate_itinerary
    monkeypatch.setattr("app.nodes.validate.load_pois", lambda: SYNTHETIC)
    profile = UserProfile(days=1, transport="taxi", budget=5000, include_evening=False, hotel=FAR_HOTEL)

    def day(identifiers):
        items = [Item(kind="poi", ref_id=identifier, name=SYNTHETIC[identifier].name, start=540 + 90 * index,
                      end=600 + 90 * index, travel_min=10, travel_mode="打车") for index, identifier in enumerate(identifiers)]
        return Itinerary(days=[DayPlan(day=1, items=items)])

    codes = lambda identifiers: [v.code for v in validate_itinerary(day(identifiers), profile, restaurants={}).violations]  # noqa: E731
    assert "area_scattered" not in codes([1, 2, 6])         # 主片区＋相邻片区
    assert "area_scattered" in codes([1, 4])                # 不相邻
    assert "area_scattered" in codes([1, 6, 2])             # 来回跨区


def test_repair_removes_the_place_far_from_the_main_area(monkeypatch):
    from app.nodes.repair import repair
    monkeypatch.setattr("app.nodes.repair.load_pois", lambda: SYNTHETIC)
    items = [Item(kind="poi", ref_id=identifier, name=SYNTHETIC[identifier].name, start=540 + 90 * index, end=600 + 90 * index)
             for index, identifier in enumerate([1, 2, 4])]
    state = {"profile": UserProfile(days=1, hotel=FAR_HOTEL), "itinerary": Itinerary(days=[DayPlan(day=1, items=items)]), "dropped": [],
             "selected": [Selection(poi_id=identifier, reason="") for identifier in (1, 2, 4)],
             "candidates": [Candidate(poi_id=identifier, score=0.6) for identifier in (1, 2, 4)],
             "report": ValidationReport(passed=True, violations=[Violation(code="area_scattered", severity="soft", message="", day=1)])}
    selected, _, log, changed = repair(state)
    assert changed and [selection.poi_id for selection in selected] == [1, 2] and "地点4" in log[-1]


@needs_data
def test_seed_days_keep_each_area_together():
    from app.areas import area_index
    from app.nodes.schedule import _seed_days
    index = area_index()
    profile = UserProfile(days=2, transport="transit")
    # 钱江新城一带两处 + 南宋御街一带三处：两天各在一个片区，不拆开
    groups = _seed_days([300, 162, 57, 91, 26], profile)
    assert sorted(len({index.area_of(identifier) for identifier in group}) for group in groups) == [1, 1]
    assert {300, 162} in [set(group) for group in groups]


@needs_data
def test_meal_order_variants_rotate_whole_areas():
    from app.areas import area_index
    from app.planning_quality import meal_order_candidates
    index = area_index()
    for candidate in meal_order_candidates([[15, 36, 20, 91]]):
        zones = [index.area_of(identifier) for identifier in candidate[0]]
        assert sum(before != after for before, after in zip(zones, zones[1:])) == len(set(zones)) - 1


@needs_data
def test_candidates_carry_area_and_selection_groups_by_area():
    from app.nodes.retrieve import retrieve_with_report
    from app.nodes.select import candidate_lines
    candidates, report = retrieve_with_report(UserProfile(days=2, transport="transit"))
    assert all(candidate.area_id and candidate.area_label for candidate in candidates)
    assert report["area_coverage"] and all(entry["candidate_count"] >= 1 for entry in report["area_coverage"])
    text = candidate_lines(candidates)
    assert text.startswith("【片区：") and text.count("【片区：") == len({c.area_id for c in candidates})
