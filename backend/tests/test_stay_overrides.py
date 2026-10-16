import json
from pathlib import Path

from app.data import load_pois
from tests.conftest import needs_data

pytestmark = needs_data
ASSETS = Path(__file__).resolve().parents[1] / "app" / "assets"


def test_user_audit_overrides_scheduling_stay():
    pois = load_pois()
    overrides = json.loads((ASSETS / "stay_overrides.json").read_text(encoding="utf-8"))
    assert overrides["policy"] == "manual_only" and overrides["places"]
    for entry in overrides["places"]:
        place = pois[entry["poi_id"]]
        assert (place.min_hours * 60, place.max_hours * 60) == (entry["low_min"], entry["high_min"])
        assert "人工核对" in place.duration_source
    assert (pois[73].min_hours, pois[73].max_hours) == (1, 2)   # 钱王祠：携程 1-2 小时


def test_reviewed_stays_are_kept():
    pois = load_pois()
    overrides = json.loads((ASSETS / "stay_overrides.json").read_text(encoding="utf-8"))
    kept = {entry["poi_id"] for entry in overrides["kept_reviewed"]}
    assert {1, 100, 199} <= kept and not kept & {entry["poi_id"] for entry in overrides["places"]}
    assert (pois[1].min_hours, pois[1].max_hours) == (6, 8) and pois[1].day_anchor   # 宋城仍整天
    assert pois[100].max_hours <= .5 and pois[100].visit_role == "walk_segment"     # 白堤仍只算驻足
