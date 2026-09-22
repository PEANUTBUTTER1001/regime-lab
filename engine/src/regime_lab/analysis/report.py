"""결과 화면용 데이터 (FR-U3, FR-U4, FR-X6).

모든 값은 같은 run_id 의 거래 내역에서만 파생한다. 계산 로직은 엔진에 두고, 화면(클라이언트)은 표시만 한다.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

from regime_lab.backtest.metrics import equity_curve, trade_stats
from regime_lab.analysis.validation import split_trades

# 손익 분포 고정 구간: -30% ~ +30% 를 2%p 단위, 양 끝은 초과 구간 (실행 간 비교 가능)
HIST_EDGES = [-math.inf] + [round(x, 2) for x in np.arange(-0.30, 0.3001, 0.02)] + [math.inf]


def to_jsonable(o):
    """NaN·inf → None, numpy·pandas 스칼라 → 파이썬 기본형 (JSON 직렬화용)."""
    if isinstance(o, dict):
        return {str(k): to_jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [to_jsonable(v) for v in o]
    if isinstance(o, (bool, np.bool_)):
        return bool(o)
    if isinstance(o, (int, np.integer)):
        return int(o)
    if isinstance(o, (float, np.floating)):
        return None if not math.isfinite(float(o)) else float(o)
    if isinstance(o, pd.Timestamp):
        return None if pd.isna(o) else o.strftime("%Y-%m-%d")
    if o is None or (not isinstance(o, str) and pd.isna(o)):
        return None
    return o


def included(trades: pd.DataFrame) -> pd.DataFrame:
    return trades[~trades["excluded"].astype(bool)] if len(trades) else trades


def pnl_histogram(trades: pd.DataFrame) -> list[dict]:
    """net_ret 고정 구간 분포. 합계 = 집계 포함 거래 수."""
    r = included(trades)["net_ret"].astype(float)
    counts = pd.cut(r, HIST_EDGES, right=False).value_counts(sort=False)
    return [{"lo": iv.left, "hi": iv.right, "count": int(c)} for iv, c in counts.items()]


def ticker_table(trades: pd.DataFrame, names: dict[str, str], min_n: int) -> list[dict]:
    """종목별 표본 표. 기본 정렬은 거래 수 내림차순 (수익률 순위로 강조하지 않음, design.md §6.4)."""
    tr = included(trades)
    if tr.empty:
        return []
    g = tr.groupby("ticker")
    df = pd.DataFrame({
        "trades": g.size(),
        "win_rate": g["net_ret"].apply(lambda x: float((x > 0).mean())),
        "mean_ret": g["net_ret"].mean(),
        "mean_excess": g["excess_ret"].mean(),
        "market": g["market"].agg(lambda x: x.mode().iat[0] if len(x.mode()) else None),
    }).reset_index()
    df["name"] = df["ticker"].map(names)
    df["sample_insufficient"] = df["trades"] < min_n
    df = df.sort_values(["trades", "ticker"], ascending=[False, True])
    return df[["ticker", "name", "market", "trades", "win_rate", "mean_ret", "mean_excess",
               "sample_insufficient"]].to_dict(orient="records")


def split_comparison(trades: pd.DataFrame, cfg: dict) -> dict:
    """기간 분할 전·후반 기본 통계 (진입일 기준, FR-A3)."""
    h1, h2 = split_trades(included(trades), cfg["analysis"]["split_date"])
    return {"split_date": cfg["analysis"]["split_date"], "first_half": trade_stats(h1), "second_half": trade_stats(h2)}


def strategy_result(name: str, res: dict, validation_row: dict, names: dict[str, str], cfg: dict) -> dict:
    min_n = int(cfg["analysis"]["min_cell_trades"])
    return {
        "strategy": name,
        "summary": res["summary"],
        "validation": validation_row,
        "cells_market": res["cells_market"].to_dict(orient="records"),
        "cells_stock": res["cells_stock"].to_dict(orient="records"),
        "pnl_histogram": pnl_histogram(res["trades"]),
        "tickers": ticker_table(res["trades"], names, min_n),
        "split": split_comparison(res["trades"], cfg),
        # 동시 보유 종목 동일가중 일별 자산곡선 (A9). 시작값 1.0
        "equity": [{"date": d, "equity": e, "positions": int(p)}
                   for d, e, p in zip(res["equity"]["date"], res["equity"]["equity"], res["equity"]["positions"])],
    }


def _segments(dates: pd.Series, labels: pd.Series) -> list[dict]:
    """연속된 같은 국면을 [start, end] 구간으로 압축 (차트 배경용)."""
    lab = labels.astype(object).where(labels.notna(), None).to_numpy()
    d = dates.to_numpy()
    out, start = [], 0
    for i in range(1, len(lab) + 1):
        if i == len(lab) or lab[i] != lab[start]:
            if lab[start] is not None:
                out.append({"start": pd.Timestamp(d[start]), "end": pd.Timestamp(d[i - 1]), "regime": lab[start]})
            start = i
    return out


def stock_detail(frame: pd.DataFrame, trades: pd.DataFrame, ticker: str, names: dict[str, str],
                 cfg: dict) -> dict:
    """종목 상세 (FR-U4): 가격·이동평균·국면 구간·매매 마커·거래 표·종목 자산곡선과 단순 보유 비교."""
    f = frame[(frame["ticker"].astype(str) == ticker) & ~frame["is_warmup"].astype(bool)]
    f = f.sort_values("date")
    tr = trades[trades["ticker"].astype(str) == ticker].sort_values("entry_date")
    inc = included(tr)
    series = {
        "date": f["date"].tolist(),
        "open": f["open"].tolist(), "high": f["high"].tolist(), "low": f["low"].tolist(),
        "close": f["close"].tolist(), "volume": f["volume"].tolist(),
        "sma20": f["sma20"].tolist(), "sma200": f["sma200"].tolist(),
    }
    equity, buy_hold = [], []
    if len(inc):
        eq = equity_curve(inc, frame)
        first, last = inc["entry_date"].min(), inc["exit_date"].max()
        base = f.loc[f["date"] == first, "open"]
        px = f.set_index("date")["close"]
        if len(base) and base.iat[0] > 0:
            bh = px.loc[first:last] / base.iat[0]
            buy_hold = [{"date": d, "value": v} for d, v in bh.items()]
        equity = [{"date": d, "value": v} for d, v in zip(eq["date"], eq["equity"])]
    cols = ["signal_date", "entry_date", "entry_price", "exit_date", "exit_price", "exit_reason", "hold_days",
            "gross_ret", "cost", "net_ret", "index_ret", "excess_ret", "market_regime", "stock_regime",
            "cap_group", "liq_group", "sector", "excluded"]
    return {
        "ticker": ticker,
        "name": names.get(ticker),
        "series": series,
        "stock_regime_segments": _segments(f["date"], f["stock_regime"]),
        "market_regime_segments": _segments(f["date"], f["market_regime"]),
        "markers": [{"date": r.entry_date, "type": "entry", "price": r.entry_price} for r in tr.itertuples()]
                   + [{"date": r.exit_date, "type": "exit", "price": r.exit_price, "reason": r.exit_reason}
                      for r in tr.itertuples()],
        "trades": tr[[c for c in cols if c in tr.columns]].to_dict(orient="records"),
        "equity": equity,
        "buy_and_hold": buy_hold,
        "sample": {"trades": int(len(inc)), "open_at_data_date": int(len(tr) - len(inc)),
                   "sample_insufficient": bool(len(inc) < int(cfg["analysis"]["min_cell_trades"]))},
        "stats": trade_stats(inc) if len(inc) else None,
    }
