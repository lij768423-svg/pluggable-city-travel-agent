import csv
import json

import pytest
from pydantic import ValidationError

from app.reasonableness import load_stay_references
from app.schemas import StayEstimate
from scripts.estimate_stay import aggregate, messages_for
from scripts.report_stay import build_audit


def _estimate(identifier=1, low=20, high=50, typical=30):
    return {"poi_id": identifier, "low_min": low, "high_min": high, "typical_min": typical,
            "scope": "仅当前祠堂", "reason": "展区较小", "confidence": "medium"}


def _artifact(tmp_path):
    sample = _estimate()
    place = {**sample, "name": "小祠堂", "source": "llm_estimate", "model": "fixture-model",
             "generated_at": "2026-10-16T00:00:00Z",
             "spread": {"low_min": 0, "high_min": 0, "typical_min": 0, "max_min": 0}, "samples": [sample] * 3}
    path = tmp_path / "reference.json"
    path.write_text(json.dumps({"places": [place]}), encoding="utf-8")
    return path


def _audit(tmp_path, **updates):
    row = {"poi_id": 1, "name": "小祠堂", "数据集区间": "120-210", "LLM区间": "20-50",
           "来源 URL": "https://example.test/guide", "原文摘录": "建议游览半小时",
           "人工结论": "25-40分钟", "核对人": "测试者", "核对日期": "2026-10-16"}
    row.update(updates)
    path = tmp_path / "audit.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(row))
        writer.writeheader()
        writer.writerow(row)
    return path


def test_load_independent_reference(tmp_path):
    assert load_stay_references(_artifact(tmp_path), None) == {1: (20, 50, "llm_estimate")}


@pytest.mark.parametrize("conclusion,expected", [("25-40分钟", (25, 40)), ("采用LLM", (20, 50)),
                                                  ("采用数据集", (120, 210))])
def test_manual_conclusion_takes_priority(tmp_path, conclusion, expected):
    assert load_stay_references(_artifact(tmp_path), _audit(tmp_path, 人工结论=conclusion))[1] == (*expected, "manual")


def test_unreviewed_source_does_not_become_manual(tmp_path):
    assert load_stay_references(_artifact(tmp_path), _audit(tmp_path, 人工结论="", 核对人=""))[1][2] == "llm_estimate"
    with pytest.raises(ValueError, match="缺来源"):
        load_stay_references(_artifact(tmp_path), _audit(tmp_path, 核对人=""))


def test_reference_identity_and_invalid_intervals_fail(tmp_path):
    path = _artifact(tmp_path)
    with pytest.raises(ValueError, match="身份"):
        load_stay_references(path, None, {})
    with pytest.raises(ValidationError):
        StayEstimate.model_validate(_estimate(low=60, high=40))
    with pytest.raises(ValidationError):
        StayEstimate.model_validate(_estimate(low=0))


def test_duplicate_references_and_inverted_manual_range_fail(tmp_path):
    path = _artifact(tmp_path)
    data = json.loads(path.read_text())
    data["places"].append(data["places"][0])
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="重复"):
        load_stay_references(path, None)
    with pytest.raises(ValueError, match="区间"):
        load_stay_references(_artifact(tmp_path), _audit(tmp_path, 人工结论="60-30分钟"))


def test_inconsistent_sample_provenance_fails(tmp_path):
    path = _artifact(tmp_path)
    data = json.loads(path.read_text())
    data["places"][0]["samples"][0]["poi_id"] = 2
    path.write_text(json.dumps(data))
    with pytest.raises(ValidationError, match="样本POI身份"):
        load_stay_references(path, None)


def test_samples_differ_and_contain_no_dataset_duration():
    rows = [{"poi_id": 1, "name": "小祠堂", "type": "历史古迹", "summary": "小型祠堂",
             "price": 10, "opening_hours": "08:00-17:00"}]
    messages = [messages_for(rows, number) for number in (1, 2, 3)]
    assert len({json.dumps(message) for message in messages}) == 3
    assert all("min_hours" not in json.dumps(message) and "recommendmintime" not in json.dumps(message)
               for message in messages)


def test_medians_and_disagreement_are_preserved():
    samples = [_estimate(low=20, high=40, typical=30), _estimate(low=30, high=60, typical=45),
               _estimate(low=90, high=150, typical=120)]
    records = [{"output": {"items": [sample]}, "model": "fixture-model"} for sample in samples]
    result = aggregate([{"poi_id": 1, "name": "小祠堂"}], records, "2026-10-16T00:00:00Z")[0]
    assert (result["low_min"], result["typical_min"], result["high_min"]) == (30, 45, 60)
    assert result["spread"]["typical_min"] == 90 and result["confidence"] == "low"


def test_report_rebuild_preserves_human_conclusion(tmp_path, monkeypatch):
    from app import config

    backend = tmp_path / "backend"
    (backend / "app/assets").mkdir(parents=True)
    (backend / "app/assets/poi_reviews.json").write_text(json.dumps({"places": [{"id": 1, "name": "小祠堂"}]}))
    monkeypatch.setattr(config, "BACKEND_DIR", backend)
    monkeypatch.setattr("scripts.report_stay.audit_selection", lambda: [{"id": 1, "name": "小祠堂"}])
    audit = _audit(tmp_path, 人工结论="25-40分钟", 核对人="用户")
    sources = tmp_path / "sources.json"
    sources.write_text("[]")
    rows = [{"poi_id": 1, "name": "小祠堂", "dataset_low": 120, "dataset_high": 210,
             "low_min": 20, "high_min": 50}]
    result = build_audit(rows, audit, sources)
    with audit.open(encoding="utf-8-sig", newline="") as handle:
        retained = list(csv.DictReader(handle))[0]
    assert result["manual"] == 1
    assert retained["人工结论"] == "25-40分钟" and retained["核对人"] == "用户"
    assert retained["来源 URL"] == "https://example.test/guide"
