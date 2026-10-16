"""估计独立停留参考；默认 377 点、每批 10 点、3 次采样，共 114 次，无自动重试。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from statistics import median

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import config
from app.llm import OpenAICompatLLM, chat_json
from app.schemas import StayEstimate, StayEstimateBatch, StayReference

PROMPT_VERSION = "hangzhou-stay-v1"
SYSTEM = """你为旅游行程评估提供独立的第二意见。根据名称、类型、简介、票价、开放时间，
估计普通成年游客首次到访该景点的典型停留区间，单位为整数分钟。
这是估计而不是事实；不能假装查阅了网页，不能编造来源、实际场次、排队时长。
范围必须对应此单个POI：寺庙说明是否含毗邻景区，桥/亭/墓只估单点观景，
街道/步行段不重复计入景点之间的交通步行，博物馆明确展区，主题园区可含必要休息和园内用餐，
大型自然景区明确选择普通游客常走的路线而非遍历全域；不把同名周边景点都算进来。
若简介夹带建议游玩时长，请忽略该数字并独立判断。缺乏知识或地点含混时 confidence=low，
reason 说明不确定性，不要据名称编造设施。low/high 是通常游览差异的区间，不是安全硬约束。
每次采样独立估计，不依赖其他采样的答案。
只输出JSON：{"items":[{"poi_id":0,"low_min":30,"high_min":60,"typical_min":45,
"scope":"只含当前景点","reason":"一句估计依据","confidence":"high|medium|low"}]}。
逐条原样返回所有poi_id，不能缺失或重复，要求 0 < low_min <= typical_min <= high_min <= 1440。
"""
PROMPT_SHA256 = hashlib.sha256(SYSTEM.encode()).hexdigest()


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def input_rows() -> list[dict]:
    frame = pd.read_parquet(config.CITY_DIR / "attractions.parquet")
    tags_path = config.CITY_DIR / "attraction_tags.parquet"
    tags = pd.read_parquet(tags_path).set_index("id") if tags_path.exists() else None
    return [{"poi_id": int(row.id), "name": row.name, "type": row.type,
             "summary": str(tags.loc[row.id, "summary"]) if tags is not None and row.id in tags.index else "",
             "price": float(row.price), "opening_hours": f"{row.opentime}-{row.endtime}"}
            for row in frame.sort_values("id").itertuples()]


def messages_for(rows: list[dict], sample: int) -> list[dict]:
    return [{"role": "system", "content": SYSTEM},
            {"role": "user", "content": json.dumps({"prompt_version": PROMPT_VERSION,
             "independent_sample": sample, "places": rows}, ensure_ascii=False)}]


def aggregate(rows: list[dict], records: list[dict], generated_at: str) -> list[dict]:
    collected: dict[int, list[tuple[StayEstimate, str]]] = {row["poi_id"]: [] for row in rows}
    for record in records:
        for item in StayEstimateBatch.model_validate(record["output"]).items:
            collected[item.poi_id].append((item, record["model"]))
    results = []
    confidence_order = {"low": 0, "medium": 1, "high": 2}
    for row in rows:
        values = collected[row["poi_id"]]
        if len(values) != 3:
            raise ValueError(f"{row['poi_id']} 需要且仅允许三个样本")
        samples = [item for item, _ in values]
        fields = ("low_min", "high_min", "typical_min")
        middle = {key: int(median(getattr(item, key) for item in samples)) for key in fields}
        spread = {key: max(getattr(item, key) for item in samples) - min(getattr(item, key) for item in samples)
                  for key in fields}
        spread["max_min"] = max(spread.values())
        representative = min(samples, key=lambda item: sum(abs(getattr(item, key) - middle[key]) for key in fields))
        confidence = min((item.confidence for item in samples), key=confidence_order.get)
        if spread["typical_min"] >= max(60, middle["typical_min"] * .5):
            confidence = "low"
        models = sorted({model for _, model in values})
        if len(models) != 1:
            raise ValueError("同一POI的三次响应模型不同，不能合并")
        results.append(StayReference(poi_id=row["poi_id"], name=row["name"], **middle,
                       scope=representative.scope, reason=representative.reason, confidence=confidence,
                       spread=spread, samples=samples, source="llm_estimate", model=models[0],
                       generated_at=generated_at).model_dump())
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--output", type=Path, default=config.BACKEND_DIR / "app/assets/stay_reference.json")
    parser.add_argument("--checkpoint", type=Path, default=config.DATA_DIR / "stay-reference-v1/checkpoint.json")
    args = parser.parse_args()
    if not 10 <= args.batch_size <= 12:
        parser.error("每批必须为10至12个景点，以控制总调用数")
    rows = input_rows()
    batches = [rows[offset:offset + args.batch_size] for offset in range(0, len(rows), args.batch_size)]
    expected = len(batches) * 3
    print(f"POI={len(rows)}，每批={args.batch_size}，独立采样=3，预计调用={expected}，模型={config.LLM_MODEL}，缓存={config.LLM_CACHE}", flush=True)
    if expected > 120:
        parser.error("预计超过120次，停止")
    identity = {"prompt_version": PROMPT_VERSION, "prompt_sha256": PROMPT_SHA256,
                "input_sha256": digest(rows), "configured_model": config.LLM_MODEL,
                "thinking": config.LLM_THINKING, "batch_size": args.batch_size,
                "temperature": .7, "max_tokens": 12000}
    if args.dry_run:
        print(json.dumps(identity, ensure_ascii=False))
        return 0
    checkpoint = {**identity, "records": [], "failures": [], "sessions": []}
    if args.checkpoint.exists():
        checkpoint = json.loads(args.checkpoint.read_text(encoding="utf-8"))
        if any(checkpoint.get(key) != value for key, value in identity.items()):
            raise ValueError("检查点与输入/提示词/模型/参数不一致；请指定新的checkpoint路径")
    llm = OpenAICompatLLM(retry_attempts=1, sdk_max_retries=0)
    started = time.perf_counter()
    session = {"started_at": datetime.now(timezone.utc).isoformat(), "request_attempts": 0,
               "cache_hits": 0, "seconds": 0.0}
    checkpoint["sessions"].append(session)
    completed = {record["request_sha256"] for record in checkpoint["records"]}
    attempts_used = sum(run["request_attempts"] for run in checkpoint["sessions"])
    if checkpoint["failures"] and len(completed) != expected:
        print("检查点记录过失败；为保护共用额度，本次不自动恢复。先检查失败类型和额度，再人工决定后续。", flush=True)
        return 1
    if attempts_used >= 120 and len(completed) != expected:
        print("历史请求已达120次，停止。", flush=True)
        return 1
    consecutive_errors = 0
    stopped = False
    for sample in range(1, 4):
        for batch_number, batch in enumerate(batches, 1):
            messages = messages_for(batch, sample)
            request_sha = digest({**identity, "messages": messages})
            if request_sha in completed:
                continue
            request_started = time.perf_counter()
            before_attempts, before_cached = llm.request_attempts, llm.usage.cached_calls
            llm.response_models.clear()
            try:
                output = chat_json(llm, messages, StayEstimateBatch, tag="estimate_stay",
                                   temperature=.7, max_tokens=12000, retries=0)
                ids = [item.poi_id for item in output.items]
                if len(ids) != len(set(ids)) or set(ids) != {row["poi_id"] for row in batch}:
                    raise ValueError("响应的POI ID缺失、重复或超出批次")
                if len(llm.response_models) != 1:
                    raise ValueError("实际模型ID缺失或不一致")
                checkpoint["records"].append({"sample": sample, "batch": batch_number,
                    "request_sha256": request_sha, "model": next(iter(llm.response_models)),
                    "seconds": round(time.perf_counter() - request_started, 3),
                    "request_attempts": llm.request_attempts - before_attempts,
                    "cache_hits": llm.usage.cached_calls - before_cached, "output": output.model_dump()})
                completed.add(request_sha)
                consecutive_errors = 0
                print(f"sample={sample}/3 batch={batch_number}/{len(batches)} 完成；调用={llm.request_attempts} 缓存={llm.usage.cached_calls}", flush=True)
            except Exception as error:
                status = getattr(error, "status_code", None)
                checkpoint["failures"].append({"sample": sample, "batch": batch_number,
                    "request_sha256": request_sha, "error_type": type(error).__name__, "http_status": status})
                consecutive_errors += 1
                stopped = status in {401, 402, 403, 429} or consecutive_errors >= 2
                print(f"sample={sample} batch={batch_number} 失败：{type(error).__name__} HTTP={status}，连续错误={consecutive_errors}，停止={stopped}", flush=True)
            session.update(request_attempts=llm.request_attempts, cache_hits=llm.usage.cached_calls,
                           seconds=round(time.perf_counter() - started, 3), usage=llm.usage.as_dict())
            write_json(args.checkpoint, checkpoint)
            attempts = sum(run["request_attempts"] for run in checkpoint["sessions"])
            if stopped or attempts >= 120:
                stopped = True
                break
        if stopped:
            break
    if len(completed) != expected:
        print(f"未完成：{len(completed)}/{expected}批次；保留检查点，不发布不完整参考。", flush=True)
        return 1
    generated_at = datetime.now(timezone.utc).isoformat()
    artifact = {**identity, "prompt": SYSTEM, "generated_at": generated_at,
                "input_fields": list(rows[0]), "sampling": {"samples_per_poi": 3, "expected_calls": expected,
                "aggregation": "各数值字段中位数；scope/reason取数值最接近中位数的样本；confidence取三次最低，高分歧再降为low",
                "spread": "每个数值字段max-min；max_min取三字段最大差；大分歧=max_min>=60且>=typical_min*0.5",
                "independence": "每次请求含不同independent_sample；来自同一模型，不能视作独立事实来源"},
                "run_stats": {"actual_calls": sum(run["request_attempts"] for run in checkpoint["sessions"]),
                "cache_hits": sum(run["cache_hits"] for run in checkpoint["sessions"]),
                "elapsed_seconds": round(sum(run["seconds"] for run in checkpoint["sessions"]), 3),
                "response_models": sorted({record["model"] for record in checkpoint["records"]}),
                "failures": checkpoint["failures"], "sessions": checkpoint["sessions"]},
                "requests": [{key: value for key, value in record.items() if key != "output"}
                             for record in checkpoint["records"]],
                "places": aggregate(rows, checkpoint["records"], generated_at)}
    write_json(args.output, artifact)
    print(json.dumps({"output": str(args.output), "places": len(artifact["places"]),
                      "run_stats": artifact["run_stats"]}, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
