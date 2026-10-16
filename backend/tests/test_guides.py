from __future__ import annotations

import json

import pytest

from app.guides import eligible, guide_context, load_corpus, retrieve_guides, validate_corpus
from app.llm import SnapshotLLM, Usage
from app.nodes.retrieve import retrieve_node
from app.nodes.select import select_llm
from app.schemas import UserProfile


def test_pilot_has_traceable_samples_for_its_distribution() -> None:
    corpus = load_corpus()
    distributions = {
        "hangzhou-guides-pilot-2026-10-15-v1": (20, 9),
        "hangzhou-guides-pilot-2026-10-15-v1-public": (11, 1),
    }
    assert (len(corpus.samples), len(corpus.sources)) == distributions[corpus.version]
    assert not validate_corpus(corpus)
    assert all(not source.training_allowed for source in corpus.sources)
    assert all(source.kind != "authorized_xhs" for source in corpus.sources)


def test_withdrawal_and_ambiguous_mapping_excluded() -> None:
    corpus = load_corpus()
    sample = corpus.samples[0]
    assert eligible(sample, corpus)
    next(source for source in corpus.sources if source.id == sample.source_id).withdrawn = True
    assert not eligible(sample, corpus)
    corpus = load_corpus()
    corpus.samples[0].places[0].confidence = 0.6
    assert not eligible(corpus.samples[0], corpus)


def test_corpus_rejects_false_mapping_and_source_leakage() -> None:
    corpus = load_corpus()
    corpus.samples[0].places[0].canonical_name = "另一处地点"
    corpus.samples[1].split = "holdout"
    errors = validate_corpus(corpus)
    assert any("不一致" in error for error in errors)
    assert any("跨知识库" in error for error in errors)


def test_xhs_requires_explicit_permission() -> None:
    corpus = load_corpus()
    corpus.sources[0].kind = "authorized_xhs"
    assert any("书面授权" in error for error in validate_corpus(corpus))


def test_retrieval_respects_candidates_and_season() -> None:
    profile = UserProfile(start_date="2026-11-07", interests=["自然风光"])
    candidates = retrieve_node({"profile": profile})["candidates"]
    evidence = retrieve_guides(candidates, profile)
    assert len(evidence) <= 6
    assert all(set(entry["candidate_ids"]) <= {candidate.poi_id for candidate in candidates} for entry in evidence)
    assert all(entry["id"] != "HZ-G17" for entry in evidence)
    assert guide_context([]) == ""


def test_selection_filters_fabricated_citations() -> None:
    profile = UserProfile(days=1)
    candidates = retrieve_node({"profile": profile})["candidates"]
    identifier = candidates[0].poi_id
    class FakeLLM:
        usage = Usage()
        def complete(self, messages: list[dict], **kwargs) -> str:
            if "攻略证据数据" in messages[1]["content"]:
                assert '"guide_ids"' in messages[0]["content"]
            return json.dumps({"selected": [{"id": identifier, "reason": "test", "guide_ids": ["real", "wrong-poi", "invented"]}]})
    evidence = [{"id": "real", "candidate_ids": [identifier]}, {"id": "wrong-poi", "candidate_ids": [-1]}]
    selected = select_llm(candidates, profile, 1, FakeLLM(), evidence)
    assert selected[0].guide_ids == ["real"]
    assert select_llm(candidates, profile, 1, FakeLLM())[0].guide_ids == []


def test_offline_snapshot_fails_closed_and_detects_corruption(tmp_path, monkeypatch) -> None:
    class FakeLLM:
        usage = Usage()
        def complete(self, messages: list[dict], **kwargs) -> str:
            return '{"selected":[]}'
    monkeypatch.delenv("TTA_OFFLINE", raising=False)
    arguments = {"tag": "select", "temperature": 0.2, "max_tokens": 3000}
    snapshot = SnapshotLLM(tmp_path, FakeLLM())
    snapshot.complete([], **arguments)
    monkeypatch.setenv("TTA_OFFLINE", "1")
    assert SnapshotLLM(tmp_path).complete([], **arguments) == '{"selected":[]}'
    with pytest.raises(RuntimeError, match="禁止联网"):
        SnapshotLLM(tmp_path, FakeLLM()).complete([{"role":"user","content":"new"}], **arguments)
    path = next(tmp_path.glob("*.json"))
    record = json.loads(path.read_text())
    record["content"] = "changed"
    path.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="损坏"):
        SnapshotLLM(tmp_path).complete([], **arguments)
