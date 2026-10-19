"""独立补充资料的发布检查与逐旅行日期准入；不调用外部服务。"""
from __future__ import annotations

from datetime import date, timedelta
import json
from pathlib import Path

from .geo import gcj02_to_bd09, haversine_km
from .schemas import (POI, FieldEvidence, OpeningRules, PlanningAdmission, SupplementCoordinate,
                      SupplementRecord)

REQUIRED_EVIDENCE = ("position", "entrance", "ticket", "opening", "duration")
FACT_FIELDS = ("position", "entrance", "ticket", "opening")
PROVIDER_POLICY = json.loads((Path(__file__).parent / "assets/poi_provider_policy.json").read_text())


def persistence_issues(record: SupplementRecord) -> list[str]:
    """发布之前先限制可落盘来源；审阅不改变提供商资料权属。"""
    issues = []
    for field, evidence in record.evidence.items():
        if (not evidence.storage_allowed or evidence.origin in PROVIDER_POLICY["prohibited_origins"]
                or any(domain in evidence.reference.casefold()
                       for domain in PROVIDER_POLICY["prohibited_reference_domains"])):
            issues.append(f"{field}:来源不允许持久化")
    for field in REQUIRED_EVIDENCE:
        value = {
            "position": record.position, "entrance": record.entrance,
            "ticket": record.ticket_price_upper,
            "opening": record.weekly_opening or record.date_opening,
            "duration": record.duration_min_hours,
        }[field]
        if value is not None and value != {} and field not in record.evidence:
            issues.append(f"{field}:已有字段缺少可持久化来源")
    return issues


def coordinate_bd09(point: SupplementCoordinate) -> tuple[float, float]:
    if point.crs == "BD09":
        return point.lat, point.lon
    if point.crs == "GCJ02":
        return gcj02_to_bd09(point.lat, point.lon)
    raise ValueError("未支持的坐标系，不进行猜测转换")


def publication_issues(record: SupplementRecord, *, as_of: date) -> list[str]:
    """published只审核基础资料；不保证任意日期营业。"""
    issues = persistence_issues(record)
    if record.kind != "attraction":
        issues.append("设施/组内子地点不能作为独立景点发布")
    for field in ("position", "entrance"):
        point = getattr(record, field)
        if point is None:
            issues.append(f"{field}:缺坐标")
        elif point.crs not in ("BD09", "GCJ02"):
            issues.append(f"{field}:不支持的坐标系")
    if record.position and record.entrance and not any("坐标" in x for x in issues):
        if haversine_km(*coordinate_bd09(record.position), *coordinate_bd09(record.entrance)) > 3:
            issues.append("入口与地点相距超过3公里，需要人工确认身份与游览范围")
    if record.ticket_price_upper is None:
        issues.append("ticket:缺已知票价上界，不能当免费")
    if record.ticket_price_upper == 0 or record.ticket_price_exact == 0:
        evidence = record.evidence.get("ticket")
        if evidence is None or evidence.authority != "official":
            issues.append("ticket:免费需明确官方声明，不能由消费均价或未知推导")
    if not record.weekly_opening and not record.date_opening:
        issues.append("opening:开放时段未知，不能当全天")
    if (record.duration_min_hours is None or record.duration_max_hours is None
            or not record.duration_scope.strip()):
        issues.append("duration:缺明确游览范围与规划时长建议")
    if record.source_conflicts:
        issues.append("存在未解决的来源冲突")
    for field in REQUIRED_EVIDENCE:
        evidence = record.evidence.get(field)
        if evidence is None:
            issues.append(f"{field}:缺字段证据")
            continue
        if evidence.reviewed_at is None or evidence.reviewed_at > as_of:
            issues.append(f"{field}:未审阅或审阅日在未来")
        if evidence.source_date and evidence.source_date > as_of:
            issues.append(f"{field}:来源日在未来")
        if evidence.observed_at and evidence.observed_at > as_of:
            issues.append(f"{field}:观测日在未来")
        if field in FACT_FIELDS and (evidence.basis not in ("observed", "manual_review")
                                    or evidence.origin == "project_planning"):
            issues.append(f"{field}:估计/规划建议不能当事实")
        if field in ("ticket", "opening") and (
            evidence.valid_from is None or evidence.valid_until is None
        ):
            issues.append(f"{field}:适用有效期未确认")
        if evidence.valid_until and evidence.valid_until < as_of:
            issues.append(f"{field}:证据已过期")
    return sorted(set(issues))


def admit_supplement(
    record: SupplementRecord, travel_dates: list[date], *, as_of: date
) -> PlanningAdmission:
    issues = publication_issues(record, as_of=as_of)
    if record.state != "published":
        issues.append("资料尚未发布或已归档")
    if not travel_dates:
        issues.append("旅行日期缺失，不能核对新增资料适用性")
    result = PlanningAdmission(canonical_key=record.canonical_key, issues=issues)
    if issues:
        return result
    for day in sorted(set(travel_dates)):
        key = day.isoformat()
        reasons = []
        if day < as_of:
            reasons.append("旅行日期早于核对日期")
        for field in REQUIRED_EVIDENCE:
            evidence = record.evidence[field]
            if evidence.valid_from and day < evidence.valid_from:
                reasons.append(f"{field}:尚未生效")
            if evidence.valid_until and day > evidence.valid_until:
                reasons.append(f"{field}:超过已确认有效期")
        if key in record.date_opening:
            intervals = record.date_opening[key]
        else:
            if day.weekday() not in record.weekly_opening:
                reasons.append("当日开放时段未知，未列出不代表闭园或全天")
            intervals = record.weekly_opening.get(day.weekday(), [])
        if day in record.closed_dates:
            intervals = []
        usable = [x for x in intervals if x.end - x.start >= record.duration_min_hours * 60]
        if not usable:
            reasons.append("当日闭园或没有容纳建议停留的连续开放区间")
        if reasons:
            result.rejected_dates[key] = sorted(set(reasons))
        else:
            result.allowed_dates.append(day)
            result.intervals[key] = usable
    result.admitted = bool(result.allowed_dates)
    if result.admitted:
        result.ticket_price_upper = record.ticket_price_upper
        result.ticket_price_exact = record.ticket_price_exact
        result.duration_min_hours = record.duration_min_hours
        result.duration_max_hours = record.duration_max_hours
        for field in ("position", "entrance"):
            lat, lon = coordinate_bd09(getattr(record, field))
            setattr(result, field, SupplementCoordinate(lat=lat, lon=lon, crs="BD09"))
    return result


