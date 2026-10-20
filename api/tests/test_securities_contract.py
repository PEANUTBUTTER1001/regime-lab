"""Display catalog contracts, independent of original data or external requests."""
from types import SimpleNamespace

from fastapi.testclient import TestClient

from regime_api.main import create_app
from regime_api.settings import Settings
from regime_lab.config import Paths
from test_auth_contract import auth_app, signin
from test_lan_allowlist import lan_app, paths


def test_catalog_preserves_codes_and_names(tmp_path):
    paths = Paths(tmp_path / 'store', tmp_path / 'dump', tmp_path / 'cache', tmp_path / 'runs')
    names = {'0000A1': '검증산업', '005930': '검증전자', '000001': '<회사>'}
    app = create_app(Settings(paths=paths), prep=SimpleNamespace(names=names), rag=object(), serve_web=False)
    with TestClient(app) as client:
        response = client.get('/api/securities')
        assert response.status_code == 200
        assert response.json() == {'items': [
            {'ticker': ticker, 'name': name} for ticker, name in sorted(names.items())]}
        assert names == {'0000A1': '검증산업', '005930': '검증전자', '000001': '<회사>'}
        app.state.rl.prep = SimpleNamespace(names={})
        assert client.get('/api/securities').json() == {'items': []}


def test_catalog_loading_and_failure(tmp_path):
    paths = Paths(tmp_path / 'store', tmp_path / 'dump', tmp_path / 'cache', tmp_path / 'runs')
    app = create_app(Settings(paths=paths), prep=object(), rag=object(), serve_web=False)
    app.state.rl.prep = None
    # No lifespan: intentionally inspect readiness without starting a data loader.
    client = TestClient(app)
    app.state.rl.status = 'warming_up'
    response = client.get('/api/securities')
    assert response.status_code == 503
    assert response.json()['code'] == 'warming_up'
    assert response.json()['retryable'] is True
    app.state.rl.status = 'failed'
    app.state.rl.load_error = 'synthetic unavailable'
    response = client.get('/api/securities')
    assert response.status_code == 503
    assert response.json()['code'] == 'data_unavailable'


def test_catalog_requires_session(auth_app):
    app = auth_app
    app.state.rl.prep = SimpleNamespace(names={'000001': '검증회사'})
    with TestClient(app) as client:
        assert client.get('/api/securities').status_code == 401
        signin(client)
        assert client.get('/api/securities').status_code == 200


def test_catalog_lan_restriction(lan_app):
    lan_app.state.rl.prep = SimpleNamespace(names={'000001': '검증회사'})
    with TestClient(lan_app, client=('192.168.40.22', 1234)) as client:
        response = client.get('/api/securities')
        assert response.status_code == 403
        assert response.json()['code'] == 'network_not_allowed'
