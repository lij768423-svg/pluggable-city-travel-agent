"""选餐把为吃这顿多出来的车费算进去，预算推出的每餐上限对近店放宽；茶饮连锁不当正餐。

2026-10-28 用户：预算很紧时，系统打车去很远的地方吃一顿很便宜的饭，不合理。
"""
from types import SimpleNamespace

from app.meal_ranking import flexible_cap, rank_restaurants
from app.nodes.meals import meal_suitability
from app.schemas import Hotel, Restaurant, UserProfile

HOME = (30.25, 120.16)


def place(identifier: int, name: str, price: float, north_km: float, cuisine: str = "江浙菜") -> Restaurant:
    return Restaurant(id=identifier, name=name, cuisine=cuisine, lat=HOME[0] + north_km / 111, lon=HOME[1],
                      price=price, open_min=600, close_min=1320)


def profile(**fields) -> UserProfile:
    return UserProfile(hotel=Hotel(name="酒店", lat=HOME[0], lon=HOME[1]), **fields)


def ranked(user: UserProfile, places: list[Restaurant], cap: float = 20) -> list[dict]:
    return rank_restaurants(HOME, None, 720, user, set(), cap, 810, {r.id: r for r in places})


def test_nearby_place_slightly_over_derived_cap_beats_far_cheap_ride():
    near, far = place(1, "近处小馆", 25, 0.3), place(2, "远处便宜店", 12, 2.0)
    options = ranked(profile(), [near, far])
    assert options[0]["restaurant"].id == 1
    assert options[0]["extra_fare"] == 0
    assert next(o for o in options if o["restaurant"].id == 2)["extra_fare"] > 0


def test_stated_meal_budget_is_not_relaxed():
    user = profile(meal_budget_max=20)
    assert flexible_cap(20, user) == 20
    assert [o["restaurant"].id for o in ranked(user, [place(1, "近处小馆", 25, 0.3), place(2, "远处便宜店", 12, 2.0)])] == [2]


class TaxiRoute:
    """1 公里内步行、不花钱；更远一律打车 15 分钟、25 元。"""
    source, scenario = "amap", "taxi"

    def prefetch_walks(self, center, points):
        pass

    def detail(self, lat1, lon1, lat2, lon2, live=True):
        if abs(lat1 - lat2) * 111 < 1:
            return SimpleNamespace(available=True, minutes=8, mode="步行", source="amap", cost=0.0)
        return SimpleNamespace(available=True, minutes=15, mode="打车", source="amap", cost=25.0)

    def leg(self, lat1, lon1, lat2, lon2, live=True):
        detail = self.detail(lat1, lon1, lat2, lon2, live)
        return detail.minutes, detail.mode, detail.cost


def by_taxi(places: list[Restaurant]) -> list[int]:
    return [option["restaurant"].id for option in rank_restaurants(
        HOME, None, 720, profile(), set(), 20, 810, {r.id: r for r in places}, route=TaxiRoute())]


def test_over_cap_place_must_save_the_ride():
    # 超出每餐上限只换来“不用多花车费”才值得：远处贵店不选；同样远的店不超上限就照常可选
    assert by_taxi([place(3, "远处贵店", 28, 3.0)]) == []
    assert by_taxi([place(4, "远处小馆", 18, 3.0)]) == [4]


def test_price_plus_extra_fare_over_cap_is_dropped_only_when_something_affordable_exists():
    near, far = place(1, "近处小馆", 25, 0.3), place(4, "远处小馆", 18, 3.0)  # 远处 18 元，往返多出车费 50 元
    assert by_taxi([near, far]) == [1]
    assert by_taxi([far]) == [4]  # 只剩它时仍给出，不让这一餐落空


def test_tea_drink_chains_are_not_meals():
    user = UserProfile()
    assert meal_suitability(place(1, "茶百道(杭州灵隐店)", 20, 0.1, "休闲餐饮场所"), user) is None
    assert meal_suitability(place(2, "喜茶(湖滨店)", 25, 0.1, "江浙菜"), user) is None
    assert meal_suitability(place(3, "休闲小店", 25, 0.1, "休闲餐饮场所"), user) is None
    assert meal_suitability(place(4, "老街面馆", 25, 0.1, "休闲餐饮场所"), user) is not None
    assert meal_suitability(place(5, "东坡序STOP COFFEE", 28, 0.1, "中餐厅"), user) is None
    assert meal_suitability(place(6, "良栖·茶·咖·宿(龙井店)", 27, 0.1, "中餐厅"), user) is None


def test_shops_are_not_meals_and_restaurants_are_not_sights():
    from app.nodes.retrieve import eligibility
    from app.restaurants import normalize_restaurant
    from app.schemas import POI
    user = UserProfile()
    assert meal_suitability(place(1, "安之秀菓(南山路一店)", 56, 0.1, "购物相关场所"), user) is None
    assert meal_suitability(place(2, "某某鲜果切", 30, 0.1, "餐饮相关"), user) is None
    assert meal_suitability(place(3, "乾堂里·杭州老八鲜", 68, 0.1, "餐饮相关"), user) is not None
    raw = {"id": "B_SHOP", "name": "湖边小店", "cityname": "杭州市", "location": "120.16,30.25",
           "type": "餐饮服务;餐饮相关场所;餐饮相关|购物服务;购物相关场所;购物相关场所", "business": {"cost": "56"}}
    assert normalize_restaurant(raw) is None
    raw["type"] = "餐饮服务;中餐厅;江浙菜|生活服务;生活服务场所;生活服务场所"
    assert normalize_restaurant(raw).cuisine == "江浙菜"  # 菜系取餐饮那一段，不取最后一段
    listing = POI(id=1, name="望湖楼茶餐厅", type="其它", lat=30.27, lon=120.16, open_min=600, close_min=1260, price=0,
                  min_hours=1, max_hours=1.5)
    assert eligibility(listing, user) == ([], "restaurant_listing")
    temple = listing.model_copy(update={"name": "灵隐寺", "type": "历史古迹"})
    assert eligibility(temple, user)[1] != "restaurant_listing"
