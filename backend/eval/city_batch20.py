"""南京C4：先录制完整流程输入响应，再在禁止网络的条件下重放评估。

capture 是数据采集，不据实时请求报告评测分数；replay 只读冻结的模型/高德响应。
高德按原来的查询顺序逐次回放，从空的派生缓存开始，保留额度耗尽与降级行为。
"""
from __future__ import annotations
import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from app.amap import AmapBudgetExceeded,AmapRouter,AmapError
from app.amap_limits import RequestBudgetExceeded
from app.llm import OpenAICompatLLM,SnapshotLLM,chat_json
from eval.factorial.common import block_network


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


class RecordedRouter(AmapRouter):
    """只在采集阶段允许HTTP。replay 保持业务分支不变，HTTP出口严格改为文件。"""
    def __init__(self,path:Path,replay:bool=False,reuse_path:Path|None=None):
        super().__init__(key='offline-placeholder' if replay else None)
        self.offline=False  # 保持 RestaurantCatalog 的相同查询分支；出口由 _get 及 socket 双重封锁。
        self.record_path=path
        self.replay=replay
        self.entries=[json.loads(line) for line in path.read_text().splitlines()] if replay else []
        self.reuse_entries={}
        if reuse_path and reuse_path.exists():
            for line in reuse_path.read_text().splitlines():
                entry=json.loads(line)
                if not entry.get('error'):
                    self.reuse_entries[digest({'path':entry['path'],'params':entry['params']})]=entry
        self.cursor=[0]  # fork 共享顺序指针；生产 AmapRouter 不受影响。
        if not replay and path.exists():
            raise ValueError('不覆盖已有高德采集记录')
    def _consume(self):
        # 与线上 AmapRouter._get 一致：额度用尽转为 AmapBudgetExceeded，由路线层降级为估算，不让整份规划失败
        try:
            if self.budget:
                self.budget.consume()
        except RequestBudgetExceeded as error:
            raise AmapBudgetExceeded(str(error)) from None

    def _get(self,path,params):
        if any(key.lower() in {'key','token','api_key','authorization'} for key in params):
            raise ValueError('快照参数不能含密钥')
        if self.replay:
            index=self.cursor[0]
            if index>=len(self.entries):
                raise RuntimeError('离线回放缺少高德响应，禁止联网')
            entry=self.entries[index]
            if entry['path']!=path or entry['params']!=params:
                raise RuntimeError('离线回放的高德请求顺序/参数与冻结记录不同')
            self.cursor[0]+=1
            for _ in range(entry['http_attempts']):
                self._consume()
                self.calls+=1
            if entry.get('budget_exceeded'):
                raise AmapBudgetExceeded(entry['error'])
            if entry.get('error'):
                raise AmapError(entry['error'])
            return entry['response']
        reused=self.reuse_entries.get(digest({'path':path,'params':params}))
        if reused is not None:
            consumed=0
            try:
                for _ in range(reused['http_attempts']):
                    self._consume()
                    self.calls+=1
                    consumed+=1
                entry={**reused,'actual_http_attempts':0,'reused_from_previous_capture':True}
            except AmapBudgetExceeded as error:
                # 额度用尽也要落一条记录，回放才能在同一位置重现降级
                entry={'path':path,'params':params,'error':str(error)[:180],'budget_exceeded':True,
                       'http_attempts':consumed,'actual_http_attempts':0}
            self._record(entry)
            if entry.get('budget_exceeded'):
                raise AmapBudgetExceeded(entry['error'])
            if entry.get('error'):
                raise AmapError(entry['error'])
            return entry['response']
        before=self.calls
        entry={'path':path,'params':params,'captured_at':datetime.now(timezone.utc).isoformat()}
        try:
            result=super()._get(path,params)
            entry['response']=result
            return result
        except AmapError as error:
            entry['error']=str(error)[:180]
            if isinstance(error,AmapBudgetExceeded):
                entry['budget_exceeded']=True
            raise
        finally:
            entry['http_attempts']=self.calls-before
            entry['actual_http_attempts']=self.calls-before
            self._record(entry)

    def _record(self,entry):
        self.entries.append(entry)
        self.record_path.parent.mkdir(parents=True,exist_ok=True)
        with self.record_path.open('a') as target:
            target.write(json.dumps(entry,ensure_ascii=False)+'\n')


