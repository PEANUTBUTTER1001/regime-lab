"""X3 authentication contracts: no original data, real keys, accounts or external HTTP."""
from types import SimpleNamespace
from pathlib import Path
import logging
import httpx
import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from regime_api.auth.contracts import User, Tokens
from regime_api.auth.supabase import SupabaseProvider, AuthAccessLogFilter
from regime_api.errors import ApiError
from regime_api.jobs import Busy
from regime_api.main import create_app
from regime_api.settings import Settings
from regime_lab.config import Paths

A = "11111111-1111-4111-8111-111111111111"
B = "22222222-2222-4222-8222-222222222222"

class FakeProvider:
    def __init__(self):
        self.users = {"a@example.test":User(A, "a@example.test"), "b@example.test":User(B, "b@example.test")}
        self.tokens = {}
        self.refreshed = 0
        self.updated = []
        self.mail = []
        self.revoked = []
    def bundle(self, user):
        access, refresh = "fake-access-" + user.id, "fake-refresh-" + user.id
        self.tokens[access] = user
        self.tokens[refresh] = user
        return Tokens(user, access, refresh, 300)
    def login(self, email, password):
        if email not in self.users or password != "testing-password":
            raise ApiError(401, "auth_failed", "Invalid credentials.")
        return self.bundle(self.users[email])
    def signup(self, email, password): self.mail.append(email)
    def recover(self, email): self.mail.append(email)
    def user(self, access): return self.tokens[access]
    def refresh(self, refresh):
        self.refreshed += 1
        return self.bundle(self.tokens[refresh])
    def verify(self, token_hash, kind):
        if token_hash != "fake-verification": raise ApiError(401, "auth_failed", "Invalid link.")
        return self.bundle(self.users["a@example.test"])
    def password(self, access, password): self.updated.append(password)
    def logout(self, access, scope="local"): self.revoked.append((access,scope))

class FakeJobs:
    def __init__(self): self.rows = []; self.cancelled = []
    def submit(self, strategies):
        if self.rows: raise Busy(self.rows[0]["run_id"])
        row = {"run_id":"fake-run", "status":"queued"}
        self.rows.append(row)
        return SimpleNamespace(**row)
    def list(self, limit=50): return self.rows[:limit]
    def get(self, rid): return next((r for r in self.rows if r["run_id"] == rid), None)
    def cancel(self, rid): self.cancelled.append(rid); return True
    def list_searches(self, limit=50): return []

@pytest.fixture
def auth_app(tmp_path):
    paths = Paths(tmp_path/'store', tmp_path/'dump', tmp_path/'cache', tmp_path/'runs')
    settings = Settings(paths=paths, auth_mode="supabase", supabase_url="https://project.supabase.co",
                        supabase_publishable_key="sb_publishable_" + "test", auth_site_url="http://testserver",
                        auth_state_dir=tmp_path/'auth', auth_admin_ids=[A])
    # Test-only HTTP origin; production settings require HTTPS or loopback.
    settings.auth_site_url = "http://127.0.0.1"
    provider = FakeProvider()
    app = create_app(settings, prep=object(), rag=object(), serve_web=False, auth_provider=provider)
    app.state.rl.auth.site_url = "http://testserver"
    clock = [1000.0]
    app.state.rl.auth.now = lambda:clock[0]
    app.state.rl.jobs = FakeJobs()
    @app.get("/api/echo")
    def echo(request:Request): return {"id":request.state.auth_user.id}
    app.state.test_provider = provider
    app.state.test_clock = clock
    return app

def csrf(c):
    token = c.get('/api/auth/config').json()['csrf_token']
    return {'Origin':'http://testserver', 'X-CSRF-Token':token}

def signin(c, who='a'):
    headers = csrf(c)
    result = c.post('/api/auth/login',json={'email':who+'@example.test','password':'testing-password'},headers=headers)
    assert result.status_code == 200, result.text
    return headers

def test_login_logout_and_cookie_replay(auth_app):
    with TestClient(auth_app) as c:
        headers = signin(c)
        raw = c.cookies.get('rl_session')
        assert c.get('/api/auth/me').json()['user']['id'] == A
        assert c.get('/api/echo').json()['id'] == A
        assert c.post('/api/auth/logout',headers=headers).status_code == 204
        c.cookies.set('rl_session',raw)
        assert c.get('/api/echo').status_code == 401
        assert auth_app.state.test_provider.revoked

def test_bad_credentials_and_csrf(auth_app):
    with TestClient(auth_app) as c:
        headers=csrf(c)
        assert c.post('/api/auth/login',json={'email':'a@example.test','password':'wrong'},headers=headers).status_code == 401
        assert c.post('/api/auth/signup',json={'email':'a@example.test','password':'testing-password'},headers={'Origin':'http://evil.test'}).status_code == 403
        assert c.post('/api/auth/login',json={'email':'a@example.test','password':'testing-password'},headers={'Origin':'http://testserver'}).status_code == 403
        assert c.get('/api/echo').status_code == 401

def test_cookie_flags_no_provider_tokens_and_no_cache(auth_app):
    with TestClient(auth_app) as c:
        headers=csrf(c)
        res=c.post('/api/auth/login',json={'email':'a@example.test','password':'testing-password'},headers=headers)
        assert 'HttpOnly' in res.headers['set-cookie']
        assert 'SameSite=lax' in res.headers['set-cookie']
        assert 'no-store' == res.headers['cache-control']
        assert 'fake-access' not in res.text and 'fake-refresh' not in res.text
        assert 'sb_publishable' not in c.get('/api/auth/config').text

