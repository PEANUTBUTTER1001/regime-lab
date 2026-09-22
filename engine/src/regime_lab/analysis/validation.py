"""3중 검증: 기간 분할 (FR-A3), BH-FDR (FR-A5, A12), 무작위 벤치마크 (FR-A4, A13).

세 검증을 모두 통과한 전략만 분석 대상(analysis_target=True)이다 (FR-A5).
검정 통계량은 거래별 초과수익(excess_ret) 기준이며 집계 제외(end_of_data) 거래는 쓰지 않는다.

FDR 가족 크기 m 은 한 실행 요청에 들어 있는 전략 수다 (E3). 입력 기간(period)이 주어지면
기간 분할은 기간이 분할일을 사이에 둘 때만 판정하고, 무작위 진입일도 기간 안에서만 뽑는다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy import stats

from regime_lab.context import NULL_CONTEXT, RunContext


def _included(tr: pd.DataFrame) -> pd.DataFrame:
    tr = tr[~tr["excluded"].astype(bool)] if len(tr) else tr
    return tr[tr["excess_ret"].notna()]


# ---------------------------------------------------------------- 기간 분할
def split_trades(tr: pd.DataFrame, split_date: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """진입일 기준 전반(≤ split_date)·후반(> split_date). 한 거래는 한 쪽에만 속한다."""
    d = pd.Timestamp(split_date)
    return tr[tr["entry_date"] <= d], tr[tr["entry_date"] > d]


def split_applicable(period: dict | None, split_date: str) -> bool:
    """입력 기간이 분할일을 사이에 두는가 (start ≤ 분할일 < end). 기간이 없으면 전체 기간이므로 True."""
    if not period:
        return True
    d = pd.Timestamp(split_date)
    return pd.Timestamp(period["start"]) <= d < pd.Timestamp(period["end"])


def _period_of(periods, name):
    """periods: None | 전략 공통 기간 dict | {전략명: 기간 dict} → 해당 전략의 기간."""
    if not periods:
        return None
    if "start" in periods and "end" in periods:
        return periods
    return periods.get(name)


def period_split(results: dict[str, pd.DataFrame], cfg: dict, periods: dict | None = None) -> pd.DataFrame:
    acfg = cfg["analysis"]
    min_n = int(acfg["min_cell_trades"])
    rows = []
    for name, tr in results.items():
        h1, h2 = split_trades(_included(tr), acfg["split_date"])
        rows.append({"strategy": name, "split_applicable": split_applicable(_period_of(periods, name),
                                                                              acfg["split_date"]),
                     "h1_trades": len(h1), "h2_trades": len(h2),
                     "h1_mean_excess": h1["excess_ret"].mean() if len(h1) else np.nan,
                     "h2_mean_excess": h2["excess_ret"].mean() if len(h2) else np.nan})
    df = pd.DataFrame(rows)
    df["h1_rank"] = df["h1_mean_excess"].rank(ascending=False, method="min")
    df["h2_rank"] = df["h2_mean_excess"].rank(ascending=False, method="min")

    def judge(r) -> str:
        if not r.split_applicable:
            return "not_applicable"
        if r.h1_trades < min_n or r.h2_trades < min_n:
            return "sample_insufficient"
        if np.sign(r.h1_mean_excess) != np.sign(r.h2_mean_excess):
            return "reversed"
        if r.h1_mean_excess <= 0:
            return "negative_both"
        return "maintained" if r.h2_rank <= r.h1_rank else "weakened"

    df["split_judgement"] = df.apply(judge, axis=1) if len(df) else []
    df["split_pass"] = df["split_judgement"].isin(acfg["split_pass_judgements"])
    return df


# ---------------------------------------------------------------- FDR
def one_sided_pvalue(x: np.ndarray) -> float:
    """H0: 평균 ≤ 0, H1: 평균 > 0 단측 t-검정 (A12)."""
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    if len(x) < 2 or np.std(x, ddof=1) == 0:
        return np.nan
    return float(stats.ttest_1samp(x, 0.0, alternative="greater").pvalue)


def bh_reject(pvals: np.ndarray, q: float) -> np.ndarray:
    """Benjamini-Hochberg: p(k) ≤ k/m·q 를 만족하는 최대 k 까지 기각."""
    p = np.asarray(pvals, float)
    ok = ~np.isnan(p)
    m = ok.sum()
    out = np.zeros(len(p), bool)
    if m == 0:
        return out
    idx = np.flatnonzero(ok)
    order = idx[np.argsort(p[idx], kind="stable")]
    thresh = q * np.arange(1, m + 1) / m
    passed = p[order] <= thresh
    if passed.any():
        k = np.flatnonzero(passed).max()
        out[order[: k + 1]] = True
    return out


def fdr(results: dict[str, pd.DataFrame], cfg: dict) -> pd.DataFrame:
    names = list(results)
    tr = [_included(results[n]) for n in names]
    p = np.array([one_sided_pvalue(t["excess_ret"].to_numpy()) for t in tr])
    return pd.DataFrame({
        "strategy": names,
        "trades": [len(t) for t in tr],
        "mean_excess": [t["excess_ret"].mean() if len(t) else np.nan for t in tr],
        "p_value": p,
        "fdr_pass": bh_reject(p, float(cfg["analysis"]["fdr_q"])),
    })


# ---------------------------------------------------------------- 무작위 벤치마크
def _market_index_open(frame: pd.DataFrame, index: pd.DataFrame) -> np.ndarray:
    key = frame[["date"]].assign(market=frame["market"].astype(object))
    m = key.merge(index[["date", "market", "open"]], on=["date", "market"], how="left")
    return m["open"].to_numpy(float)


def random_benchmark(trades: pd.DataFrame, frame: pd.DataFrame, index: pd.DataFrame, cfg: dict,
                     seed: int | None = None, period: dict | None = None,
                     ctx: RunContext = NULL_CONTEXT) -> dict:
    """같은 종목·같은 보유기간(거래일 수)으로 진입일만 무작위 추출한 평균 초과수익 분포 (A13).

    무작위 거래 수익률 = open(r+h)/open(r) - 1 - 비용, 초과수익 = 이 값 - 같은 기간 시장 지수 시가 수익률.
    진입 후보 r 은 백테스트 시작일 이후·워밍업 아님·r+h 가 데이터 안인 행이다. 입력 기간이 있으면 r 의 날짜도
    [start, end] 안이어야 한다. 시가 결측 표본은 평균에서 제외한다.
    """
    acfg = cfg["analysis"]
    n_iter = int(acfg["random_bench_iterations"])
    rng = np.random.default_rng(acfg["random_seed"] if seed is None else seed)
    cost = cfg["execution"]["round_trip_cost_pct"] / 100
    tr = _included(trades)
    if tr.empty:
        return {"actual_mean_excess": np.nan, "percentile": np.nan, "random_means": np.array([]), "n_trades": 0}

    f = frame.reset_index(drop=True)
    tick = f["ticker"].astype(str).to_numpy()
    ends = pd.Series(np.arange(len(f))).groupby(tick).max()
    ok_start = (f["date"] >= pd.Timestamp(cfg["data"]["backtest_start"])).to_numpy(copy=True)
    if "is_warmup" in f:
        ok_start = ok_start & ~f["is_warmup"].to_numpy(bool)
    rowno = np.arange(len(f))
    if period:
        ok_start = ok_start & (f["date"] >= pd.Timestamp(period["start"])).to_numpy()
        last_in = pd.Series(np.where((f["date"] <= pd.Timestamp(period["end"])).to_numpy(), rowno, -1)).groupby(tick).max()
    else:
        last_in = ends
    first_ok = pd.Series(np.where(ok_start, rowno, np.iinfo(np.int64).max)).groupby(tick).min()

    open_ = f["open"].to_numpy(float, copy=True)
    open_[~(open_ > 0)] = np.nan  # 결측·0 이하 시가 표본은 평균에서 제외
    idx_open = _market_index_open(f, index)
    pos = pd.Series(np.arange(len(f)), index=pd.MultiIndex.from_arrays([tick, f["date"]]))
    e_row = pos.reindex(pd.MultiIndex.from_arrays([tr["ticker"].astype(str), tr["entry_date"]])).to_numpy()
    x_row = pos.reindex(pd.MultiIndex.from_arrays([tr["ticker"].astype(str), tr["exit_date"]])).to_numpy()
    h = np.maximum(x_row - e_row, 1).astype(np.int64)
    lo = first_ok.reindex(tr["ticker"].astype(str)).to_numpy()
    tk = tr["ticker"].astype(str)
    hi = np.minimum(ends.reindex(tk).to_numpy() - h, last_in.reindex(tk).to_numpy())  # r+h ≤ 마지막 행, r ≤ 기간 끝
    valid = hi >= lo
    lo, hi, h = lo[valid], hi[valid], h[valid]
    span = (hi - lo + 1).astype(np.int64)

    means = np.empty(n_iter)
    for i in range(n_iter):
        if i % 50 == 0:
            ctx.progress(i, n_iter)
            ctx.check_cancel()
        r = lo + (rng.random(len(span)) * span).astype(np.int64)
        ret = open_[r + h] / open_[r] - 1 - cost
        mkt = idx_open[r + h] / idx_open[r] - 1
        means[i] = np.nanmean(ret - mkt)
    ctx.progress(n_iter, n_iter)
    actual = float(tr["excess_ret"].mean())
    pct = float((means < actual).mean() + 0.5 * (means == actual).mean())
    return {"actual_mean_excess": actual, "percentile": pct, "random_means": means,
            "n_trades": int(valid.sum())}


def random_benchmarks(results: dict[str, pd.DataFrame], frame: pd.DataFrame, index: pd.DataFrame,
                      cfg: dict, periods: dict | None = None, ctx: RunContext = NULL_CONTEXT) -> pd.DataFrame:
    th = float(cfg["analysis"]["random_bench_pass_percentile"])
    rows = []
    for name, tr in results.items():
        rb = random_benchmark(tr, frame, index, cfg, period=_period_of(periods, name), ctx=ctx)
        rows.append({"strategy": name, "random_n_trades": rb["n_trades"],
                     "random_mean_of_means": float(np.mean(rb["random_means"])) if len(rb["random_means"]) else np.nan,
                     "random_percentile": rb["percentile"],
                     "random_pass": bool(rb["percentile"] >= th) if not np.isnan(rb["percentile"]) else False})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------- 통합
def validate(results: dict[str, pd.DataFrame], frame: pd.DataFrame, index: pd.DataFrame, cfg: dict,
             periods: dict | None = None, ctx: RunContext = NULL_CONTEXT) -> pd.DataFrame:
    """results 의 전략들을 한 FDR 가족(m = 전략 수)으로 3중 검증한다.

    periods: 전략 공통 기간 dict 또는 {전략명: 기간 dict}. 없으면 전체 기간.
    """
    out = (fdr(results, cfg)
           .merge(period_split(results, cfg, periods), on="strategy")
           .merge(random_benchmarks(results, frame, index, cfg, periods, ctx), on="strategy"))
    out["fdr_family_size"] = len(results)
    out["analysis_target"] = out["split_pass"] & out["fdr_pass"] & out["random_pass"]
    return out
