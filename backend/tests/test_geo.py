from app.geo import bd09_to_gcj02, haversine_km, hm, parse_hm, travel


def test_haversine_known_distance():
    # 雷峰塔 → 六和塔（数据集 BD-09 坐标），直线约 4.1 km
    d = haversine_km(30.236901, 120.155363, 30.202099, 120.139068)
    assert 3.8 < d < 4.4


def test_travel_modes():
    assert travel(30.0, 120.0, 30.0, 120.0) == (0, "原地", 0.0)
    m, mode, cost = travel(30.2369, 120.1554, 30.2400, 120.1554)  # ~0.34km
    assert mode == "步行" and cost == 0 and 1 <= m <= 10
    m, mode, cost = travel(30.2369, 120.1554, 30.2021, 120.1391)
    assert mode == "公交/打车" and m > 10 and cost > 3


def test_bd09_to_gcj02_offset_is_small_and_westward():
    lat, lon = bd09_to_gcj02(30.236901, 120.155363)
    assert 0.004 < 120.155363 - lon < 0.008
    assert 0.004 < 30.236901 - lat < 0.008


def test_hm_roundtrip():
    assert hm(parse_hm("09:05")) == "09:05"
    assert parse_hm("24:00") == 1440
