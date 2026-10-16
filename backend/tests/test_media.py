import json
from datetime import date

import pytest
from pydantic import ValidationError

import app.media as media
from app.amap import AmapError, AmapRouter, pt
from app.data import load_pois
from tests.conftest import needs_data

pytestmark = needs_data


class FakeResp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


@pytest.fixture
def registry_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "MEDIA_DIR", tmp_path)
    monkeypatch.setattr(media, "FILES_DIR", tmp_path / "files")
    monkeypatch.setattr(media, "REGISTRY_PATH", tmp_path / "registry.json")
    (tmp_path / "files").mkdir()
    return tmp_path


def fake_router(handler):
    router = AmapRouter(key="test", qps=1000)
    router.log = []

    def get(url, params):
        router.log.append(url.rsplit("/", 2)[-2:])
        return FakeResp(handler(url, params))

    router._client.get = get
    return router


def amap_photo(name, n=3):
    return [{"title": [] if i else "外景", "url": f"http://store.is.autonavi.com/showpic/{name}{i}"} for i in range(n)]


def test_url_normalization_and_fingerprint():
    assert media.normalize_url("http://store.is.autonavi.com/showpic/a") == "https://store.is.autonavi.com/showpic/a"
    assert media.normalize_url("http://example.com/x") == "http://example.com/x"  # 只改高德图片域名
    assert media.fingerprint("http://store.is.autonavi.com/showpic/a") == media.fingerprint("https://store.is.autonavi.com/showpic/a")


def test_photos_of_filters_foreign_hosts_and_list_titles():
    poi = {"photos": [*amap_photo("x", 2), {"title": "广告", "url": "https://evil.example.com/a.jpg"}, {"url": ""}]}
    photos = media._photos_of(poi, "amap", "高德地图 · 本地点")
    assert len(photos) == 2 and photos[0].caption == "外景" and photos[1].caption == ""
    assert all(p.url.startswith("https://store.is.autonavi.com/") for p in photos)


def test_related_pois_rule():
    lingyin = next(p for p in load_pois().values() if p.name == "灵隐寺")
    here = pt(lingyin.lat, lingyin.lon)
    lon, lat = map(float, here.split(","))
    far = f"{lon + 0.02:.6f},{lat:.6f}"  # 约 1.9 km
    pois = [{"id": "A", "name": "灵隐寺", "typecode": "110205", "location": here},
            {"id": "B", "name": "灵隐景区", "typecode": "110200", "location": here},  # 同源、近、风景名胜 → 关联
            {"id": "C", "name": "灵隐路公交站", "typecode": "150700", "location": here},  # 非风景名胜
            {"id": "D", "name": "灵隐飞来峰", "typecode": "110200", "location": far},  # 太远
            {"id": "E", "name": "龙井村", "typecode": "110200", "location": here}]  # 名称不同源
    assert [p["id"] for p in media.related_pois(lingyin, pois[0], pois)] == ["B"]


def test_merge_manual_first_hidden_dedup_and_max_five(registry_dir):
    lingyin = next(p for p in load_pois().values() if p.name == "灵隐寺")
    here = pt(lingyin.lat, lingyin.lon)
    (registry_dir / "files" / "poi0-aaaa.jpg").write_bytes(b"x")
    hidden = media.fingerprint("http://store.is.autonavi.com/showpic/main1")
    (registry_dir / "registry.json").write_text(json.dumps({"version": 1, "places": {f"poi:{lingyin.id}": {
        "name": "灵隐寺", "hide": [hidden],
        "photos": [{"file": "poi0-aaaa.jpg", "caption": "山门", "author": "测试者", "license": "CC BY 4.0",
                    "source_url": "https://example.org/p", "added_at": "2026-10-15"}]}}}), encoding="utf-8")
    router = fake_router(lambda url, params: {"status": "1", "pois": [
        {"id": "A", "name": "灵隐寺", "typecode": "110205", "location": here, "photos": amap_photo("main")},
        {"id": "B", "name": "灵隐景区", "typecode": "110200", "location": here, "photos": amap_photo("rel")}]})
    result = media.get_place_media("poi", lingyin.id, router=router)
    assert result.status == "ready" and len(result.photos) == 5
    assert result.photos[0].source == "manual" and result.photos[0].url == "/media/files/poi0-aaaa.jpg"
    assert hidden not in [p.fingerprint for p in result.photos]
    assert [p.source for p in result.photos] == ["manual", "amap", "amap", "amap_related", "amap_related"]
    assert "关联地点「灵隐景区」" in result.photos[3].source_label
    # 第二次读内存缓存，不再请求
    calls = len(router.log)
    media.get_place_media("poi", lingyin.id, router=router)
    assert len(router.log) == calls


