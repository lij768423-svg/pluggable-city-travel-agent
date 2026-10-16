from app.graph import run_plan
from app.progress import Ticker


def test_ticker_counts_distinct_items_and_throttles(monkeypatch):
    monkeypatch.setattr("app.progress.time.monotonic", lambda: 0.0)
    seen = []
    ticker = Ticker(seen.append, "meals", interval=0)
    ticker.track("r", [1, 2, 2], "已比较 {n} 家餐厅")
    ticker.track("r", [2, 3], "已比较 {n} 家餐厅")
    assert seen == ["已比较 2 家餐厅", "已比较 3 家餐厅"]
    quiet = Ticker(seen.append, "meals", interval=999)
    quiet.say("第一条")
    quiet.say("被限流吃掉")
    assert seen[-1] == "第一条"
    monkeypatch.setattr("app.progress.time.monotonic", lambda: 999.0)
    quiet.say("下一条")
    assert seen[-2:] == ["第一条", "下一条"]
    assert Ticker(seen.append, "meals").wants({"meals"})
    assert not Ticker(seen.append, "schedule").wants({"meals"})


def test_each_stage_reports_only_its_own_live_counts(mock_llm):
    """执行途中的实时数量：排程报交通段数，餐饮报餐厅家数，校验报天数，互不串台。"""
    events = []
    run_plan("", {"days": 3, "interests": ["人文历史"], "budget": 2000}, llm=mock_llm,
             select_mode="rule", progress=events.append)
    live: dict[str, list[str]] = {}
    for event in events:
        if event.get("type") == "progress" and event["status"] == "running" and event["detail"]:
            live.setdefault(event["stage"], []).append(event["detail"])
    assert live["schedule"] and all("段交通" in detail for detail in live["schedule"])
    assert live["meals"] and all("家餐厅" in detail for detail in live["meals"])
    assert live["validate"] and all("天的安排" in detail for detail in live["validate"])
    assert not {"parse", "retrieve", "select", "finalize"} & set(live)
