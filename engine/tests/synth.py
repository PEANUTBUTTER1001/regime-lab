"""합성 일봉 프레임 (단위 테스트용)."""

import numpy as np
import pandas as pd


def make_frame(closes, ticker="T1", start="2021-01-04", **cols) -> pd.DataFrame:
    closes = np.asarray(closes, dtype=float)
    n = len(closes)
    df = pd.DataFrame({
        "date": pd.bdate_range(start, periods=n),
        "ticker": ticker,
        "open": closes, "high": closes, "low": closes, "close": closes,
        "volume": 1000.0, "value": 1e9, "marketcap": 1e11,
        "market": "KOSPI", "dept": "", "kind": "보통주",
        "open_raw": closes, "close_raw": closes, "volume_raw": 1000.0,
        "halted": False, "is_warmup": False,
    })
    for k, v in cols.items():
        df[k] = v
    return df


def make_market(cfg: dict, n_tickers: int = 50, seed: int = 0, end: str | None = None):
    """여러 종목의 합성 시장을 실제 준비 단계(지표·유니버스·그룹·국면)에 통과시킨 Prepared.

    원본 데이터 없이 execute() 전체 흐름과 실행 시간을 확인하는 용도다. 가격은 종목별 기하 랜덤워크,
    지수는 시장별 랜덤워크이며 워밍업 구간은 warmup_source_start ~ backtest_start 전날이다.
    """
    from regime_lab.indicators import compute_indicators
    from regime_lab.pipeline import Prepared
    from regime_lab.regime import attach_regimes
    from regime_lab.universe import apply_universe, assign_groups

    dcfg = cfg["data"]
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(dcfg["warmup_source_start"], end or dcfg["as_of_date"])
    n = len(dates)
    tickers = [f"{i:06d}" for i in range(n_tickers)]
    markets = np.where(np.arange(n_tickers) % 2 == 0, "KOSPI", "KOSDAQ")

    ret = rng.normal(0.0003, 0.025, (n_tickers, n))
    close = 10_000 * np.exp(np.cumsum(ret, axis=1))
    gap = rng.normal(0, 0.008, (n_tickers, n))
    open_ = close * np.exp(gap)
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.01, (n_tickers, n))))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.01, (n_tickers, n))))
    volume = rng.lognormal(11, 0.6, (n_tickers, n))
    shares = rng.lognormal(16, 1.0, n_tickers)[:, None]
    warm = np.broadcast_to(dates < pd.Timestamp(dcfg["backtest_start"]), (n_tickers, n))

    frame = pd.DataFrame({
        "date": np.tile(dates, n_tickers),
        "ticker": pd.Categorical(np.repeat(tickers, n)),
        "open": open_.ravel(), "high": high.ravel(), "low": low.ravel(), "close": close.ravel(),
        "volume": volume.ravel(), "value": (close * volume).ravel(), "marketcap": (close * shares).ravel(),
        "market": pd.Categorical(np.repeat(markets, n)), "dept": "", "kind": cfg["universe"]["kind"],
        "open_raw": open_.ravel(), "close_raw": close.ravel(), "volume_raw": volume.ravel(),
        "halted": False, "is_warmup": warm.ravel(),
    })
    idx_ret = rng.normal(0.0002, 0.011, (2, n))
    idx_close = 2_500 * np.exp(np.cumsum(idx_ret, axis=1))
    index = pd.DataFrame({
        "date": np.tile(dates, 2), "market": np.repeat(["KOSPI", "KOSDAQ"], n),
        "open": idx_close.ravel(), "high": idx_close.ravel(), "low": idx_close.ravel(), "close": idx_close.ravel(),
    })

    first = pd.Series(pd.Timestamp(dcfg["warmup_source_start"]) - pd.Timedelta(days=1), index=tickers)
    frame = compute_indicators(frame, cfg)
    frame = apply_universe(frame, first, cfg)
    frame = assign_groups(frame, cfg)
    frame = attach_regimes(frame, index, cfg)
    names = {t: f"SYN{t}" for t in tickers}
    return Prepared(frame, index, set(), pd.DataFrame(columns=["date", "ticker", "sector"]), names)
