"""S3 유니버스 (FR-D4, U1~U3, A4) / S4 시총 그룹 (A5)."""

import numpy as np
import pandas as pd
import pytest
from synth import make_frame

from regime_lab.universe import apply_universe, assign_groups


def _first(df):
    return df.groupby("ticker")["date"].min()


def test_listed_days_rule(cfg):
    df = make_frame(np.full(300, 100.0))
    u = apply_universe(df, _first(df), cfg)
    assert (u["exclude_reason"].iloc[:249] == "listed_lt_250").all()
    assert u["eligible"].iloc[249:].all()


def test_long_history_counts_as_listed(cfg):
    df = make_frame(np.full(30, 100.0))
    first = pd.Series({"T1": pd.Timestamp("2000-01-03")})
    u = apply_universe(df, first, cfg)
    assert u["eligible"].iloc[19:].all()  # 20일 평균 거래대금 산출 이후 모두 편입


def test_liquidity_uses_only_past_values(cfg):
    value = np.full(300, 1e9)
    value[260:] = 1e8  # 260일부터 거래대금 급감
    df = make_frame(np.full(300, 100.0), value=value)
    u = apply_universe(df, _first(df), cfg)
    avg = u["avg_value20"]
    # t일 평균은 t-19..t 만 사용
    assert avg.iloc[265] == pytest.approx((14 * 1e9 + 6 * 1e8) / 20)
    assert u["exclude_reason"].iloc[275] == "low_liquidity"
    assert u["eligible"].iloc[259]


def test_common_stock_warmup_and_dept(cfg):
    a = make_frame(np.full(300, 100.0), ticker="PREF", kind="우선주")
    b = make_frame(np.full(300, 100.0), ticker="MGD")
    b.loc[280:, "dept"] = "관리종목(소속부없음)"
    c = make_frame(np.full(300, 100.0), ticker="WRM")
    c.loc[:259, "is_warmup"] = True
    df = pd.concat([a, b, c], ignore_index=True)
    u = apply_universe(df, _first(df), cfg).set_index(["ticker", "date"])
    assert (u.loc["PREF", "exclude_reason"] == "not_common_stock").all()
    mg = u.loc["MGD"]
    assert (mg["exclude_reason"].iloc[280:] == "managed_or_warning").all()
    assert mg["eligible"].iloc[279]
    wr = u.loc["WRM"]
    assert (wr["exclude_reason"].iloc[:260] == "warmup").all()
    assert wr["eligible"].iloc[260:].all()


def test_cap_groups_30_40_30_per_market(cfg):
    frames = []
    for i in range(10):
        frames.append(make_frame([100.0], ticker=f"K{i}", marketcap=float(i + 1), avg_value20=float(100 - i)))
        frames.append(make_frame([100.0], ticker=f"Q{i}", marketcap=float(100 - i), market="KOSDAQ",
                                 avg_value20=float(i + 1)))
    df = pd.concat(frames, ignore_index=True)
    g = assign_groups(df, cfg).set_index("ticker")
    cap, liq = g["cap_group"].astype(str), g["liq_group"].astype(str)
    assert list(cap[[f"K{i}" for i in range(10)]]) == ["small"] * 3 + ["mid"] * 4 + ["large"] * 3
    assert list(cap[[f"Q{i}" for i in range(10)]]) == ["large"] * 3 + ["mid"] * 4 + ["small"] * 3
    # 유동성 그룹: 시장별 20일 평균 거래대금 30/40/30 (시총과 독립)
    assert list(liq[[f"K{i}" for i in range(10)]]) == ["large"] * 3 + ["mid"] * 4 + ["small"] * 3
    assert list(liq[[f"Q{i}" for i in range(10)]]) == ["small"] * 3 + ["mid"] * 4 + ["large"] * 3


@pytest.mark.data
def test_real_groups_match_data_review(store, cfg):
    """데이터_검토_결과 §3.5: 2026-09-18 KOSPI 241/321/241, KOSDAQ 522/694/522."""
    from regime_lab.data.loader import load_daily

    d = load_daily(store, end="2026-09-18")
    d = d[d["date"] == "2026-09-18"].copy()
    d["is_warmup"] = False
    d["avg_value20"] = np.nan
    g = assign_groups(d, cfg)
    g = g[g["cap_group"].notna()]
    cnt = g.groupby([g["market"].astype(str), g["cap_group"].astype(str)]).size()
    assert cnt[("KOSPI", "large")] == 241 and cnt[("KOSPI", "mid")] == 321 and cnt[("KOSPI", "small")] == 241
    assert cnt[("KOSDAQ", "large")] == 522 and cnt[("KOSDAQ", "mid")] == 694 and cnt[("KOSDAQ", "small")] == 522
