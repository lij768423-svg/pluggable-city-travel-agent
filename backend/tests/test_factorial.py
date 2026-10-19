from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess

import pytest

from eval.factorial.common import GROUPS, NetworkForbidden, SnapshotError, audit_rows, block_network, digest, offline_environment, read_json, selection_contract, verify_snapshot, write_json
from eval.factorial.metrics import summarize
from eval.factorial.snapshot import builtin_cases, make_matrix
from eval.factorial.study import blind_packet, export_study, import_responses
from tests.test_meal_previews import edit_case


@pytest.fixture
def results_case():
    case = {"id": "case1", "split": "regression", "profile": {"days": 1, "interests": ["自然风光"]},
        "pools": {name: {"candidates": [{"poi_id": 1, "score": 0.8}], "selected": [{"poi_id": 1}]}
                  for name in ("baseline", "coverage")}, "restaurant_ids": [11], "route_file": "routes/case1.json.gz",
        "flags": {"select_mode": "rule", "enable_repair": True, "enable_refine": True}}
    inputs = {"cases": [case], "evaluator_sha256": "validator"}
    rows = [{"case_id": "case1", "group": group, "split": "regression", "status": "ok", "flags": case["flags"],
        "input_contract": selection_contract(case, factors["coverage"]), "evaluator_sha256": "validator",
        "snapshot_sha256": "same-snapshot", "network_attempts": 0, "llm_live_calls": 0,
        "report": {"constraint_status": "passed", "arrangement_status": "complete", "evidence_status": "partial",
                   "complete_executable": False, "violations": []},
        "metrics": {"interest_coverage": 1.0}, "itinerary": {"days": []}}
        for group, factors in GROUPS.items()]
    return inputs, rows


def test_four_groups_are_the_exact_factorial():
    assert {(group["coverage"], group["route"]) for group in GROUPS.values()} == {(False, False), (True, False), (False, True), (True, True)}


def test_canonical_hash_is_order_stable_and_rejects_nan():
    assert digest({"a": 1, "b": 2}) == digest({"b": 2, "a": 1})
    with pytest.raises(ValueError):
        digest({"value": float("nan")})


def test_compressed_json_is_reproducible_and_not_overwritten(tmp_path):
    for name in ("first.json.gz", "second.json.gz"):
        write_json(tmp_path / name, {"value": [1, 2, 3]})
    assert (tmp_path / "first.json.gz").read_bytes() == (tmp_path / "second.json.gz").read_bytes()
    assert read_json(tmp_path / "first.json.gz") == {"value": [1, 2, 3]}
    with pytest.raises(FileExistsError):
        write_json(tmp_path / "first.json.gz", {})


def test_network_guard_blocks_and_restores_socket():
    original = socket.create_connection
    with block_network() as attempts:
        with pytest.raises(NetworkForbidden):
            socket.create_connection(("127.0.0.1", 1))
        assert attempts["blocked"] == 1
    assert socket.create_connection is original


def test_offline_environment_is_restored_even_after_failure(monkeypatch):
    monkeypatch.setenv("TTA_OFFLINE", "original")
    monkeypatch.delenv("TTA_ROUTE_SNAPSHOT", raising=False)
    with pytest.raises(RuntimeError):
        with offline_environment():
            assert os.environ["TTA_OFFLINE"] == "1"
            os.environ["TTA_ROUTE_SNAPSHOT"] = "temporary"
            raise RuntimeError("test")
    assert os.environ["TTA_OFFLINE"] == "original" and "TTA_ROUTE_SNAPSHOT" not in os.environ


def minimal_snapshot(directory: Path) -> dict:
    write_json(directory / "inputs.json", {"cases": [{"id": "test"}]})
    manifest = {"schema": "tta-factorial-v1", "groups": GROUPS, "runtime": {},
                "files": {"inputs.json": hashlib.sha256((directory / "inputs.json").read_bytes()).hexdigest()}}
    write_json(directory / "manifest.json", manifest)
    return manifest


def test_snapshot_modification_is_rejected(tmp_path):
    minimal_snapshot(tmp_path)
    assert verify_snapshot(tmp_path, check_runtime=False)["schema"] == "tta-factorial-v1"
    (tmp_path / "inputs.json").write_text("{}")
    with pytest.raises(SnapshotError, match="摘要"):
        verify_snapshot(tmp_path, check_runtime=False)


def test_snapshot_cannot_read_external_paths(tmp_path):
    manifest = minimal_snapshot(tmp_path)
    manifest["files"] = {"../outside": "fake"}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(SnapshotError, match="路径"):
        verify_snapshot(tmp_path, check_runtime=False)


