"""백테스트 성과 지표 (FR-E7, A9).

- 거래 단위 지표: 집계 제외(end_of_data) 거래를 뺀 거래의 net_ret 기준.
- 샤프: 거래별 net_ret 의 평균/표준편차 (무위험수익률 0, 연율화 없음 — A9 거래 단위).
- 손익비: 평균 이익 / |평균 손실|.
- MDD·자산곡선: 동시 보유 종목 동일가중 일별 자산곡선 (A9). 비용은 청산일 수익률에서 차감.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

METRIC_DEFS = {
    "trades": ("거래 수", "건", "집계 포함 거래 수"),
    "win_rate": ("승률", "비율", "net_ret > 0 거래 비율"),
    "mean_ret": ("평균 수익률", "비율", "mean(net_ret)"),
    "median_ret": ("중앙값 수익률", "비율", "median(net_ret)"),
    "mean_hold_ret": ("평균 보유 수익률", "비율", "mean(gross_ret) — 비용 차감 전"),
    "mean_excess": ("평균 초과수익", "비율", "mean(net_ret - 소속 시장 지수 동기간 수익률)"),
    "payoff_ratio": ("손익비", "배", "mean(이익 거래 net_ret) / |mean(손실 거래 net_ret)|"),
    "sharpe": ("샤프(거래 단위)", "배", "mean(net_ret)/std(net_ret, ddof=1), rf=0, 연율화 없음"),
    "mdd": ("최대낙폭", "비율", "동일가중 일별 자산곡선의 최대 낙폭"),
    "total_cost": ("총 비용", "비율합", "거래 수 × 왕복 비용률"),
    "excluded_trades": ("제외 거래 수", "건", "기준일 보유 중(end_of_data) 거래"),
    "skipped_entries": ("진입 스킵 수", "건", "상한가·진입일 정지·다음 거래일 없음"),
}


def trade_stats(tr: pd.DataFrame) -> dict:
    """거래 집합의 기본 통계 (집계 셀에서도 사용)."""
    r = tr["net_ret"].astype(float)
    n = len(r)
    if n == 0:
        return {"trades": 0, "win_rate": np.nan, "mean_ret": np.nan, "median_ret": np.nan,
                "mean_hold_ret": np.nan, "mean_excess": np.nan, "payoff_ratio": np.nan, "sharpe": np.nan}
    wins, losses = r[r > 0], r[r < 0]
    payoff = wins.mean() / abs(losses.mean()) if len(wins) and len(losses) else np.nan
    sd = r.std(ddof=1) if n > 1 else np.nan
    return {
        "trades": n,
        "win_rate": float((r > 0).mean()),
        "mean_ret": float(r.mean()),
        "median_ret": float(r.median()),
        "mean_hold_ret": float(tr["gross_ret"].mean()),
        "mean_excess": float(tr["excess_ret"].mean()),
        "payoff_ratio": float(payoff),
        "sharpe": float(r.mean() / sd) if sd and sd > 0 else np.nan,
    }


def equity_curve(trades: pd.DataFrame, frame: pd.DataFrame) -> pd.DataFrame:
    """동시 보유 종목 동일가중 일별 자산곡선.

    각 포지션의 일별 수익률: 진입일 close/entry-1, 보유 중 close/prev_close-1,
    시가 청산일 exit/prev_close-1, 종가 청산일 close/prev_close-1. 청산일에 비용을 차감한다.
    포지션이 없는 날의 수익률은 0 이다.
    """
    if trades.empty:
        return pd.DataFrame(columns=["date", "ret", "equity", "positions"])
    f = frame.loc[frame["ticker"].astype(str).isin(set(trades["ticker"])), ["ticker", "date", "close"]]
    f = f.assign(ticker=f["ticker"].astype(str)).sort_values(["ticker", "date"]).reset_index(drop=True)
    pos = pd.Series(np.arange(len(f)), index=pd.MultiIndex.from_frame(f[["ticker", "date"]]))
    e = pos.reindex(pd.MultiIndex.from_arrays([trades["ticker"].astype(str), trades["entry_date"]])).to_numpy()
    x = pos.reindex(pd.MultiIndex.from_arrays([trades["ticker"].astype(str), trades["exit_date"]])).to_numpy()
    lens = (x - e + 1).astype(int)
    tid = np.repeat(np.arange(len(trades)), lens)
    offs = np.arange(lens.sum()) - np.repeat(np.cumsum(lens) - lens, lens)
    rows = np.repeat(e.astype(int), lens) + offs
    close = f["close"].to_numpy(float)
    prev = np.r_[np.nan, close[:-1]]
    r = close[rows] / prev[rows] - 1
    first = offs == 0
    last = offs == np.repeat(lens - 1, lens)
    entry_px = trades["entry_price"].to_numpy(float)[tid]
    exit_px = trades["exit_price"].to_numpy(float)[tid]
    at_close = trades["exit_at_close"].to_numpy(bool)[tid]
    r = np.where(first, close[rows] / entry_px - 1, r)
    open_exit = last & ~at_close
    r = np.where(open_exit & ~first, exit_px / prev[rows] - 1, r)
    r = np.where(open_exit & first, exit_px / entry_px - 1, r)
    r = np.where(last, r - trades["cost"].to_numpy(float)[tid], r)
    daily = pd.DataFrame({"date": f["date"].to_numpy()[rows], "r": np.nan_to_num(r)}).groupby("date")["r"]
    daily = daily.agg(["mean", "size"])
    cal = frame.loc[(frame["date"] >= trades["entry_date"].min()) & (frame["date"] <= trades["exit_date"].max()),
                    "date"].drop_duplicates().sort_values()
    daily = daily.reindex(cal, fill_value=0)
    out = pd.DataFrame({"date": daily.index, "ret": daily["mean"].to_numpy(), "positions": daily["size"].to_numpy()})
    out["equity"] = (1 + out["ret"]).cumprod()
    return out


def max_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return np.nan
    peak = np.maximum.accumulate(np.r_[1.0, equity.to_numpy()])[1:]
    return float((equity.to_numpy() / peak - 1).min())


def summarize(trades: pd.DataFrame, skipped: pd.DataFrame, frame: pd.DataFrame) -> dict:
    inc = trades[~trades["excluded"]] if len(trades) else trades
    stats = trade_stats(inc)
    eq = equity_curve(inc, frame)
    stats.update({
        "period_start": str(inc["entry_date"].min().date()) if len(inc) else None,
        "period_end": str(inc["exit_date"].max().date()) if len(inc) else None,
        "mdd": max_drawdown(eq["equity"]) if len(eq) else np.nan,
        "total_cost": float(inc["cost"].sum()) if len(inc) else 0.0,
        "excluded_trades": int(trades["excluded"].sum()) if len(trades) else 0,
        "skipped_entries": int(len(skipped)),
        "skipped_by_reason": skipped["reason"].value_counts().astype(int).to_dict() if len(skipped) else {},
        "exit_by_reason": trades["exit_reason"].value_counts().astype(int).to_dict() if len(trades) else {},
    })
    return stats
