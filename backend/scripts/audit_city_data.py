"""输出新城市数据问题与待人审表，不将模型分歧计作事实错误。"""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime,timezone
import json
from pathlib import Path
import sys
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.cities import city_config
from app.data import load_base_pois
from app.geo import parse_hm


def audit(city: str) -> dict:
    settings=city_config(city)
    raw=pd.read_parquet(settings.data_dir/'attractions.parquet')
    pois=load_base_pois(city)
    review_path=settings.assets_dir/'poi_reviews.json'
    reviews=json.loads(review_path.read_text()) if review_path.exists() else {'places':[]}
    reference_path=settings.assets_dir/'stay_reference.json'
    reference=json.loads(reference_path.read_text()) if reference_path.exists() else None
    references={entry['poi_id']:entry for entry in reference['places']} if reference else {}
    issues=[]
    for row in raw.itertuples():
        flags=[]
        if '马鞍山市花山区' in pois[row.id].review_note and not pois[row.id].recommendable:
            flags.append('outside_city_quarantined')
        if parse_hm(row.endtime)<=parse_hm(row.opentime):
            flags.append('cross_midnight_or_invalid_hours')
        if row.price==0 and any(word in row.name for word in ('游船','画舫','乐园','艺术博物馆','园博园')):
            flags.append('zero_price_scope_needs_review')
        if any(word in row.name for word in ('桥','牌坊','玄武门','流徽榭')) and row.recommendmintime>=2:
            flags.append('landmark_duration_scope_needs_review')
        if '桥' in row.name and row.type=='游乐园/体育娱乐':
            flags.append('type_scope_needs_review')
        ref=references.get(row.id)
        if ref:
            raw_mid=(row.recommendmintime+row.recommendmaxtime)*30
            if abs(raw_mid-ref['typical_min'])>=60 and max(raw_mid,ref['typical_min'])>=2*min(raw_mid,ref['typical_min']):
                flags.append('model_duration_disagreement_not_ground_truth')
            if ref['confidence']=='low':
                flags.append('model_reference_low_confidence')
        if flags:
            issues.append({'id':int(row.id),'name':row.name,'type':row.type,'price':float(row.price),
                           'raw_open':row.opentime,'raw_close':row.endtime,
                           'raw_low_min':row.recommendmintime*60,'raw_high_min':row.recommendmaxtime*60,
                           'model_typical_min':ref['typical_min'] if ref else None,
                           'flags':flags,'auto_recommendable':pois[row.id].recommendable,
                           'review_status':'pending_human_review'})
    return {'city':city,'created_at':datetime.now(timezone.utc).isoformat(),'pois':len(raw),
            'ai_review_entries':len(reviews['places']),'ai_review_unique_pois':len({entry['id'] for entry in reviews['places']}),
            'human_reviewed_pois':0,'human_review_minutes':0,
            'human_review_accuracy':None,'human_time_for_full_city':'未实测，不能用AI会话壁钟代替',
            'reference_count':len(references),'issue_pois':len(issues),
            'flag_counts':dict(Counter(flag for issue in issues for flag in issue['flags'])),
            'issues':issues,
            'limits':['AI来源审阅不是人类或现场核验','独立停留参考为三次模型估计，不作真实时长或准确率标签','价格上限不是当期成交价','未列出问题不等于已核实正确']}


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--city',required=True)
    args=parser.parse_args()
    result=audit(args.city)
    out=city_config(args.city).data_dir/'audit-v1'
    out.mkdir(exist_ok=True)
    (out/'issues.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    pd.DataFrame(result['issues']).to_csv(out/'issues.csv',index=False,encoding='utf-8-sig')
    print(json.dumps({key:value for key,value in result.items() if key not in {'issues','limits'}},ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
