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
    def social_providers(self): return {"google": True, "kakao": True}
    def oauth_url(self, provider, challenge, callback):
        from urllib.parse import urlencode
        return "https://project.supabase.co/auth/v1/authorize?" + urlencode({"provider":provider, "code_challenge":challenge, "redirect_to":callback})
    def exchange(self, code, verifier):
        if code != "fake-code": raise ApiError(401, "auth_failed", "Invalid code.")
        return self.bundle(User(A, None, False, ("google", "kakao")))
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


def start_social(c, provider='google'):
    res = c.post('/api/auth/oauth', json={'provider':provider}, headers=csrf(c))
    assert res.status_code == 200, res.text
    return res

@pytest.mark.parametrize('provider', ['google', 'kakao'])
def test_oauth_cookie_exchange_refresh_logout(auth_app, provider):
    with TestClient(auth_app) as c:
        start = start_social(c, provider)
        fid = c.cookies.get('rl_oauth')
        assert 'HttpOnly' in start.headers['set-cookie'] and 'SameSite=lax' in start.headers['set-cookie']
        assert set(start.json()) == {'url'}
        res = c.get('/api/auth/oauth/callback?code=fake-code&redirect_to=https://evil.test', follow_redirects=False)
        assert res.status_code == 303 and res.headers['location'] == 'http://testserver/#/builder'
        assert c.cookies.get('rl_oauth') is None
        assert res.headers['referrer-policy'] == 'no-referrer'
        assert c.get('/api/auth/me').json()['user'] == {'id':A, 'email':None, 'email_confirmed':False}
        auth_app.state.test_clock[0] += 301
        assert c.get('/api/echo').json()['id'] == A
        assert auth_app.state.test_provider.refreshed == 1
        sid = c.cookies.get('rl_session')
        c.cookies.set('rl_oauth', fid)
        replay = c.get('/api/auth/oauth/callback?code=fake-code', follow_redirects=False)
        assert 'auth_oauth_failed' in replay.headers['location']
        assert c.cookies.get('rl_session') == sid
        assert c.post('/api/auth/logout', headers=csrf(c)).status_code == 204
        assert c.get('/api/echo').status_code == 401

@pytest.mark.parametrize('kind', ['missing-cookie','expired','cancelled','bad-code','missing-code','oversized-code'])
def test_oauth_failure_does_not_replace_existing_session(auth_app, kind):
    with TestClient(auth_app) as c:
        signin(c)
        start_social(c)
        sid = c.cookies.get('rl_session')
        code, error = 'fake-code', ''
        if kind == 'missing-cookie': c.cookies.delete('rl_oauth')
        if kind == 'expired': auth_app.state.test_clock[0] += 300
        if kind == 'cancelled': error = 'access_denied'
        if kind == 'bad-code': code = 'bad'
        if kind == 'missing-code': code = ''
        if kind == 'oversized-code': code = 'x' * 2049
        res = c.get('/api/auth/oauth/callback', params={'code':code,'error':error}, follow_redirects=False)
        assert res.headers['location'] == 'http://testserver/#/login?auth_error=auth_oauth_failed'
        assert c.cookies.get('rl_session') == sid
        assert c.cookies.get('rl_oauth') is None
        assert c.get('/api/echo').json()['id'] == A


def test_oauth_bound_verifier_and_replacement(auth_app, monkeypatch):
    from urllib.parse import parse_qs, urlsplit
    import base64, hashlib
    auth = auth_app.state.rl.auth
    with TestClient(auth_app) as c, TestClient(auth_app) as other:
        first = start_social(c)
        first_id = c.cookies.get('rl_oauth')
        second = start_social(c)
        second_id = c.cookies.get('rl_oauth')
        assert first_id != second_id and auth.store.take_flow(first_id) is None
        own = start_social(other)
        own_id = other.cookies.get('rl_oauth')
        own_flow = auth.store.take_flow(own_id)
        auth.store.put_flow(own_id, own_flow, 0)
        challenge = parse_qs(urlsplit(own.json()['url']).query)['code_challenge'][0]
        assert challenge == base64.urlsafe_b64encode(hashlib.sha256(own_flow.verifier.encode()).digest()).decode().rstrip('=')
        assert len(own_flow.verifier) == len(challenge) == 43
        assert own_flow.verifier not in own.text and own_flow.verifier not in repr(own_flow)
        def exchange(code, verifier):
            if verifier != own_flow.verifier: raise ApiError(401,'auth_failed','Code/verifier mismatch')
            return auth_app.state.test_provider.bundle(User(A,None,False,('google',)))
        monkeypatch.setattr(auth_app.state.test_provider, 'exchange', exchange)
        # A code for the other browser cannot complete this browser's pending flow.
        bad = c.get('/api/auth/oauth/callback?code=fake-code', follow_redirects=False)
        assert 'auth_oauth_failed' in bad.headers['location']
        assert c.cookies.get('rl_session') is None
        good = other.get('/api/auth/oauth/callback?code=fake-code', follow_redirects=False)
        assert good.headers['location'].endswith('/#/builder')


def test_oauth_start_requires_csrf_and_enabled_allowlisted_provider(auth_app, monkeypatch):
    with TestClient(auth_app) as c:
        assert c.post('/api/auth/oauth',json={'provider':'google'}).status_code == 403
        headers = csrf(c)
        assert c.post('/api/auth/oauth',json={'provider':'evil'},headers=headers).status_code == 422
        monkeypatch.setattr(auth_app.state.test_provider,'social_providers',lambda:{'google':False,'kakao':False})
        assert c.post('/api/auth/oauth',json={'provider':'google'},headers=headers).json()['code'] == 'auth_oauth_unavailable'
        assert c.cookies.get('rl_oauth') is None
        assert c.get('/api/auth/config').json()['social_providers'] == {'google':False,'kakao':False}


