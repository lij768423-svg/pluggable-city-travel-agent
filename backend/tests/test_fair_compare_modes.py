"""fair_compare 的四种模式（docs/评测协议v2.md 第 4 节）：采集→禁网回放逐字段一致→汇总；只改裁判时离线重算；
只改部分方法时复用其余方法的录制并回放核对。全部不联网：模型用 MockLLM，裁判路程用估算函数。"""
import json
import re

import pytest

from app.llm import MockLLM
from tests.conftest import needs_data

pytestmark = needs_data


@pytest.fixture
def fair_env(tmp_path, monkeypatch):
    from app import amap, config, llm as llm_module
    from app.geo import travel
    from eval import city_batch20, fair_compare  # noqa: F401 —— 先导入，RecordedRouter 的基类保持真实的 AmapRouter
    from tests.conftest import mock_select
    calls = {"pure": 0, "candidate": 0}

    def pure(messages):
        calls["pure"] += 1
        return json.dumps({"days": [{"day": 1, "items": [
            {"type": "attraction", "name": "西湖", "start": "09:30", "end": "11:30"},
            {"type": "restaurant", "name": "不存在的餐厅", "start": "12:00", "end": "13:00"}]}]}, ensure_ascii=False)

    def candidate(messages):
        calls["candidate"] += 1
        ids = [int(x) for x in re.findall(r"^id=(\d+) \|", messages[-1]["content"], flags=re.M)]
        return json.dumps({"days": [{"day": 1, "items": [{"type": "attraction", "id": ids[0], "start": "09:30", "end": "11:00"}]}]})

    responses = {"parse": '{"days": 1, "budget": 800, "interests": ["人文历史"]}', "baseline_pure_llm": pure,
                 "baseline_candidate_llm": candidate, "select": mock_select}
    constructed = []
    monkeypatch.setattr(llm_module, "OpenAICompatLLM",
                        lambda **kwargs: constructed.append((config.LLM_TIMEOUT, kwargs)) or MockLLM(responses))

    class FakeJudge:
        walk_limit_m = 1500
        ttl = 900.0
        instances = []

        def __init__(self, cache_path=None):
            self.cache_path = cache_path
            FakeJudge.instances.append(self)

        def travel_fn(self, scenario="taxi"):
            return travel

        def leg_detail(self, lat1, lon1, lat2, lon2):
            minutes, _, _ = travel(lat1, lon1, lat2, lon2)
            return {"walk": {"m": minutes * 75, "min": minutes}}

    monkeypatch.setattr(amap, "_router", amap._router)  # 测试结束后恢复全局路由器
    monkeypatch.setattr(amap, "AmapRouter", FakeJudge)
    monkeypatch.setattr(config, "AMAP_WEB_KEY", "test-placeholder")  # RecordedRouter 构造需要；估算排程不会发请求
    cases = tmp_path / "cases"
    cases.mkdir()
    (cases / "x01.json").write_text(json.dumps({"id": "x01", "query": "杭州一天，人文历史", "note": ""}, ensure_ascii=False))
    return {"root": tmp_path, "cases": cases, "calls": calls, "constructed": constructed, "fc": fair_compare, "judges": FakeJudge.instances}


