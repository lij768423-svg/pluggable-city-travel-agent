"""C2：城市贯穿画像、规划入口、接口与 MCP；停用城市的历史只读；旧记录按杭州。"""
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app import api
from app.cities import CityDisabled
from app.graph import run_plan
from app.poi_catalog import base_catalog, resolve_catalog
from app.schemas import UserProfile
from tests.test_cities_c1 import synthetic_cities  # noqa: F401 — 复用两座合成城市（编号与杭州重叠）


def test_profile_city_drives_default_origin_and_bounds(synthetic_cities):  # noqa: F811
    profile = UserProfile(city="testcity")
    assert profile.hotel.name == "测试起点" and profile.hotel.lat == 31.9
    with pytest.raises(ValidationError, match="测试城范围内"):
        UserProfile(city="testcity", hotel={"name": "西湖", "lat": 30.25, "lon": 120.17})
    with pytest.raises(ValidationError, match="未知城市"):
        UserProfile(city="missing")
    old = UserProfile.model_validate({"days": 2, "hotel": {"name": "湖滨", "lat": 30.25, "lon": 120.17}})
    assert old.city == "hangzhou"  # C2 之前的记录没有城市


def test_plan_uses_form_city_and_records_it(synthetic_cities, monkeypatch, mock_llm):  # noqa: F811
    monkeypatch.setenv("TTA_CITIES", "hangzhou,testcity")
    out = run_plan("", {"city": "testcity", "days": 1, "budget": 500}, llm=mock_llm, select_mode="rule",
                   travel_source="estimate")
    assert out["profile"].city == "testcity" and out["poi_catalog"]["city"] == "testcity"
    visits = [item for day in out["itinerary"].days for item in day.items if item.kind == "poi"]
    # 编号 0 在杭州是另一处景点；这里必须解析为合成城市自己的地点
    assert visits and all(item.name == "测试景点" for item in visits)
    assert resolve_catalog(out["poi_catalog"]).city == "testcity"


def test_disabled_city_blocks_new_and_edits_but_history_resolves(synthetic_cities, monkeypatch, mock_llm):  # noqa: F811
    monkeypatch.setenv("TTA_CITIES", "hangzhou")
    with pytest.raises(CityDisabled):
        run_plan("", {"city": "testcity", "days": 1}, llm=mock_llm, select_mode="rule", travel_source="estimate")
    with pytest.raises(ValidationError):
        api.PlanRequest(form={"city": "testcity", "days": 1})
    reference = base_catalog("testcity").reference()
    assert resolve_catalog(reference).city == "testcity"  # 历史仍可解析查看
    with pytest.raises(HTTPException) as blocked:
        api.ensure_editable({"poi_catalog": reference})
    assert blocked.value.status_code == 409 and "只能查看" in blocked.value.detail
    api.ensure_editable({"poi_catalog": {}})  # 旧记录没有城市，按杭州，可修改


def test_meta_and_place_search_follow_enabled_cities(synthetic_cities, monkeypatch):  # noqa: F811
    client = TestClient(api.app)
    monkeypatch.setenv("TTA_CITIES", "hangzhou")
    assert client.get("/api/meta", params={"city": "testcity"}).status_code == 409
    assert client.get("/api/meta", params={"city": "missing"}).status_code == 404
    assert client.get("/api/places/search", params={"q": "测试", "city": "testcity"}).status_code == 409
    default = client.get("/api/meta").json()
    assert default["city"] == "hangzhou" and [entry["key"] for entry in default["cities"]] == ["hangzhou"]
    monkeypatch.setenv("TTA_CITIES", "hangzhou,testcity")
    meta = client.get("/api/meta", params={"city": "testcity"}).json()
    assert meta["city"] == "testcity" and meta["hotels"] == []
    assert [entry["key"] for entry in meta["cities"]] == ["hangzhou", "testcity"]
    assert next(entry for entry in meta["cities"] if entry["key"] == "testcity")["brand"]


def test_mcp_prepare_rejects_disabled_or_unknown_city(synthetic_cities, monkeypatch, tmp_path):  # noqa: F811
    from app import accounts
    from app.mcp_domain import MCPError
    from app.mcp_service import MCPService
    from app.sessions import SessionStore
    from tests.test_mcp import OWNER, PROFILE
    monkeypatch.setenv("TTA_CITIES", "hangzhou")
    service = MCPService(store=accounts.AccountStore(tmp_path / "accounts.sqlite3"), sessions=SessionStore(),
                         ledger_path=tmp_path / "requests.sqlite3", site_url="https://test.invalid")
    try:
        for city in ("testcity", "missing"):
            try:
                result = service.prepare(OWNER, {**PROFILE, "city": city}, f"prepare-city-{city}")
            except MCPError as error:
                result = error.payload
            assert result["code"] == "city_unavailable" and result["enabled"] == ["hangzhou"]
    finally:
        service.close()
