"""私网首次配置授权、账号隔离、重启持久化与浏览器登录保持。"""
import importlib.util
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import accounts
from app.mcp_login import MCPLoginStore, router, BROWSER_AGE
from app.mcp_service import MCPService
from app.mcp_transport import create_mcp_app


def setup(tmp_path, monkeypatch):
    store = accounts.AccountStore(tmp_path / 'accounts.sqlite3')
    monkeypatch.setattr(accounts, 'STORE', store)
    monkeypatch.setattr(accounts, '_attempts', {})
    app = FastAPI()
    app.include_router(accounts.router)
    app.include_router(router)
    login = MCPLoginStore(tmp_path / 'mcp.sqlite3', store, 'http://testserver')
    app.state.mcp_login = login
    return store, login, TestClient(app)


def test_first_login_consent_persistence_and_browser_reopen(tmp_path, monkeypatch):
    store, login, browser = setup(tmp_path, monkeypatch)
    token = secrets.token_urlsafe(32)
    pending = login.status(token, 'codex-mac')
    identifier = pending['verification_url'].split('=')[1]
    assert token not in str(pending)
    assert browser.get(f'/api/mcp/connect/{identifier}').json()['approved'] is False
    assert login.user(token) is None  # GET 不授予权限
    assert browser.post('/api/mcp/connect', json={'connection_id': identifier}).status_code == 401
    registered = browser.post('/api/auth/register', json={'username': 'owner', 'password': 'password-for-tests'})
    user = registered.json()['user']
    before = browser.cookies.get(accounts.AUTH_COOKIE)
    assert browser.post('/api/mcp/connect', headers={'Origin': 'https://evil.invalid'}, json={'connection_id': identifier}).status_code == 403
    response = browser.post('/api/mcp/connect', json={'connection_id': identifier})
    assert response.status_code == 200
    assert f'Max-Age={BROWSER_AGE}' in response.headers['set-cookie']
    assert 'HttpOnly' in response.headers['set-cookie'] and 'SameSite=strict' in response.headers['set-cookie']
    assert store.user(before) is None
    restored = MCPLoginStore(login.path, store, 'http://testserver')
    assert restored.status(token, 'codex-mac')['user'] == user
    assert restored.user(secrets.token_urlsafe(32)) is None
    # 模拟关闭标签/新标签：同一浏览器持久Cookie可直接读取本人历史。
    reopened = TestClient(browser.app)
    reopened.cookies.update(browser.cookies)
    assert reopened.get('/api/auth/me').json()['user'] == user
    assert reopened.get('/api/history').status_code == 200
    assert token.encode() not in login.path.read_bytes()
    assert login.path.stat().st_mode & 0o777 == 0o600
    assert browser.get(f'/api/mcp/connect/{identifier}').json()['account_matches'] is True
    other = TestClient(browser.app)
    other.post('/api/auth/register', json={'username': 'another_owner', 'password': 'password-for-tests'})
    assert other.get(f'/api/mcp/connect/{identifier}').json()['account_matches'] is False
    assert other.post('/api/mcp/connect', json={'connection_id': identifier}).status_code == 409
    assert browser.post('/api/mcp/connect', json={'connection_id': identifier}).status_code == 409
    restored.revoke(token)
    assert restored.user(token) is None
    assert browser.post('/api/mcp/connect', json={'connection_id': identifier}).status_code == 410
    assert restored.status(token, 'codex-mac')['verification_url'] != pending['verification_url']


def test_expired_link_and_grant_cannot_be_reused(tmp_path, monkeypatch):
    store, login, browser = setup(tmp_path, monkeypatch)
    token = secrets.token_urlsafe(32)
    first = login.status(token, 'test')
    identifier = first['verification_url'].split('=')[1]
    with login.connect() as db:
        db.execute('UPDATE devices SET expires=?', (time.time()-1,))
    assert browser.get(f'/api/mcp/connect/{identifier}').status_code == 410
    second = login.status(token, 'test')
    assert second['verification_url'] != first['verification_url']
    user = store.register('test_owner', 'password-for-tests')
    login.approve(second['verification_url'].split('=')[1], user['id'])
    with login.connect() as db:
        db.execute('UPDATE grants SET expires=?', (time.time()-1,))
    assert login.user(token) is None
    # 过期授权对应的旧同意页不可再次批准。
    assert browser.get('/api/auth/me').json()['user'] is None


def test_bound_transport_checks_actual_browser_account_and_revocation(tmp_path, monkeypatch):
    store, login, browser = setup(tmp_path, monkeypatch)
    first = store.register('first_user', 'password-for-tests')
    second = store.register('second_user', 'password-for-tests')
    service = MCPService(store=store, ledger_path=tmp_path/'ledger.sqlite3')
    secret = 'test-private-transport-credential'
    token = secrets.token_urlsafe(32)
    app = create_mcp_app(service, f"user:{first['id']}", secret, port=18687, login=login)
    headers = {'Authorization': f'Bearer {token}', 'X-Travel-Transport': secret, 'X-Travel-Client': 'test-client', 'Accept': 'application/json, text/event-stream'}
    try:
        with TestClient(app, base_url='http://127.0.0.1:18687') as client:
            assert client.post('/mcp', json={}).status_code == 401
            assert client.post('/mcp', headers={'Authorization': f'Bearer {secret}'}, json={}).status_code == 401
            assert client.post('/mcp', headers={'Authorization': f'Bearer {token}'}, json={}).status_code == 401
            response = client.post('/mcp', headers=headers, json={'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'test','version':'1'}}})
            assert response.status_code == 200, response.text
            headers['MCP-Protocol-Version'] = response.json()['result']['protocolVersion']
            def call(name, args={}):
                response = client.post('/mcp', headers=headers, json={'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':name,'arguments':args}})
                assert response.status_code == 200, response.text
                return response.json()['result']['structuredContent']
            pending = call('get_trip', {'trip_id':'private-trip'})
            assert pending['status'] == 'login_required'
            identifier = call('connect_account')['verification_url'].split('=')[1]
            # 授权所选second_user，不沿用后台first_user预绑定身份。
            login.approve(identifier, second['id'])
            assert call('connect_account')['user'] == second
            seen = []
            monkeypatch.setattr(service, 'get_trip', lambda owner, *args: seen.append(owner) or {'status':'ok'})
            assert call('get_trip', {'trip_id':'private-trip'})['status'] == 'ok'
            assert seen == [f"user:{second['id']}"]
            assert call('disconnect_account')['status'] == 'disconnected'
            assert call('get_trip', {'trip_id':'private-trip'})['status'] == 'login_required'
            assert len(seen) == 1
    finally:
        service.close()


def test_stdio_profile_credential_is_persistent_and_private(tmp_path):
    spec = importlib.util.spec_from_file_location('travel_stdio', Path(__file__).parents[1]/'scripts/mcp_stdio.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    token = module.client_credential('codex', tmp_path)
    assert token == module.client_credential('codex', tmp_path)
    assert token != module.client_credential('claude', tmp_path)
    assert (tmp_path/'codex.token').stat().st_mode & 0o777 == 0o600
    import pytest
    with pytest.raises(ValueError):
        module.client_credential('../outside', tmp_path)
