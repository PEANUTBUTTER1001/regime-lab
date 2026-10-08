"""P2-4 1분봉 정규화·시간봉 집계 — 합성 수기 대조, as_of 절단 불변 (docs/분봉_데이터_설계.md §2)."""

import numpy as np
import pandas as pd
import pytest

from regime_lab.bars import (add_segments, aggregate_bars, apply_price_basis, confirmed_bars, judge_price_basis,
                             normalize_minutes, slot_edges)

COLS = ["code", "dt", "open_p", "high_p", "low_p", "close_p", "volume", "value"]


def _raw(rows):
    df = pd.DataFrame(rows, columns=COLS)
    df["dt"] = pd.to_datetime(df["dt"])
    return df


def _day(code, day, minutes, price=100, cum0=0):
    """(HH:MM, 거래량) 목록 → 누적 거래대금(value)이 이어지는 원본 행."""
    rows, cum = [], cum0
    for hhmm, vol in minutes:
        cum += price * vol
        rows.append((code, f"{day} {hhmm}:00", price, price + 1, price - 1, price, vol, cum))
    return rows


def test_value_1m_uses_premarket_rows_before_session_filter(cfg):
    """08:59/09:00 경계: 장 전 누적 거래대금이 09:00 value_1m 에 섞이지 않는다 (N4 → N1 순서)."""
    raw = _raw(_day("005930", "2026-09-01", [("08:30", 50), ("08:59", 30), ("09:00", 10), ("09:01", 5)]))
    out, stats = normalize_minutes(raw, cfg)
    assert out["dt"].dt.strftime("%H:%M").tolist() == ["09:00", "09:01"]
    assert out["value_1m"].tolist() == [100 * 10, 100 * 5]  # 가격×거래량
    assert out["value_cum"].tolist() == [100 * 90, 100 * 95]
    assert stats == {"rows_in": 4, "outliers": 0, "off_session": 2, "rows_out": 2}


def test_value_1m_restarts_each_day_and_code(cfg):
    raw = _raw(_day("000660", "2026-09-01", [("09:00", 3), ("09:05", 4)])
               + _day("000660", "2026-09-02", [("09:00", 7)])
               + _day("005930", "2026-09-01", [("09:00", 2)]))
    out, _ = normalize_minutes(raw, cfg)
    assert out["value_1m"].tolist() == [300, 400, 700, 200]


def test_session_outliers_and_nxt_flag(cfg):
    rows = _day("005930", "2026-04-23", [("09:00", 1), ("15:30", 1), ("15:31", 1), ("20:01", 1)])
    rows += _day("005930", "2026-04-24", [("15:20", 1), ("15:30", 1)])
    rows.append(("005930", "2026-04-24 10:00:00", 105, 101, 99, 100, 1, 10**6))  # 시가가 고가 위 (N7)
    out, stats = normalize_minutes(_raw(rows), cfg)
    assert out["dt"].dt.strftime("%m-%d %H:%M").tolist() == ["04-23 09:00", "04-23 15:30", "04-24 15:20", "04-24 15:30"]
    assert out["nxt_period"].tolist() == [False, False, True, True]
    assert stats["outliers"] == 1 and stats["off_session"] == 2


def test_slot_edges_from_config(cfg):
    assert slot_edges("1h", cfg).tolist() == [540, 600, 660, 720, 780, 840, 900, 931]
    assert slot_edges("3h", cfg).tolist() == [540, 720, 931]
    bad = {**cfg, "minutes": {**cfg["minutes"], "bar_starts": {"1h": ["10:00", "09:00"]}}}
    with pytest.raises(ValueError):
        slot_edges("1h", bad)


