import asyncio
import json
from types import SimpleNamespace
from fastapi.testclient import TestClient
from regime_api import main
from regime_api.settings import Settings
from regime_lab.config import Paths


def settings(tmp_path):
    return Settings(paths=Paths(tmp_path, tmp_path/'unused', tmp_path/'cache', tmp_path/'runs', tmp_path/'old'),
                    news_db={'poll_seconds': 0.01, 'runtime_dir': str(tmp_path/'runtime')})


def test_failure_retains_data_and_recovers(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    old = object()
    state = SimpleNamespace(settings=cfg, paths=cfg.paths, rag=old, docs=old, rag_status='ready', rag_error=None)
    def fail(state):
        raise RuntimeError('private upstream credential')
    monkeypatch.setattr(main, '_read_evidence_update', fail)
    asyncio.run(main._refresh_evidence(state))
    assert state.rag is old and state.docs is old
    assert state.news_refresh['status'] == 'stale'
    assert 'private' not in json.dumps(state.news_refresh)
    new = object()
    monkeypatch.setattr(main, '_read_evidence_update', lambda _: (tmp_path/'new', {'snapshot_id':'new'},new,new))
    asyncio.run(main._refresh_evidence(state))
    assert state.rag is new and state.docs is new
    assert state.settings.paths.ext_store == tmp_path/'new'
    assert state.news_refresh['status'] == 'ok'
    monkeypatch.setattr(main, '_read_evidence_update', lambda _: None)
    asyncio.run(main._refresh_evidence(state))
    assert state.rag is new and state.news_refresh['last_success']['snapshot_id'] == 'new'


def test_lifespan_polls_and_stops(tmp_path, monkeypatch):
    import time
    cfg = settings(tmp_path)
    calls=[]
    def update(state):
        calls.append(1)
        return None
    monkeypatch.setattr(main, '_read_evidence_update', update)
    app = main.create_app(cfg, prep=object(), rag=object(), serve_web=False)
    with TestClient(app):
        deadline=time.monotonic()+2
        while len(calls)<3 and time.monotonic()<deadline:
            time.sleep(.01)
        assert len(calls)>=3
    stopped=len(calls)
    time.sleep(.05)
    assert len(calls)==stopped


def test_http_reads_replaced_index_without_restart(tmp_path, monkeypatch):
    cfg=settings(tmp_path)
    old,new=object(),object()
    app=main.create_app(cfg,prep=object(),rag=old,serve_web=False)
    monkeypatch.setattr(main,'_read_evidence_update',lambda _:None)
    monkeypatch.setattr(main.retrieval,'browse',lambda index,*args: {'status':'ok','total':1 if index is old else 2})
    with TestClient(app) as client:
        assert client.get('/api/evidence/documents').json()['total']==1
        monkeypatch.setattr(main,'_read_evidence_update',lambda _: (tmp_path/'new',{'snapshot_id':'new'},new,new))
        client.portal.call(main._refresh_evidence,app.state.rl)
        assert client.get('/api/evidence/documents').json()['total']==2
