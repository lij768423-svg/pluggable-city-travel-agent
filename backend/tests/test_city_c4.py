from __future__ import annotations
import json
from types import SimpleNamespace
import pytest
from app.amap import AmapError
from app.amap_limits import RequestBudget
from eval.city_batch20 import RecordedRouter


def test_transport_replay_preserves_http_attempt_budget_and_fork_order(tmp_path):
    path=tmp_path/'amap.jsonl'
    entries=[{'path':'v3/distance','params':{'type':1},'http_attempts':3,'response':{'status':'1','results':[]}},
             {'path':'v5/place/around','params':{'page_num':1},'http_attempts':1,'error':'高德请求未成功（10001）'}]
    path.write_text('\n'.join(json.dumps(row) for row in entries)+'\n')
    base=RecordedRouter(path,replay=True)
    budget=RequestBudget(4)
    router=base.fork(budget)
    assert router._get('v3/distance',{'type':1})==entries[0]['response']
    with pytest.raises(AmapError,match='10001'):
        router._get('v5/place/around',{'page_num':1})
    assert budget.used==4 and base.cursor==[2]
    with pytest.raises(RuntimeError,match='缺少'):
        router._get('v3/distance',{'type':1})


def test_transport_replay_rejects_changed_request_and_secret_params(tmp_path):
    path=tmp_path/'amap.jsonl'
    path.write_text(json.dumps({'path':'x','params':{'city':'南京'},'http_attempts':1,'response':{}})+'\n')
    router=RecordedRouter(path,replay=True)
    with pytest.raises(RuntimeError,match='顺序/参数'):
        router._get('x',{'city':'杭州'})
    assert router.cursor==[0]
    with pytest.raises(ValueError,match='密钥'):
        router._get('x',{'key':'test-placeholder'})


def test_explicit_reasoning_effort_is_in_payload_and_cache_identity(tmp_path,monkeypatch):
    from app import config
    from app.llm import OpenAICompatLLM
    monkeypatch.setattr(config,'LLM_CACHE_DIR',tmp_path)
    monkeypatch.setattr(config,'LLM_THINKING','adaptive')
    payloads=[]
    def response(**payload):
        payloads.append(payload)
        return SimpleNamespace(model='MiniMax-M3.1-Flash-Preview', choices=[SimpleNamespace(message=SimpleNamespace(content='{"ok":true}'))],usage=None)
    first=OpenAICompatLLM(api_key='test-placeholder',model='MiniMax-M3.1-Flash-Preview',cache=True,reasoning_effort=None)
    low=OpenAICompatLLM(api_key='test-placeholder',model='MiniMax-M3.1-Flash-Preview',cache=True,reasoning_effort='low')
    monkeypatch.setattr(first.client.chat.completions,'create',response)
    monkeypatch.setattr(low.client.chat.completions,'create',response)
    messages=[{'role':'user','content':'test'}]
    first.complete(messages)
    low.complete(messages)
    low.complete(messages)
    assert len(payloads)==2
    assert 'reasoning_effort' not in payloads[0]
    assert payloads[1]['reasoning_effort']=='low'
    assert low.usage.cached_calls==1
    assert len(list(tmp_path.rglob('*.json')))==2


def test_nanjing_review_keeps_upper_bound_separate_from_verified_sale():
    from app.cities import city_config
    from app.data import load_base_pois
    if not (city_config('nanjing').data_dir/'attractions.parquet').exists():
        pytest.skip('先按C4说明下载南京数据')
    places=load_base_pois('nanjing')
    assert len(places)==323
    assert places[22].price==10 and not places[22].price_verified
    assert places[22].field_evidence['price'].basis=='curator_inference'
    assert places[7].price==35 and places[7].price_verified
    assert places[6].opening_rules.closed_weekdays==[0]
    assert places[23].opening_rules.last_entry==990
    assert not places[5].recommendable and not places[314].recommendable and not places[227].recommendable
    assert places[248].max_hours==.5


def test_reuse_saved_transport_response_does_not_repeat_network(tmp_path,monkeypatch):
    from app import config
    monkeypatch.setattr(config,'AMAP_WEB_KEY','test-placeholder')
    old=tmp_path/'old.jsonl'
    entry={'path':'v3/distance','params':{'type':1},'http_attempts':2,'response':{'status':'1','results':[]},'captured_at':'2026-10-19T14:00:00Z'}
    old.write_text(json.dumps(entry)+'\n')
    output=tmp_path/'new.jsonl'
    router=RecordedRouter(output,reuse_path=old).fork(RequestBudget(4))
    def forbidden(*args,**kwargs):
        raise AssertionError('unexpected HTTP request')
    monkeypatch.setattr(router._client,'get',forbidden)
    assert router._get('v3/distance',{'type':1})==entry['response']
    saved=json.loads(output.read_text())
    assert saved['actual_http_attempts']==0 and saved['http_attempts']==2
    assert saved['captured_at']==entry['captured_at'] and router.budget.used==2