def test_expiry_refresh_and_relogin_rotation(auth_app):
    with TestClient(auth_app) as c:
        signin(c)
        old=c.cookies.get('rl_session')
        auth_app.state.test_clock[0]+=301
        assert c.get('/api/auth/me').status_code==200
        assert auth_app.state.test_provider.refreshed==1
        signin(c)
        assert c.cookies.get('rl_session') != old
        c.cookies.set('rl_session',old)
        assert c.get('/api/auth/me').status_code==401
        signin(c)
        auth_app.state.test_clock[0]+=1801
        assert c.get('/api/echo').status_code==401

def test_rate_limit_and_password_length(auth_app):
    with TestClient(auth_app) as c:
        headers=csrf(c)
        assert c.post('/api/auth/signup',json={'email':'a@example.test','password':'short'},headers=headers).status_code==422
        for _ in range(5):
            assert c.post('/api/auth/login',json={'email':'a@example.test','password':'bad'},headers=headers).status_code==401
        assert c.post('/api/auth/login',json={'email':'a@example.test','password':'bad'},headers=headers).status_code==429

def test_recovery_has_restricted_session_and_invalid_links(auth_app):
    with TestClient(auth_app) as c:
        signin(c)
        assert c.post('/api/auth/password',json={'password':'new-password-value'},headers=csrf(c)).status_code==401
        res=c.get('/api/auth/recovery/confirm?token_hash=fake-verification',follow_redirects=False)
        assert res.status_code==303 and res.headers['location'].endswith('/#/password')
        assert c.post('/api/auth/password',json={'password':'new-password-value'},headers=csrf(c)).status_code==204
        assert auth_app.state.test_provider.updated==['new-password-value']
        assert c.get('/api/echo').status_code==401
        res=c.get('/api/auth/confirm?token_hash=invalid&redirect_to=https://evil.test',follow_redirects=False)
        assert res.headers['location']=='http://testserver/#/login?auth_error=auth_failed'

def test_recovery_cookie_cannot_access_regular_api(auth_app):
    with TestClient(auth_app) as c:
        c.get('/api/auth/recovery/confirm?token_hash=fake-verification',follow_redirects=False)
        assert c.get('/api/echo').status_code==401

def test_sensitive_callback_log_query_is_removed():
    record=logging.LogRecord('uvicorn.access',20,'',1,'%s %s %s %s %s',('client','GET','/api/auth/confirm?token_hash=TOP_SECRET','1.1',303),None)
    AuthAccessLogFilter().filter(record)
    assert 'TOP_SECRET' not in record.getMessage()

def test_supabase_error_never_echoes_body_or_key():
    transport=httpx.MockTransport(lambda req:httpx.Response(400,json={'message':'private-key-and-password'}))
    p=SupabaseProvider('https://project.supabase.co','PRIVATE_KEY',10,'http://localhost',httpx.Client(transport=transport))
    with pytest.raises(ApiError) as exc:p.login('a@example.test','PRIVATE_PASSWORD')
    assert 'private-key' not in str(exc.value.message)
    assert 'PRIVATE_KEY' not in str(exc.value.message)
    assert exc.value.status==401

def test_bad_settings_fail_closed(tmp_path):
    paths=Paths(tmp_path/'store',tmp_path/'dump',tmp_path/'cache',tmp_path/'runs')
    with pytest.raises(ValueError):create_app(Settings(paths=paths,auth_mode='supabase'),serve_web=False)
    with pytest.raises(ValueError):create_app(Settings(paths=paths,host='0.0.0.0'),serve_web=False)



def test_unconfirmed_account_is_rejected(auth_app):
    auth_app.state.test_provider.users['a@example.test'] = User(A,'a@example.test',False)
    with TestClient(auth_app) as c:
        res=c.post('/api/auth/login',json={'email':'a@example.test','password':'testing-password'},headers=csrf(c))
        assert res.status_code==401 and res.json()['code']=='auth_unconfirmed'
        assert c.cookies.get('rl_session') is None

def test_refresh_cannot_change_identity(auth_app,monkeypatch):
    with TestClient(auth_app) as c:
        signin(c)
        monkeypatch.setattr(auth_app.state.test_provider,'refresh',lambda token:auth_app.state.test_provider.bundle(User(B,'b@example.test')))
        auth_app.state.test_clock[0]+=301
        assert c.get('/api/echo').status_code==401

def test_maximum_session_lifetime_even_with_activity(auth_app):
    with TestClient(auth_app) as c:
        signin(c)
        for _ in range(28):
            auth_app.state.test_clock[0]+=1000
            assert c.get('/api/echo').status_code==200
        auth_app.state.test_clock[0]+=801
        assert c.get('/api/echo').status_code==401

def test_signup_does_not_reveal_account_existence(auth_app,monkeypatch):
    with TestClient(auth_app) as c:
        headers=csrf(c)
        body={'email':'a@example.test','password':'testing-password'}
        ordinary=c.post('/api/auth/signup',json=body,headers=headers)
        def exists(*args):raise ApiError(401,'auth_failed','Account exists')
        monkeypatch.setattr(auth_app.state.test_provider,'signup',exists)
        duplicate=c.post('/api/auth/signup',json=body,headers=headers)
        assert duplicate.status_code==ordinary.status_code==202
        assert duplicate.json()==ordinary.json()

def test_https_session_cookie_is_secure(auth_app):
    auth_app.state.rl.auth.site_url='https://testserver'
    with TestClient(auth_app,base_url='https://testserver') as c:
        token=c.get('/api/auth/config').json()['csrf_token']
        response=c.post('/api/auth/login',json={'email':'a@example.test','password':'testing-password'},headers={'Origin':'https://testserver','X-CSRF-Token':token})
        assert response.status_code==200
        assert 'Secure' in response.headers['set-cookie']
