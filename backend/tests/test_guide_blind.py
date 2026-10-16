from __future__ import annotations

import json

from eval.guide_blind import build, score


def test_blind_scores_require_complete_unique_matching_responses(tmp_path) -> None:
    responses = tmp_path / "responses"
    responses.mkdir()
    assignments = [{"pair": f"Q{index}", "case": "case", "left": "pilot", "right": "off"} for index in range(3)]
    (tmp_path / "blind-key-private.json").write_text(json.dumps({"experiment":"frozen", "assignments":{"P01":assignments}}))
    (tmp_path / "summary.json").write_text(json.dumps({"new_failure_cases":[]}))
    empty = score(tmp_path, responses)
    assert empty["participants"] == 0 and not empty["human_threshold_met"]
    response = {"participant":"P01", "experiment":"frozen", "consent":True,
                "answers":[{"pair":f"Q{index}", "choice":"left", **{metric:{"left":4,"right":3} for metric in ("fit","route","meals")}} for index in range(3)]}
    (responses / "a.json").write_text(json.dumps(response))
    (responses / "b-duplicate.json").write_text(json.dumps(response))
    report = score(tmp_path, responses)
    assert report["participants"] == 1
    assert report["pilot_preferred_participants"] == 1
    assert len(report["rejected"]) == 1 and not report["human_threshold_met"]
    response["experiment"] = "different"
    (responses / "c-other-version.json").write_text(json.dumps(response))
    assert len(score(tmp_path, responses)["rejected"]) == 2


def test_blind_packets_counterbalance_each_case_without_exposing_methods(tmp_path) -> None:
    cohort = [{"id": f"C{index}", "label": "湖岸·1天·family_kids", "profile": {}} for index in range(24)]
    results = [{"case": case["id"], "mode": mode, "ok": True, "itinerary": {"days": []}}
               for case in cohort for mode in ("off", "pilot")]
    (tmp_path / "cases.json").write_text(json.dumps(cohort))
    (tmp_path / "results.json").write_text(json.dumps(results))
    (tmp_path / "manifest.json").write_text("{}")
    build(tmp_path)
    key = json.loads((tmp_path / "blind-key-private.json").read_text())
    assignments = [pair for assigned in key["assignments"].values() for pair in assigned]
    assert len(key["assignments"]) == 20 and len(assignments) == 60
    assert len({pair["pair"] for pair in assignments}) == 60
    for case in cohort:
        sides = [pair["left"] for pair in assignments if pair["case"] == case["id"]]
        assert set(sides) == {"off", "pilot"}
        assert abs(sides.count("off") - sides.count("pilot")) <= 1
    page = (tmp_path / "blind-review.html").read_text()
    assert "亲子出行" in page and "family_kids" not in page
    assert '"left": "off"' not in page and '"left": "pilot"' not in page
