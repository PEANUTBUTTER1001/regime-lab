"""P2-4 1분봉 정규화·시간봉 집계 — 합성 수기 대조, as_of 절단 불변 (docs/분봉_데이터_설계.md §2)."""

import numpy as np
import pandas as pd
import pytest

from regime_lab.bars import aggregate_bars, confirmed_bars, normalize_minutes, slot_edges

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


def test_close_outside_range_is_outlier(cfg):
    """N7: 종가가 고가 위·저가 아래인 행도 제외 (seongmin-claude #44 리뷰)."""
    rows = [("005930", "2026-09-01 09:00:00", 100, 103, 99, 150, 1, 150),   # 종가 > 고가
            ("005930", "2026-09-01 09:01:00", 100, 103, 99, 98, 1, 248),    # 종가 < 저가
            ("005930", "2026-09-01 09:02:00", 100, 103, 99, 101, 1, 349)]
    out, stats = normalize_minutes(_raw(rows), cfg)
    assert out["dt"].dt.strftime("%H:%M").tolist() == ["09:02"] and stats["outliers"] == 2


def test_empty_bars_keep_dtypes(cfg):
    """빈 결과도 봉이 있을 때와 열 형식이 같다 (seongmin-claude #44 리뷰)."""
    full, _ = normalize_minutes(_raw(_day("005930", "2026-09-01", [("09:00", 1)])), cfg)
    empty, _ = normalize_minutes(_raw([]), cfg)
    some, none = aggregate_bars(full, "1h", cfg), aggregate_bars(empty.astype(full.dtypes.to_dict()), "1h", cfg)
    assert none.empty and none.dtypes.equals(some.dtypes)


def test_confirmed_bars_accepts_timezone_aware_as_of(cfg):
    """as_of 가 +09:00·UTC 처럼 시간대가 있어도 KST 로 맞춰 시간대 없는 값과 같은 결과 (seongmin-claude #44 리뷰)."""
    minutes, _ = normalize_minutes(_raw(_day("005930", "2026-09-01", [("09:00", 1), ("10:30", 1), ("15:30", 1)])), cfg)
    bars = aggregate_bars(minutes, "1h", cfg)
    naive = confirmed_bars(bars, "2026-09-01 11:00")
    assert naive["slot"].tolist() == [1, 2]
    for t in ("2026-09-01T11:00:00+09:00", "2026-09-01T02:00:00+00:00", pd.Timestamp("2026-09-01 02:00", tz="UTC")):
        pd.testing.assert_frame_equal(confirmed_bars(bars, t), naive)
    assert confirmed_bars(bars, "2026-09-01T01:59:59+00:00")["slot"].tolist() == [1]


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
