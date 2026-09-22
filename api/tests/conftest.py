import pytest
from fastapi.testclient import TestClient

from regime_api.main import create_app
from regime_api.settings import Settings
from regime_lab.config import load_config, load_paths


@pytest.fixture(scope="session")
def base_paths():
    p = load_paths()
    if not (p.store / "prices.parquet").exists():
        pytest.skip("store/ 원본 데이터가 없어 skip")
    return p


@pytest.fixture(scope="session")
def sample_prep(base_paths):
    from regime_lab.data.loader import load_sample_tickers
    from regime_lab.pipeline import prepare

    return prepare(base_paths, load_config(), load_sample_tickers(base_paths.store))


@pytest.fixture()
def make_client(base_paths, sample_prep, tmp_path):
    """sample30 준비 프레임 + 임시 runs·cache 폴더로 앱을 띄운다."""
    from regime_lab.config import Paths

    def _make(prep=sample_prep, **kw) -> TestClient:
        paths = Paths(base_paths.store, base_paths.sql_dump, tmp_path / "cache", tmp_path / "runs")
        settings = Settings(paths=paths, sample=True, **kw)
        return TestClient(create_app(settings, prep=prep, serve_web=False))

    return _make


@pytest.fixture()
def client(make_client):
    with make_client() as c:
        yield c


STRAT = {"name": "bo", "patterns": ["breakout_20d"]}


def run_to_end(client, body=None, timeout=120):
    r = client.post("/api/runs", json=body or {"strategies": [STRAT]})
    assert r.status_code == 202, r.text
    run_id = r.json()["run_id"]
    snap = client.app.state.rl.jobs.wait(run_id, timeout)
    return run_id, snap
