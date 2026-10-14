import asyncio
import json
from types import SimpleNamespace
from fastapi.testclient import TestClient
from regime_api import main
from regime_api.settings import Settings
from regime_lab.config import Paths


def settings(tmp_path):
    return Settings(paths=Paths(tmp_path, tmp_path/'unused', tmp_path/'cache', tmp_path/'runs', tmp_path/'old'),
                    news_api={'poll_seconds': 0.01, 'runtime_dir': str(tmp_path/'runtime')})


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


def test_initial_refresh_does_not_block_web_start(tmp_path, monkeypatch):
    import threading
    entered, release = threading.Event(), threading.Event()
    cfg = settings(tmp_path)
    def slow(state):
        entered.set()
        assert release.wait(3)
        return None
    monkeypatch.setattr(main, '_read_evidence_update', slow)
    monkeypatch.setattr(main.AppState, 'load_rag', lambda _: (_ for _ in ()).throw(AssertionError('local loader must not race HTTP')))
    app = main.create_app(cfg, prep=object(), serve_web=False)
    try:
        with TestClient(app) as client:
            assert entered.wait(1)
            response = client.get('/api/evidence/documents')
            assert response.status_code == 503
            assert response.json()['code'] == 'warming_up'
            release.set()
    finally:
        release.set()


def test_incomplete_ingest_does_not_replace_index(tmp_path, monkeypatch):
    cfg = settings(tmp_path)
    old = object()
    state = SimpleNamespace(settings=cfg, paths=cfg.paths, cfg={}, rag=old, docs=old, rag_status='ready')
    state.news_refresh = {'last_success': {'snapshot_id': 'old'}}
    def incomplete(config, previous):
        assert previous == 'old'
        raise ValueError('Incomplete snapshot manifests/membership')
    monkeypatch.setattr(main, 'read_news_api_snapshot', incomplete)
    asyncio.run(main._refresh_evidence(state))
    assert state.rag is old and state.docs is old
    assert state.news_refresh['status'] == 'stale'
    assert state.news_refresh['last_success']['snapshot_id'] == 'old'


def test_opt_in_and_no_database_settings(tmp_path, monkeypatch):
    from regime_api import settings as module
    from dataclasses import replace
    paths = replace(settings(tmp_path).paths, news_snapshots=tmp_path/'news')
    monkeypatch.setattr(module, 'REPO_ROOT', tmp_path/'repository')
    monkeypatch.setattr(module, 'load_paths', lambda: paths)
    monkeypatch.delenv('REGIME_NEWS_REFRESH', raising=False)
    monkeypatch.setenv('REGIME_NEWS_DB_CONFIG', 'nonexistent-legacy-file')
    assert Settings.from_env().news_api is None
    monkeypatch.setenv('REGIME_NEWS_REFRESH', '1')
    monkeypatch.setenv('REGIME_DATA_API_URL', 'https://example.invalid')
    monkeypatch.setenv('REGIME_DATA_API_TOKEN', 'private-token')
    cfg = Settings.from_env()
    assert cfg.news_api['sources'] == ['n8n_news', 'n8n_disclosure']
    assert cfg.news_api['poll_seconds'] == 30
    assert 'private-token' not in repr(cfg)
    monkeypatch.setattr(module, 'load_paths', lambda: replace(paths, news_snapshots=module.REPO_ROOT/'news'))
    import pytest
    with pytest.raises(ValueError, match='outside'):
        Settings.from_env()


def test_news_works_without_price_store(tmp_path, monkeypatch):
    import time
    cfg = settings(tmp_path)
    index = object()
    monkeypatch.setattr(main, '_read_evidence_update', lambda _: (tmp_path/'snapshot', {'snapshot_id': 'new'}, index, index))
    monkeypatch.setattr(main.retrieval, 'browse', lambda actual, *args: {'status': 'ok', 'total': 2 if actual is index else 0})
    app = main.create_app(cfg, serve_web=False)
    with TestClient(app) as client:
        deadline = time.monotonic() + 3
        while (app.state.rl.rag is None or app.state.rl.status == 'warming_up') and time.monotonic() < deadline:
            time.sleep(.01)
        assert app.state.rl.status == 'failed'  # price files do not exist
        assert client.get('/api/evidence/documents').json()['total'] == 2
