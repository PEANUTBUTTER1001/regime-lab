import pytest

from regime_lab.config import load_config, load_paths


@pytest.fixture(scope="session")
def cfg():
    return load_config()


@pytest.fixture(scope="session")
def paths():
    return load_paths()


@pytest.fixture(scope="session")
def store(paths):
    if not (paths.store / "prices.parquet").exists():
        pytest.skip("store/ 원본 데이터가 없어 skip")
    return paths.store


@pytest.fixture(scope="session")
def sample_daily(store):
    from regime_lab.data.loader import load_daily, load_sample_tickers

    return load_daily(store, load_sample_tickers(store))


@pytest.fixture(scope="session")
def warm_source(paths):
    from regime_lab.data.warmup import load_warmup_source

    w = load_warmup_source(paths.cache)
    if w is None:
        pytest.skip("cache/warmup 없음 — scripts/extract_sqldump.py 실행 필요")
    return w


@pytest.fixture(scope="session")
def sample_frame(sample_daily, warm_source, cfg):
    """sample30 + 워밍업 연결 프레임."""
    from regime_lab.data.warmup import attach_warmup

    return attach_warmup(sample_daily, warm_source, cfg)


@pytest.fixture(scope="session")
def indicator_reference(paths):
    import pandas as pd

    ref = paths.cache / "reference" / "kor_indicators_sample30.parquet"
    inp = paths.cache / "reference" / "kor_price_sample30.parquet"
    if not (ref.exists() and inp.exists()):
        pytest.skip("cache/reference 없음 — scripts/extract_sqldump.py 실행 필요 (원본 파생 데이터라 Git 제외)")
    return pd.read_parquet(inp), pd.read_parquet(ref)


@pytest.fixture(scope="session")
def sample_prepared(store, warm_source, paths, cfg):
    from regime_lab.data.loader import load_sample_tickers
    from regime_lab.pipeline import prepare

    return prepare(paths, cfg, load_sample_tickers(store))
