"""住哪儿卡片（2026-10-30 用户决定“先只做卡片”）：起点不定的多日行程按每天往返路程推荐住的片区；同日加按推荐住处重排与详情页“在高德打开”（用户：“那就不要大众点评其他默认”）。

附近住处按高德类型分档列出，不写房价：高德住宿 POI 不给价格，住宿表的价格年份与入住日期都不明。
"""
import pytest
from fastapi.testclient import TestClient

from app.data import load_pois
from app.geo import haversine_km
from app.graph import run_plan
from app.llm import MockLLM
from app.lodging import LONG_COMMUTE_MIN, MAX_REPLANS, SIMILAR_MIN, replan_with_hotel, suggest_lodging, wanted
from app.place_details import amap_url, describe_place
from app.presentation import lodging_view, serialize_plan
from app.replan import ReplanError
from app.schemas import DayPlan, Item, Itinerary, UserProfile
from app.travel import Travel

from .test_amap import fake_router


def by_name(name: str):
    return next(place for place in load_pois().values() if place.name == name)


def day(number: int, *names: str) -> DayPlan:
    return DayPlan(day=number, items=[Item(kind="poi", ref_id=by_name(name).id, name=name, start=540 + 150 * index, end=660 + 150 * index)
                                      for index, name in enumerate(names)])


def trip(*days: DayPlan, **profile) -> dict:
    return {"profile": UserProfile(**{"days": len(days), "origin_known": False, **profile}),
            "itinerary": Itinerary(days=list(days)), "restaurants": {}}


class Route:
    def __init__(self, router):
        self.router = router

    def _can_go_live(self, restaurant=False):
        return True


def hotel(name, kind, rating, area="西溪", offset=0.0):
    def place(params):
        lon, lat = map(float, params["location"].split(","))
        return {"id": "B0" + name.encode().hex()[:8], "name": name, "type": kind, "cityname": "杭州市", "adname": "西湖区",
                "address": "某路 1 号", "location": f"{lon + offset:.6f},{lat:.6f}",
                "business": {"rating": rating, "business_area": area}}
    return place


HOTELS = [hotel("甲经济酒店", "住宿服务;宾馆酒店;经济型连锁酒店", "4.5"), hotel("乙经济酒店", "住宿服务;宾馆酒店;经济型连锁酒店", "4.7", offset=0.002),
          hotel("丙经济酒店", "住宿服务;宾馆酒店;经济型连锁酒店", "4.1"), hotel("丁大酒店", "住宿服务;宾馆酒店;四星级宾馆", "4.4", area="文新"),
          hotel("戊大饭店", "住宿服务;宾馆酒店;四星级宾馆|餐饮服务;中餐厅;中餐厅", "4.6"),
          hotel("小院民宿", "住宿服务;住宿服务相关;住宿服务相关", "4.9"), hotel("某宾馆", "住宿服务;宾馆酒店;宾馆酒店", []),
          hotel("隔壁面馆", "餐饮服务;中餐厅;中餐厅", "4.8"),
          hotel("某安缦·蒸菜馆", "住宿服务;宾馆酒店;奢华酒店|餐饮服务;中餐厅;中餐厅", "4.9")]  # 酒店里的餐厅


def around(places):
    def handler(kind, params):
        assert kind == "around" and params["types"] == "100000"
        return {"status": "1", "pois": [place(params) for place in places]}
    return handler


def test_only_visitors_without_a_fixed_start_on_multi_day_trips_get_a_suggestion():
    assert wanted(UserProfile(days=2, origin_known=False))
    assert not wanted(UserProfile(days=2, origin_known=True))  # 已经定了住处
    assert not wanted(UserProfile(days=1, origin_known=False))  # 一日游不用住
    assert not wanted(UserProfile(days=3, origin_known=False, familiarity="local"))  # 本地人回家住
    assert suggest_lodging(trip(day(1, "西溪国家湿地公园"), day(2, "西溪梅墅"), origin_known=True)) is None