def profiles(city='nanjing'):
    import pandas as pd
    from app.cities import city_config
    if city!='nanjing':
        # 新城市：同一组 20 个条件，起点按住宿分布自动取（scripts/onboard_city.py）
        from app.data import load_base_pois
        from scripts.onboard_city import generic_profiles
        settings=city_config(city)
        return generic_profiles(city,pd.read_parquet(settings.data_dir/'accommodations.parquet'),settings.default_origin,load_base_pois(city),
                                (settings.name,settings.province.removesuffix('省')))
    rows=pd.read_parquet(city_config('nanjing').data_dir/'accommodations.parquet').set_index('id')
    def hotel(identifier):
        row=rows.loc[identifier]
        return {'name':row['name']+'附近（数据集参考起点）','lat':float(row.lat),'lon':float(row.lon)}
    def form(identifier,days,transport,budget,pace,companions,interests=(),**extra):
        return {'city':'nanjing','hotel':hotel(identifier),'days':days,'transport':transport,'budget':budget,
                'pace':pace,'companions':companions,'interests':list(interests),**extra}
    return [
      ('01-south-2d-transit',form(0,2,'transit',300,'normal','solo')),
      ('02-south-1d-photo',form(0,1,'transit',200,'relaxed','couple',['拍照出片'])),
      ('03-south-3d-food-night',form(0,3,'taxi',1000,'normal','friends',['美食探店','夜景夜游'])),
      ('04-central-1d-history',form(42,1,'transit',500,'intense','solo',['人文历史','博物馆展览'])),
      ('05-central-2d-elderly',form(42,2,'transit',1500,'relaxed','elderly',['自然风光','园林古镇'])),
      ('06-central-3d-couple-dated',form(42,3,'transit',2000,'normal','couple',['拍照出片','夜景夜游'],start_date='2026-10-24')),
      ('07-lake-2d-kids',form(29,2,'taxi',1500,'normal','family_kids',['亲子游乐'])),
      ('08-south-1d-must-museum',form(0,1,'transit',400,'normal','solo',must_visit=['南京博物院'])),
      ('09-lake-2d-shopping',form(29,2,'transit',800,'intense','friends',['购物街区','美食探店'])),
      ('10-hexi-2d-art',form(117,2,'taxi',1200,'normal','couple',['艺术文创','小众安静'])),
      ('11-hexi-3d-kids',form(117,3,'transit',1500,'relaxed','family_kids',['亲子游乐','自然风光'])),
      ('12-jiangning-2d-hiking',form(10,2,'transit',1000,'normal','solo',['自然风光','户外徒步'])),
      ('13-jiangning-1d-temples',form(10,1,'taxi',600,'relaxed','elderly',['宗教寺庙'])),
      ('14-lake-2d-night',form(29,2,'transit',1200,'normal','couple',['夜景夜游','拍照出片'])),
      ('15-qinhuai-1d-nospicy',form(99,1,'transit',300,'intense','solo',['人文历史'],dietary=['不吃辣'])),
      ('16-qinhuai-2d-must-mausoleum',form(99,2,'taxi',2000,'normal','friends',['人文历史','园林古镇'],must_visit=['中山陵景区'])),
      ('17-jiangning-2d-themepark',form(10,2,'transit',1500,'normal','family_kids',['主题乐园'])),
      ('18-central-2d-museum-monday',form(42,2,'transit',800,'normal','solo',['博物馆展览','艺术文创'],start_date='2026-10-26')),
      ('19-central-4d-relaxed',form(42,4,'transit',3000,'relaxed','couple',['人文历史','自然风光'])),
      ('20-south-2d-taxi',form(0,2,'taxi',500,'normal','solo')),
    ]


def clean(value):
    if isinstance(value,dict):
        return {key:clean(item) for key,item in value.items() if key not in {'fetched_at','fetched_epoch','created_at'}}
    if isinstance(value,list):
        return [clean(item) for item in value]
    return value


