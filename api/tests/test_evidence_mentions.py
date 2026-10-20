"""Opt-in name mentions: old contracts, readiness and original metadata remain unchanged."""
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest
from fastapi.testclient import TestClient

from regime_api.main import create_app
from regime_api.settings import Settings
from regime_lab.config import Paths

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'engine' / 'tests'))
from rag_synth import T0, make_doc, write_store


@pytest.fixture
def mention_client(tmp_path):
    docs = [make_doc(0, '', '삼성전자 자기주식 발표', '', T0),
            make_doc(1, '', '삼성전자서비스 자기주식 발표', '', T0),
            make_doc(2, '', '삼성전자 자기주식 미래 발표', '', T0 + timedelta(days=2))]
    paths = Paths(tmp_path / 'store', tmp_path / 'dump', tmp_path / 'cache', tmp_path / 'runs', write_store(tmp_path / 'ext', docs))
    app = create_app(Settings(paths=paths), prep=SimpleNamespace(names={'005930': '삼성전자'}), serve_web=False)
    app.state.rl.load_rag()
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize('endpoint', ['search', 'documents'])
def test_opt_in_flag_and_response(mention_client, endpoint):
    params = {'as_of': (T0 + timedelta(hours=1)).isoformat(), 'q': '삼성전자', 'tickers': '005930'}
    url = '/api/evidence/' + endpoint
    client = mention_client
    original = client.get(url, params=params).json()
    assert original['items'] == [] and 'ticker_filter' not in original['index']
    result = client.get(url, params={**params, 'include_name_mentions': 'true'})
    assert result.status_code == 200
    body = result.json()
    assert len(body['items']) == 1
    assert body['index']['ticker_filter'] == 'code_or_name_mention'
    match = body['items'][0]['ticker_match']
    assert match == {'codes': [], 'name_mentions': [{'code': '005930', 'name': '삼성전자', 'field': 'title'}]}
    if endpoint == 'documents':
        assert body['items'][0]['tickers'] == []
    unknown = client.get(url, params={**params, 'tickers': '999999', 'include_name_mentions': 'true'}).json()
    assert unknown['items'] == [] and unknown['index']['ticker_filter'] == 'code_or_name_mention'
    client.app.state.rl.prep = None
    client.app.state.rl.status = 'warming_up'
    assert client.get(url, params=params).status_code == 200
    assert client.get(url, params={**params, 'include_name_mentions': 'true'}).status_code == 503
