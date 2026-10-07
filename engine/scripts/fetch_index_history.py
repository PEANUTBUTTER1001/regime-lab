"""과거 지수 확보 (X5, T-5) — 시장 국면의 200일선·20일 기울기 워밍업용.

FinanceDataReader 로 KOSPI(KS11)·KOSDAQ(KQ11) 일봉을 받아 [warmup_source_start, backtest_start) 구간을
cache/warmup/index_warmup.parquet 로 저장한다 (컬럼: date, market, open, high, low, close — store 지수와 같음).

store 지수와 겹치는 구간(backtest_start 이후 CHECK_DAYS 일)의 OHLC 가 모두 일치할 때만 저장한다.
FinanceDataReader 는 이 스크립트만 쓰므로 프로젝트 의존성에 넣지 않고 실행할 때만 붙인다.

사용: uv run --project engine --with finance-datareader python engine/scripts/fetch_index_history.py
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from regime_lab.config import load_config, load_paths  # noqa: E402
from regime_lab.data.loader import load_index  # noqa: E402

SYMBOLS = {"KOSPI": "KS11", "KOSDAQ": "KQ11"}
CHECK_DAYS = 60  # store 지수와 대조할 겹침 구간 (달력일)
PRICE_COLS = ["open", "high", "low", "close"]


def main() -> int:
    try:
        import FinanceDataReader as fdr
    except ImportError:
        print("FinanceDataReader 가 없습니다. 'uv run --project engine --with finance-datareader ...' 로 실행하세요.")
        return 2

    cfg, paths = load_config(), load_paths()
    start = cfg["data"]["warmup_source_start"]
    t0 = pd.Timestamp(cfg["data"]["backtest_start"])
    need = int(cfg["regime"]["ma_window"]) + int(cfg["regime"]["slope_window"])
    store_idx = load_index(paths.store)

    parts = []
    for market, symbol in SYMBOLS.items():
        raw = fdr.DataReader(symbol, start, (t0 + pd.Timedelta(days=CHECK_DAYS)).date().isoformat())
        f = pd.DataFrame({"date": pd.to_datetime(raw.index).astype("datetime64[ns]"), "market": market,
                          **{c: raw[c.capitalize()].astype(float).to_numpy() for c in PRICE_COLS}})
        j = f.merge(store_idx[store_idx["market"] == market], on=["date", "market"], suffixes=("", "_store"))
        if j.empty:
            print(f"[stop] {market}: store 지수와 겹치는 날짜가 없어 대조할 수 없습니다.")
            return 1
        bad = np.zeros(len(j), bool)
        for c in PRICE_COLS:
            bad |= ~np.isclose(j[c], j[f"{c}_store"], rtol=0, atol=1e-6)
        if bad.any():
            print(f"[stop] {market}: store 지수와 겹치는 {len(j)}일 중 {int(bad.sum())}일의 OHLC 가 다릅니다. 저장하지 않습니다.")
            return 1
        w = f[f["date"] < t0]
        if len(w) < need:
            print(f"[warn] {market}: 워밍업 {len(w)}행 < 필요 {need}행 — 시장 국면이 {t0.date()} 부터 다 나오지 않을 수 있습니다.")
        print(f"{market} ({symbol}): 겹침 {len(j)}일 OHLC 일치, 워밍업 {len(w)}행 "
              f"{w['date'].min().date()} ~ {w['date'].max().date()}")
        parts.append(w)

    out = pd.concat(parts, ignore_index=True).sort_values(["market", "date"]).reset_index(drop=True)
    dest = paths.cache / "warmup" / "index_warmup.parquet"
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(dest, index=False)
    print(f"저장: {dest} ({len(out)}행)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
