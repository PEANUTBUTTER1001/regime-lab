"""실험 (병합 대상 아님, 결정 NXT-1 B안 판단용): NXT 이후 store 일봉 OHLC 를 KRX 원주가 × 구간 수정비율로 바꾸면
백테스트 결과가 얼마나 달라지는지 본다. 원본은 읽기만 하고 집계만 출력한다 (원본 값·종목명 출력 없음).

구간 규칙 (seonghwan-claude 와 합의, docs/분봉_데이터_설계.md N3 과 같은 정의):
  ① 직전 거래일 KRX 정규장 종가(close_raw) vs 당일 KRX 시가(open_raw)  ② open_raw > 0 인 날만
  ③ |변화| > --gap-pct(기본 30%) 면 그날부터 새 구간 (정확히 30% 는 같은 구간, 부동소수 여유 1e-9)
수정비율 (시점 정합, codex-01a0fb4b 리뷰 반영): t 일 비율 = 같은 구간에서 t 일까지의 최근 --window(기본 60) 거래일의
store close / close_raw 중앙값. t 이후 행은 쓰지 않는다. 구간이 cutover 전부터 이어지면 NXT 전의 믿을 만한 날들이
창을 채워, cutover 뒤 일부 날의 시간외 종가가 섞여도 중앙값이 흔들리지 않는다.
--self-check 로 마지막 20거래일을 잘라 다시 계산해 겹치는 날의 보정값이 같은지(절단 불변) 확인한다.
cutover 이후 행만 open·high·low·close = 원주가 × 비율로 바꾼다 (원주가가 0·결측이면 store 값 유지,
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
    jump = ok & ((d["open_raw_k"] / prev - 1).abs() > gap_pct / 100 + 1e-9)
    first = d["ticker"].ne(d["ticker"].shift(1))
    return (jump | first).astype(int).groupby(d["ticker"]).cumsum()


def ratios(m: pd.DataFrame, seg: pd.Series, window: int) -> pd.Series:
    """t 일 수정비율 = 같은 (종목, 구간)에서 t 일까지 최근 window 행의 close/close_raw 중앙값 (t 이후 미사용)."""
    rc = (m["close"] / m["close_raw_k"]).where(m["close_raw_k"].gt(0))
    return rc.groupby([m["ticker"], seg]).transform(lambda s: s.rolling(window, min_periods=1).median())


def corrector(raw: pd.DataFrame, cutover: pd.Timestamp, gap_pct: float, window: int, stats: dict):
    orig = pl.load_daily

    def load(store, tickers, end=None):
        d = orig(store, tickers, end=end)
        m = d.merge(raw, on=["date", "ticker"], how="left").sort_values(["ticker", "date"], kind="stable")
        seg = segments(m, gap_pct)
        post = m["date"] >= cutover
        f = ratios(m, seg, window)
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
    ap.add_argument("--window", type=int, default=60, help="수정비율 중앙값 창 (거래일)")
    ap.add_argument("--self-check", action="store_true", help="마지막 20거래일 절단 불변 확인만")
    a = ap.parse_args()
    cfg, paths = load_config(), load_paths()
    cutover = pd.Timestamp(a.cutover)
    tickers = load_sample_tickers(paths.store) if a.sample else None
    cols = ["date", "ticker", "open_raw", "high_raw", "low_raw", "close_raw"]
    raw = pd.read_parquet(paths.store / "raw" / "krx_daily", columns=cols)
    raw["date"] = pd.to_datetime(raw["date"]).astype("datetime64[ns]")
    raw = raw.rename(columns={c: c + "_k" for c in cols[2:]}).drop_duplicates(["date", "ticker"])

    if a.self_check:
        d = pl.load_daily(paths.store, tickers)
        m = d.merge(raw, on=["date", "ticker"], how="left").sort_values(["ticker", "date"], kind="stable")
        full = ratios(m, segments(m, a.gap_pct), a.window)
        cut_day = sorted(m["date"].unique())[-21]
        mt = m[m["date"] <= cut_day]
        part = ratios(mt, segments(mt, a.gap_pct), a.window)
        same = np.allclose(full.loc[mt.index].to_numpy(float), part.to_numpy(float), equal_nan=True, rtol=0, atol=0)
        print(f"절단 불변 (마지막 20거래일 제거, 겹치는 {len(mt):,}행 수정비율 동일): {same}")
        return
    stats: dict = {}
    orig, load = corrector(raw, cutover, a.gap_pct, a.window, stats)
    base = pl.prepare(paths, cfg, tickers)
    pl.load_daily = load
    try:
        fixed = pl.prepare(paths, cfg, tickers)
    finally:
        pl.load_daily = orig
    print(f"범위: {'sample30' if a.sample else '전 종목'}, cutover {a.cutover}, 구간 갭 {a.gap_pct}%, 비율 창 {a.window}일 (시점 정합)")
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
