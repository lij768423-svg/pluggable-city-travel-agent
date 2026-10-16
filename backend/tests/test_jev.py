import json

import httpx
import pytest

from app import config
from app.llm import MockLLM, evaluate_jev
from app.nodes import select
from app.nodes.retrieve import retrieve
from app.schemas import UserProfile


@pytest.fixture
def jev_environment(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(config, "TYPESAFE_API_KEY", "test-credential")
    monkeypatch.setattr(config, "TYPESAFE_PROXY_URL", "http://127.0.0.1:9988")
    monkeypatch.delenv("TTA_OFFLINE", raising=False)


def answer(score=2, confidence=0.9):
    return {"type": "score", "score": score, "confidence": confidence}


def test_batch_request_and_cache_are_isolated_by_model(monkeypatch, jev_environment):
    requests = []
    def post(url, **kwargs):
        requests.append((url, kwargs))
        return httpx.Response(200, json={"model": "jev-test", "answers": {key: answer() for key in kwargs["json"]["questions"]},
                                       "usage": {"input_tokens": 100, "output_tokens": 10}})
    monkeypatch.setattr(httpx, "post", post)
    questions = {"1": {}, "2": {}}
    first = evaluate_jev({"preferences": "安静"}, questions)
    cached = evaluate_jev({"preferences": "安静"}, questions)
    assert not first["cached"] and cached["cached"]
    assert len(requests) == 1 and len(requests[0][1]["json"]["questions"]) == 2
    assert requests[0][0] == "https://api.typesafe.ai/v1/systemone"
    assert requests[0][1]["follow_redirects"] is False
    assert requests[0][1]["proxy"] == "http://127.0.0.1:9988"
    assert requests[0][1]["trust_env"] is False
    monkeypatch.setattr(config, "TYPESAFE_MODEL", "other-version")
    assert not evaluate_jev({"preferences": "安静"}, questions)["cached"]


@pytest.mark.parametrize("answers", [{}, {"1": answer(), "999": answer()}, {"1": answer(float("nan"))},
                                      {"1": answer(confidence=2)}, {"1": answer(True)}, {"1": {"type": "choice"}}])
def test_malformed_or_incomplete_answers_fail_closed(monkeypatch, jev_environment, answers):
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: httpx.Response(
        200, content=json.dumps({"model": "test", "answers": answers})))
    with pytest.raises(RuntimeError, match="jev_invalid_response"):
        evaluate_jev({}, {"1": {}})
    assert not list((config.DATA_DIR / "jev_cache").glob("*.json"))


def test_http_error_does_not_expose_body_or_credentials(monkeypatch, jev_environment):
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: httpx.Response(429, text="test-credential private payload"))
    with pytest.raises(RuntimeError, match="^jev_http_429$"):
        evaluate_jev({}, {"1": {}})


def test_timeout_is_sanitized(monkeypatch, jev_environment):
    def fail(*args, **kwargs):
        raise httpx.ReadTimeout("test-credential")
    monkeypatch.setattr(httpx, "post", fail)
    with pytest.raises(RuntimeError, match="^jev_network_error$"):
        evaluate_jev({}, {"1": {}})


def test_proxy_failure_does_not_retry_direct_or_expose_proxy(monkeypatch, jev_environment):
    attempts = []
    def fail(*args, **kwargs):
        attempts.append(kwargs)
        raise httpx.ProxyError("http://private-user:private-password@proxy")
    monkeypatch.setattr(httpx, "post", fail)
    with pytest.raises(RuntimeError, match="^jev_network_error$"):
        evaluate_jev({}, {"1": {}})
    assert len(attempts) == 1 and attempts[0]["proxy"] == config.TYPESAFE_PROXY_URL


@pytest.mark.parametrize("status,cached,expected", [
    ("fallback", False, "Jev 调用失败"),
    ("completed", True, "已复用 Jev 评分"),
    ("completed", False, "Jev 实时评分成功"),
])
def test_progress_distinguishes_failure_cache_and_live(status, cached, expected):
    from app.progress import stage_event
    event = stage_event("select", "completed", {"selected": [], "selection_stats": {
        "status": status, "cached": cached, "seconds": 0.4}})
    assert expected in event["detail"]
    if status == "fallback":
        assert "实时评分成功" not in event["detail"]
        assert "已由原模型完成选点" in event["detail"]


def test_offline_never_calls_network(monkeypatch, jev_environment):
    monkeypatch.setenv("TTA_OFFLINE", "1")
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: pytest.fail("offline network"))
    with pytest.raises(RuntimeError, match="jev_offline_cache_miss"):
        evaluate_jev({}, {"1": {}})


def test_must_visit_and_low_confidence_keep_rule_provenance(monkeypatch):
    profile = UserProfile(must_visit=["宋城", "灵隐寺"])
    candidates = retrieve(profile)
    captured = {}
    def evaluate(state, questions, **kwargs):
        captured.update(state)
        return {"model": "test", "answers": {key: answer(0, 0.2) for key in questions},
                "usage": {}, "seconds": 0.5, "cached": False}
    monkeypatch.setattr(select, "evaluate_jev", evaluate)
    chosen, stats = select.select_jev(candidates, profile, 1)
    must = {candidate.poi_id for candidate in candidates if candidate.breakdown.get("must_visit")}
    assert must <= {item.poi_id for item in chosen}
    assert all(item.source == "rule" for item in chosen)
    assert "hotel" not in captured["preferences"]
    assert stats["confident_candidates"] == 0


def test_failure_returns_to_existing_model(monkeypatch):
    profile = UserProfile()
    candidates = retrieve(profile)
    def fail(*args, **kwargs):
        raise RuntimeError("jev_network_error")
    monkeypatch.setattr(select, "evaluate_jev", fail)
    llm = MockLLM({"select": '{"selected": []}'})
    result = select.select_node({"profile": profile, "candidates": candidates, "llm": llm, "select_mode": "jev"})
    assert result["selection_stats"]["fallback"] == "llm"
    assert len(llm.calls) == 1 and result["selected"]


def test_api_rejects_mixed_experiments():
    from app.api import PlanRequest
    from pydantic import ValidationError
    assert PlanRequest(select_mode="jev").select_mode == "jev"
    with pytest.raises(ValidationError):
        PlanRequest(select_mode="jev", guide_mode="pilot")
