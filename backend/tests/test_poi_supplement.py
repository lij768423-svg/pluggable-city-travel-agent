"""补充目录用合成地点验证，不调用地图或模型接口。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json

import pytest
from pydantic import ValidationError

from app.schemas import SupplementRecord
from app.poi_admission import admit_supplement, publication_issues
from app.poi_catalog import SupplementCatalog, normalize_alias


TODAY = date(2026, 10, 18)


def record(key="synthetic:park", **changes):
    evidence = {
        field: {
            "basis": "manual_review" if field != "duration" else "project_recommendation",
            "origin": "user_survey" if field != "duration" else "project_planning",
            "reference": "合成测试资料，不是真实地点",
            "source_date": "2026-10-18", "reviewed_at": "2026-10-18",
            "valid_from": "2026-10-01", "valid_until": "2026-11-30",
            "storage_allowed": True,
        }
        for field in ("position", "entrance", "ticket", "opening", "duration")
    }
    data = dict(
        canonical_key=key, name="合成公园", district="钱塘区",
        area="下沙", aliases=["合成园"], state="published",
        position={"lat": 30.30, "lon": 120.30, "crs": "GCJ02"},
        entrance={"lat": 30.301, "lon": 120.301, "crs": "GCJ02"},
        ticket_price_upper=20, ticket_price_exact=None,
        weekly_opening={str(day): [{"start": 540, "end": 720}, {"start": 780, "end": 1020}]
                        for day in range(7)},
        duration_min_hours=1, duration_max_hours=2,
        duration_scope="一次园内步行游览，项目建议",
        evidence=evidence,
    )
    data["weekly_opening"]["0"] = []
    data.update(changes)
    return SupplementRecord.model_validate(data)


def test_admission_preserves_lunch_closure_and_weekday():
    r = record()
    result = admit_supplement(r, [date(2026, 10, 19), date(2026, 10, 20)], as_of=TODAY)
    assert result.allowed_dates == [date(2026, 10, 20)]
    assert [(x.start, x.end) for x in result.intervals["2026-10-20"]] == [(540, 720), (780, 1020)]
    assert "2026-10-19" in result.rejected_dates
    assert not admit_supplement(r, [], as_of=TODAY).admitted


@pytest.mark.parametrize("field", ["position", "entrance", "ticket_price_upper",
                                  "duration_min_hours", "duration_max_hours"])
def test_unknown_never_becomes_free_or_all_day(field):
    r = record(**{field: None})
    assert publication_issues(r, as_of=TODAY)
    assert not admit_supplement(r, [date(2026, 10, 20)], as_of=TODAY).admitted


def test_unknown_hours_expired_future_and_model_facts():
    assert not admit_supplement(record(weekly_opening={}), [TODAY], as_of=TODAY).admitted
    assert not admit_supplement(record(), [date(2026, 12, 1)], as_of=TODAY).admitted
    r = record()
    r.evidence["opening"].reviewed_at = date(2026, 10, 21)
    assert publication_issues(r, as_of=TODAY)
    r = record()
    r.evidence["ticket"].basis = "model_estimate"
    assert publication_issues(r, as_of=TODAY)
    r = record()
    r.evidence["ticket"].valid_until = None
    assert not admit_supplement(r, [TODAY], as_of=TODAY).admitted


def test_provider_storage_facility_and_unsupported_crs_rejected(tmp_path):
    r = record()
    r.evidence["position"].origin = "amap"
    with pytest.raises(ValueError, match="持久"):
        SupplementCatalog(tmp_path).register([r])
    assert not publication_issues(record(), as_of=TODAY)
    assert publication_issues(record(kind="facility"), as_of=TODAY)
    assert publication_issues(record(entrance={"lat": 30.3, "lon": 120.3, "crs": "WGS84"}), as_of=TODAY)
    with pytest.raises(ValidationError):
        record(ticket_price_upper=float("nan"))
    with pytest.raises(ValidationError):
        record(ticket_price_upper=10, ticket_price_exact=20)
    with pytest.raises(ValidationError):
        record(weekly_opening={"1": [{"start": 720, "end": 540}]})


def test_registry_stable_concurrent_and_transactional(tmp_path):
    catalog = SupplementCatalog(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda _: catalog.register([record()])[0].internal_id, range(8)))
    assert set(ids) == {1_000_000}
    assert catalog.register([record("synthetic:other", name="另一合成公园", aliases=[])])[0].internal_id == 1_000_001
    bad = record("synthetic:bad")
    bad.evidence["ticket"].storage_allowed = False
    with pytest.raises(ValueError):
        catalog.register([record("synthetic:rollback"), bad])
    assert "synthetic:rollback" not in {r.canonical_key for r in catalog.records()}
    with pytest.raises(ValueError):
        catalog.register([record(internal_id=42)])
    with pytest.raises(ValueError):
        catalog.register([record(), record()])


def test_publish_is_atomic_immutable_and_archive_only_affects_next_snapshot(tmp_path, monkeypatch):
    catalog = SupplementCatalog(tmp_path)
    registered = catalog.register([record()])
    first = catalog.publish(as_of=TODAY, base_digest="synthetic-base")
    assert first.records()[0].internal_id == registered[0].internal_id
    assert first.aliases()[normalize_alias("合成园")] == (1_000_000,)
    mutated = first.records()[0]
    mutated.name = "外部修改"
    assert first.records()[0].name == "合成公园"
    r = record(name="新版本公园")
    catalog.register([r])
    import app.poi_catalog as module
    original = module.os.replace
    def fail_manifest(src, dst):
        if dst.name == "manifest.json":
            raise OSError("模拟断电")
        return original(src, dst)
    monkeypatch.setattr(module.os, "replace", fail_manifest)
    with pytest.raises(OSError):
        catalog.publish(as_of=TODAY, base_digest="synthetic-base")
    assert catalog.load().digest == first.digest
    monkeypatch.setattr(module.os, "replace", original)
    second = catalog.publish(as_of=TODAY, base_digest="synthetic-base")
    assert second.digest != first.digest
    assert catalog.load(first.digest).records()[0].name == "合成公园"
    catalog.register([record(state="archived")])
    assert not catalog.publish(as_of=TODAY, base_digest="synthetic-base").records()
    assert first.records()[0].name == "合成公园"


def test_invalid_publish_keeps_pointer_and_checks_digest(tmp_path):
    catalog = SupplementCatalog(tmp_path)
    catalog.register([record()])
    first = catalog.publish(as_of=TODAY, base_digest="synthetic-base")
    catalog.register([record(ticket_price_upper=None)])
    with pytest.raises(ValueError):
        catalog.publish(as_of=TODAY, base_digest="synthetic-base")
    assert catalog.load().digest == first.digest
    version = tmp_path / "versions" / f"{first.digest}.json"
    version.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="摘要"):
        catalog.load()
    with pytest.raises(ValueError):
        catalog.load("../manifest")


def test_alias_collision_is_ambiguous_and_experience_group_not_duplicated(tmp_path):
    catalog = SupplementCatalog(tmp_path)
    catalog.register([record(), record("synthetic:other", name="其他公园")])
    snap = catalog.publish(as_of=TODAY, base_digest="synthetic-base")
    assert len(snap.aliases()[normalize_alias("合成园")]) == 2
    r = record("synthetic:beach", name="公园沙滩", experience_group="synthetic:park")
    catalog.register([r])
    with pytest.raises(ValueError, match="游览组"):
        catalog.publish(as_of=TODAY, base_digest="synthetic-base")


def test_admission_returns_typed_known_values_and_converts_once():
    from app.geo import gcj02_to_bd09
    r = record()
    result = admit_supplement(r, [date(2026, 10, 20)], as_of=TODAY)
    assert result.ticket_price_upper == 20
    assert result.ticket_price_exact is None
    assert result.duration_min_hours == 1
    assert (result.entrance.lat, result.entrance.lon) == gcj02_to_bd09(30.301, 120.301)
    assert result.entrance.crs == "BD09"
    r.entrance = result.entrance
    second = admit_supplement(r, [date(2026, 10, 20)], as_of=TODAY)
    assert second.entrance == result.entrance


def test_missing_weekday_is_unknown_and_explicit_override_is_honored():
    r = record(weekly_opening={})
    result = admit_supplement(r, [date(2026, 10, 20)], as_of=TODAY)
    assert not result.admitted
    r = record(weekly_opening={1: [{"start": 540, "end": 720}]})
    result = admit_supplement(r, [date(2026, 10, 21)], as_of=TODAY)
    assert "未知" in " ".join(result.rejected_dates["2026-10-21"])
    r.date_opening = {"2026-10-19": []}
    assert not admit_supplement(r, [date(2026, 10, 19)], as_of=TODAY).admitted
    r.date_opening = {"2026-10-19": [r.weekly_opening[1][0]]}
    assert admit_supplement(r, [date(2026, 10, 19)], as_of=TODAY).admitted
    r.closed_dates = [date(2026, 10, 19)]
    assert not admit_supplement(r, [date(2026, 10, 19)], as_of=TODAY).admitted


def test_project_recommendation_is_not_independent_fact():
    r = record()
    r.evidence["ticket"].origin = "project_planning"
    assert publication_issues(r, as_of=TODAY)
    r = record(position={"lat": 10, "lon": 10, "crs": "BD09"})
    assert publication_issues(r, as_of=TODAY)


def test_distinct_concurrent_ids_remain_unique_and_not_reused(tmp_path):
    catalog = SupplementCatalog(tmp_path)
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = list(pool.map(lambda i: catalog.register([record(
            f"synthetic:park-{i}", name=f"合成公园{i}", aliases=[]
        )])[0].internal_id, range(12)))
    assert sorted(ids) == list(range(1_000_000, 1_000_012))
    catalog.register([record("synthetic:park-0", state="archived")])
    assert catalog.register([record("synthetic:new")])[0].internal_id == 1_000_012


def test_review_cli_is_offline_dry_run_and_has_all_districts(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    root = tmp_path / "absent"
    run = subprocess.run(
        [sys.executable, "scripts/review_supplement_pois.py", "--root", str(root),
         "--as-of", "2026-10-18", "--date", "2026-10-20"],
        cwd=backend, capture_output=True, text=True, check=True,
    )
    report = json.loads(run.stdout)
    assert report["mode"] == "dry_run"
    assert len({r["district"] for r in report["region_queue"]}) == 13
    assert report["publication_ready"] == 5  # 下沙两批5处公园的依据均经用户确认
    assert not report["catalog_connected_to_planning"]
    assert not root.exists()
    applied = subprocess.run(
        [sys.executable, "scripts/review_supplement_pois.py", "--root", str(root),
         "--as-of", "2026-10-18", "--apply"], cwd=backend,
        capture_output=True, text=True, check=True,
    )
    assert len(json.loads(applied.stdout)["records"]) == 5
    assert not (root / "manifest.json").exists()
    denied = subprocess.run(
        [sys.executable, "scripts/review_supplement_pois.py", "--root", str(root), "--publish"],
        cwd=backend, capture_output=True, text=True,
    )
    assert denied.returncode == 2
    assert not (root / "manifest.json").exists()


def test_payload_validation_happens_before_manifest_swap(tmp_path, monkeypatch):
    import app.poi_catalog as module
    catalog = SupplementCatalog(tmp_path)
    catalog.register([record()])
    first = catalog.publish(as_of=TODAY, base_digest="synthetic-base")
    catalog.register([record(name="待发布的新名")])
    def reject_payload(**kwargs):
        raise ValueError("模拟目录schema校验拒绝")
    with monkeypatch.context() as patch:
        patch.setattr(module, "CatalogSnapshot", reject_payload)
        with pytest.raises(ValueError):
            catalog.publish(as_of=TODAY, base_digest="synthetic-base")
    assert catalog.load().digest == first.digest


def test_seed_reimport_preserves_reviewed_fields_and_publish_requires_input(tmp_path):
    import subprocess
    import sys
    from pathlib import Path
    backend = Path(__file__).resolve().parents[1]
    catalog = SupplementCatalog(tmp_path)
    reviewed = record("hz:qiantang:jinshahu-park", name="金沙湖公园", district="钱塘区")
    original_id = catalog.register([reviewed])[0].internal_id
    run = subprocess.run(
        [sys.executable, "scripts/review_supplement_pois.py", "--root", str(tmp_path),
         "--as-of", "2026-10-18", "--apply"], cwd=backend,
        capture_output=True, text=True, check=True,
    )
    saved = next(r for r in catalog.records() if r.canonical_key == reviewed.canonical_key)
    assert saved.internal_id == original_id
    assert saved.ticket_price_upper == 20
    assert saved.state == "published"
    assert saved.entrance == reviewed.entrance
    denied = subprocess.run(
        [sys.executable, "scripts/review_supplement_pois.py", "--root", str(tmp_path),
         "--as-of", "2026-10-18", "--apply", "--publish"],
        cwd=backend, capture_output=True, text=True,
    )
    assert denied.returncode == 2
    assert not (tmp_path / "manifest.json").exists()


@pytest.mark.parametrize("upper", [0, 20])
def test_zero_ticket_requires_explicit_official_confirmation(upper):
    r = record(ticket_price_upper=upper, ticket_price_exact=0)
    assert publication_issues(r, as_of=TODAY)
    r.evidence["ticket"].authority = "official"
    assert not publication_issues(r, as_of=TODAY)
    assert admit_supplement(r, [date(2026, 10, 20)], as_of=TODAY).admitted
