"""同一景区合成一次游览（2026-10-28 用户决定）：灵隐寺、飞来峰等并入灵隐飞来峰景区，按景区建议时长排一次。"""
from app.bundles import bundle_notice, bundles
from app.data import load_pois
from app.nodes.retrieve import must_visit_ids, retrieve_with_report
from app.nodes.schedule import service_min
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