def test_oauth_discovery_outage_keeps_email_login(auth_app, monkeypatch):
    def unavailable(): raise ApiError(503,'auth_unavailable','Unavailable')
    monkeypatch.setattr(auth_app.state.test_provider,'social_providers',unavailable)
    with TestClient(auth_app) as c:
        assert c.get('/api/auth/config').json()['social_providers'] == {'google':False,'kakao':False}
        signin(c)
        assert c.get('/api/echo').status_code == 200

@pytest.mark.parametrize('user', [User(A,'a@example.test',True,('kakao',)), User(B,None,False,('google',))])
def test_oauth_wrong_identity_is_rejected(auth_app, monkeypatch, user):
    with TestClient(auth_app) as c:
        start_social(c)
        monkeypatch.setattr(auth_app.state.test_provider,'user',lambda token:user)
        res = c.get('/api/auth/oauth/callback?code=fake-code',follow_redirects=False)
        assert 'auth_oauth_failed' in res.headers['location'] and c.cookies.get('rl_session') is None


def test_social_identity_does_not_bypass_password_confirmation(auth_app, monkeypatch):
    provider = auth_app.state.test_provider
    provider.users['a@example.test'] = User(A,'a@example.test',False,('google',))
    with TestClient(auth_app) as c:
        assert c.post('/api/auth/login',json={'email':'a@example.test','password':'testing-password'},headers=csrf(c)).status_code == 401
        start_social(c)
        c.get('/api/auth/oauth/callback?code=fake-code',follow_redirects=False)
        monkeypatch.setattr(provider,'user',lambda token:User(A,None,False,('kakao',)))
        assert c.get('/api/echo').status_code == 401


def test_logout_cancels_pending_flow(auth_app):
    with TestClient(auth_app) as c:
        start_social(c)
        fid = c.cookies.get('rl_oauth')
        assert c.post('/api/auth/logout',headers=csrf(c)).status_code == 204
        assert c.cookies.get('rl_oauth') is None
        assert auth_app.state.rl.auth.store.take_flow(fid) is None


def test_pending_flow_capacity_and_expiry(auth_app):
    from regime_api.auth.contracts import OAuthFlow
    store = auth_app.state.rl.auth.store
    store.max_sessions = 1
    store.put_flow('one',OAuthFlow('google','private-verifier',1000),700)
    with pytest.raises(RuntimeError): store.put_flow('two',OAuthFlow('kakao','private-verifier2',1001),701)
    store.put_flow('two',OAuthFlow('kakao','private-verifier2',1300),1000)
    assert store.take_flow('one') is None
    assert store.take_flow('two') is not None
    assert store.take_flow('two') is None


def test_supabase_oauth_rest_and_metadata_privacy():
    from urllib.parse import parse_qs, urlsplit
    import json
    calls = []
    user = {'id':A, 'identities':[{'provider':'kakao'}]}
    def respond(req):
        calls.append(req)
        if req.url.path.endswith('/settings'):
            return httpx.Response(200,json={'external':{'google':True,'kakao':False,'secret':'PRIVATE_SECRET'}})
        return httpx.Response(200,json={'user':user,'access_token':'private-access','refresh_token':'private-refresh','expires_in':300})
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        p = SupabaseProvider('https://project.supabase.co','PRIVATE_KEY',10,'http://localhost',client)
        assert p.social_providers() == {'google':True,'kakao':False}
        url = p.oauth_url('kakao','challenge','http://localhost/api/auth/oauth/callback')
        params = parse_qs(urlsplit(url).query, keep_blank_values=True)
        assert params == {'provider':['kakao'],'code_challenge':['challenge'],'code_challenge_method':['s256'],'redirect_to':['http://localhost/api/auth/oauth/callback'],'scope':['']}
        assert 'scope' not in parse_qs(urlsplit(p.oauth_url('google','challenge','http://localhost/api/auth/oauth/callback')).query, keep_blank_values=True)
        tokens = p.exchange('private-code','private-verifier')
        assert tokens.user.email is None and tokens.user.providers == ('kakao',)
        assert calls[-1].url.query == b'grant_type=pkce'
        assert json.loads(calls[-1].content) == {'auth_code':'private-code','code_verifier':'private-verifier'}
        assert calls[-1].headers['apikey'] == 'PRIVATE_KEY'
        with pytest.raises(ApiError): p.oauth_url('evil','challenge','https://evil.test')
    with pytest.raises(ApiError):
        SupabaseProvider._user({'id':A,'user_metadata':{'provider':'google'},'app_metadata':{'provider':'google'}})


def test_https_oauth_cookies_and_query_log_filter(auth_app):
    auth_app.state.rl.auth.site_url = 'https://testserver'
    with TestClient(auth_app,base_url='https://testserver') as c:
        token = c.get('/api/auth/config').json()['csrf_token']
        res = c.post('/api/auth/oauth',json={'provider':'google'},headers={'Origin':'https://testserver','X-CSRF-Token':token})
        assert 'Secure' in res.headers['set-cookie']
        res = c.get('/api/auth/oauth/callback?code=fake-code',follow_redirects=False)
        assert all('Secure' in cookie for cookie in res.headers.get_list('set-cookie'))
    record = logging.LogRecord('uvicorn.access',20,'',1,'%s %s %s %s %s',('client','GET','/api/auth/oauth/callback?code=PRIVATE_CODE','1.1',303),None)
    AuthAccessLogFilter().filter(record)
    assert 'PRIVATE_CODE' not in record.getMessage()
