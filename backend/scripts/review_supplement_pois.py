"""离线审阅与发布命令；缺省dry-run，data不入Git，禁止原始提供商数据入库。"""
from __future__ import annotations

import argparse
from datetime import date, datetime
import json
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import config
from app.poi_admission import admit_supplement, admitted_poi, persistence_issues, publication_issues
from app.poi_catalog import SupplementCatalog
from app.schemas import SupplementRecord

REPO = Path(__file__).resolve().parents[2]
SOURCES = REPO / "backend/app/assets/poi_supplement_sources.json"


def base_digest() -> str:
    """原库输入摘要，不读取凭据、API响应或实验输出；与任务目录版本共用同一算法。"""
    from app.data import base_digest as digest
    return digest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="自有SupplementRecord JSON数组；缺省为待核对种子")
    parser.add_argument("--root", type=Path, default=REPO / "data/poi_supplement")
    parser.add_argument("--as-of", type=date.fromisoformat,
                        default=datetime.now(ZoneInfo("Asia/Shanghai")).date())
    parser.add_argument("--date", action="append", type=date.fromisoformat, default=[])
    parser.add_argument("--apply", action="store_true", help="明确登记审阅资料；缺省不写磁盘")
    parser.add_argument("--publish", action="store_true", help="发布registry全部published记录；需同时--apply")
    args = parser.parse_args(argv)
    try:
        if args.publish and not args.apply:
            raise ValueError("--publish需要同时--apply，避免dry-run误发布")
        if args.publish and args.input is None:
            raise ValueError("发布须明确--input审阅资料，默认种子不能用于发布")
        source = json.loads(SOURCES.read_text())
        raw = json.loads(args.input.read_text()) if args.input else source["seeds"]
        if not isinstance(raw, list):
            raise ValueError("输入必须是记录数组，不能导入提供商搜索响应")
        records = [SupplementRecord.model_validate(item) for item in raw]
        if len({r.canonical_key for r in records}) != len(records):
            raise ValueError("输入canonical_key重复")
        rejected = {r.canonical_key: persistence_issues(r) for r in records if persistence_issues(r)}
        if args.apply and rejected:
            raise ValueError(f"拒绝持久化来源: {rejected}")
        digest = None
        if args.apply:
            catalog = SupplementCatalog(args.root)
            if args.input is None:
                # 种子是发现线索；再次导入不能抹掉已人工核对的字段或发布状态。
                existing = {r.canonical_key: r for r in catalog.records()}
                records = [existing.get(r.canonical_key, r) for r in records]
            records = catalog.register(records)
            if args.publish:
                digest = catalog.publish(as_of=args.as_of, base_digest=base_digest()).digest
        output = {
            "mode": "apply" if args.apply else "dry_run",
            "as_of": args.as_of.isoformat(),
            # 只有本进程配置为叠加补充目录且读的正是这个目录时，发布结果才会进入规划
            "catalog_connected_to_planning": config.POI_CATALOG_MODE == "with_supplement"
                and args.root.resolve() == config.POI_SUPPLEMENT_DIR.resolve(),
            "published_digest": digest,
            "region_queue": source["regions"],
            "count": len(records),
            "publication_ready": sum(not publication_issues(r, as_of=args.as_of) for r in records),
            "records": [
                {"canonical_key": r.canonical_key, "internal_id": r.internal_id,
                 "name": r.name, "district": r.district, "state": r.state,
                 "publication_issues": publication_issues(r, as_of=args.as_of),
                 "admission": admit_supplement(r, args.date, as_of=args.as_of).model_dump(mode="json"),
                 # 发布后能否转为可排程地点（至少有一个准入日期）；未发布记录恒为否
                 "schedulable_when_published": admitted_poi(
                     r.model_copy(update={"internal_id": r.internal_id or 1_000_000}), as_of=args.as_of) is not None}
                for r in records
            ],
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