def test_the_spot_follows_where_the_days_are_and_beats_the_city_centre():
    # 两天都在西溪一带，默认起点湖滨离西溪约 10 公里
    lodging = suggest_lodging(trip(day(1, "西溪国家湿地公园", "西溪梅墅"), day(2, "西溪天堂商业街", "西溪空中揽胜氦气球")))
    wetland = by_name("西溪国家湿地公园")
    assert haversine_km(lodging["lat"], lodging["lon"], wetland.lat, wetland.lon) < 4
    assert lodging["saving_minutes"] >= SIMILAR_MIN and not lodging["similar"]
    assert lodging["default_name"] == "西湖湖滨"
    assert [entry["day"] for entry in lodging["daily"]] == [1, 2]
    # 没有高德（估算模式、离线评测）时照样给位置，只是不列店名
    assert lodging["search_status"] == "offline" and lodging["hotels"] == [] and lodging["area"]


def test_days_far_from_any_single_spot_are_flagged():
    # 良渚在北、湘湖在南，住哪儿都有一天要来回一个多小时
    lodging = suggest_lodging(trip(day(1, "良渚古城遗址公园", "良渚博物院"), day(2, "湘湖")))
    assert lodging["long_days"] and all(entry["minutes"] > LONG_COMMUTE_MIN for entry in lodging["daily"] if entry["day"] in lodging["long_days"])


def test_nearby_hotels_are_grouped_by_tier_and_carry_no_price(tmp_path):
    router = fake_router(tmp_path, around(HOTELS))
    lodging = suggest_lodging(trip(day(1, "西溪国家湿地公园"), day(2, "西溪梅墅")), Route(router))
    assert len(router.log) == 1  # 只查一次
    assert lodging["search_status"] == "ok" and lodging["area"] == "西溪" and lodging["area_source"] == "amap_business_area"
    assert [(item["tier"], item["name"]) for item in lodging["hotels"]] == [
        ("经济型", "乙经济酒店"), ("经济型", "甲经济酒店"), ("四星", "戊大饭店"), ("四星", "丁大酒店"),
        ("民宿/青旅", "小院民宿"), ("其他", "某宾馆")]
    assert all("price" not in item and "cost" not in item for item in lodging["hotels"])
    view = lodging_view(lodging)
    assert "lat" not in view and view["gcj"]["lat"] and view["search_label"] == "在高德地图搜「西溪 酒店」"
    assert view["hotels"][0]["link"].startswith("https://www.amap.com/place/B0") and "lat" not in view["hotels"][0]


def test_amap_failure_keeps_the_suggestion_without_hotels(tmp_path):
    router = fake_router(tmp_path, lambda kind, params: {"status": "0", "infocode": "10001", "info": "INVALID_USER_KEY"})
    lodging = suggest_lodging(trip(day(1, "西溪国家湿地公园"), day(2, "西溪梅墅")), Route(router))
    assert lodging["search_status"] == "unavailable" and lodging["hotels"] == []
    assert lodging["area"] and lodging["area_source"] == "area_index"
    view = lodging_view(lodging)  # 没有商圈名：按位置打开，不在全城搜“酒店”
    assert view["search_link"].startswith("https://uri.amap.com/marker?position=") and view["gcj"]["lon"] > 120


def test_plans_carry_the_card_only_when_the_start_is_open():
    def plan(**form):
        result = run_plan("", {"city": "hangzhou", "days": 2, "pace": "normal", "transport": "taxi", **form},
                          llm=MockLLM({}), select_mode="rule", travel_source="estimate")
        return serialize_plan(result, {"select_mode": "rule", "enable_repair": True, "guide_mode": "off"})
    assert plan(origin_known=False)["lodging"]["daily"]
    assert "lodging" not in plan()


def planned() -> dict:
    form = {"city": "hangzhou", "days": 2, "pace": "normal", "transport": "taxi", "origin_known": False}
    return run_plan("", form, llm=MockLLM({}), select_mode="rule", travel_source="estimate")


def offer(result: dict) -> dict:
    """离线规划不查高德、卡片里没有店：放一家假的推荐住处（BD-09，西溪一带）。"""
    wetland = by_name("西溪国家湿地公园")
    result["lodging"]["hotels"] = [{"name": "某西溪酒店", "tier": "四星", "amap_type": "四星级宾馆", "rating": 4.6, "address": "",
                                    "external_id": "B0TEST0001", "lat": wetland.lat + 0.003, "lon": wetland.lon, "distance_m": 300}]
    return result