# 逐日准入的最长展开范围；有效期更长的资料也只列出这段日期，到期前须重新发布
HORIZON_DAYS = 366
EVIDENCE_BASIS = {"observed": "observed", "manual_review": "manual_review",
                  "project_recommendation": "curator_inference", "model_estimate": "model_estimate"}


def admitted_poi(record: SupplementRecord, *, as_of: date) -> POI | None:
    """把已发布补充记录按逐日准入结果转为可排程POI；没有任何可排程日期时返回None。

    只列出准入日期（dates_listed_only），其余日期一律不可排程。各日可用时段不同时，
    取“覆盖天数×时段长度”最大的单一连续时段，不能容纳它的日期剔除，不取跨午休的包络。
    票价按已知上界扣预算。
    公园按用户决定默认每天全天开放，不受准入日期限制（见设计决策记录）。
    """
    if record.internal_id is None:
        return None
    ends = [e.valid_until for e in record.evidence.values() if e.valid_until is not None]
    starts = [e.valid_from for e in record.evidence.values() if e.valid_from is not None]
    first = max([as_of, *starts])
    last = min([as_of + timedelta(days=HORIZON_DAYS), *ends])
    dates = [first + timedelta(days=n) for n in range((last - first).days + 1)]
    admission = admit_supplement(record, dates, as_of=as_of)
    if not admission.admitted:
        return None
    windows = {key: [(x.start, x.end) for x in intervals] for key, intervals in admission.intervals.items()}

    def covered(key: str, window: tuple[int, int]) -> bool:
        return any(start <= window[0] and window[1] <= end for start, end in windows[key])

    options = {window for intervals in windows.values() for window in intervals}
    chosen = max(options, key=lambda w: (sum(covered(key, w) for key in windows) * (w[1] - w[0]), -w[0]))
    open_dates = [day for day in admission.allowed_dates if covered(day.isoformat(), chosen)]
    park = record.place_type == "公园"
    if park:
        chosen = (0, 1440)
    from .data import TYPE_RULES
    from .poi_semantics import enrich_semantics
    interests, stamina, indoor = TYPE_RULES.get(record.place_type, ([], 2, False))
    inferred = [f for f in ("ticket", "opening") if record.evidence[f].inferred]
    exact = (record.ticket_price_exact is not None and record.ticket_price_exact == record.ticket_price_upper
             and "ticket" not in inferred)

    def evidence(field: str) -> FieldEvidence:
        item = record.evidence[field]
        # 推断得来的事实标为整理推断，展示和MCP不把它当作来源直接陈述
        return FieldEvidence(basis="curator_inference" if item.inferred else EVIDENCE_BASIS[item.basis], reference=item.reference,
                             source_date=item.source_date, reviewed_at=item.reviewed_at)

    curated = FieldEvidence(basis="curator_inference", reference=f"补充记录 {record.canonical_key} 人工整理标签")
    notes = []
    if inferred:
        labels = "、".join({"ticket": "门票", "opening": "开放时段"}[f] for f in inferred)
        notes.append(f"{labels}依据规则推断，不是管理方公布，出发前须确认")
    if not exact and "ticket" not in inferred:
        notes.append(f"门票按已知上界¥{admission.ticket_price_upper:g}预留，不是实际售价")
    horizon = [record.evidence[f].valid_until for f in ("ticket", "opening")
               if record.evidence[f].validity_basis == "review_horizon"]
    if horizon:
        notes.append(f"开放/票价资料来源未写截止日，按项目复核期限使用至{min(horizon).isoformat()}，到期须重新核对")
    opening = record.evidence["opening"]
    poi = POI(
        id=record.internal_id, name=record.name, type=record.place_type or "补充景点",
        lat=admission.entrance.lat, lon=admission.entrance.lon,
        open_min=chosen[0], close_min=chosen[1], price=admission.ticket_price_upper,
        min_hours=admission.duration_min_hours, max_hours=admission.duration_max_hours,
        interests=list(record.interests) or interests, stamina=stamina, audience=list(record.audience), indoor=indoor,
        opening_rules=OpeningRules(open_dates=[] if park else open_dates, dates_listed_only=not park, verified_at=opening.reviewed_at,
                                   source_url=opening.reference if opening.reference.startswith("http") else ""),
        duration_source=f"{record.duration_scope}（项目规划建议，非官方最少游玩时间）",
        price_verified=exact,
        review_note="；".join(notes),
        aliases=list(record.aliases), catalog_source="supplement",
        field_evidence={"coordinates": evidence("entrance"), "opening_hours": evidence("opening"),
                        "price": evidence("ticket"), "duration": evidence("duration"),
                        "type": curated, "interests": curated, "audience": curated},
    )
    poi = enrich_semantics(poi)
    # 补充记录有人工核对的真实片区；原库条目仍用坐标网格，不冒充行政边界
    return poi.model_copy(update={"region_label": record.area or record.district})
