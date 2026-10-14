"""다른 사용자 자원 접근을 차단하는 HTTP 계약. 원본·실제 키 없이 검증한다."""
import pytest
from fastapi.testclient import TestClient
from regime_api.auth.store import AuthStore
from test_auth_contract import auth_app, signin, csrf, start_social, A, B

STRATEGY={"patterns":["breakout_20d"]}

def test_other_user_cannot_read_cancel_or_generate_report(auth_app):
    with TestClient(auth_app) as a, TestClient(auth_app) as b:
        ha=signin(a,'a');hb=signin(b,'b')
        result=a.post('/api/runs',json={'strategies':[{"name":"mine",**STRATEGY}]},headers=ha)
        assert result.status_code==202, result.text
        rid=result.json()['run_id']
        assert a.get('/api/runs/'+rid).status_code==200
        assert b.get('/api/runs/'+rid).status_code==404
        assert b.get('/api/runs/'+rid+'/result').status_code==404
        assert b.get('/api/runs/'+rid+'/stocks/000001').status_code==404
        assert b.post('/api/runs/'+rid+'/cancel',headers=hb).status_code==404
        assert b.post('/api/runs/'+rid+'/report',headers=hb).status_code==404
        assert b.delete('/api/runs/'+rid,headers=hb).status_code==404  # 다른 사용자는 지울 수 없다 (10-13 E20)
        assert a.get('/api/runs/'+rid).status_code==200
        assert b.get('/api/runs').json()=={'runs':[]}
        assert len(a.get('/api/runs').json()['runs'])==1
        busy=b.post('/api/runs',json={'strategies':[{"name":"another",**STRATEGY}]},headers=hb)
        assert busy.status_code==409 and busy.json()['detail'] is None
        assert auth_app.state.rl.jobs.cancelled==[]

def test_preset_names_and_crud_are_private(auth_app):
    with TestClient(auth_app) as a, TestClient(auth_app) as b:
        ha=signin(a,'a');hb=signin(b,'b')
        one=a.post('/api/presets',json={'name':'same-name','strategy':STRATEGY},headers=ha)
        two=b.post('/api/presets',json={'name':'same-name','strategy':STRATEGY},headers=hb)
        assert one.status_code==201,one.text
        assert two.status_code==201,two.text
        pid=one.json()['id']
        assert b.get('/api/presets/'+pid).status_code==404
        assert b.put('/api/presets/'+pid,json={'revision':1,'name':'stolen'},headers=hb).status_code==404
        assert b.delete('/api/presets/'+pid,headers=hb).status_code==404
        assert [p['id'] for p in a.get('/api/presets').json()['presets']]==[pid]
        assert a.delete('/api/presets/'+pid,headers=ha).status_code==204

def test_search_reference_and_legacy_resource_are_hidden(auth_app):
    with TestClient(auth_app) as a, TestClient(auth_app) as b:
        signin(a,'a');hb=signin(b,'b')
        auth_app.state.rl.auth.store.claim('search','foreign-search',A)
        assert b.get('/api/searches/foreign-search').status_code==404
        assert b.post('/api/presets',json={'name':'stolen','from_search':{'search_id':'foreign-search','candidate_id':'candidate'}},headers=hb).status_code==404
        auth_app.state.rl.jobs.rows.append({'run_id':'legacy','status':'completed'})
        assert a.get('/api/runs/legacy').status_code==404
        assert a.get('/api/runs').json()=={'runs':[]}
        assert b.get('/api/llm/config').status_code==403

def test_ownership_is_durable_and_immutable(tmp_path):
    store=AuthStore(tmp_path/'auth',10)
    store.claim('run','resource',A)
    with pytest.raises(RuntimeError):store.claim('run','resource',B)
    restarted=AuthStore(tmp_path/'auth',10)
    assert restarted.owner('run','resource')==A
    assert restarted.get('not-a-session') is None

def test_failed_ownership_record_returns_no_success(auth_app,monkeypatch):
    with TestClient(auth_app) as c:
        headers=signin(c)
        def fail(*args):raise OSError('internal disk failure')
        monkeypatch.setattr(auth_app.state.rl.auth.store,'claim',fail)
        response=c.post('/api/runs',json={'strategies':[{"name":"mine",**STRATEGY}]},headers=headers)
        assert response.status_code==503
        assert 'internal disk failure' not in response.text
        assert c.get('/api/runs/fake-run').status_code==404


def test_social_login_preserves_uuid_ownership(auth_app):
    with TestClient(auth_app) as c, TestClient(auth_app) as other:
        headers = signin(c)
        run = c.post('/api/runs',json={'strategies':[{'name':'mine',**STRATEGY}]},headers=headers)
        rid = run.json()['run_id']
        old_sid = c.cookies.get('rl_session')
        start_social(c, 'kakao')
        c.get('/api/auth/oauth/callback?code=fake-code',follow_redirects=False)
        assert auth_app.state.rl.auth.store.get(old_sid) is None
        assert c.get('/api/runs/'+rid).status_code == 200
        signin(other, 'b')
        assert other.get('/api/runs/'+rid).status_code == 404
        assert other.get('/api/runs').json() == {'runs':[]}


def test_guests_keep_separate_workspaces_and_cannot_call_paid_models(auth_app):
    with TestClient(auth_app) as guest, TestClient(auth_app) as other, TestClient(auth_app) as member:
        hg, ho = csrf(guest), csrf(other)
        assert guest.post('/api/auth/guest',headers=hg).status_code == 200
        assert other.post('/api/auth/guest',headers=ho).status_code == 200
        hm = signin(member)
        uid = guest.get('/api/auth/me').json()['user']['id']
        assert uid != other.get('/api/auth/me').json()['user']['id']
        run = guest.post('/api/runs',json={'strategies':[{'name':'guest-test',**STRATEGY}]},headers=hg)
        assert run.status_code == 202, run.text
        rid = run.json()['run_id']
        assert guest.get('/api/runs/'+rid).status_code == 200
        assert len(guest.get('/api/runs').json()['runs']) == 1
        for c, headers in ((other,ho),(member,hm)):
            assert c.get('/api/runs/'+rid).status_code == 404
            assert c.get('/api/runs').json()['runs'] == []
            assert c.post('/api/runs/'+rid+'/cancel',headers=headers).status_code == 404
        preset = guest.post('/api/presets',json={'name':'guest-test','strategy':STRATEGY},headers=hg)
        assert preset.status_code == 201, preset.text
        pid = preset.json()['id']
        assert other.get('/api/presets/'+pid).status_code == 404
        assert member.get('/api/presets/'+pid).status_code == 404
        assert guest.post('/api/runs/'+rid+'/report',headers=hg).status_code == 403
        assert guest.get('/api/llm/config').status_code == 403
        assert guest.put('/api/llm/config',json={},headers=hg).status_code == 403
        assert guest.post('/api/auth/logout',headers=hg).status_code == 204
        assert guest.post('/api/auth/guest',headers=csrf(guest)).status_code == 200
        assert guest.get('/api/runs/'+rid).status_code == 404
        assert guest.get('/api/presets/'+pid).status_code == 404
        signin(guest)
        assert guest.get('/api/runs/'+rid).status_code == 404
