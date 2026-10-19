"""P4：补充记录逐日准入转POI、别名/片区召回、按日期排程与校验、按行程版本展示；全部合成资料，不联网。"""
from __future__ import annotations

from datetime import date

from fastapi.testclient import TestClient
import pytest

from app import api, config
from app.calendar import closed_on
from app.data import base_digest, load_pois
from app.graph import run_plan
from app.matching import match_name, match_place
from app.place_details import describe_place
from app.poi_admission import admitted_poi
from app.poi_catalog import SupplementCatalog, published_catalog
from app.schemas import UserProfile
from tests.test_mcp import OWNER, PROFILE, service  # noqa: F401 — 复用MCP合成夹具
from tests.test_poi_catalog_context import synthetic
from tests.test_poi_supplement import TODAY, record

SUPPLEMENT_ID = 1_000_000


def converted(**changes):
    return admitted_poi(record(internal_id=SUPPLEMENT_ID, **changes), as_of=TODAY)


def profile(start: str | None, **changes) -> UserProfile:
    return UserProfile.model_validate({**PROFILE, "start_date": start, **changes})


@pytest.fixture
def supplement_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POI_CATALOG_MODE", "with_supplement")
    monkeypatch.setattr(config, "POI_SUPPLEMENT_DIR", tmp_path)
    store = SupplementCatalog(tmp_path)

    def publish(*records):
        store.register(list(records))
        return store.publish(as_of=TODAY, base_digest=base_digest())
    return publish


def test_admission_becomes_dated_single_window_poi_with_upper_price():
    poi = converted()
    assert poi.id == SUPPLEMENT_ID and poi.catalog_source == "supplement" and poi.aliases == ["合成园"]
    # 午休分成两段：取一个连续时段，不取9:00–17:00包络
    assert (poi.open_min, poi.close_min) == (780, 1020)
    assert poi.price == 20 and not poi.price_verified and "上界" in poi.review_note
    rules = poi.opening_rules
    assert rules.dates_listed_only and min(rules.open_dates) == TODAY and max(rules.open_dates) == date(2026, 11, 29)
    assert all(day.weekday() != 0 for day in rules.open_dates)  # 周一明确闭园（11-30为周一）
    assert poi.region_label == "下沙"
    assert poi.field_evidence["price"].basis == "manual_review"
    assert poi.field_evidence["duration"].basis == "curator_inference"
    # 坐标取路线入口并只转换一次（GCJ02→BD09）
    from app.geo import gcj02_to_bd09
    assert (poi.lat, poi.lon) == gcj02_to_bd09(30.301, 120.301)


def test_closed_on_uses_only_admitted_dates():
    poi = converted()
    assert closed_on(poi, profile("2026-10-19"), 1)       # 周一
    assert not closed_on(poi, profile("2026-10-20"), 1)   # 周二
    assert closed_on(poi, profile("2026-12-01"), 1)       # 超过已确认有效期
    assert closed_on(poi, profile(None), 1)               # 没有日期不能核对


def test_date_override_outside_chosen_window_is_dropped_and_unknown_weekday_stays_closed():
    weekly = {str(day): [{"start": 540, "end": 720}, {"start": 780, "end": 1020}] for day in range(5)}
    poi = converted(weekly_opening=weekly, date_opening={"2026-10-21": [{"start": 600, "end": 700}]})
    open_dates = set(poi.opening_rules.open_dates)
    # 当天只开10:00–11:40：不把全部日期压缩成这段短时，也不跨午休，单独剔除该日
    assert (poi.open_min, poi.close_min) == (780, 1020)
    assert date(2026, 10, 20) in open_dates and date(2026, 10, 21) not in open_dates
    assert not any(day.weekday() in (5, 6) for day in open_dates)  # 周末时段未知


def test_park_is_open_all_day_every_day_without_trip_date():
    # 用户决定：公园默认每天全天开放，未填日期、准入日期以外或周一都可排程
    poi = converted(place_type="公园")
    assert (poi.open_min, poi.close_min) == (0, 1440)
    assert not poi.opening_rules.dates_listed_only and poi.opening_rules.open_dates == []
    assert not closed_on(poi, profile(None), 1)
    assert not closed_on(poi, profile("2026-10-19"), 1)
    assert not closed_on(poi, profile("2026-12-01"), 1)


