"""P2：任务固定目录版本、线程/会话隔离与网页/MCP共用；全部合成或离线输入，不联网。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from app import config, poi_catalog as catalogs
from app.data import base_digest, load_base_pois, load_pois
from app.graph import run_plan
from app.mcp_domain import data_version
from app.planning_context import poi_catalog
from app.poi_catalog import (CatalogUnavailable, PlanningCatalog, SupplementCatalog, base_catalog,
                             current_catalog, published_catalog, register_catalog, resolve_catalog,
                             result_catalog, use_catalog)
from app.presentation import serialize_plan
from tests.test_mcp import OWNER, PROFILE, service, verified_result  # noqa: F401 — 复用MCP合成夹具
from tests.test_poi_supplement import TODAY, record

REQUEST = {"select_mode": "llm", "guide_mode": "off", "enable_repair": True, "input": {"form": PROFILE}}


def synthetic(label: str) -> PlanningCatalog:
    """同一原库ID改名，便于确认读到的是哪个目录；摘要为合成值，不冒充真实发布。"""
    pois = {key: poi.model_copy(update={"name": f"{poi.name}·{label}"}) for key, poi in load_base_pois().items()}
    return register_catalog(PlanningCatalog.build(pois, f"synthetic-base-{label}", f"synthetic-supplement-{label}"))


def poi_names(result: dict) -> list[str]:
    return [item.name for day in result["itinerary"].days for item in day.items if item.kind == "poi"]


def test_base_only_default_keeps_original_catalog_and_identity():
    assert config.POI_CATALOG_MODE == "base_only"
    assert poi_catalog.get() is None
    assert load_pois() is load_base_pois()
    catalog = current_catalog()
    assert catalog is base_catalog() is published_catalog()
    assert catalog.version == base_digest() == data_version()
    assert catalog.reference() == {"city": "hangzhou", "version": base_digest(), "base_digest": base_digest(), "supplement_digest": None}
    # 旧结果没有目录记录时按原库解析
    assert resolve_catalog(None) is catalog and resolve_catalog({}) is catalog


def test_context_is_per_thread_and_not_inherited_by_executor():
    a, b = synthetic("A"), synthetic("B")
    barrier = threading.Barrier(2)
    seen = {}

    def read(catalog):
        with use_catalog(catalog):
            barrier.wait(5)
            seen[catalog.version] = load_pois()[1].name
            barrier.wait(5)
            return current_catalog().version

    with ThreadPoolExecutor(2) as pool:
        versions = list(pool.map(read, [a, b]))
    assert versions == [a.version, b.version]
    assert seen[a.version].endswith("·A") and seen[b.version].endswith("·B")
    with use_catalog(a), ThreadPoolExecutor(1) as pool:
        # 线程池不会自动带上调用方目录：后台任务必须显式安装
        assert pool.submit(lambda: current_catalog().version).result() == base_digest()
    assert current_catalog().version == base_digest()


def test_web_plan_pins_catalog_through_graph_and_serializes_by_result(mock_llm):
    a, b = synthetic("A"), synthetic("B")
    results = {}

    def plan(catalog):
        with use_catalog(catalog):
            results[catalog.version] = run_plan(form=PROFILE, llm=mock_llm, travel_source="estimate", use_solver=False)

    threads = [threading.Thread(target=plan, args=(catalog,)) for catalog in (a, b)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    ra, rb = results[a.version], results[b.version]
    assert ra["poi_catalog"] == a.reference() and rb["poi_catalog"] == b.reference()
    assert poi_names(ra) and all(name.endswith("·A") for name in poi_names(ra))
    assert poi_names(rb) and all(name.endswith("·B") for name in poi_names(rb))
    # 展示/历史在其他目录上下文中仍按结果自己的版本解析
    with use_catalog(b):
        shown = serialize_plan(ra, REQUEST, "plan-a", 0)
    assert all(c["name"].endswith("·A") for c in shown["candidates"])
    assert shown["poi_catalog"] == a.reference()


def test_base_only_plan_output_has_no_catalog_field(mock_llm):
    result = run_plan(form=PROFILE, llm=mock_llm, travel_source="estimate", use_solver=False)
    assert result["poi_catalog"]["supplement_digest"] is None
    assert "poi_catalog" not in serialize_plan(result, REQUEST, "plan-base", 0)
    legacy = {key: value for key, value in result.items() if key != "poi_catalog"}
    assert serialize_plan(legacy, REQUEST, "plan-base", 0)["candidates"] == serialize_plan(result, REQUEST, "plan-base", 0)["candidates"]


def test_result_catalog_rejects_tampered_or_unknown_version():
    a = synthetic("A")
    with result_catalog({"poi_catalog": a.reference()}):
        assert load_pois()[1].name.endswith("·A")
    with pytest.raises(CatalogUnavailable):
        resolve_catalog({**a.reference(), "version": "0" * 64})
    with pytest.raises(CatalogUnavailable):
        resolve_catalog({"version": base_digest(), "base_digest": "other-base", "supplement_digest": None})
    with pytest.raises(CatalogUnavailable):
        register_catalog(PlanningCatalog.build({}, "synthetic-base-A", "synthetic-supplement-A"))


def test_with_supplement_pins_published_version_and_new_publish_only_affects_next(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "POI_CATALOG_MODE", "with_supplement")
    monkeypatch.setattr(config, "POI_SUPPLEMENT_DIR", tmp_path)
    assert published_catalog() is base_catalog()  # 尚未发布
    store = SupplementCatalog(tmp_path)
    store.register([record()])
    first = store.publish(as_of=TODAY, base_digest=base_digest())
    v1 = published_catalog()
    assert v1.supplement_digest == first.digest and v1.version != base_digest()
    # 准入的补充记录叠加在原库之上，原库条目不变
    assert 1_000_000 in v1.pois and 1_000_000 in load_pois()
    assert {k: v for k, v in v1.pois.items() if k != 1_000_000} == load_base_pois()
    with use_catalog(v1):
        store.register([record(name="新版本公园")])
        second = store.publish(as_of=TODAY, base_digest=base_digest())
        assert current_catalog() is v1
    v2 = published_catalog()
    assert v2.supplement_digest == second.digest != first.digest
    # 进程内登记被清空后，仍能从不可变版本文件按记录原样复现
    monkeypatch.setattr(catalogs, "_CATALOGS", {})
    assert resolve_catalog(v1.reference()).version == v1.version
    store.register([record(name="另一版本")])
    stale = store.publish(as_of=TODAY, base_digest="synthetic-base")
    with pytest.raises(CatalogUnavailable, match="原库摘要"):
        catalogs.supplement_catalog(stale.digest)
    monkeypatch.setattr(config, "POI_CATALOG_MODE", "everything")
    with pytest.raises(ValueError, match="未知景点目录模式"):
        published_catalog()


def test_supplement_plan_records_catalog_in_output(tmp_path, monkeypatch, mock_llm):
    monkeypatch.setattr(config, "POI_CATALOG_MODE", "with_supplement")
    monkeypatch.setattr(config, "POI_SUPPLEMENT_DIR", tmp_path)
    store = SupplementCatalog(tmp_path)
    store.register([record()])
    snapshot = store.publish(as_of=TODAY, base_digest=base_digest())
    result = run_plan(form=PROFILE, llm=mock_llm, travel_source="estimate", use_solver=False)
    assert result["poi_catalog"]["supplement_digest"] == snapshot.digest
    shown = serialize_plan(result, REQUEST, "plan-supplement", 0)
    assert shown["poi_catalog"] == result["poi_catalog"]
    assert set(shown["poi_catalog"]) == {"city", "version", "base_digest", "supplement_digest"}
    assert shown["poi_catalog"]["city"] == "hangzhou"


def test_mcp_job_keeps_prepared_catalog_after_publish_and_worker_installs_it(service, monkeypatch):  # noqa: F811
    a, b = synthetic("A"), synthetic("B")
    published = {"catalog": a}
    monkeypatch.setattr("app.mcp_service.published_catalog", lambda city="hangzhou": published["catalog"])
    seen = {}
    done = threading.Event()

    def execute(profile, candidates, selected, report, progress=None):
        seen["version"] = current_catalog().version
        seen["name"] = load_pois()[selected[0].poi_id].name
        done.set()
        return {**verified_result(), "poi_catalog": current_catalog().reference()}

    monkeypatch.setattr("app.mcp_service.run_selected", execute)
    monkeypatch.setattr("app.mcp_service.delivery_status", lambda *a, **k: {"status": "data_unavailable", "code": "synthetic"})
    first = service.prepare(OWNER, PROFILE, "prepare-catalog-a")
    assert all(c["name"].endswith("·A") for c in first["candidates"])
    published["catalog"] = b  # 其他人发布新版
    queued = service.generate(OWNER, first["job_id"], first["preparation_revision"], first["candidate_set_id"],
                              [{"poi_id": c["poi_id"], "reason": "符合用户兴趣"} for c in first["candidates"][:2]],
                              "generate-catalog-a")
    assert queued["status"] == "queued"
    assert done.wait(10)
    assert seen["version"] == a.version and seen["name"].endswith("·A")
    second = service.prepare(OWNER, PROFILE, "prepare-catalog-b")
    assert all(c["name"].endswith("·B") for c in second["candidates"])
    assert service.jobs[second["job_id"]].version == b.version
    assert service.jobs[first["job_id"]].version == a.version


def test_mcp_session_preview_and_trip_resolve_session_catalog(service, monkeypatch, mock_llm):  # noqa: F811
    a, b = synthetic("A"), synthetic("B")
    with use_catalog(a):
        result = run_plan(form=PROFILE, llm=mock_llm, travel_source="estimate", use_solver=False)
    session = service.sessions.create(result, REQUEST, OWNER)
    with use_catalog(b):
        trip = service.get_trip(OWNER, session.plan_id)
    names = [item["name"] for day in trip["plan"]["days"] for item in day["items"] if item["kind"] == "poi"]
    assert names and all(name.endswith("·A") for name in names)
    from app.meal_edits import build_preview
    from app.schemas import MealEditPreviewRequest
    day = next(d for d in result["itinerary"].days if any(i.kind == "meal" and i.alternatives for i in d.items))
    index, item = next((n, i) for n, i in enumerate(day.items) if i.kind == "meal" and i.alternatives)
    seen = []
    import app.meal_edits as meal_edits
    original = meal_edits.swap_meal

    def spy(*args, **kwargs):
        seen.append(current_catalog().version)
        return original(*args, **kwargs)

    monkeypatch.setattr(meal_edits, "swap_meal", spy)
    request = MealEditPreviewRequest(action="replace", day=day.day, item_index=index,
                                     restaurant_id=item.alternatives[0].ref_id, revision=0)
    with use_catalog(b):
        try:
            build_preview(result, request)
        except Exception:  # noqa: BLE001 — 只核对预览使用的目录，不判断换餐本身能否排入
            pass
    assert seen == [a.version]