def test_fair_compare_capture_replay_assess_offline(fair_env, monkeypatch):
    from app import config
    fc, cases, constructed = fair_env["fc"], fair_env["cases"], fair_env["constructed"]
    timeout_before = config.LLM_TIMEOUT
    root = fair_env["root"] / "run"
    methods = ["A", "Ap", "E"]
    fc.run(root, "capture", methods, cases, None)
    monkeypatch.setenv("TTA_OFFLINE", "1")
    fc.run(root, "replay", ["B"], cases, None)  # 回放沿用采集时的方法，与命令行参数无关
    replayed = json.loads((root / "replay.json").read_text(encoding="utf-8"))["results"][0]
    assert list(replayed["results"]) == methods and all(replayed["capture_equal"].values())
    assert not [name for name, result in replayed["results"].items() if "error" in result], replayed["results"]
    assert all("objective_feasible" in result["j2_sys_walk"] for result in replayed["results"].values())
    assert replayed["results"]["A"]["j1"]["unknown_rate"] > 0 and replayed["results"]["A"]["j2"]["objective_feasible"] is False
    assert all(result["all_http_records_consumed"] for result in replayed["results"].values())
    # 裁判路段在一个用例内只查一次：缓存不按 15 分钟过期，采集与回放用同一个值
    assert fair_env["judges"] and all(judge.ttl == float("inf") for judge in fair_env["judges"])
    assert {"design.json", "capture.json", "replay.json", "config-capture.json", "config-replay.json",
            "manifest-capture.json", "manifest-replay.json"} <= {path.name for path in root.iterdir()}
    fc.assess(root)
    assert constructed and all(entry == (300, {"cache": False, "sdk_max_retries": 0, "stream": True}) for entry in constructed)
    assert config.LLM_TIMEOUT == timeout_before  # 运行结束后恢复进程级设置
    design = json.loads((root / "design.json").read_text(encoding="utf-8"))
    assert design["llm_call"] == {"timeout_seconds": 300, "sdk_max_retries": 0, "app_retry_attempts": 3, "stream": True}
    report = (root / "results.md").read_text(encoding="utf-8")
    assert "A′ 候选感知纯大模型" in report and "J2′ 客观可行率" in report


def test_objective_judges_run_before_own_validator(fair_env, monkeypatch):
    """J1 算改打车路段的车费时要看裁判缓存里有没有同一路段的公交记录；J2 先把它们查到，采集与回放时 J1 才看到同样的缓存。"""
    from eval import objective_metrics as om
    fc, order = fair_env["fc"], []
    real_metrics, real_validator = om.objective_metrics, fc.own_validator
    monkeypatch.setattr(om, "objective_metrics", lambda *a, **k: order.append("J2") or real_metrics(*a, **k))
    monkeypatch.setattr(fc, "own_validator", lambda *a, **k: order.append("J1") or real_validator(*a, **k))
    fc.run(fair_env["root"] / "run", "capture", ["E"], fair_env["cases"], None)
    assert order == ["J2", "J2", "J1"]


def test_rescore_accepts_judge_change_but_not_generation_change(fair_env, monkeypatch):
    """只改裁判代码：replay 因设计不一致拒绝，rescore 从录制重算并核对生成结果；改了生成代码则 rescore 也拒绝。"""
    fc, cases = fair_env["fc"], fair_env["cases"]
    root = fair_env["root"] / "run"
    fc.run(root, "capture", ["A", "E"], cases, None)
    monkeypatch.setenv("TTA_OFFLINE", "1")
    fc.run(root, "replay", [], cases, None)
    real, overrides = fc.file_hashes, {"backend/eval/objective_metrics.py": "0" * 64}
    monkeypatch.setattr(fc, "file_hashes", lambda paths: {**real(paths), **overrides})
    with pytest.raises(SystemExit, match="objective_metrics"):
        fc.run(root, "replay", [], cases, None)
    fc.run(root, "rescore", [], cases, None)
    rescored = [path for path in root.iterdir() if path.name.startswith("rescore-") and path.suffix == ".json"]
    assert len(rescored) == 1 and (root / "replay.json").exists()
    payload = json.loads(rescored[0].read_text(encoding="utf-8"))
    assert payload["judge_code_sha256"]["backend/eval/objective_metrics.py"] == "0" * 64
    assert payload["frozen_judge_code_sha256"]["backend/eval/objective_metrics.py"] != "0" * 64
    row = payload["results"][0]
    assert row["generation_equal"] == {"A": True, "E": True} and row["score_changed"] == {"A": [], "E": []}
    assert json.loads((root / "design.json").read_text(encoding="utf-8"))["code_sha256"]["backend/eval/objective_metrics.py"] != "0" * 64
    fc.assess(root, rescored[0].stem)
    report = (root / f"results-{rescored[0].stem}.md").read_text(encoding="utf-8")
    assert "离线重算" in report and "重算的生成结果与采集不一致：无" in report and (root / "results.md").exists() is False
    overrides["backend/app/graph.py"] = "1" * 64
    with pytest.raises(SystemExit, match="code:backend/app/graph.py"):
        fc.run(root, "rescore", [], cases, None)


