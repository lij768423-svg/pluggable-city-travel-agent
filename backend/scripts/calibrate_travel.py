"""用已缓存的高德真实路线标定"兜底模型"，写入 data/travel_calibration.json。

兜底模型只在两种情况下使用：公交模式下求解器的路程矩阵（公交没有批量接口），
以及高德调用失败 / 超出单次规划调用上限时。所有系数都以**直线距离**为自变量，便于在没有任何查询时直接使用。

用法：uv run python scripts/calibrate_travel.py   （需先有 data/amap_cache.jsonl，例如跑过一次 eval/run_eval.py）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.config import DATA_DIR
from app.cities import city_config  # noqa: E402
from app.geo import haversine_km  # noqa: E402


def gcj_km(key: str) -> float:
    _, o, d = key.split("|")
    (lo1, la1), (lo2, la2) = (map(float, o.split(",")), map(float, d.split(",")))
    return haversine_km(la1, lo1, la2, lo2)


def fit(x, y):
    b, a = np.polyfit(x, y, 1)
    idx = np.arange(len(x))
    np.random.default_rng(0).shuffle(idx)
    errs = []
    for f in np.array_split(idx, 5):  # 5 折交叉验证
        m = np.ones(len(x), bool)
        m[f] = False
        bb, aa = np.polyfit(x[m], y[m], 1)
        errs += list(np.abs(aa + bb * x[f] - y[f]))
    return {"a": round(float(a), 3), "b": round(float(b), 3), "cv_mae": round(float(np.mean(errs)), 2), "n": int(len(x))}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--city", default="hangzhou")
    parser.add_argument("--cache", type=Path)
    args = parser.parse_args()
    settings = city_config(args.city)
    cache_path = args.cache or (DATA_DIR / "amap_cache.jsonl" if settings.key == "hangzhou" else settings.data_dir / "amap_cache.jsonl")
    cache = {}
    for line in cache_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            parts = r["k"].split("|")
            if len(parts) != 3 or parts[0] not in {"walk", "drive", "transit"}:
                continue
            try:
                endpoints = [tuple(map(float, item.split(","))) for item in parts[1:]]
                if all(settings.contains(lat, lon) for lon, lat in endpoints):
                    cache[r["k"]] = r["v"]
            except (TypeError, ValueError):
                continue
    walk = [(gcj_km(k), v["m"] / 1000) for k, v in cache.items() if k.startswith("walk|") and v and v["m"] > 0]
    drive = [(gcj_km(k), v) for k, v in cache.items() if k.startswith("drive|") and v]
    transit = [(gcj_km(k), v["min"]) for k, v in cache.items() if k.startswith("transit|") and v]
    wx, wy = np.array([a for a, _ in walk]), np.array([b for _, b in walk])
    out = {
        "fitted_at": datetime.now(timezone.utc).isoformat(),
        "source": "data/amap_cache.jsonl（高德 Web服务真实路线）",
        "walk_detour": round(float(np.median(wy / np.maximum(wx, 1e-6))), 3),  # 步行距离 / 直线距离 的中位数
        "drive_min": fit(np.array([a for a, _ in drive]), np.array([v["min"] for _, v in drive])),
        "transit_min": fit(np.array([a for a, _ in transit]), np.array([m for _, m in transit])),
        "taxi_cost": fit(np.array([a for a, v in drive if v.get("taxi_cost") is not None]),
                         np.array([v["taxi_cost"] for _, v in drive if v.get("taxi_cost") is not None])),
    }
    settings.calibration.parent.mkdir(parents=True, exist_ok=True)
    settings.calibration.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
