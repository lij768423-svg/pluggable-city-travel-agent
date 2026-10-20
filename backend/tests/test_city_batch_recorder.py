"""评估录制器：复用旧响应时额度用尽也要落记录，回放在同一位置重现降级。"""
import json

import pytest

from app.amap import AmapBudgetExceeded
from app.amap_limits import RequestBudget
from eval.city_batch20 import RecordedRouter


def test_budget_exhaustion_during_reuse_is_recorded_and_replayed(tmp_path, monkeypatch):
    monkeypatch.setenv("TTA_OFFLINE", "1")  # 只走复用分支，不需要真 key
    previous = tmp_path / "previous.jsonl"
    previous.write_text("\n".join(json.dumps({"path": "p", "params": {"n": n}, "response": {"n": n}, "http_attempts": 1})
                                  for n in (1, 2)) + "\n")
    record = tmp_path / "amap.jsonl"
    capture = RecordedRouter(record, reuse_path=previous)
    capture.budget = RequestBudget(1)
    assert capture._get("p", {"n": 1}) == {"n": 1}
    with pytest.raises(AmapBudgetExceeded):
        capture._get("p", {"n": 2})
    entries = [json.loads(line) for line in record.read_text().splitlines()]
    assert len(entries) == 2 and entries[1]["budget_exceeded"] and entries[1]["http_attempts"] == 0

    replay = RecordedRouter(record, replay=True)
    replay.budget = RequestBudget(1)
    assert replay._get("p", {"n": 1}) == {"n": 1}
    with pytest.raises(AmapBudgetExceeded):
        replay._get("p", {"n": 2})