def poi_ids(result: dict) -> list[int]:
    return [item.ref_id for day in result["itinerary"].days for item in day.items if item.kind == "poi" and not item.evening]


def test_replanning_from_a_recommended_hotel_keeps_the_places_and_counts_the_ride_home():
    result = offer(planned())
    updated = replan_with_hotel(result, "B0TEST0001", route=Travel("estimate", "taxi"))
    # 景点沿用原行程：不新增；不跑修复，排程放不进的列为未排入
    assert set(poi_ids(updated)) <= set(poi_ids(result)) <= set(poi_ids(updated)) | set(updated["dropped"])
    assert updated["profile"].origin_known and updated["profile"].hotel.name == "某西溪酒店"
    assert any(day.return_travel_min > 0 for day in updated["itinerary"].days)  # 回住处的路程算进行程
    assert updated["operation"] == "lodging_replan" and updated["usage"]["calls"] == 0
    assert updated["lodging"]["chosen"]["external_id"] == "B0TEST0001" and "未调用大模型" in updated["log"][0]
    with pytest.raises(ReplanError):
        replan_with_hotel(result, "B0NOTHERE", route=Travel("estimate", "taxi"))


def test_api_replans_at_most_twice_and_undo_does_not_refund(monkeypatch):
    import app.api as api
    import app.lodging as lodging
    real_plan, real_travel = api.run_plan, lodging.Travel
    monkeypatch.setattr(api, "run_plan", lambda *a, **k: real_plan(*a, **{**k, "llm": MockLLM({}), "travel_source": "estimate"}))
    monkeypatch.setattr(lodging, "Travel", lambda source, scenario, **options: real_travel("estimate", scenario))
    client = TestClient(api.app)
    form = {"city": "hangzhou", "days": 2, "pace": "normal", "transport": "taxi", "origin_known": False}
    plan = client.post("/api/plan", json={"query": "", "form": form, "select_mode": "rule"}).json()
    assert plan["lodging"]["replans_left"] == MAX_REPLANS
    offer(api.SESSIONS.get(plan["plan_id"]).result())
    url = f"/api/plans/{plan['plan_id']}"
    assert client.post(f"{url}/lodging-replan", json={"revision": 0, "hotel_id": "B0NOTHERE"}).status_code == 404
    first = client.post(f"{url}/lodging-replan", json={"revision": 0, "hotel_id": "B0TEST0001"})
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["revision"] == 1 and body["profile"]["hotel"]["name"] == "某西溪酒店" and body["meta"]["operation"] == "lodging_replan"
    assert body["lodging"]["chosen"]["name"] == "某西溪酒店" and body["lodging"]["replans_left"] == MAX_REPLANS - 1
    assert client.post(f"{url}/lodging-replan", json={"revision": 0, "hotel_id": "B0TEST0001"}).status_code == 409
    undone = client.post(f"{url}/revert", json={"revision": 0, "base_revision": 1}).json()
    assert undone["profile"]["origin_known"] is False and undone["lodging"].get("chosen") is None
    assert client.post(f"{url}/lodging-replan", json={"revision": 2, "hotel_id": "B0TEST0001"}).status_code == 200
    assert client.post(f"{url}/lodging-replan", json={"revision": 3, "hotel_id": "B0TEST0001"}).status_code == 429
    api.SESSIONS.get(plan["plan_id"]).request.update({"source": "mcp", "lodging_replans": 0})
    assert client.post(f"{url}/lodging-replan", json={"revision": 3, "hotel_id": "B0TEST0001"}).status_code == 409  # MCP 行程不从网页改


def test_place_details_open_in_amap():
    detail = describe_place(by_name("灵隐寺"), "poi")
    assert detail["amap_url"].startswith("https://uri.amap.com/search?keyword=") and "&city=" in detail["amap_url"]
    assert amap_url("某店", "B0ABC12345") == "https://www.amap.com/place/B0ABC12345"