def test_unadmitted_records_do_not_become_places():
    expired = {field: {"basis": "manual_review", "origin": "user_survey", "reference": "合成测试资料",
                       "source_date": "2026-09-01", "reviewed_at": "2026-09-01", "valid_from": "2026-09-01",
                       "valid_until": "2026-10-10", "storage_allowed": True}
               for field in ("position", "entrance", "ticket", "opening", "duration")}
    assert converted(evidence=expired) is None
    assert converted(ticket_price_upper=None) is None
    assert converted(state="needs_review") is None


def test_exact_price_and_type_defaults():
    poi = converted(ticket_price_exact=20, place_type="公园", interests=[])
    assert poi.price_verified and poi.review_note == ""
    assert poi.type == "公园" and poi.interests == ["自然风光", "小众安静"]


def test_alias_match_is_exact_and_ambiguity_is_not_guessed():
    base = load_pois()
    assert match_place("灵隐寺", base) == match_name("灵隐寺", {i: p.name for i, p in base.items()})
    one = converted()
    places = {**base, one.id: one}
    assert match_place("合成园", places) == SUPPLEMENT_ID
    other = admitted_poi(record("synthetic:other", internal_id=SUPPLEMENT_ID + 1, name="另一合成公园",
                                experience_group="other"), as_of=TODAY)
    assert match_place("合成园", {**places, other.id: other}) is None


def test_supplement_place_is_planned_only_on_admitted_date(supplement_mode, mock_llm):
    supplement_mode(record())
    assert SUPPLEMENT_ID in published_catalog().pois
    form = {**PROFILE, "must_visit": ["合成园"], "preferred_regions": ["下沙"]}
    tuesday = run_plan(form={**form, "start_date": "2026-10-20"}, llm=mock_llm, travel_source="estimate", use_solver=False)
    items = [item for day in tuesday["itinerary"].days for item in day.items if item.kind == "poi" and item.ref_id == SUPPLEMENT_ID]
    assert len(items) == 1 and 780 <= items[0].start and items[0].end <= 1020
    assert not any(SUPPLEMENT_ID == getattr(v, "poi_id", None) for v in tuesday["report"].hard)
    report = tuesday["retrieval_report"]
    assert [entry["status"] for entry in report["must_visit"]] == ["covered"]
    assert [entry["status"] for entry in report["region_coverage"]] == ["covered"]
    assert any("上界" in text for text in tuesday["report"].unknowns)
    shown = api.serialize_plan(tuesday, {"select_mode": "llm", "guide_mode": "off", "enable_repair": True}, "p", 0)
    detail = next(i["detail"] for d in shown["days"] for i in d["items"] if i["kind"] == "poi" and i["ref_id"] == SUPPLEMENT_ID)
    assert "补充资料" in detail["content_source"] and shown["poi_catalog"]["supplement_digest"]
    monday = run_plan(form={**form, "start_date": "2026-10-19"}, llm=mock_llm, travel_source="estimate", use_solver=False)
    assert SUPPLEMENT_ID not in {item.ref_id for day in monday["itinerary"].days for item in day.items if item.kind == "poi"}
    assert monday["retrieval_report"]["must_visit"][0]["status"] != "covered"


