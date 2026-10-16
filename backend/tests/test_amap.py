import pytest

from app.amap import AmapError, AmapRouter, pt
from app.nodes.validate import validate_itinerary
from app.schemas import DayPlan, Item, Itinerary, UserProfile
from app.travel import Travel, calibrated


class FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def fake_router(tmp_path, handler):
    r = AmapRouter(key="test", qps=1000, cache_path=tmp_path / "c.jsonl")
    r.log = []

    def get(url, params):
        r.log.append((url.rsplit("/", 1)[-1], params))
        return FakeResp(handler(url.rsplit("/", 1)[-1], params))

    r._client.get = get
    return r


def distance_handler(walk_m=5600, drive=(5600, 1260)):
    def h(kind, params):
        if kind == "distance":
            n = len(params["origins"].split("|"))
            if params["type"] == 3:
                return {"status": "1", "results": [{"distance": str(walk_m), "duration": "9999"}] * n}
            return {"status": "1", "results": [{"distance": str(drive[0]), "duration": str(drive[1])}] * n}
        if kind == "integrated":
            return {"status": "1", "route": {"transits": [{"duration": "3180", "cost": "2", "walking_distance": "1"},
                                                          {"duration": "2760", "cost": "3", "walking_distance": "1"}]}}
        raise AssertionError(kind)
    return h


A, B = (30.2369, 120.1554), (30.2021, 120.1391)  # 约 4 km：直线已超 1.5 km，不查步行
NEAR = (30.2400, 120.1554)  # 约 0.35 km


def test_far_leg_skips_walk_and_uses_drive_or_transit(tmp_path):
    r = fake_router(tmp_path, distance_handler())
    taxi = Travel("amap", "taxi", router=r, live_budget=None)
    m, mode, cost = taxi.leg(*A, *B)
    assert (m, mode) == (26, "打车") and cost > 0  # 1260 s = 21 分钟 + 5 分钟叫车；费用来自标定模型
    assert [k for k, _ in r.log] == ["distance"]  # 只查了一次驾车
    tr = Travel("amap", "transit", router=r, live_budget=None)
    assert tr.leg(*A, *B) == (46, "公交/地铁", 3.0)  # 取最快方案


def test_near_leg_uses_real_walk_distance(tmp_path):
    r = fake_router(tmp_path, distance_handler(walk_m=900))
    t = Travel("amap", "taxi", router=r, live_budget=None)
    assert t.leg(*A, *NEAR) == (12, "步行", 0.0)  # 900 m / 75 m/min


def test_cache_persists_and_offline_falls_back(tmp_path):
    r = fake_router(tmp_path, distance_handler())
    Travel("amap", "taxi", router=r, live_budget=None).leg(*A, *B)
    r2 = AmapRouter(key="test", cache_path=tmp_path / "c.jsonl")
    t2 = Travel("amap", "taxi", router=r2, live_budget=None)
    assert t2.leg(*A, *B)[0] == 26 and r2.calls == 0
    # 未缓存 + 只读缓存 → 兜底模型，计入试探计数而不是定稿计数
    t3 = Travel("amap", "transit", router=r2, live_budget=None)
    assert t3.leg(*A, *B, live=False) == calibrated(*A, *B, "transit")
    assert t3.approx_planning == 1 and t3.approx_legs == 0


def test_budget_exhausted_uses_calibrated_and_counts(tmp_path):
    r = fake_router(tmp_path, distance_handler())
    t = Travel("amap", "taxi", router=r, live_budget=0)
    assert t.leg(*A, *B) == calibrated(*A, *B, "taxi")
    assert t.approx_legs == 1 and r.calls == 0


def test_matrix_prefetch_batches_by_destination(tmp_path):
    r = fake_router(tmp_path, distance_handler())
    t = Travel("amap", "taxi", router=r, live_budget=None)
    pts = [A, B, (30.25, 120.17)]
    M = t.matrix(pts)
    assert M[0][0] == 0 and M[0][1] == 26
    # 3 个终点 × 1 次驾车批量（三点两两直线均 > 1.5 km，不查步行）
    assert sum(1 for k, p in r.log if k == "distance") == 3


def test_api_error_falls_back(tmp_path):
    r = fake_router(tmp_path, lambda k, p: {"status": "0", "info": "INVALID_USER_KEY", "infocode": "10001"})
    t = Travel("amap", "taxi", router=r, live_budget=None)
    assert t.leg(*A, *B) == calibrated(*A, *B, "taxi") and "10001" in t.errors[0]
    with pytest.raises(AmapError):
        r.drive(pt(*A), pt(*B))


def test_validator_accepts_independent_travel_fn():
    from app.data import load_pois
    pois = load_pois()
    a = next(p for p in pois.values() if p.name == "雷峰塔")
    b = next(p for p in pois.values() if p.name == "六和塔")
    prof = UserProfile(days=1, budget=5000)
    it = Itinerary(days=[DayPlan(day=1, items=[
        Item(kind="poi", ref_id=a.id, name=a.name, start=9 * 60 + 30, end=10 * 60 + 30),
        Item(kind="poi", ref_id=b.id, name=b.name, start=10 * 60 + 55, end=11 * 60 + 55),
    ])])
    assert "travel_time" not in {v.code for v in validate_itinerary(it, prof).violations}
    slow = lambda *a: (46, "公交/地铁", 3.0)  # noqa: E731
    assert "travel_time" in {v.code for v in validate_itinerary(it, prof, travel_fn=slow).violations}


def test_pipeline_with_fake_amap_uses_real_source(tmp_path, mock_llm, monkeypatch):
    import app.travel as travel_mod
    from app.graph import run_plan
    r = fake_router(tmp_path, distance_handler(walk_m=1200, drive=(6000, 1500)))
    monkeypatch.setattr("app.amap.get_router", lambda: r)
    monkeypatch.setattr(travel_mod.config, "AMAP_WEB_KEY", "fake")
    out = run_plan("", {"days": 1, "interests": ["人文历史"], "transport": "taxi"}, llm=mock_llm, select_mode="rule")
    assert out["travel_stats"]["source"] == "amap" and out["travel_stats"]["live_calls"] > 0
    assert any("高德真实路线" in line for line in out["log"])


def test_fill_in_rejects_additions_that_fail_real_check(tmp_path, mock_llm, monkeypatch):
    """回归（c09）：补空档试探只读缓存，采纳前必须按真实路线复核。"""
    import app.travel as travel_mod
    from app.graph import run_plan
    r = fake_router(tmp_path, distance_handler(walk_m=1400, drive=(9000, 2400)))  # 真实路程明显比估算慢
    monkeypatch.setattr("app.amap.get_router", lambda: r)
    monkeypatch.setattr(travel_mod.config, "AMAP_WEB_KEY", "fake")
    out = run_plan("", {"days": 1, "interests": ["自然风光"], "pace": "normal"}, llm=mock_llm, select_mode="rule")
    if any("空档修复" in line for line in out["log"]):
        assert out["report"].passed, [v.message for v in out["report"].violations]