def test_hourly_bars_hand_calculated(cfg):
    """1시간봉 수기 대조: slot 1 (09:00~09:59), 마지막 slot 7 = 15:00~15:30 (동시호가·종가 포함, 31분)."""
    rows = [
        ("005930", "2026-09-01 09:00:00", 100, 103, 99, 102, 10, 1_020),
        ("005930", "2026-09-01 09:30:00", 102, 108, 101, 107, 5, 1_555),
        ("005930", "2026-09-01 09:59:00", 107, 107, 95, 96, 20, 3_475),
        ("005930", "2026-09-01 10:00:00", 96, 97, 96, 97, 1, 3_572),
        ("005930", "2026-09-01 15:19:00", 97, 98, 97, 98, 2, 3_768),
        ("005930", "2026-09-01 15:25:00", 98, 98, 98, 98, 1, 3_866),   # 드문 동시호가 구간 행
        ("005930", "2026-09-01 15:30:00", 99, 99, 99, 99, 30, 6_836),  # 종가 단일가 체결
    ]
    minutes, _ = normalize_minutes(_raw(rows), cfg)
    bars = aggregate_bars(minutes, "1h", cfg)
    assert bars["slot"].tolist() == [1, 2, 7]
    b1, b7 = bars.iloc[0], bars.iloc[2]
    assert (b1["open"], b1["high"], b1["low"], b1["close"]) == (100, 108, 95, 96)
    assert (b1["volume"], b1["value_1m"], b1["n_minutes"], b1["bar_minutes"]) == (35, 3_475, 3, 60)
    assert b1["bar_start"] == pd.Timestamp("2026-09-01 09:00") and b1["available_at"] == pd.Timestamp("2026-09-01 10:00")
    assert (b7["open"], b7["close"], b7["volume"], b7["n_minutes"], b7["bar_minutes"]) == (97, 99, 33, 3, 31)
    assert b7["value_1m"] == 6_836 - 3_572
    assert b7["available_at"] == pd.Timestamp("2026-09-01 15:31")


def test_three_hour_bars_and_late_open(cfg):
    """3시간봉 180·211분, 늦은 개장일(10:00)은 시각 기준 slot 을 유지해 1시간봉이 slot 2부터."""
    rows = _day("005930", "2025-11-13", [("10:00", 1), ("11:59", 1), ("12:00", 1), ("15:30", 1)])
    minutes, _ = normalize_minutes(_raw(rows), cfg)
    h3 = aggregate_bars(minutes, "3h", cfg)
    assert h3["slot"].tolist() == [1, 2]
    assert h3["bar_minutes"].tolist() == [180, 211] and h3["n_minutes"].tolist() == [2, 2]
    assert h3["available_at"].dt.strftime("%H:%M").tolist() == ["12:00", "15:31"]
    h1 = aggregate_bars(minutes, "1h", cfg)
    assert h1["slot"].tolist()[0] == 2  # 09:00 봉 없음


def test_bars_never_cross_days(cfg):
    rows = _day("005930", "2026-09-01", [("15:30", 1)]) + _day("005930", "2026-09-02", [("09:00", 1)])
    bars = aggregate_bars(normalize_minutes(_raw(rows), cfg)[0], "3h", cfg)
    assert bars["date"].dt.strftime("%m-%d").tolist() == ["09-01", "09-02"]


def test_empty_and_unnormalized_input(cfg):
    empty, _ = normalize_minutes(_raw([]), cfg)
    assert aggregate_bars(empty, "1h", cfg).empty
    off = pd.DataFrame({"code": ["005930"], "dt": [pd.Timestamp("2026-09-01 16:00")], "date": [pd.Timestamp("2026-09-01")],
                        "open": [1], "high": [1], "low": [1], "close": [1], "volume": [1], "value_1m": [1],
                        "nxt_period": [True]})
    with pytest.raises(ValueError):
        aggregate_bars(off, "1h", cfg)


