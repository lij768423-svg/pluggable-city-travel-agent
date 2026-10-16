from collections import OrderedDict

import httpx
import pytest
from fastapi.testclient import TestClient

from app import config, places
from app.api import app
from app.geo import bd09_to_gcj02, gcj02_to_bd09, haversine_km

client = TestClient(app)


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setattr(config, "AMAP_WEB_KEY", "test-secret")
    monkeypatch.setattr(places, "_cache", OrderedDict())
    monkeypatch.setattr(places, "_last_request", 0)
    state = {"data": {"status": "1", "pois": []}, "calls": 0}

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params):
            state["calls"] += 1
            assert params["city"] == "330100" and params["citylimit"] == "true"
            if state.get("fail"):
                raise httpx.ConnectError("https://example.invalid/?key=test-secret")
            return httpx.Response(200, json=state["data"], request=httpx.Request("GET", url))

    monkeypatch.setattr(places.httpx, "AsyncClient", FakeClient)
    return state


def test_coordinate_roundtrip():
    original = (30.255323, 120.164248)
    internal = gcj02_to_bd09(*original)
    assert haversine_km(*original, *internal) > 0.5
    assert haversine_km(*original, *bd09_to_gcj02(*internal)) < 0.001


def test_search_normalizes_filters_and_caches(provider):
    provider["data"]["pois"] = [
        {"id": "metro", "name": "龙翔桥地铁站A口", "location": "120.164248,30.255323", "cityname": "杭州市", "address": "1号线"},
        {"id": "other", "name": "同名酒店", "location": "120.16,30.25", "cityname": "上海市"},
        {"id": "missing", "name": "无坐标", "location": [], "cityname": "杭州市"},
    ]
    response = client.get("/api/places/search", params={"q": "龙翔桥地铁站"})
    assert response.status_code == 200
    results = response.json()["places"]
    assert len(results) == 1 and results[0]["name"] == "龙翔桥地铁站A口"
    assert results[0]["gcj"] == {"lat": 30.255323, "lon": 120.164248}
    assert results[0]["coordinate_system"] == "BD-09"
    assert "test-secret" not in response.text
    assert client.get("/api/places/search", params={"q": "龙翔桥地铁站"}).json() == response.json()
    assert provider["calls"] == 1


@pytest.mark.parametrize("query", ["", "杭", "   ", "杭" * 81])
def test_invalid_search_never_calls_provider(provider, query):
    assert client.get("/api/places/search", params={"q": query}).status_code == 422
    assert provider["calls"] == 0


def test_search_unavailable_without_key():
    response = client.get("/api/places/search", params={"q": "龙翔桥"})
    assert response.status_code == 503
    assert "预设起点" in response.json()["detail"]


@pytest.mark.parametrize("failure", ["network", "denied", "malformed"])
def test_search_failure_does_not_expose_key(provider, failure):
    if failure == "network":
        provider["fail"] = True
    else:
        provider["data"] = {"status": "0", "info": "test-secret"} if failure == "denied" else {"status": "1", "pois": None}
    response = client.get("/api/places/search", params={"q": "龙翔桥"})
    assert response.status_code == 503
    assert "test-secret" not in response.text


@pytest.mark.parametrize("location", ["nan,30", "120,inf", "120", "0,0"])
def test_reject_invalid_coordinates(location):
    assert places.normalize_place({"name": "测试地点", "cityname": "杭州市", "location": location}) is None