def test_base_only_ignores_published_supplement(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POI_SUPPLEMENT_DIR", tmp_path)
    store = SupplementCatalog(tmp_path)
    store.register([record()])
    store.publish(as_of=TODAY, base_digest=base_digest())
    assert SUPPLEMENT_ID not in load_pois()
    assert all(place.catalog_source == "chinatravel" and not place.aliases for place in load_pois().values())


def test_place_details_resolve_by_plan_catalog():
    a = synthetic("A")
    session = api.SESSIONS.create({"poi_catalog": a.reference()}, {}, None)
    client = TestClient(api.app)
    scoped = client.post("/api/place-details", json={"places": [{"kind": "poi", "id": 1}], "plan_id": session.plan_id}).json()
    plain = client.post("/api/place-details", json={"places": [{"kind": "poi", "id": 1}]}).json()
    expired = client.post("/api/place-details", json={"places": [{"kind": "poi", "id": 1}], "plan_id": "missing-plan"}).json()
    assert scoped["places"][0]["name"].endswith("·A")
    assert plain["places"][0]["name"] == expired["places"][0]["name"] == load_pois()[1].name
    media = client.get(f"/api/place-media?kind=poi&id=1&plan_id={session.plan_id}").json()
    assert media["name"].endswith("·A")


def test_supplement_detail_source_label():
    detail = describe_place(converted(), "poi")
    assert "补充资料" in detail["content_source"] and "ChinaTravel" not in detail["content_source"]
    assert "规划建议" in " ".join(detail["tips"])


def test_mcp_candidates_mark_supplement_dates_and_price_basis(supplement_mode, service):  # noqa: F811
    supplement_mode(record())
    prepared = service.prepare(OWNER, {**PROFILE, "start_date": "2026-10-20", "must_visit": ["合成园"]}, "prepare-supplement")
    assert prepared["status"] == "prepared"
    entry = next(c for c in prepared["candidates"] if c["poi_id"] == SUPPLEMENT_ID)
    assert entry["catalog_source"] == "supplement" and entry["price_basis"] == "upper_bound"
    assert entry["available_dates_only"] and entry["price"] == 20
    assert all("catalog_source" not in c for c in prepared["candidates"] if c["poi_id"] != SUPPLEMENT_ID)
    monday = service.prepare(OWNER, {**PROFILE, "start_date": "2026-10-19", "must_visit": ["合成园"]}, "prepare-monday")
    assert monday["status"] == "needs_changes" and monday["required_gaps"]


def test_review_horizon_is_capped_labelled_and_limits_dates():
    from datetime import timedelta
    from pydantic import ValidationError
    from app.schemas import SupplementEvidence

    def evidence(until, basis="review_horizon", **changes):
        data = {"basis": "manual_review", "origin": "user_survey", "authority": "official", "reference": "合成测试资料",
                "source_date": "2026-10-18", "reviewed_at": "2026-10-18", "valid_from": "2026-10-18",
                "valid_until": until, "validity_basis": basis, "storage_allowed": True}
        return {**data, **changes}

    assert SupplementEvidence.model_validate(evidence("2027-01-16")).validity_basis == "review_horizon"  # 第90天
    with pytest.raises(ValidationError, match="90天"):
        SupplementEvidence.model_validate(evidence("2027-01-17"))
    with pytest.raises(ValidationError, match="核对日"):
        SupplementEvidence.model_validate(evidence("2027-01-10", reviewed_at=None))
    assert SupplementEvidence.model_validate(evidence("2027-06-30", basis="source")).validity_basis == "source"
    base = record().evidence
    changed = {**{k: v.model_dump(mode="json") for k, v in base.items()},
               "ticket": evidence("2027-01-16"), "opening": evidence("2027-01-16")}
    poi = converted(evidence={**changed, **{f: {**changed[f], "valid_until": "2027-06-30"} for f in ("position", "entrance", "duration")}})
    assert "复核期限使用至2027-01-16" in poi.review_note and "上界" in poi.review_note
    assert max(poi.opening_rules.open_dates) <= date(2026, 10, 18) + timedelta(days=90)


def test_inferred_free_all_day_is_never_shown_as_verified():
    data = {k: v.model_dump(mode="json") for k, v in record().evidence.items()}
    for field in ("ticket", "opening"):
        data[field] |= {"authority": "official", "inferred": True}
    poi = converted(evidence=data, ticket_price_upper=0, ticket_price_exact=0,
                    weekly_opening={str(day): [{"start": 0, "end": 1440}] for day in range(7)})
    assert poi.price == 0 and not poi.price_verified
    assert (poi.open_min, poi.close_min) == (0, 1440)
    assert "门票、开放时段依据规则推断" in poi.review_note and "上界" not in poi.review_note
    assert poi.field_evidence["price"].basis == poi.field_evidence["opening_hours"].basis == "curator_inference"
    from app.mcp_domain import public_candidates
    from app.poi_catalog import PlanningCatalog, use_catalog
    from app.schemas import Candidate
    with use_catalog(PlanningCatalog.build({poi.id: poi}, "synthetic-base-inferred", "synthetic-supplement-inferred")):
        entry = public_candidates([Candidate(poi_id=poi.id, score=1.0)])[0]
    assert entry["price_basis"] == "inferred"