def test_dependency_drift_is_rejected(tmp_path):
    minimal_snapshot(tmp_path)
    with pytest.raises(SnapshotError, match="依赖"):
        verify_snapshot(tmp_path)


def test_unregistered_code_is_rejected(tmp_path):
    minimal_snapshot(tmp_path)
    (tmp_path / "injected.py").write_text("value = 1")
    with pytest.raises(SnapshotError, match="未登记"):
        verify_snapshot(tmp_path, check_runtime=False)


@pytest.mark.parametrize("field", ["profile", "candidates", "selection", "restaurants", "routes", "flags"])
def test_input_contract_drift_is_rejected(results_case, field):
    inputs, rows = results_case
    rows[2]["input_contract"][field] = "changed"
    with pytest.raises(SnapshotError):
        audit_rows(inputs, rows)


@pytest.mark.parametrize("field,value", [("evaluator_sha256", "different"), ("snapshot_sha256", "different"),
    ("network_attempts", 1), ("llm_live_calls", 1), ("route_snapshot_misses", 1)])
def test_execution_contract_drift_is_rejected(results_case, field, value):
    inputs, rows = results_case
    rows[1][field] = value
    with pytest.raises(SnapshotError):
        audit_rows(inputs, rows)


def test_missing_duplicate_and_missing_flag_rows_are_rejected(results_case):
    inputs, rows = results_case
    for changed in (rows[:-1], rows + [rows[0]]):
        with pytest.raises(SnapshotError):
            audit_rows(inputs, changed)
    rows[0]["flags"] = {}
    with pytest.raises(SnapshotError):
        audit_rows(inputs, rows)


def test_generation_failure_stays_in_the_denominator(results_case):
    inputs, rows = results_case
    rows[0].update(status="error", error="synthetic")
    second = deepcopy(inputs["cases"][0])
    second["id"] = "case2"
    inputs["cases"].append(second)
    rows.extend([{**deepcopy(row), "case_id": "case2", "status": "ok"} for row in rows[:4]])
    summary = summarize(inputs, rows)
    assert summary["groups"]["baseline"]["requests"] == 2
    assert summary["groups"]["baseline"]["rates"]["constraint_passed"] == 0.5
    assert summary["groups"]["baseline"]["errors"] == 1


def test_partial_evidence_is_not_a_complete_preference_pair(results_case):
    inputs, rows = results_case
    summary = summarize(inputs, rows)
    assert summary["paired_preference"]["baseline__full"]["n"] == 0
    assert summary["paired_preference"]["baseline__full"]["interest_coverage_delta_mean"] is None
    for row in (rows[0], rows[3]):
        row["report"].update(evidence_status="verified", complete_executable=True)
    assert summarize(inputs, rows)["paired_preference"]["baseline__full"]["n"] == 1


def test_regression_and_holdout_are_not_mixed(results_case):
    inputs, rows = results_case
    assert summarize(inputs, rows, "regression")["groups"]["full"]["requests"] == 1
    assert summarize(inputs, rows, "holdout")["groups"]["full"]["requests"] == 0
    cases = builtin_cases()
    assert {case["split"] for case in cases} == {"regression", "holdout"}


def test_route_matrix_covers_all_pairs_and_known_contexts():
    from app.schemas import UserProfile
    profile = UserProfile(days=2, start_date="2026-10-20", day_start=840)
    result = make_matrix(profile, [(30.25, 120.16), (30.26, 120.17)], "estimate")
    assert len(result["points"]) == 2
    assert all(len(legs) == 4 for legs in result["matrices"].values())
    assert any(context["date"] == "2026-10-21" and context["departure"] == "14:00" for context in result["contexts"])
    assert any(context["date"] is None and context["walk_limit_m"] == 2500 for context in result["contexts"])


def test_amap_matrix_missing_cache_never_falls_back(tmp_path, monkeypatch):
    from app.amap import AmapRouter, MissingSnapshotError
    from app.schemas import UserProfile
    monkeypatch.setenv("TTA_OFFLINE", "1")
    cache = tmp_path / "empty.jsonl"
    cache.write_text("")
    monkeypatch.setenv("TTA_ROUTE_SNAPSHOT", str(cache))
    with block_network() as attempts:
        with pytest.raises(MissingSnapshotError):
            make_matrix(UserProfile(days=1), [(30.25, 120.16), (30.3, 120.22)], "amap", AmapRouter(key="offline", cache_path=cache))
        assert attempts["blocked"] == 0


def test_blind_default_does_not_include_incomplete_plans(results_case, tmp_path):
    inputs, rows = results_case
    public, private = blind_packet(inputs, rows)
    assert public["packets"] == [] and private["assignments"] == {}
    export_study(inputs, rows, tmp_path)
    assert "正式问卷暂未启用" in (tmp_path / "blind.html").read_text()