def run(root:Path,replay=False,only=None,reuse_root:Path|None=None,city='nanjing'):
    from app import amap,config
    from app.graph import run_plan
    from app.poi_catalog import published_catalog
    from app.nodes import select
    config.LLM_THINKING='adaptive'
    select.chat_json=lambda *a,**kw:chat_json(*a,**{**kw,'retries':0})
    root.mkdir(parents=True,exist_ok=True)
    from app.cities import city_config
    settings=city_config(city)
    source_files=[file for file in (Path(__file__).resolve().parent.parent/'app').rglob('*') if file.is_file() and file.suffix in {'.py','.json'}]
    source_hashes={str(file.relative_to(root_path)):hashlib.sha256(file.read_bytes()).hexdigest() for file in source_files for root_path in [Path(__file__).resolve().parents[2]]}
    data_files=[settings.data_dir/file for file in ('attractions.parquet','restaurants.parquet','accommodations.parquet','attraction_tags.parquet','travel_calibration.json')]
    data_hashes={file.name:hashlib.sha256(file.read_bytes()).hexdigest() for file in data_files}
    design={'app_source_sha256':source_hashes,'city_data_sha256':data_hashes,'city':city,'profiles':[{'id':name,'form':form} for name,form in profiles(city)],
            'poi_catalog':published_catalog(city).reference(),'model':config.LLM_MODEL,
            'thinking':'adaptive','reasoning_effort':'low','sdk_max_retries':0,'json_retries':0,
            'http_limit_per_case':__import__('app.amap_limits',fromlist=['PLAN_HTTP_LIMIT']).PLAN_HTTP_LIMIT,'online_restaurants':config.ONLINE_RESTAURANTS,
            'note':'酒店坐标为ChinaTravel历史参考，不保证当前酒店入口；跨城对照为描述统计，画像地点及来源条件不同。'}
    design_path=root/'design.json'
    if design_path.exists() and json.loads(design_path.read_text())!=design:
        raise ValueError('画像/目录/模型配置与冻结设计不一致')
    if not design_path.exists():
        design_path.write_text(json.dumps(design,ensure_ascii=False,indent=2)+'\n')
    output=root/('replay.json' if replay else 'capture.json')
    rows=json.loads(output.read_text())['results'] if output.exists() else []
    done={row['id'] for row in rows}
    captured={row['id']:row for row in json.loads((root/'capture.json').read_text())['results']} if replay else {}
    for name,form in profiles(city):
        if name in done or only and not any(name.startswith(prefix) for prefix in only):
            continue
        case=root/name
        case.mkdir(exist_ok=True)
        router=RecordedRouter(case/'amap.jsonl',replay,reuse_root/name/'amap.jsonl' if reuse_root else None)
        amap._router=router
        delegate=None if replay else OpenAICompatLLM(retry_attempts=1,sdk_max_retries=0,reasoning_effort='low')
        llm=SnapshotLLM(case/'llm',delegate=delegate)
        start=time.monotonic()
        row={'id':name,'form':form}
        try:
            with block_network() if replay else nullcontext():
                out=run_plan('',dict(form),llm=llm,travel_source='amap',enable_repair=True)
            row.update(profile=out['profile'].model_dump(mode='json'),selected=[item.poi_id for item in out['selected']],
                dropped=out.get('dropped',[]),initial_selected_ids=out.get('initial_selected_ids',[]),
                schedule_attempts=out.get('schedule_attempts',[]),repair_removed_ids=out.get('repair_removed_ids',[]),
                usage=out.get('usage',{}),poi_catalog=out['poi_catalog'],report=out['report'].model_dump(mode='json'),
                itinerary=out['itinerary'].model_dump(mode='json'),log=out['log'],travel_stats=out.get('travel_stats'))
            if replay:
                fields=('profile','selected','dropped','initial_selected_ids','schedule_attempts','repair_removed_ids','poi_catalog','report','itinerary')
                row['capture_equal_fields']={field:clean(row[field])==clean(captured[name].get(field)) for field in fields}
                row['all_http_records_consumed']=router.cursor[0]==len(router.entries)
                row['actual_network_requests']=0
            else:
                row['model_request_attempts']=delegate.request_attempts
                row['actual_response_models']=sorted(delegate.response_models)
        except Exception as error:
            row['error']=f'{type(error).__name__}: {str(error)[:220]}'
        row['seconds']=round(time.monotonic()-start,2)
        row['transport_sha256']=hashlib.sha256((case/'amap.jsonl').read_bytes()).hexdigest() if (case/'amap.jsonl').exists() else None
        rows.append(row)
        output.write_text(json.dumps({'scope':'offline full-pipeline replay, network forbidden' if replay else 'source capture; not evaluation scores',
            'design_sha256':digest(design),'results':rows},ensure_ascii=False,indent=2)+'\n')
        print(name,'failed' if 'error' in row else 'ok',row['seconds'],flush=True)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=('capture','replay','assess'))
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--only',nargs='*')
    parser.add_argument('--city',default='nanjing')
    parser.add_argument('--reuse-root',type=Path,help='修订采集时只复用参数完全相同的已保存响应，记录来源及实际HTTP次数')
    args=parser.parse_args()
    if args.mode=='replay':
        os.environ['TTA_OFFLINE']='1'
    if args.mode=='assess':
        from app.poi_catalog import published_catalog,use_catalog
        from eval import batch20
        with use_catalog(published_catalog(args.city)):
            batch20.assess([args.root/'replay.json'],args.root/'assess.md')
    else:
        run(args.root,args.mode=='replay',args.only,args.reuse_root,args.city)

if __name__=='__main__':
    main()
