import numpy as np
import pandas as pd
import pytest

from regime_lab.data.loader import load_delisted, load_index, load_sector_snapshots

pytestmark = pytest.mark.data


def test_daily_frame_types_and_keys(sample_daily):
    df = sample_daily
    assert df["ticker"].nunique() == 30
    assert not df.duplicated(["ticker", "date"]).any()
    for c in ("open", "high", "low", "close", "volume", "value", "marketcap"):
        assert df[c].dtype == "float64", c
    assert df["close"].notna().all()
    assert df["market"].notna().all()
    assert df["date"].min() == pd.Timestamp("2020-09-01")


def test_halted_rows(sample_daily):
    h = sample_daily[sample_daily["halted"]]
    assert h["open"].isna().all()
    assert (h["volume"] == 0).all()


def test_samsung_known_close(sample_daily):
    # collect.log 검증값: 삼성전자 2021-01-04 종가 83,000원
    row = sample_daily[(sample_daily.ticker == "005930") & (sample_daily.date == "2021-01-04")]
    assert row["close"].item() == 83000.0


def test_index_delisted_sector(store):
    idx = load_index(store)
    assert set(idx["market"]) == {"KOSPI", "KOSDAQ"}
    assert idx.groupby("market").size().eq(1483).all()
    assert len(load_delisted(store)) == 333
    sec = load_sector_snapshots(store)
    assert {"date", "ticker", "sector"} <= set(sec.columns)


def test_end_cut(store):
    from regime_lab.data.loader import load_daily

    df = load_daily(store, ["005930"], end="2021-01-04")
    assert df["date"].max() == pd.Timestamp("2021-01-04")


def test_nonpositive_prices_read_as_missing(store):
    from regime_lab.data.loader import load_daily

    df = load_daily(store, ["010780"], end="2026-08-31")
    row = df[df["date"] == "2026-08-13"].iloc[0]
    assert np.isnan(row["open"]) and not row["halted"] and row["close"] > 0
    assert (df[["open", "high", "low"]].fillna(1) > 0).all().all()
