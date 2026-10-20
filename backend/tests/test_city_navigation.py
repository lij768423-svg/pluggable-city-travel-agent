"""真实请求schema + 城市上下文 + 高德HTTP替身，避免只测前端模拟响应。"""
import httpx
import pytest
from fastapi.testclient import TestClient
from app import api
from app.cities import current_city
from app.poi_catalog import base_catalog
from app.schemas import UserProfile
from tests.test_accounts import account_store  # noqa: F401
from tests.test_navigation_details_evening import make_router


def body(city='nanjing', **values):
    origin = {'lat':32.02,'lon':118.78} if city == 'nanjing' else {'lat':30.25,'lon':120.16}
    destination = {**origin, 'lon':origin['lon']+.01}
    return {'city':city,'date':'2026-10-20','legs':[
        {'origin':origin,'destination':destination,'mode':'公交/地铁','departure':600} for _ in range(6)],**values}


@pytest.fixture
def navigation_router(monkeypatch):
    calls=[]
    def handle(request):
        calls.append(dict(request.url.params))
        origin=request.url.params['origin'];destination=request.url.params['destination']
        return httpx.Response(200,json={'status':'1','route':{'transits':[
            {'duration':'600','distance':'1000','segments':[{'bus':{'buslines':[
                {'name':'测试地铁','polyline':origin+';'+destination,'distance':'1000'}]}}]}]}})
    router=make_router(handle)
    monkeypatch.setattr('app.navigation.get_router',lambda:router)
    return calls


@pytest.mark.parametrize('city',['hangzhou','nanjing'])
@pytest.mark.parametrize('plan_id',[None,'expired-history'])
def test_navigation_real_schema_and_service_geometry(city,plan_id,navigation_router):
    response=TestClient(api.app).post('/api/navigation',json=body(city,plan_id=plan_id))
    assert response.status_code == 200,response.text
    result=response.json()
    assert len(result['legs']) == 6
    assert all(leg['status']=='ready' and leg['segments'][0]['path'] for leg in result['legs'])
    assert navigation_router[0]['city'] == ('南京' if city=='nanjing' else '杭州')
    assert len(navigation_router) == 1  # 相同路段仍复用缓存
    assert current_city().key == 'hangzhou'  # 请求后上下文还原


def test_old_navigation_without_city_remains_hangzhou(navigation_router):
    payload=body('hangzhou');payload.pop('city')
    assert TestClient(api.app).post('/api/navigation',json=payload).status_code == 200
    assert navigation_router[0]['city']=='杭州'


def test_authorized_plan_city_overrides_caller_city(account_store,navigation_router):  # noqa: F811
    result=api.run_plan()
    result['profile']=UserProfile(city='nanjing')
    result['poi_catalog']=base_catalog('nanjing').reference()
    session=api.SESSIONS.create(result,{})
    payload=body(plan_id=session.plan_id);payload['city']='hangzhou'
    response=TestClient(api.app).post('/api/navigation',json=payload)
    assert response.status_code==200,response.text
    assert navigation_router[0]['city']=='南京'


def test_other_owner_plan_uses_explicit_city_fallback(account_store,navigation_router):  # noqa: F811
    session=api.SESSIONS.create(api.run_plan(),{},owner='user:someone-else')
    response=TestClient(api.app).post('/api/navigation',json=body(plan_id=session.plan_id))
    assert response.status_code==200,response.text
    assert navigation_router[0]['city']=='南京'


def test_cross_city_points_rejected_once_before_external_call(navigation_router):
    payload=body();payload['city']='hangzhou'
    response=TestClient(api.app).post('/api/navigation',json=payload)
    assert response.status_code==422
    assert response.json()['detail']=='导航起终点须在杭州范围内，请检查行程地点后重试。'
    assert not navigation_router


def test_unknown_city_is_rejected_before_external_call(navigation_router):
    payload=body();payload['city']='missing'
    response=TestClient(api.app).post('/api/navigation',json=payload)
    assert response.status_code==422 and not navigation_router


def test_disabled_city_history_can_still_read_navigation(monkeypatch,navigation_router):
    monkeypatch.setenv('TTA_CITIES','hangzhou')
    response=TestClient(api.app).post('/api/navigation',json=body(plan_id='expired-history'))
    assert response.status_code==200,response.text
    assert navigation_router[0]['city']=='南京'