def test_restaurant_with_external_id_uses_detail_endpoint(registry_dir, monkeypatch):
    from app.place_details import remember_restaurants
    from app.schemas import Restaurant
    r = Restaurant(id=-424242, name="测试餐厅", lat=30.25, lon=120.16, price=60, cuisine="江浙菜", open_min=600,
                   close_min=1260, external_id="B0TEST", provider="amap")
    remember_restaurants([r])
    router = fake_router(lambda url, params: {"status": "1", "pois": [{"id": "B0TEST", "photos": amap_photo("food", 2)}]})
    result = media.get_place_media("meal", -424242, router=router)
    assert router.log == [["place", "detail"]]
    assert result.status == "partial" and len(result.photos) == 2 and result.photos[0].source_label == "高德地图 · 本店"


def test_amap_unavailable_keeps_manual_and_reports_status(registry_dir):
    poi = next(iter(load_pois().values()))

    class Broken:
        def fork(self, budget):
            return self

        def has(self, key):
            return False

        def _get(self, path, params):
            raise AmapError("down")
    result = media.get_place_media("poi", poi.id, router=Broken())
    assert result.status == "unavailable" and result.photos == []


def test_registry_validation():
    with pytest.raises(ValidationError):
        media.Registry.model_validate({"version": 1, "places": {"poi:1": {"name": "x", "photos": [
            {"file": "a.jpg", "author": "", "license": "CC0", "source_url": "u", "added_at": "2026-10-15"}]}}})
    with pytest.raises(ValidationError):
        media.Registry.model_validate({"version": 1, "places": {"hotel:1": {"name": "x"}}})
    with pytest.raises(ValidationError):
        media.ManualPhoto(file="../etc/passwd.jpg", author="a", license="b", source_url="c", added_at=date.today())


def test_add_check_hide_remove_roundtrip(registry_dir, tmp_path):
    poi = next(p for p in load_pois().values() if p.name == "灵隐寺")
    image = tmp_path / "in.jpg"
    image.write_bytes(b"\xff\xd8fake-jpeg")
    photo = media.add_manual_photo("poi", poi.id, poi.name, image, caption="大殿", author="我",
                                   license="CC BY 4.0", source_url="https://example.org")
    assert (registry_dir / "files" / photo.file).exists()
    assert media.check_registry() == []
    value = media.set_hidden("poi", poi.id, poi.name, "http://store.is.autonavi.com/showpic/x")
    assert value in media.load_registry().places[f"poi:{poi.id}"].hide
    (registry_dir / "files" / "orphan.jpg").write_bytes(b"x")
    assert any("孤儿文件" in problem for problem in media.check_registry())
    assert media.remove_manual_photo("poi", poi.id, photo.file)
    assert not (registry_dir / "files" / photo.file).exists()
    media.set_hidden("poi", poi.id, poi.name, value, hidden=False)
    assert f"poi:{poi.id}" not in media.load_registry().places


def test_api_place_media_endpoint(monkeypatch, registry_dir):
    from fastapi.testclient import TestClient
    from app.api import app
    poi = next(p for p in load_pois().values() if p.name == "灵隐寺")
    here = pt(poi.lat, poi.lon)
    router = fake_router(lambda url, params: {"status": "1", "pois": [
        {"id": "A", "name": "灵隐寺", "typecode": "110205", "location": here, "photos": amap_photo("m")}]})
    monkeypatch.setattr(media, "get_router", lambda: router)
    client = TestClient(app)
    body = client.get(f"/api/place-media?kind=poi&id={poi.id}").json()
    assert body["status"] == "ready" and len(body["photos"]) == 3 and body["policy"]
    assert client.get("/api/place-media?kind=poi&id=999999").status_code == 404
    assert client.get("/api/place-media?kind=hotel&id=1").status_code == 422


def test_comment_photos_get_server_side_thumbnails():
    full, thumb = media.sized("https://aos-comment.amap.com/B0X/comment/a_2048_2048_80.jpg")
    assert thumb.endswith("resize,w_720/quality,q_80") and full.endswith("resize,w_1600/quality,q_85")
    assert media.sized("https://store.is.autonavi.com/showpic/a") == ("https://store.is.autonavi.com/showpic/a",) * 2
    photo = media._photos_of({"photos": [{"url": "https://aos-comment.amap.com/B0X/comment/a.jpg"}]}, "amap", "x")[0]
    # 屏蔽指纹按原始地址计算，不受缩放参数影响
    assert photo.fingerprint == media.fingerprint("https://aos-comment.amap.com/B0X/comment/a.jpg")


def test_malformed_photo_urls_are_skipped():
    values = ["broken", "/relative.jpg", "https://[invalid", "ftp://store.is.autonavi.com/a",
              "https://store.is.autonavi.com.evil.test/a", "https://user@store.is.autonavi.com/a",
              "http://store.is.autonavi.com/a"]
    photos = media._photos_of({"photos": [{"url": value} for value in values]}, "amap", "本店")
    assert [photo.url for photo in photos] == ["https://store.is.autonavi.com/a"]
