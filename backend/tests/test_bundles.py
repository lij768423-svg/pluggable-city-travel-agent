"""同一景区合成一次游览（2026-10-28 用户决定）：灵隐寺、飞来峰等并入灵隐飞来峰景区，按景区建议时长排一次。"""
from collections import Counter
import json

import pytest

from app.bundles import PATH, bundle_notice, bundles, umbrella_of
from app.data import load_pois
from app.nodes.retrieve import must_visit_ids, retrieve_with_report
from app.nodes.schedule import service_min
from app.poi_catalog import base_catalog
from app.poi_semantics import evening_only
from app.schemas import UserProfile


def by_name(name: str) -> int:
    return next(identifier for identifier, place in load_pois().items() if place.name == name)


def test_lingyin_members_become_one_visit_with_the_area_duration():
    umbrella = by_name("灵隐飞来峰景区")
    profile = UserProfile(days=1, must_visit=["灵隐寺"], interests=["宗教寺庙"])
    candidates, report = retrieve_with_report(profile)
    ids = {candidate.poi_id for candidate in candidates}
    assert umbrella in ids and not ids & {by_name("灵隐寺"), by_name("飞来峰"), by_name("飞来峰造像")}
    assert must_visit_ids(profile) == {umbrella}
    assert report["must_visit"][0]["status"] == "covered"
    entry = next(candidate for candidate in candidates if candidate.poi_id == umbrella)
    members = [load_pois()[identifier] for identifier in (umbrella, by_name("灵隐寺"), by_name("飞来峰"))]
    assert {tag for place in members for tag in place.interests} <= set(entry.interests)  # 成员的兴趣标签并过来
    assert service_min(load_pois()[umbrella], "normal") == 210  # 景区建议 3–5 小时，适中取 3.5 小时
    assert "灵隐寺" in bundle_notice(umbrella)


def test_avoiding_a_member_avoids_the_whole_area():
    candidates, _ = retrieve_with_report(UserProfile(days=1, avoid=["飞来峰"]))
    assert by_name("灵隐飞来峰景区") not in {candidate.poi_id for candidate in candidates}
    assert by_name("灵隐飞来峰景区") in bundles()


def test_written_fee_note_replaces_the_automatic_extra_ticket_line():
    from app.bundles import Bundle
    from unittest.mock import patch
    umbrella = by_name("灵隐飞来峰景区")
    member = by_name("灵隐寺")
    with patch("app.bundles.bundles", return_value={umbrella: Bundle(umbrella, ["灵隐寺"], "寺在景区里", None, (member,), "进寺另买香花券")}):
        assert bundle_notice(umbrella) == "同一景区一次游览：含灵隐寺。寺在景区里。进寺另买香花券。"


# 2026-11-05 第三轮（用户：“合并标签”）：同一地点的别称和重复记录。键是城市，值是 成员 → 主条目。
THIRD_ROUND = {
    "hangzhou": {"雷峰夕照": "雷峰塔", "南宋德寿宫遗址博物馆": "德寿宫遗址", "森泊幻想岛": "杭州开元森泊度假乐园",
                 "杭州钱塘江夜游": "钱塘江夜游"},
    "beijing": {"复兴之路展厅": "中国国家博物馆", "五塔寺塔": "石刻艺术博物馆", "钟楼": "钟鼓楼"},
    "chengdu": {"浩海立方海洋乐园": "海滨城浩海立方海洋公园", "刘文辉旧居陈列馆": "刘文辉公馆延庆园", "成都凤凰山体育中心": "成都市凤凰山体育公园"},
    "chongqing": {"重庆工业博物馆": "重庆工业文化博览园", "涂鸦一条街": "黄桷坪涂鸦艺术街", "重庆歌乐山革命纪念馆": "歌乐山烈士陵园",
                  "重庆三峡游船": "重庆长江三峡游船", "阿依河漂流": "阿依河景区", "人民解放纪念碑": "解放碑步行街", "红岩村": "红岩革命纪念馆",
                  "重庆美术馆": "重庆国泰艺术中心", "联合国际大厦": "重庆云端之眼观景台", "洪崖洞重逢1980街区": "洪崖洞"},
    "guangzhou": {"广州美术馆(新馆)": "广州艺术博物院(新馆)"},
    "nanjing": {"四方当代美术馆": "四方当代艺术湖区", "王导谢安纪念馆": "乌衣巷", "龙出没穿越漂流": "南京龙出没乐园"},
    "shanghai": {"七宝老街": "七宝古镇", "上海淞沪抗战纪念公园": "淞沪抗战纪念馆", "汽车博物馆·MAGIC MOTOWN 摩都": "上海汽车博物馆",
                 "武康大楼": "武康路", "金茂大厦云中漫步": "金茂大厦", "中国银行": "外滩"},
    "shenzhen": {"红立方公共艺术馆": "深圳红立方", "深圳市大鹏新区古城博物馆": "大鹏所城"},
    "suzhou": {"罗汉堂": "西园寺"},
    "wuhan": {"江汉关大楼": "江汉关博物馆", "长江武汉关": "江汉关博物馆", "东湖海洋乐园": "武汉东湖海洋世界", "武汉革命博物馆": "农讲所旧址纪念馆"},
}


@pytest.mark.parametrize("city", sorted(THIRD_ROUND))
def test_third_round_names_are_real_and_belong_to_one_group(city, monkeypatch):
    monkeypatch.setenv("TTA_CITIES", ",".join(THIRD_ROUND))
    names = {place.name: place for place in base_catalog(city).pois.values()}
    entries = json.loads(PATH.read_text(encoding="utf-8"))[city]
    owners = Counter(name for entry in entries for name in {entry["umbrella"], *entry["members"]})
    for member, umbrella in THIRD_ROUND[city].items():
        assert member in names and umbrella in names  # 名字对不上会被静默跳过，等于没并
        assert owners[member] == 1
        assert any(entry["umbrella"] == umbrella and member in entry["members"] for entry in entries)
        # 白天的主条目不能吞掉只在晚上开的项目，反过来也一样
        assert evening_only(names[member]) == evening_only(names[umbrella]), member


def test_leifeng_xizhao_counts_as_leifeng_tower():
    tower, xizhao = by_name("雷峰塔"), by_name("雷峰夕照")
    profile = UserProfile(days=2, must_visit=["雷峰夕照"], interests=["人文历史"])
    candidates, report = retrieve_with_report(profile)
    ids = {candidate.poi_id for candidate in candidates}
    assert tower in ids and xizhao not in ids
    assert must_visit_ids(profile) == {tower}
    assert report["must_visit"][0]["status"] == "covered"
    assert "雷峰夕照" in bundle_notice(tower)


def test_old_plan_candidates_still_cannot_add_the_same_place():
    """合并前生成、已保存的行程，候选里还留着成员，靠 umbrella_of 挡住。"""
    tower, xizhao = by_name("雷峰塔"), by_name("雷峰夕照")
    assert umbrella_of(xizhao) == umbrella_of(tower) == tower