def _synthetic_days(seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for code in ("005930", "0161M0"):
        for day in ("2026-04-23", "2026-04-24", "2026-04-27"):
            mins = sorted(rng.choice(np.arange(8 * 60, 20 * 60), size=150, replace=False))
            cum, price = 0, 1000
            for m in mins:
                price = max(1, price + int(rng.integers(-5, 6)))
                vol = int(rng.integers(1, 50))
                cum += price * vol
                rows.append((code, f"{day} {m // 60:02d}:{m % 60:02d}:00", price, price + 2, price - 2, price + 1, vol, cum))
    return _raw(rows)


@pytest.mark.parametrize("timeframe", ["1h", "3h"])
@pytest.mark.parametrize("as_of", ["2026-04-24 09:00", "2026-04-24 10:00", "2026-04-24 12:00",
                                   "2026-04-24 15:31", "2026-04-27 11:00"])
def test_as_of_truncation_invariant(cfg, timeframe, as_of):
    """as_of 이후 1분 행을 지워도 as_of 에 확정된 봉이 같다 (FR-F4, 장중 절단 불변)."""
    raw = _synthetic_days()
    t = pd.Timestamp(as_of)
    full = confirmed_bars(aggregate_bars(normalize_minutes(raw, cfg)[0], timeframe, cfg), t)
    cut = raw[raw["dt"] < t]  # as_of 에 이용 가능한 1분 행 = 그 분이 끝난 행 (dt + 1분 ≤ as_of)
    part = confirmed_bars(aggregate_bars(normalize_minutes(cut, cfg)[0], timeframe, cfg), t)
    pd.testing.assert_frame_equal(full, part)
    assert (full["available_at"] <= t).all()


def _daily_raw(rows):
    return pd.DataFrame(rows, columns=["code", "date", "close_raw"]).assign(date=lambda d: pd.to_datetime(d["date"]))


def test_price_basis_judged_against_krx_close_and_used_next_day(cfg):
    """N2: 정규장 종가가 KRX 원주가와 허용오차(0.2%) 안이면 raw. 판정은 다음 거래일부터 쓴다."""
    raw = _raw(_day("005930", "2026-09-01", [("09:00", 1), ("15:30", 1)], price=1000)
               + _day("005930", "2026-09-02", [("09:00", 1), ("15:30", 1)], price=500)     # 수정주가 기준 행
               + _day("005930", "2026-09-03", [("09:00", 1)], price=1000)
               + _day("005930", "2026-09-04", [("09:00", 1)], price=1000))
    daily = _daily_raw([("005930", "2026-09-01", 1001), ("005930", "2026-09-02", 1000),
                        ("005930", "2026-09-03", 1000), ("005930", "2026-09-04", 1000)])
    minutes, _ = normalize_minutes(raw, cfg)
    v = judge_price_basis(minutes, daily, cfg)
    assert v["basis"].tolist() == ["raw", "mismatch", "raw", "raw"]  # 1001 은 0.1% 차이 → raw
    assert v["available_at"].dt.strftime("%m-%d %H:%M").tolist()[:3] == ["09-02 09:00", "09-03 09:00", "09-04 09:00"]
    kept, stats = apply_price_basis(minutes, v)
    # 09-01: 직전 판정 없음, 09-02: 09-01 판정(raw)을 이어 써서 남음(그날 어긋남은 다음 날에야 앎), 09-03: 09-02 판정 mismatch → 제외
    assert kept["date"].dt.strftime("%m-%d").unique().tolist() == ["09-02", "09-04"]
    assert stats == {"no_verdict": 2, "not_raw": 1}


def test_price_basis_missing_daily_is_not_raw(cfg):
    minutes, _ = normalize_minutes(_raw(_day("005930", "2026-09-01", [("09:00", 1)])), cfg)
    v = judge_price_basis(minutes, _daily_raw([("000660", "2026-09-01", 100)]), cfg)
    assert v["basis"].tolist() == ["no_daily"]


def test_segments_break_only_beyond_price_limit(cfg):
    """N3: 전일 마지막 가격 대비 당일 첫 가격이 ±30% 밖이면 새 구간. 제한폭 안(상한가)은 같은 구간."""
    rows = (_day("005930", "2026-09-01", [("15:30", 1)], price=1000)
            + _day("005930", "2026-09-02", [("09:00", 1), ("15:30", 1)], price=1290)   # +29% (상한가 안)
            + _day("005930", "2026-09-03", [("09:00", 1)], price=258)                  # 5:1 분할 (-80%)
            + _day("000660", "2026-09-01", [("09:00", 1)], price=100))
    seg = add_segments(normalize_minutes(_raw(rows), cfg)[0], cfg)
    got = seg.groupby(["code", "date"])["segment"].first()
    assert got.loc["005930"].tolist() == [0, 0, 1] and got.loc["000660"].tolist() == [0]
    bars = aggregate_bars(seg, "1h", cfg)
    assert "segment" in bars and bars.loc[bars["code"] == "005930", "segment"].tolist() == [0, 0, 0, 1]


@pytest.mark.parametrize("as_of", ["2026-04-24 10:00", "2026-04-27 09:00", "2026-04-27 12:00"])
def test_basis_and_segments_truncation_invariant(cfg, as_of):
    """as_of 이후 1분 행·일봉을 지워도 가격 기준 적용·구간 번호·확정 봉이 같다 (FR-F4)."""
    raw = _synthetic_days(seed=1)
    t = pd.Timestamp(as_of)
    days = raw["dt"].dt.normalize().drop_duplicates()
    daily = pd.DataFrame([(c, d) for c in raw["code"].unique() for d in days], columns=["code", "date"])
    last = raw[(raw["dt"].dt.strftime("%H:%M") >= "09:00") & (raw["dt"].dt.strftime("%H:%M") <= "15:30")]
    last = last.sort_values("dt").groupby(["code", last["dt"].dt.normalize()])["close_p"].last()
    daily["close_raw"] = [last.get((c, d), np.nan) for c, d in zip(daily["code"], daily["date"])]

    def pipeline(raw_in, daily_in):
        m, _ = normalize_minutes(raw_in, cfg)
        m, _ = apply_price_basis(m, judge_price_basis(m, daily_in, cfg))
        return confirmed_bars(aggregate_bars(add_segments(m, cfg), "1h", cfg), t)

    full = pipeline(raw, daily)
    part = pipeline(raw[raw["dt"] < t], daily[daily["date"] < t.normalize()])
    pd.testing.assert_frame_equal(full, part)
    assert not full.empty


@pytest.mark.data
def test_real_split_starts_new_segment_and_basis_mostly_raw(paths, cfg):
    """실분봉: 000040 2026-07-28 기준가 변경(일봉 수정주가 비율 0.2)에서 새 가격 구간, 판정 대부분 raw."""
    from regime_lab.data.loader import load_daily
    from regime_lab.data.minutes import load_minutes, month_files

    mdir = paths.store.parent / "minutes"
    if not month_files(mdir):
        pytest.skip(f"{mdir} 에 1분봉 사본 없음")
    raw = load_minutes(mdir, codes=["000040"], start="2026-07-01", end="2026-08-31")
    daily = load_daily(paths.store, tickers=["000040"], end="2026-08-31").rename(columns={"ticker": "code"})
    daily = daily.assign(code=daily["code"].astype(str))[["code", "date", "close_raw"]]
    m, _ = normalize_minutes(raw, cfg)
    v = judge_price_basis(m, daily, cfg)
    assert (v["basis"] == "raw").mean() > 0.9
    seg = add_segments(apply_price_basis(m, v)[0], cfg).groupby("date")["segment"].first()
    assert seg.diff().fillna(0).gt(0).sum() == 1
    assert seg.idxmax() == pd.Timestamp("2026-07-28") and seg.loc[:"2026-07-27"].eq(0).all()


@pytest.mark.data
def test_real_value_1m_matches_price_times_volume(paths, cfg):
    """실분봉: 005930 2026-09-01 은 장 전 행이 있어도 09:00 value_1m ≈ 가격×거래량 (P2-3 실측 1.001배)."""
    from regime_lab.data.minutes import load_minutes, month_files

    mdir = paths.store.parent / "minutes"
    if not month_files(mdir):
        pytest.skip(f"{mdir} 에 1분봉 사본 없음")
    raw = load_minutes(mdir, codes=["005930"], start="2026-09-01", end="2026-09-01")
    out, stats = normalize_minutes(raw, cfg)
    assert stats["off_session"] > 0  # 장 전 행이 실제로 있다
    first = out.iloc[0]
    assert first["dt"] == pd.Timestamp("2026-09-01 09:00")
    assert abs(first["value_1m"] / (first["close"] * first["volume"]) - 1) < 0.02
    bars = aggregate_bars(out, "1h", cfg)
    assert bars["slot"].tolist() == list(range(1, 8)) and bars["n_minutes"].sum() == len(out)
