"""유니버스 (FR-D4, U1~U3, A4) 와 시총·유동성 그룹 (FR-D3, A5).

모든 판정은 해당일(t)까지의 값만 쓴다. 유니버스는 신호일 기준으로 행 단위(eligible) 판정한다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from regime_lab.data.warmup import listed_trading_days
from regime_lab.indicators import _rolling

# 제외 사유 (우선순위 순, 첫 번째로 걸린 사유를 기록)
REASONS = [
    "warmup",            # 워밍업 구간 (FR-E8)
    "not_common_stock",  # 우선주·스팩·리츠·기타 (U2)
    "market",            # KOSPI/KOSDAQ 외
    "listed_lt_250",     # 첫 거래일 기준 250거래일 미만 (U1)
    "managed_or_warning",  # 관리종목·투자주의환기 (U3)
    "low_liquidity",     # 20일 평균 거래대금 < 5억 원 (A4)
]


def apply_universe(frame: pd.DataFrame, first_dates: pd.Series, cfg: dict) -> pd.DataFrame:
    """eligible, exclude_reason, listed_days, avg_value20 컬럼을 추가한다."""
    ucfg, dcfg = cfg["universe"], cfg["data"]
    out = frame.copy()
    out["listed_days"] = listed_trading_days(out, first_dates, dcfg["warmup_source_start"])
    if "avg_value20" not in out.columns:
        out["avg_value20"] = _rolling(out, out["value"].fillna(0), ucfg["liquidity_window"], "mean")

    conds = {
        "warmup": out["is_warmup"].to_numpy(bool) if "is_warmup" in out else np.zeros(len(out), bool),
        "not_common_stock": (out["kind"].astype(object) != ucfg["kind"]).to_numpy(),
        "market": ~out["market"].astype(object).isin(dcfg["markets"]).to_numpy(),
        "listed_lt_250": (out["listed_days"] < ucfg["min_listed_days"]).to_numpy(),
        "managed_or_warning": out["dept"].astype(object).isin(ucfg["excluded_depts"]).to_numpy(),
        "low_liquidity": ~(out["avg_value20"] >= ucfg["min_avg_value_krw"]).to_numpy(),
    }
    reason = np.full(len(out), None, dtype=object)
    for name in reversed(REASONS):  # 역순으로 덮어써 우선순위가 높은 사유가 남는다
        reason[conds[name]] = name
    out["exclude_reason"] = pd.Categorical(reason, categories=REASONS)
    out["eligible"] = out["exclude_reason"].isna()
    return out


def exclusion_summary(frame: pd.DataFrame) -> pd.DataFrame:
    """제외 사유별 종목·일 수 (FR-D4 사유 기록)."""
    s = frame["exclude_reason"].astype(object).fillna("eligible").value_counts().rename("rows")
    return s.rename_axis("reason").reset_index()


def _split_labels(values: pd.Series, split: list[float]) -> pd.Series:
    """값 내림차순 percent_rank((rank-1)/(n-1)) → large/mid/small.

    상위 split[0] 이하 large, split[0]+split[1] 이상 small, 나머지 mid (상·하위 대칭 경계).
    """
    n = values.notna().sum()
    rank = values.rank(ascending=False, method="first")
    pr = (rank - 1) / (n - 1) if n > 1 else rank * 0
    top, low = split[0], split[0] + split[1]
    lab = np.where(pr <= top, "large", np.where(pr >= low, "small", "mid"))
    return pd.Series(np.where(values.isna(), None, lab), index=values.index, dtype=object)


def assign_groups(frame: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """일자 × 시장별 30/40/30 시총 그룹(A5)과 유동성 그룹을 부여한다.

    분위 모집단은 그날 유니버스 기본 조건(보통주·KOSPI/KOSDAQ)을 만족하는 종목이다.
    진입 시점 그룹은 백테스트에서 신호일 값을 기록한다.
    """
    out = frame.copy()
    split = cfg["groups"]["marketcap_split"]
    base = (
        (out["kind"].astype(object) == cfg["universe"]["kind"])
        & out["market"].astype(object).isin(cfg["data"]["markets"])
        & ~out.get("is_warmup", False)
    )
    sub = out.loc[base, ["date", "market", "marketcap"]].copy()
    sub["market"] = sub["market"].astype(object)
    out["cap_group"] = None
    out.loc[base, "cap_group"] = sub.groupby(["date", "market"])["marketcap"].transform(
        lambda v: _split_labels(v, split)
    )
    lcfg = cfg["groups"].get("liquidity_split")
    out["liq_group"] = None
    if lcfg:
        sub["avg_value20"] = out.loc[base, "avg_value20"]
        out.loc[base, "liq_group"] = sub.groupby(["date", "market"])["avg_value20"].transform(
            lambda v: _split_labels(v, lcfg)
        )
    for c in ("cap_group", "liq_group"):
        out[c] = out[c].astype("category")
    return out
