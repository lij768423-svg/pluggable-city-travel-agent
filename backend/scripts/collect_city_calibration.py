"""逐城采集有上限的交通校准样本；样本按直线距离分层，独立于规划结果。"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import sys
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.amap import AmapRouter, pt, straight_km
from app.amap_limits import RequestBudget
from app.cities import city_config
from app.poi_catalog import published_catalog, use_catalog


def sample_pairs(rows: list[dict], seed=20261019) -> tuple[list[dict], list[dict]]:
    candidates = []
    for i, first in enumerate(rows):
        for second in rows[i+1:]:
            origin, destination = pt(first['lat'], first['lon']), pt(second['lat'], second['lon'])
            distance = straight_km(origin, destination)
            candidates.append({'origin_id': int(first['id']), 'destination_id': int(second['id']),
                               'origin_name': first['name'], 'destination_name': second['name'],
                               'origin': origin, 'destination': destination, 'straight_km': round(distance, 4)})
    random.Random(seed).shuffle(candidates)
    pairs = []
    for low, high in ((.5,3),(3,8),(8,15),(15,35)):
        pool = [pair for pair in candidates if low <= pair['straight_km'] < high]
        if len(pool) < 5:
            raise ValueError(f'距离层 {low}-{high}km 不足5对')
        pairs.extend([{**pair, 'stratum_km':[low,high]} for pair in pool[:5]])
    walks = [pair for pair in candidates if .2 <= pair['straight_km'] < 1.5][:8]
    if len(walks) != 8:
        raise ValueError('步行样本不足8对')
    return pairs, walks


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--city', required=True)
    parser.add_argument('--dry-run', action='store_true')
    args=parser.parse_args()
    settings=city_config(args.city)
    # 数据集前40个景点仅作为固定取样框；不按评测通过情况筛样本。
    frame=pd.read_parquet(settings.data_dir/'attractions.parquet').sort_values('id').head(40)
    pairs,walks=sample_pairs(frame.to_dict('records'))
    root=settings.data_dir/'calibration-v1'
    root.mkdir(parents=True, exist_ok=True)
    design={'city':settings.key,'seed':20261019,'sampling_frame_ids':list(map(int,frame.id)),
            'pairs':pairs,'walk_pairs':walks,'normal_http_requests':len(pairs)*2+len(walks),'http_limit_including_retries':90}
    print(json.dumps({key:value for key,value in design.items() if key not in ('pairs','walk_pairs')},ensure_ascii=False),flush=True)
    design_path=root/'design.json'
    if design_path.exists() and json.loads(design_path.read_text()) != design:
        raise ValueError('采样设计已存在且不一致')
    design_path.write_text(json.dumps(design,ensure_ascii=False,indent=2)+'\n')
    if args.dry_run:
        return
    output=root/'collection.json'
    record=json.loads(output.read_text()) if output.exists() else {'started_at':datetime.now(timezone.utc).isoformat(),'results':[],'sessions':[]}
    used=sum(item['http_requests'] for item in record['sessions'])
    if used>=90:
        raise ValueError('历史HTTP请求已经达到90上限')
    cache=root/'amap_cache.jsonl'
    router=AmapRouter(cache_path=cache).fork(RequestBudget(limit=90-used))
    completed={(entry['kind'],entry['origin'],entry['destination']) for entry in record['results']}
    errors=[]
    with use_catalog(published_catalog(settings.key)):
        for kind, samples in (('drive',pairs),('transit',pairs),('walk',walks)):
            for pair in samples:
                origin,destination=pair['origin'],pair['destination']
                if (kind,origin,destination) in completed:
                    continue
                try:
                    if kind=='drive':
                        data=router._get('v3/direction/driving',{'origin':origin,'destination':destination,'extensions':'all'})['route']
                        best=min(data.get('paths') or [],key=lambda p:int(p['duration']))
                        cost=data.get('taxi_cost')
                        value={'m':int(best['distance']),'min':int(best['duration'])/60,
                               'taxi_cost':float(cost) if cost not in (None,'',[]) else None}
                        router._put(f'drive|{origin}|{destination}',value)
                    elif kind=='transit':
                        value=router.transit(origin,destination)
                    else:
                        router._batch('walk',[origin],destination)
                        value=router.get(f'walk|{origin}|{destination}')
                    record['results'].append({**pair,'kind':kind,'value':value})
                    print(kind,pair['origin_id'],pair['destination_id'],'ok' if value else 'no_route',flush=True)
                except Exception as error:
                    errors.append({'kind':kind, 'origin_id':pair['origin_id'],'destination_id':pair['destination_id'],
                                   'error_type':type(error).__name__,'message':str(error)[:120]})
                    print(kind,'failed',type(error).__name__,flush=True)
                    break
            if errors:
                break
    record['sessions'].append({'finished_at':datetime.now(timezone.utc).isoformat(),'http_requests':router.calls,'errors':errors})
    record['cache_sha256']=hashlib.sha256(cache.read_bytes()).hexdigest() if cache.exists() else None
    output.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n')
    if errors:
        raise SystemExit(1)
    from scripts import calibrate_travel
    sys.argv=['calibrate_travel.py','--city',settings.key,'--cache',str(cache)]
    calibrate_travel.main()
    calibration=json.loads(settings.calibration.read_text())
    calibration.update(source=str(cache.relative_to(settings.data_dir.parent.parent)), city=settings.key,
                       sample_design='data/'+settings.key+'/calibration-v1/design.json',
                       note='单次时点API估计，分层便利样本，交叉验证误差不代表未来拥堵或独立城市泛化误差')
    settings.calibration.write_text(json.dumps(calibration,ensure_ascii=False,indent=2)+'\n')

if __name__=='__main__':
    main()
