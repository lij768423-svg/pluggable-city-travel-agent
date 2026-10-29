"""按城市生成景点知名度档位 data/<city>/popularity.json（研究计划 3.3；2026-10-26）。

档位：
  landmark 城市地标：app/assets/landmarks.json 里该城市的名单（人工确认，文件里写明状态）。
  known    知名景点：高德 POI 分类为 110201 世界遗产或 110202 国家级景点。
  ""       其余（含高德没有匹配到的）。
来源说明：研究计划以国家 A 级旅游景区官方名录为主，但官方完整名录尚未整理（各省分散发布）；本版先用高德分类，
名录整理后在 merge_a_level 处合并。高德评分只存档，不参与分档（评分集中在 4.6–4.8，区分度低）。

做法：对每个可推荐景点，用高德"关键字搜索"按景点名在本城市内搜索（每秒不超过 2 次），取与本库坐标 1.5 km 内、
名称互相包含的结果；原始结果缓存在 data/<city>/popularity_amap.jsonl，重跑只补没查过的景点。

用法（backend 目录）：AMAP_WEB_KEY=… .venv/bin/python scripts/build_popularity.py --city hangzhou
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import time

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

KNOWN_TYPECODES = {"110201": "高德分类：世界遗产", "110202": "高德分类：国家级景点"}
MATCH_KM = 1.5
LANDMARKS = Path(__file__).resolve().parent.parent / "app" / "assets" / "landmarks.json"


def search(key: str, name: str, region: str) -> dict:
    for attempt in range(4):
        response = httpx.get("https://restapi.amap.com/v5/place/text", timeout=15, params={
            "key": key, "keywords": name, "region": region, "city_limit": "true", "show_fields": "business", "page_size": 5})
        body = response.json()
        if body.get("status") == "1":
            return {"pois": [{"name": p.get("name"), "typecode": p.get("typecode", ""), "location": p.get("location"),
                              "rating": (p.get("business") or {}).get("rating")} for p in body.get("pois", [])]}
        if "QPS" in str(body.get("info")) and attempt < 3:
            time.sleep(1.5)
            continue
        return {"error": f"{body.get('info')} {body.get('infocode')}"}
    return {"error": "retries exhausted"}


def best_match(poi, results: list[dict]):
    """本库景点在高德结果里的对应项：1.5 km 内且名称互相包含；有多个时优先世界遗产、国家级景点，再取最近。"""
    from app.amap import pt, straight_km
    from app.matching import norm
    here = pt(poi.lat, poi.lon)
    matches = []
    for result in results:
        if not result.get("location"):
            continue
        distance = straight_km(here, result["location"])
        a, b = norm(poi.name), norm(result["name"] or "")
        if distance <= MATCH_KM and a and b and (a in b or b in a):
            known = any(code in KNOWN_TYPECODES for code in result["typecode"].split("|"))
            matches.append((not known, distance, result))
    if not matches:
        return None
    _, distance, result = min(matches, key=lambda match: (match[0], match[1]))
    return result | {"distance_km": round(distance, 2)}


def main() -> None:
    from app import config
    from app.cities import city_config
    from app.data import load_base_pois
    from app.matching import norm
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", required=True)
    args = parser.parse_args()
    if not config.AMAP_WEB_KEY:
        raise SystemExit("需要 AMAP_WEB_KEY")
    settings = city_config(args.city)
    pois = {identifier: poi for identifier, poi in load_base_pois(args.city).items() if poi.recommendable}
    cache_path = settings.data_dir / "popularity_amap.jsonl"
    cache = {}
    if cache_path.exists():
        for line in cache_path.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            cache[record["id"]] = record
    with cache_path.open("a", encoding="utf-8") as handle:
        for identifier, poi in sorted(pois.items()):
            if identifier in cache and "error" not in cache[identifier]["result"]:
                continue
            result = search(config.AMAP_WEB_KEY, poi.name, settings.name)
            record = {"id": identifier, "name": poi.name, "result": result, "fetched_at": datetime.now(timezone.utc).isoformat()}
            cache[identifier] = record
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            time.sleep(0.5)
    landmark_file = json.loads(LANDMARKS.read_text(encoding="utf-8"))
    landmark_names = [norm(name) for name in landmark_file["cities"].get(args.city, {}).get("names", [])]
    places, counts = {}, {"landmark": 0, "known": 0, "": 0, "unmatched": 0, "errors": 0}
    for identifier, poi in sorted(pois.items()):
        result = cache[identifier]["result"]
        counts["errors"] += "error" in result
        match = best_match(poi, result.get("pois", []))
        counts["unmatched"] += match is None
        known = match is not None and next((KNOWN_TYPECODES[code] for code in match["typecode"].split("|") if code in KNOWN_TYPECODES), None)
        if norm(poi.name) in landmark_names:
            tier, source = "landmark", f"城市地标名单（{landmark_file['cities'][args.city]['status']}）"
        elif known:
            tier, source = "known", f"{known}（{match['name']}，{cache[identifier]['fetched_at'][:10]}）"
        else:
            tier, source = "", ""
        counts[tier] += 1
        places[str(identifier)] = {"name": poi.name, "tier": tier, "source": source,
                                   "amap": match and {"name": match["name"], "typecode": match["typecode"], "rating": match["rating"],
                                                      "distance_km": match["distance_km"]}}
    missing = [name for name in landmark_file["cities"].get(args.city, {}).get("names", [])
               if norm(name) not in {norm(poi.name) for poi in pois.values()}]
    payload = {"city": args.city, "built_at": datetime.now(timezone.utc).isoformat(),
               "method": "高德关键字搜索分类（110201 世界遗产、110202 国家级景点）+ 城市地标名单；A 级名录待合并",
               "landmarks_status": landmark_file["cities"].get(args.city, {}).get("status", "无名单"),
               "landmarks_missing": missing, "counts": counts, "places": places}
    (settings.data_dir / "popularity.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({k: payload[k] for k in ("city", "counts", "landmarks_status", "landmarks_missing")}, ensure_ascii=False))


if __name__ == "__main__":
    main()
