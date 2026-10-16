import csv
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import stay_audit
from app.reasonableness import load_stay_references
from tests.conftest import needs_data

pytestmark = needs_data


@pytest.fixture
def client(tmp_path, monkeypatch):
    from app.data import load_pois

    audit = tmp_path / "top50.csv"
    rows = []
    for identifier, excerpt in [(0, "1-3小时"), (2, "0.5-1天"), (5, "")]:
        row = dict.fromkeys(stay_audit.FIELDS, "")
        row.update({"poi_id": identifier, "name": load_pois()[identifier].name,
                    "数据集区间": "60-180分钟", "LLM区间": "60-180分钟",
                    "来源 URL": f"https://example.org/places/{identifier}" if excerpt else "",
                    "原文摘录": excerpt})
        rows.append(row)
    audit.write_text(stay_audit._rows_text(rows), encoding="utf-8")
    sources = tmp_path / "sources.json"
    sources.write_text(json.dumps([{"poi_id": 0, "source": "携程测试样例"}, {"poi_id": 2, "source": "携程测试样例"}]))
    for name, value in {"AUDIT_PATH": audit, "SOURCES_PATH": sources, "PICKS_PATH": tmp_path / "picks.json", "DECISION_PATH": tmp_path / "decision.json"}.items():
        monkeypatch.setattr(stay_audit, name, value)
    app = FastAPI()
    app.include_router(stay_audit.router)
    return TestClient(app)


def _row(poi_id):
    with stay_audit.AUDIT_PATH.open(encoding="utf-8-sig", newline="") as handle:
        return next(row for row in csv.DictReader(handle) if int(row["poi_id"]) == poi_id)


def _references():
    from app.data import load_pois
    return load_stay_references(stay_audit.REFERENCE_PATH, stay_audit.AUDIT_PATH, load_pois())


def _pick(client, poi_id, **body):
    return client.put(f"/api/stay-audit/picks/{poi_id}", json=body)


def test_state_lists_fixture_places_with_sources(client):
    data = client.get("/api/stay-audit").json()
    assert len(data["items"]) == 3 and data["picks"] == {}
    lingyin = next(item for item in data["items"] if item["id"] == 0)
    assert lingyin["ctrip"] == [60, 180] and lingyin["url"].startswith("https://")


def test_missing_optional_audit_starts_empty(client, tmp_path, monkeypatch):
    directory = tmp_path / "new-audit"
    monkeypatch.setattr(stay_audit, "AUDIT_PATH", directory / "top50.csv")
    monkeypatch.setattr(stay_audit, "DECISION_PATH", directory / "decision.json")
    response = client.get("/api/stay-audit")
    assert response.status_code == 200 and response.json()["items"] == []
    assert client.put("/api/stay-audit/settings", json={"policy": "manual_only", "reviewer": "tester"}).status_code == 200
    assert json.loads((directory / "decision.json").read_text())["policy"] == "manual_only"


def test_sourced_pick_becomes_manual_reference(client):
    response = _pick(client, 0, choice="ctrip", reviewer="李")
    assert response.status_code == 200 and response.json()["counted"] is True
    assert _row(0)["人工结论"] == "60-180分钟" and _row(0)["核对人"] == "李"
    assert _references()[0] == (60, 180, "manual")
    assert json.loads(stay_audit.PICKS_PATH.read_text())["0"]["choice"] == "ctrip"


def test_pick_without_reviewer_or_source_is_recorded_as_pending(client):
    data = client.get("/api/stay-audit").json()
    unsourced = next(item["id"] for item in data["items"] if not item["url"])
    assert _pick(client, 0, choice="llm").json()["counted"] is False
    assert _row(0)["人工结论"] == "待核"
    assert _pick(client, unsourced, choice="dataset", reviewer="李").json()["counted"] is False
    assert _row(unsourced)["人工结论"] == "待核" and _references()[unsourced][2] == "llm_estimate"
    done = _pick(client, unsourced, choice="custom", low=30, high=60, reviewer="李",
                 url="https://example.org/guide", excerpt="建议游玩半小时到一小时")
    assert done.json()["counted"] is True and _references()[unsourced] == (30, 60, "manual")


def test_reviewer_change_backfills_earlier_picks(client):
    _pick(client, 0, choice="llm")
    assert client.put("/api/stay-audit/settings", json={"reviewer": "李", "policy": "manual_only"}).status_code == 200
    assert _row(0)["人工结论"] == "采用LLM" and _row(0)["核对人"] == "李"
    assert json.loads(stay_audit.DECISION_PATH.read_text())["policy"] == "manual_only"


def test_rejects_bad_input_and_cross_site_writes(client):
    data = client.get("/api/stay-audit").json()
    by_day = next(item["id"] for item in data["items"] if item["ctrip_days"])
    assert _pick(client, by_day, choice="ctrip", reviewer="李").status_code == 400
    assert _pick(client, 0, choice="custom", low=90, high=30).status_code == 400
    assert _pick(client, 0, choice="llm", url="ftp://x").status_code == 422
    assert _pick(client, 99999, choice="llm").status_code == 404
    before = _row(0)["人工结论"]
    blocked = client.put("/api/stay-audit/picks/0", json={"choice": "skip"}, headers={"sec-fetch-site": "cross-site"})
    assert blocked.status_code == 403 and _row(0)["人工结论"] == before
