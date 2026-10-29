"""长堤分两端（2026-10-28 用户决定）：从近的一头进，走完从另一头出，下一段路从出口算。"""
from app.data import load_pois
from app.geo import haversine_km
from app.nodes.meals import build_itinerary
from app.schemas import Selection, UserProfile
from app.walk_ends import boat_piers, choose_direction, walk_center, walk_ends


def by_name(name: str) -> int:
    return next(identifier for identifier, place in load_pois().items() if place.name == name)


def test_sudi_is_entered_near_the_previous_stop_and_left_at_the_other_end():
    pois = load_pois()
    sudi, huagang, yuewang = pois[by_name("苏堤")], pois[by_name("花港观鱼")], pois[by_name("岳王庙景区")]
    south, north = [point for _, point in walk_ends(sudi)]
    enter, leave, note = choose_direction(sudi, (huagang.lat, huagang.lon), (yuewang.lat, yuewang.lon))
    assert enter == south and leave == north and "苏堤南口进" in note
    assert haversine_km(*leave, yuewang.lat, yuewang.lon) < 1.2  # 北口离岳王庙不到 1.2 公里
    center = walk_center(sudi)
    assert min(south[0], north[0]) < center[0] < max(south[0], north[0])


def test_timeline_leaves_sudi_from_the_north_end():
    profile = UserProfile(days=1, pace="relaxed", origin_known=False)
    order = [by_name("花港观鱼"), by_name("苏堤"), by_name("岳王庙景区")]
    selection = [Selection(poi_id=identifier, reason="测试", source="rule") for identifier in order]
    itinerary = build_itinerary([order], profile, selection)
    visits = [item for item in itinerary.days[0].items if item.kind == "poi"]
    sudi = next(item for item in visits if item.name == "苏堤")
    after = visits[visits.index(sudi) + 1]
    assert sudi.leave_point is not None and sudi.enter_point is not None
    assert after.name == "岳王庙景区" and after.travel_min <= 15  # 从北口走过去，不再打车绕回南端


def test_other_cities_have_their_long_walks_and_the_cable_car_is_a_ride():
    from types import SimpleNamespace
    from app.poi_catalog import published_catalog, use_catalog
    from app.walk_ends import walk_length_m
    for city, name in (("suzhou", "山塘街"), ("suzhou", "平江路历史街区"), ("shanghai", "外滩"), ("shanghai", "南京路步行街"),
                       ("chongqing", "长江索道"), ("wuhan", "江汉路步行街")):
        with use_catalog(published_catalog(city)):
            place = next(poi for poi in load_pois().values() if poi.name == name)
            assert walk_ends(place), (city, name)
    with use_catalog(published_catalog("chongqing")):
        cable = next(poi for poi in load_pois().values() if poi.name == "长江索道")
        enter, leave, note = choose_direction(cable, None, None)
        assert "坐到" in note
        assert walk_length_m(SimpleNamespace(name="长江索道", enter_point=enter, leave_point=leave)) == 0


def test_a_walkable_exit_beats_a_ride_that_is_a_minute_faster():
    # 2026-10-29 视频行程：北口坐车 15 分钟比南口步行 16 分钟“快”，于是从北口出，实际要打车折回南边
    sudi = load_pois()[by_name("苏堤")]
    south, north = [point for _, point in walk_ends(sudi)]

    def leg(lat1, lon1, lat2, lon2):
        return (15, "公交/打车", 4.0) if (lat1, lon1) == north else (16, "步行", 0.0)

    enter, leave, _ = choose_direction(sudi, None, (30.24, 120.15), leg)
    assert (enter, leave) == (north, south)


def test_santan_is_reached_by_boat_from_the_nearest_pier():
    pois = load_pois()
    santan, leifeng = pois[by_name("三潭印月")], pois[by_name("雷峰塔")]
    assert walk_ends(santan) is None
    piers = [point for _, point in boat_piers(santan)]
    enter, leave, note = choose_direction(santan, (leifeng.lat, leifeng.lon), (leifeng.lat, leifeng.lon))
    assert enter == leave and enter in piers and "坐船上岛" in note
    nearest = min(piers, key=lambda pier: haversine_km(*pier, leifeng.lat, leifeng.lon))
    assert haversine_km(*enter, leifeng.lat, leifeng.lon) <= haversine_km(*nearest, leifeng.lat, leifeng.lon) + 0.5
    assert walk_center(santan, near=(leifeng.lat, leifeng.lon)) == nearest


def test_timeline_takes_the_boat_and_leaves_sudi_toward_the_pier():
    pois = load_pois()
    profile = UserProfile(days=1, pace="relaxed", origin_known=False)
    order = [by_name("苏堤"), by_name("三潭印月"), by_name("雷峰塔")]
    selection = [Selection(poi_id=identifier, reason="测试", source="rule") for identifier in order]
    visits = [item for item in build_itinerary([order], profile, selection).days[0].items if item.kind == "poi"]
    sudi, santan = visits[0], visits[1]
    assert santan.name == "三潭印月" and santan.enter_point == santan.leave_point
    assert tuple(santan.enter_point) in [point for _, point in boat_piers(pois[by_name("三潭印月")])]
    assert any("坐船上岛" in notice for notice in santan.notices)
    assert haversine_km(*sudi.leave_point, *santan.enter_point) < haversine_km(*sudi.enter_point, *santan.enter_point)
