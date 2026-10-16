from __future__ import annotations

import argparse
import hashlib
import html
import json
import random
from pathlib import Path


def public_itinerary(result: dict) -> list[dict]:
    return [{"day": day["day"], "items": [{"name": item["name"], "kind": item["kind"],
              "start": item["start"], "end": item["end"], "travel_min": item["travel_min"],
              "travel_mode": item["travel_mode"], "cost": item["cost"]} for item in day["items"]],
              "return_travel_min": day["return_travel_min"]} for day in result["itinerary"]["days"]]


def build(directory: Path) -> None:
    results = json.loads((directory / "results.json").read_text())
    cohort = json.loads((directory / "cases.json").read_text())
    by_case = {(result["case"], result["mode"]): result for result in results}
    experiment = hashlib.sha256((directory / "manifest.json").read_bytes()).hexdigest()
    eligible = [case for case in cohort if all(by_case[(case["id"], mode)]["ok"] for mode in ("off", "pilot"))]
    if not eligible:
        raise ValueError("没有可比较的配对结果")
    randomizer = random.Random(20261015)
    first_left = {case["id"]: randomizer.choice(("off", "pilot")) for case in eligible}
    exposures = {case["id"]: 0 for case in eligible}
    companion_labels = {"solo": "独自旅行", "friends": "朋友结伴", "family_kids": "亲子出行", "elderly": "长辈同行"}
    assignments, packets = {}, {}
    for participant in range(20):
        code = f"P{participant+1:02}"
        assignments[code], packets[code] = [], []
        for offset in range(3):
            case = eligible[(participant*3+offset) % len(eligible)]
            modes = [first_left[case["id"]], "pilot" if first_left[case["id"]] == "off" else "off"]
            if exposures[case["id"]] % 2:
                modes.reverse()
            exposures[case["id"]] += 1
            token = f"Q-{randomizer.getrandbits(40):010x}"
            assignments[code].append({"pair": token, "case": case["id"], "left": modes[0], "right": modes[1]})
            label = case["label"]
            for raw, translated in companion_labels.items():
                label = label.replace(raw, translated)
            packets[code].append({"pair": token, "label": label, "profile": case["profile"],
                                  "left": public_itinerary(by_case[(case["id"],modes[0])]),
                                  "right": public_itinerary(by_case[(case["id"],modes[1])])})
    (directory / "blind-key-private.json").write_text(json.dumps({"experiment":experiment,"assignments":assignments},ensure_ascii=False,indent=2))
    payload = json.dumps({"experiment":experiment,"packets":packets},ensure_ascii=False).replace("<","\\u003c")
    template = Path(__file__).with_name("guide_blind_template.html").read_text()
    (directory / "blind-review.html").write_text(template.replace("__PAYLOAD__",payload))
    print("20个匿名参与代码 P01–P20，每人3组；实际回收0。请不要把 blind-key-private.json 交给评审。")


def score(directory: Path, responses: Path) -> dict:
    key = json.loads((directory / "blind-key-private.json").read_text())
    seen: set[str] = set()
    accepted, rejected = [], []
    for path in sorted(responses.glob("*.json")):
        try:
            response = json.loads(path.read_text())
            code = response["participant"]
            assert code in key["assignments"] and code not in seen, "重复或未知参与代码"
            assert response["experiment"] == key["experiment"] and response["consent"] is True, "版本或知情同意不符"
            expected = {pair["pair"]:pair for pair in key["assignments"][code]}
            assert len(response["answers"]) == 3 and {answer["pair"] for answer in response["answers"]} == set(expected), "配对结果不完整"
            votes = []
            for answer in response["answers"]:
                assert answer["choice"] in {"left","right","tie","neither"}
                for metric in ("fit", "route", "meals"):
                    assert all(type(answer[metric][side]) is int and 1 <= answer[metric][side] <= 5 for side in ("left","right")), "评分无效"
                votes.append(expected[answer["pair"]][answer["choice"]] if answer["choice"] in {"left","right"} else answer["choice"])
            seen.add(code)
            accepted.append({"participant":code,"votes":votes,"pilot_preferred":votes.count("pilot") > votes.count("off")})
        except (AssertionError,KeyError,ValueError,TypeError) as error:
            rejected.append({"file":path.name,"reason":str(error)})
    summary = json.loads((directory / "summary.json").read_text())
    return {"participants":len(accepted),"accepted":accepted,"rejected":rejected,
            "pilot_preferred_participants":sum(item["pilot_preferred"] for item in accepted),
            "human_threshold_met":len(accepted)>=15 and sum(item["pilot_preferred"] for item in accepted)>len(accepted)/2,
            "automatic_new_failures":summary["new_failure_cases"],
            "promotion":"manual_review_required_real_route_validation_pending",
            "note":"不自动改变正式开关；未填写不能记作平票；真实参与者身份与独立完成由实验组织者核验。"}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory",type=Path)
    parser.add_argument("--responses",type=Path)
    args=parser.parse_args()
    if args.responses:
        report=score(args.directory,args.responses)
        (args.directory / "blind-summary.json").write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps(report,ensure_ascii=False,indent=2))
    else:
        build(args.directory)


if __name__ == "__main__":
    main()
