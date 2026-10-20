"""下载 ChinaTravel Sandbox（CC BY 4.0）中文表，筛出杭州，写入 data/hangzhou/。

用法：uv run python scripts/fetch_data.py [--city 杭州] [--mirror https://hf-mirror.com]
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.cities import city_config, _definitions  # noqa: E402

REPO = "LAMDA-NeSy/ChinaTravel-Sandbox"
TABLES = ["attractions", "restaurants", "accommodations"]


def fetch(table: str, base: str) -> pd.DataFrame:
    url = f"{base}/datasets/{REPO}/resolve/main/data/zh/{table}.parquet"
    r = httpx.get(url, follow_redirects=True, timeout=120)
    r.raise_for_status()
    return pd.read_parquet(io.BytesIO(r.content))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", default="hangzhou", help="城市键；兼容已配置的中文城市名")
    ap.add_argument("--mirror", default="https://huggingface.co")
    args = ap.parse_args()

    key = next((key for key, raw in _definitions().items() if args.city in (key, raw["name"], raw["name_full"])), args.city)
    city = city_config(key)
    city.data_dir.mkdir(parents=True, exist_ok=True)
    meta = {"source": f"https://huggingface.co/datasets/{REPO}", "license": "CC BY 4.0",
            "city": city.name, "fetched_at": datetime.now(timezone.utc).isoformat(), "tables": {}}
    for t in TABLES:
        df = fetch(t, args.mirror)
        sub = df[df["city"] == city.name].reset_index(drop=True)
        sub.to_parquet(city.data_dir / f"{t}.parquet", index=False)
        meta["tables"][t] = {"rows_total": len(df), "rows_city": len(sub), "columns": list(sub.columns)}
        print(f"{t}: {len(sub)}/{len(df)} rows, columns={list(sub.columns)}")
    (city.data_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
