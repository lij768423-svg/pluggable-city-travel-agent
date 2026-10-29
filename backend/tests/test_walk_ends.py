"""长堤分两端（2026-10-28 用户决定）：从近的一头进，走完从另一头出，下一段路从出口算。"""
from app.data import load_pois
from app.geo import haversine_km
from app.nodes.meals import build_itinerary
from app.schemas import Selection, UserProfile
from app.walk_ends import choose_direction, walk_center, walk_ends


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
