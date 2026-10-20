"""C3 展示配置不能依赖当前问卷城市；单独启用其他城市也能启动。"""
import json
from fastapi.testclient import TestClient
from app import accounts, api
from app.presentation import serialize_plan
from tests.test_accounts import account_store, register  # noqa: F401
from tests.test_cities_c1 import synthetic_cities  # noqa: F401


def test_meta_defaults_to_enabled_city_when_hangzhou_disabled(synthetic_cities, monkeypatch):  # noqa: F811
    monkeypatch.setenv('TTA_CITIES', 'testcity')
    client = TestClient(api.app)
    payload = client.get('/api/meta').json()
    assert payload['city'] == 'testcity'
    assert [city['key'] for city in payload['cities']] == ['testcity']
    assert client.get('/api/meta?city=hangzhou').status_code == 409


def test_live_plan_presents_its_own_city(account_store, synthetic_cities):  # noqa: F811
    from app.schemas import UserProfile
    from app.poi_catalog import base_catalog
    result = api.run_plan()
    result['profile'] = UserProfile(city='testcity', days=1)
    result['poi_catalog'] = base_catalog('testcity').reference()
    payload = serialize_plan(result, {'select_mode':'rule', 'enable_repair':True, 'guide_mode':'off'})
    assert payload['city_info']['key'] == 'testcity'
    assert payload['city_info']['name'] == '测试城'
    assert payload['city_info']['default_origin']['name'] == '测试起点'


def test_old_and_disabled_history_receive_city_presentation_without_rewrite(account_store, synthetic_cities, monkeypatch):  # noqa: F811
    client = TestClient(api.app)
    user = register(client).json()['user']
    identifier = account_store.save(user['id'], 'old-source', {}, {'profile': {'days': 1}, 'days': [], 'revision': 0})
    with account_store.connect() as database:
        row = database.execute('SELECT plan_json FROM trips WHERE id=?', (identifier,)).fetchone()
        snapshot = json.loads(row[0])
        snapshot.pop('city_info', None)
        snapshot['profile'].pop('city', None)
        original = json.dumps(snapshot, ensure_ascii=False)
        database.execute('UPDATE trips SET plan_json=? WHERE id=?', (original,identifier))
    old = client.get(f'/api/history/{identifier}').json()['plan']
    assert old['city_info']['key'] == 'hangzhou' and old['city_info']['brand'] == '杭州行记'
    with account_store.connect() as database:
        assert database.execute('SELECT plan_json FROM trips WHERE id=?',(identifier,)).fetchone()[0] == original
        snapshot['profile']['city'] = 'testcity'
        database.execute('UPDATE trips SET plan_json=? WHERE id=?', (json.dumps(snapshot),identifier))
    monkeypatch.setenv('TTA_CITIES','hangzhou')
    disabled = client.get(f'/api/history/{identifier}').json()['plan']
    assert disabled['city_info']['key'] == 'testcity' and disabled['history_readonly']