def test_judge_online_only_fills_routes_and_is_not_a_score(fair_env, monkeypatch):
    """--judge-online：生成照样按录制离线重跑，只有裁判可以联网补路段；结果单独写 judgefill-*.json，不当作分数。"""
    fc, cases = fair_env["fc"], fair_env["cases"]
    root = fair_env["root"] / "run"
    fc.run(root, "capture", ["A"], cases, None)
    monkeypatch.setenv("TTA_OFFLINE", "1")
    with pytest.raises(SystemExit, match="只用于 rescore"):
        fc.run(root, "replay", [], cases, None, judge_online=True)
    fc.run(root, "rescore", [], cases, None, judge_online=True)
    filled = [path for path in root.iterdir() if path.name.startswith("judgefill-")]
    assert len(filled) == 1 and not [path for path in root.iterdir() if path.name.startswith("rescore-")]
    payload = json.loads(filled[0].read_text(encoding="utf-8"))
    assert payload["scope"].startswith("judge route fill") and payload["results"][0]["judge_legs_added"] == 0
    assert payload["results"][0]["generation_equal"] == {"A": True}
    judge = fair_env["judges"][-1]
    assert judge.offline is False and judge.persist is True and (root / "config-judgefill.json").exists()


def test_capture_reuses_recordings_of_unchanged_methods(fair_env, monkeypatch):
    """只重采 E：A、A′ 的录制从旧目录复制并离线重跑，不再调用模型；采集时核对行程与旧采集一致，回放仍逐字段一致。"""
    fc, cases, calls = fair_env["fc"], fair_env["cases"], fair_env["calls"]
    old, new = fair_env["root"] / "old", fair_env["root"] / "new"
    fc.run(old, "capture", ["A", "Ap", "E"], cases, None)
    assert calls == {"pure": 1, "candidate": 1}
    with pytest.raises(SystemExit, match="--reuse"):
        fc.run(new, "capture", ["A", "Ap", "E"], cases, None, reuse_from=old, reuse_methods=[])
    with pytest.raises(SystemExit, match="没有采集过"):
        fc.run(new, "capture", ["A", "Ap", "E"], cases, None, reuse_from=old, reuse_methods=["B"])
    fc.run(new, "capture", ["A", "Ap", "E"], cases, None, reuse_from=old, reuse_methods=["A", "Ap"])
    assert calls == {"pure": 1, "candidate": 1}  # 复用的方法没有再调用模型
    captured = json.loads((new / "capture.json").read_text(encoding="utf-8"))["results"][0]
    assert captured["results"]["A"]["reused"]["itinerary_equal"] and captured["results"]["Ap"]["reused"]["itinerary_equal"]
    assert "reused" not in captured["results"]["E"] and "error" not in captured["results"]["E"]
    assert captured["results"]["A"]["itinerary"] == json.loads((old / "capture.json").read_text(encoding="utf-8"))["results"][0]["results"]["A"]["itinerary"]
    design = json.loads((new / "design.json").read_text(encoding="utf-8"))
    assert design["reuse"]["methods"] == ["A", "Ap"] and design["reuse"]["from"].endswith("old")
    assert sorted(path.name for path in (new / "x01" / "A" / "llm").iterdir()) == sorted(path.name for path in (old / "x01" / "A" / "llm").iterdir())
    monkeypatch.setenv("TTA_OFFLINE", "1")
    fc.run(new, "replay", [], cases, None)
    replayed = json.loads((new / "replay.json").read_text(encoding="utf-8"))["results"][0]
    assert replayed["capture_equal"] == {"A": True, "Ap": True, "E": True}
    fc.assess(new)
    assert "复用自" in (new / "results.md").read_text(encoding="utf-8") and "全部一致" in (new / "results.md").read_text(encoding="utf-8")


def test_reuse_rejects_different_cases(fair_env):
    fc, cases = fair_env["fc"], fair_env["cases"]
    old = fair_env["root"] / "old"
    fc.run(old, "capture", ["A"], cases, None)
    other = fair_env["root"] / "cases2"
    other.mkdir()
    (other / "x02.json").write_text(json.dumps({"id": "x02", "query": "杭州一天，人文历史", "note": ""}, ensure_ascii=False))
    with pytest.raises(SystemExit, match="用例或模型设置"):
        fc.run(fair_env["root"] / "new", "capture", ["A"], other, None, reuse_from=old, reuse_methods=["A"])