@pytest.mark.skipif(shutil.which("node") is None, reason="仅附加检查浏览器脚本语法")
def test_blind_questionnaire_script_parses(results_case, tmp_path):
    inputs, rows = results_case
    export_study(inputs, rows, tmp_path)
    script = re.findall(r"<script>(.*?)</script>", (tmp_path / "blind.html").read_text(), re.S)[0]
    result = subprocess.run(["node", "--check"], input=script, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_diagnostic_blind_export_hides_method_labels_and_escapes_html(results_case, tmp_path):
    inputs, rows = results_case
    inputs["cases"][0]["profile"]["interests"] = ["</script><script>bad</script>"]
    public, private = blind_packet(inputs, rows, allow_incomplete=True)
    assert len(public["packets"]) == 1
    assert not public["packets"][0]["eligible_primary"]
    assert set(private["assignments"][public["packets"][0]["question"]]["assignment"].values()) == set(GROUPS)
    assert not any(group in json.dumps(public) for group in GROUPS)
    export_study(inputs, rows, tmp_path, allow_incomplete=True)
    text = (tmp_path / "blind.html").read_text()
    assert "<script>bad</script>" not in text


def test_blind_responses_require_complete_unique_ratings(results_case, tmp_path):
    inputs, rows = results_case
    public, private = blind_packet(inputs, rows, allow_incomplete=True)
    packet = public["packets"][0]
    response = {"study": public["study"], "participant": "P01", "answers": [
        {"question": packet["question"], "option": option["label"], "dimension": dimension, "score": 4}
        for option in packet["options"] for dimension in ("交通舒适", "体验匹配", "餐饮安排", "整体偏好")]}
    path = tmp_path / "response.json"
    write_json(path, response)
    result = import_responses(public, private, [path])
    assert result["participants"] == 1 and result["primary_ratings"] == 0 and result["diagnostic_only_ratings"] == 16
    with pytest.raises(SnapshotError):
        import_responses(public, private, [path, path])
    response["answers"][0]["score"] = True
    path.write_text(json.dumps(response))
    with pytest.raises(SnapshotError):
        import_responses(public, private, [path])


def test_frozen_pipeline_runs_all_four_groups_in_separate_processes(tmp_path, edit_case, monkeypatch):
    from eval.factorial.__main__ import run
    from eval.factorial.snapshot import create_snapshot
    _, _, _, original, places, _ = edit_case
    monkeypatch.setattr("app.nodes.retrieve.load_pois", lambda: places)
    monkeypatch.setattr("app.nodes.select.load_pois", lambda: places)
    root = Path(__file__).resolve().parents[2]
    if subprocess.run(["git", "rev-parse", "--verify", "HEAD"], cwd=root, capture_output=True).returncode:
        pytest.skip("完整快照集成需要 Git 源版本")
    snapshot = tmp_path / "snapshot"
    create_snapshot(root, snapshot, baseline="HEAD", cases=[{"id": "integration", "split": "regression",
        "profile": original["profile"].model_dump(mode="json")}], restaurant_limit=2)
    with pytest.raises(SnapshotError, match="预算"):
        run(snapshot, tmp_path / "wrong-budget", timeout=1)
    assert not (tmp_path / "wrong-budget").exists()
    with pytest.raises(SnapshotError, match="不可变"):
        run(snapshot, snapshot / "output")
    result = run(snapshot, tmp_path / "result")
    assert len(result["results"]) == 4
    assert all(row["status"] == "ok" for row in result["results"]), [(row["group"], row.get("error")) for row in result["results"]]
    assert all(row["network_attempts"] == row["llm_live_calls"] == 0 for row in result["results"])
    assert len({row["snapshot_sha256"] for row in result["results"]}) == 1
    assert verify_snapshot(snapshot)


def test_inconsistent_complete_state_is_not_accepted_by_replay_check(results_case, tmp_path):
    from eval.factorial.compare import compare
    inputs, rows = results_case
    for row in rows:
        row["itinerary"] = {"days": []}
        row["metrics"] = {"internal_ride_min": 0, "internal_max_ride_min": 0}
    result = {"snapshot_sha256": "same-snapshot", "results": rows, "summaries": {"all": summarize(inputs, rows)}}
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    write_json(first, result)
    write_json(second, result)
    assert compare(first, second)["semantic_matches"] == 4
    result["results"][0]["report"]["complete_executable"] = True
    second.write_text(json.dumps(result))
    with pytest.raises(SnapshotError, match="合取"):
        compare(first, second)
