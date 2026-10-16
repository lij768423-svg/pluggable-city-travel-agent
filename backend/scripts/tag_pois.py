"""用 LLM 离线给杭州景点打增强标签（兴趣、体力 1–3、适合人群、室内/室外、一句话简介）。

- 每批 20 个景点，4 线程并发；结果写入 data/hangzhou/attraction_tags.parquet，可重复运行（有缓存）。
- 同时随机抽 10%（seed=42）生成人工抽检表 backend/eval/tag_audit/sample.csv，供本人核对后统计准确率。
- 注意：标签来自 LLM 的世界知识，未经人工核对前不能当作事实；论文中要报告抽检准确率。

用法：uv run python scripts/tag_pois.py
"""
from __future__ import annotations

import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
from pydantic import BaseModel, Field

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import BACKEND_DIR, CITY_DIR  # noqa: E402
from app.data import TYPE_RULES  # noqa: E402
from app.llm import chat_json, get_llm  # noqa: E402
from app.schemas import AUDIENCES, INTERESTS  # noqa: E402

SYSTEM = f"""你是杭州本地旅游专家。为给定的每个景点打标签，只输出 JSON。
字段：
- id: 原样返回
- interests: 从词表中选 1-4 个最贴切的：{"、".join(INTERESTS)}
- stamina: 体力消耗 1（轻松，平地短时）/ 2（中等，步行较多）/ 3（较累，爬山、长距离徒步或大型乐园）
- audience: 从 {"、".join(AUDIENCES)} 中选适合人群（可多选）
- indoor: 主要在室内则 true（雨天也适合）
- summary: 不超过 30 字的中文简介；不了解该景点时写"信息不足"，不要编造
输出格式：{{"items": [{{"id": 0, "interests": [], "stamina": 2, "audience": [], "indoor": false, "summary": ""}}]}}
"""


class _Tag(BaseModel):
    id: int
    interests: list[str] = Field(default_factory=list)
    stamina: int = 2
    audience: list[str] = Field(default_factory=list)
    indoor: bool = False
    summary: str = ""


class _Out(BaseModel):
    items: list[_Tag]


def tag_batch(rows: list[dict]) -> list[dict]:
    lines = [f"id={r['id']} | {r['name']} | 类型:{r['type']} | 门票:{r['price']:.0f}元 | 建议游玩:{r['recommendmintime']}-{r['recommendmaxtime']}小时"
             for r in rows]
    out: _Out = chat_json(get_llm(), [{"role": "system", "content": SYSTEM},
                                      {"role": "user", "content": "\n".join(lines)}],
                          _Out, tag="tag_pois", temperature=0.0, max_tokens=6000)
    ids = {r["id"] for r in rows}
    res = []
    for t in out.items:
        if t.id not in ids:
            continue
        res.append({"id": t.id,
                    "interests": [i for i in t.interests if i in INTERESTS][:4],
                    "stamina": min(3, max(1, t.stamina)),
                    "audience": [a for a in t.audience if a in AUDIENCES],
                    "indoor": bool(t.indoor), "summary": t.summary[:60], "source": "llm"})
    return res


def main() -> None:
    df = pd.read_parquet(CITY_DIR / "attractions.parquet")
    rows = df.to_dict("records")
    batches = [rows[i:i + 20] for i in range(0, len(rows), 20)]
    with ThreadPoolExecutor(max_workers=4) as ex:
        results = list(ex.map(tag_batch, batches))
    tags = {t["id"]: t for batch in results for t in batch}
    n_fallback = 0
    for r in rows:  # LLM 漏掉或标签为空的：规则兜底并标注来源
        if r["id"] not in tags or not tags[r["id"]]["interests"]:
            interests, stamina, indoor = TYPE_RULES.get(r["type"], ([], 2, False))
            tags[r["id"]] = {"id": r["id"], "interests": interests, "stamina": stamina, "audience": [],
                             "indoor": indoor, "summary": "", "source": "rule"}
            n_fallback += 1
    out = pd.DataFrame([tags[r["id"]] for r in rows])
    out.to_parquet(CITY_DIR / "attraction_tags.parquet", index=False)

    audit_dir = BACKEND_DIR / "eval" / "tag_audit"
    audit_dir.mkdir(parents=True, exist_ok=True)
    random.seed(42)
    sample_ids = sorted(random.sample([r["id"] for r in rows], k=max(1, len(rows) // 10)))
    sample = out[out.id.isin(sample_ids)].merge(df[["id", "name", "type"]], on="id")
    sample = sample[["id", "name", "type", "interests", "stamina", "audience", "indoor", "summary", "source"]].copy()
    for c in ("interests", "audience"):
        sample[c] = sample[c].map("、".join)
    sample["人工_兴趣是否正确(Y/N)"] = ""
    sample["人工_体力是否正确(Y/N)"] = ""
    sample["人工_简介是否属实(Y/N/不确定)"] = ""
    sample["备注"] = ""
    sample.to_csv(audit_dir / "sample.csv", index=False, encoding="utf-8-sig")

    u = get_llm().usage.as_dict()
    print(f"tagged {len(out)} POIs; rule fallback {n_fallback}; audit sample {len(sample)}; usage {u}")
    print("stamina dist:", out.stamina.value_counts().sort_index().to_dict())
    print("top interests:", pd.Series([i for l in out.interests for i in l]).value_counts().head(15).to_dict())
    print("summary 信息不足:", int((out.summary == "信息不足").sum()))


if __name__ == "__main__":
    main()
