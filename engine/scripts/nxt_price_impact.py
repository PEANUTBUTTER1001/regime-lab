"""실험 (병합 대상 아님, 결정 NXT-1 B안 판단용): NXT 이후 store 일봉 OHLC 를 KRX 원주가 × 구간 수정비율로 바꾸면
백테스트 결과가 얼마나 달라지는지 본다. 원본은 읽기만 하고 집계만 출력한다 (원본 값·종목명 출력 없음).

구간 규칙 (seonghwan-claude 와 합의, docs/분봉_데이터_설계.md N3 과 같은 정의):
  ① 직전 거래일 KRX 정규장 종가(close_raw) vs 당일 KRX 시가(open_raw)  ② open_raw > 0 인 날만
  ③ |변화| > --gap-pct(기본 30%) 면 그날부터 새 구간
구간 수정비율 = 그 구간의 cutover 이후 날들의 store close / close_raw 중앙값 (대부분 날은 KRX 종가와 같음).
cutover 이후 행만 open·high·low·close = 원주가 × 구간 비율로 바꾼다 (원주가가 0·결측이면 store 값 유지,
store 시가가 결측(거래정지)이면 시가는 결측 유지). 거래량은 바꾸지 않는다.

사용 (저장소 루트):
  uv run --project engine python engine/scripts/nxt_price_impact.py            # 전 종목 (원본 PC)
  uv run --project engine python engine/scripts/nxt_price_impact.py --sample   # sample30
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import regime_lab.pipeline as pl  # noqa: E402
from regime_lab.config import load_config, load_paths  # noqa: E402
from regime_lab.data.loader import load_sample_tickers  # noqa: E402
from regime_lab.patterns import CORE_PATTERNS  # noqa: E402
from regime_lab.runs import Strategy, execute  # noqa: E402

EXTRA = {"high_52w+trail/be/ma": (["high_52w"], {"trailing_stop_pct": -10, "breakeven_trigger_pct": 5,
                                                  "ma_exit_window": 20})}


def segments(d: pd.DataFrame, gap_pct: float) -> pd.Series:
    prev = d.groupby("ticker")["close_raw_k"].shift(1)
    ok = d["open_raw_k"].gt(0) & prev.gt(0)
    jump = ok & ((d["open_raw_k"] / prev - 1).abs() > gap_pct / 100)
    first = d["ticker"].ne(d["ticker"].shift(1))
    return (jump | first).astype(int).groupby(d["ticker"]).cumsum()


def corrector(raw: pd.DataFrame, cutover: pd.Timestamp, gap_pct: float, stats: dict):
    orig = pl.load_daily

    def load(store, tickers, end=None):
        d = orig(store, tickers, end=end)
        m = d.merge(raw, on=["date", "ticker"], how="left").sort_values(["ticker", "date"], kind="stable")
        seg = segments(m, gap_pct)
        post = m["date"] >= cutover
        rc = (m["close"] / m["close_raw_k"]).where(post & m["close_raw_k"].gt(0))
        f = rc.groupby([m["ticker"], seg]).transform("median")
        use = post & f.notna()
        before = m[["open", "high", "low", "close"]].copy()
        for c in ("open", "high", "low", "close"):
            new = m[f"{c}_raw_k"] * f
            keep_nan = m[c].isna() if c == "open" else pd.Series(False, index=m.index)
            m[c] = np.where(use & new.gt(0) & ~keep_nan, new, m[c])
        rel = ((m[["open", "close"]] - before[["open", "close"]]).abs() / before[["open", "close"]]).fillna(0)
        stats.update(post_rows=int(post.sum()), changed_open=int((rel["open"] > 1e-4).sum()),
                     changed_close=int((rel["close"] > 1e-4).sum()), segments_with_jump=int((seg > 1).sum()))
        out = m.drop(columns=[c for c in m.columns if c.endswith("_k")]).sort_index()
        return out[d.columns]

    return orig, load


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", action="store_true", help="sample30 만")
    ap.add_argument("--cutover", default="2026-04-24")
    ap.add_argument("--gap-pct", type=float, default=30.0)
    a = ap.parse_args()
    cfg, paths = load_config(), load_paths()
    cutover = pd.Timestamp(a.cutover)
    tickers = load_sample_tickers(paths.store) if a.sample else None
    cols = ["date", "ticker", "open_raw", "high_raw", "low_raw", "close_raw"]
    raw = pd.read_parquet(paths.store / "raw" / "krx_daily", columns=cols)
    raw["date"] = pd.to_datetime(raw["date"]).astype("datetime64[ns]")
    raw = raw.rename(columns={c: c + "_k" for c in cols[2:]}).drop_duplicates(["date", "ticker"])

    stats: dict = {}
    orig, load = corrector(raw, cutover, a.gap_pct, stats)
    base = pl.prepare(paths, cfg, tickers)
    pl.load_daily = load
    try:
        fixed = pl.prepare(paths, cfg, tickers)
    finally:
        pl.load_daily = orig
    print(f"범위: {'sample30' if a.sample else '전 종목'}, cutover {a.cutover}, 구간 갭 {a.gap_pct}%")
    print(f"cutover 이후 행 {stats['post_rows']:,} · 시가 바뀐 행 {stats['changed_open']:,} · 종가 바뀐 행 {stats['changed_close']:,}")
    print(f"{'전략':24s} {'거래':>13s} {'승률':>15s} {'평균 초과':>17s} | cutover 이후 청산: 건수 · 평균 초과")
    plans = {n: ([n], None) for n in CORE_PATTERNS} | EXTRA
    for name, (pats, ex) in plans.items():
        st = Strategy.from_dict({"name": "s", "patterns": pats, **({"exit": ex} if ex else {})})
        ra, rb = execute(st, base, cfg)["results"]["s"], execute(st, fixed, cfg)["results"]["s"]
        sa, sb = ra["summary"], rb["summary"]
        ta, tb = ra["trades"], rb["trades"]
        pa_ = ta[ta["exit_date"] >= cutover] if len(ta) else ta
        pb_ = tb[tb["exit_date"] >= cutover] if len(tb) else tb
        ex_a = pa_["excess_ret"].mean() if len(pa_) else float("nan")
        ex_b = pb_["excess_ret"].mean() if len(pb_) else float("nan")
        print(f"{name:24s} {sa['trades']:6d}→{sb['trades']:<6d} {sa['win_rate']:6.1%}→{sb['win_rate']:<6.1%} "
              f"{sa['mean_excess']:+7.2%}→{sb['mean_excess']:+7.2%} | {len(pa_)}→{len(pb_)} · {ex_a:+.2%}→{ex_b:+.2%}")
    print("과거 데이터 분석이며 투자 권유가 아닙니다.")


if __name__ == "__main__":
    main()
