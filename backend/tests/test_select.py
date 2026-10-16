from app.llm import MockLLM
from app.nodes.select import select_llm
from app.schemas import Candidate, UserProfile


def test_missing_required_place_does_not_evict_another_required_place(monkeypatch):
    monkeypatch.setattr("app.nodes.select.candidate_lines", lambda candidates: "test candidates")
    candidates = [Candidate(poi_id=place_id, score=1, breakdown={"must_visit": float(place_id in (2, 3))})
                  for place_id in (1, 2, 3)]
    model = MockLLM({"select": '{"selected":[{"id":1,"reason":"optional"},{"id":2,"reason":"required"}]}'})
    selected = select_llm(candidates, UserProfile(), 2, model)
    assert {selection.poi_id for selection in selected} == {2, 3}
    assert next(selection for selection in selected if selection.poi_id == 2).reason == "required"


def test_required_places_can_exceed_the_target_count(monkeypatch):
    monkeypatch.setattr("app.nodes.select.candidate_lines", lambda candidates: "test candidates")
    candidates = [Candidate(poi_id=place_id, score=1, breakdown={"must_visit": 1}) for place_id in (1, 2, 3)]
    model = MockLLM({"select": '{"selected":[{"id":1,"reason":"required"}]}'})
    selected = select_llm(candidates, UserProfile(), 1, model)
    assert {selection.poi_id for selection in selected} == {1, 2, 3}
