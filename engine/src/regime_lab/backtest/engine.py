"""일봉 백테스트 엔진 (FR-E1~E5, E7, E8, A8~A11).

체결 규칙
- 진입: 신호일 t 의 다음 거래일 e 시가 (수정주가). 원주가 시가(e) ≥ 원주가 종가(t) × (1+limit_up) 이면 스킵(limit_up).
  e 가 거래정지이거나 거래량 0 이면 스킵(halted_entry). 다음 거래일이 없으면 스킵(no_next_day).
- 종목당 1포지션. 보유 중(진입일 ~ 청산 체결일 전날) 신호는 무시한다.
- 청산 검사: 진입일부터 매일 종가로 손절 → 익절 → 최대 보유 순으로 검사. 먼저 충족된 날의 다음 거래일 시가에 청산.
- 청산 체결일이 거래정지·거래량 0·하한가(원주가 시가 ≤ 전일 원주가 종가 × (1+limit_down)) 이면 다음 거래일 재시도.
- 상장폐지 종목은 데이터 마지막 거래일 종가로 청산(delisted). 기준일까지 보유 중이면 기준일 종가 평가(end_of_data, 집계 제외).
- 비용: 왕복 비용률을 거래 수익률에서 차감.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from regime_lab.context import NULL_CONTEXT, RunContext

EXIT_REASONS = ["stop_loss", "take_profit", "time", "delisted", "end_of_data"]
SKIP_REASONS = ["limit_up", "halted_entry", "no_next_day"]
EPS = 1e-9  # 경계값(정확히 -8%, +20%)이 부동소수 오차로 빠지지 않도록 하는 허용 오차


@dataclass(frozen=True)
class ExitRule:
    stop_loss_pct: float | None
    take_profit_pct: float | None
    max_hold_days: int
    trailing_stop_pct: None = None

    @classmethod
    def from_cfg(cls, exit_cfg: dict, limits: dict) -> "ExitRule":
        if exit_cfg.get("trailing_stop_pct") is not None:
            raise ValueError("trailing_stop_pct must be null in this release")
        mh = exit_cfg.get("max_hold_days")
        if mh is None:
            raise ValueError("max_hold_days is required")
        lo, hi = limits["max_hold_days"]
        if not (lo <= int(mh) <= hi):
            raise ValueError(f"max_hold_days must be {lo} to {hi}: {mh}")
        for k in ("stop_loss_pct", "take_profit_pct"):
            v = exit_cfg.get(k)
            if v is not None:
                lo, hi = limits[k]
                if not (lo <= v <= hi):
                    raise ValueError(f"{k} must be {lo} to {hi}: {v}")
        return cls(exit_cfg.get("stop_loss_pct"), exit_cfg.get("take_profit_pct"), int(mh))


def _tradeable(i: int, a: dict, limit_down: float) -> bool:
    """청산 체결 가능 여부: 정지·거래량 0·하한가 시가가 아니어야 한다."""
    if a["halted"][i] or not (a["volume"][i] > 0) or np.isnan(a["open"][i]):
        return False
    prev_raw = a["close_raw"][i - 1]
    if prev_raw > 0 and a["open_raw"][i] <= prev_raw * (1 + limit_down) + EPS:
        return False
    return True


def _simulate_ticker(a: dict, sig_idx: np.ndarray, rule: ExitRule, ex: dict, delisted: bool):
    """한 종목의 거래 목록(행 인덱스 기반)과 스킵 목록을 만든다."""
    n = len(a["close"])
    limit_up, limit_down = ex["limit_up_pct"] / 100, ex["limit_down_pct"] / 100
    sl = None if rule.stop_loss_pct is None else rule.stop_loss_pct / 100
    tp = None if rule.take_profit_pct is None else rule.take_profit_pct / 100
    trades, skips = [], []
    free_from = 0  # 이 행 이상의 신호만 새 포지션 가능
    for t in sig_idx:
        if t < free_from:
            continue
        e = t + 1
        if e >= n:
            skips.append((t, "no_next_day"))
            continue
        if a["halted"][e] or not (a["volume"][e] > 0) or np.isnan(a["open"][e]):
            skips.append((t, "halted_entry"))
            continue
        if a["close_raw"][t] > 0 and a["open_raw"][e] >= a["close_raw"][t] * (1 + limit_up) - EPS:
            skips.append((t, "limit_up"))
            continue
        entry = a["open"][e]
        reason, decide = None, None
        for d in range(e, n):
            r = a["close"][d] / entry - 1
            held = d - e + 1
            if sl is not None and r <= sl + EPS:
                reason = "stop_loss"
            elif tp is not None and r >= tp - EPS:
                reason = "take_profit"
            elif held >= rule.max_hold_days:
                reason = "time"
            if reason:
                decide = d
                break
        x, retries, exit_px, at_close = None, 0, np.nan, False
        if decide is not None:
            for j in range(decide + 1, n):
                if _tradeable(j, a, limit_down):
                    x, exit_px = j, a["open"][j]
                    break
                retries += 1
        if x is None:  # 데이터 끝까지 청산 체결 불가 (또는 청산 조건 미충족)
            x, exit_px, at_close = n - 1, a["close"][n - 1], True
            reason = "delisted" if delisted else "end_of_data"
        trades.append((t, e, decide, x, entry, exit_px, reason, retries, at_close))
        free_from = x  # 청산 체결일 신호부터 다시 허용 (체결일 시가 매도 후 종가 신호)
    return trades, skips


def _ticker_arrays(f: pd.DataFrame) -> dict:
    return {
        "open": f["open"].to_numpy(float),
        "close": f["close"].to_numpy(float),
        "open_raw": f["open_raw"].fillna(0).to_numpy(float),
        "close_raw": f["close_raw"].fillna(0).to_numpy(float),
        "volume": f["volume"].fillna(0).to_numpy(float),
        "halted": f["halted"].fillna(False).to_numpy(bool),
    }


def simulate_trades(
    frame: pd.DataFrame,
    signal: pd.Series,
    cfg: dict,
    delisted_tickers: set[str] | None = None,
    exit_cfg: dict | None = None,
    entry_mask: pd.Series | None = None,
    ctx: RunContext = NULL_CONTEXT,
) -> tuple[pd.DataFrame, list, list]:
    """체결 시뮬레이션. (reset 된 frame, 거래 행 목록, 스킵 행 목록)을 반환한다.

    최종 진입 후보 = signal & eligible & ~is_warmup & (date ≥ backtest_start) & entry_mask(전략 입력 필터, E1).
    ctx 에 종목 루프 진행(처리 종목 수/전체 종목 수)을 알리고, 50종목마다 취소를 확인한다.
    """
    rule = ExitRule.from_cfg(exit_cfg or cfg["exit"], cfg["exit_limits"])
    ex = cfg["execution"]
    delisted_tickers = delisted_tickers or set()

    f = frame.reset_index(drop=True)
    sig = signal.reset_index(drop=True).fillna(False).astype(bool)
    cand = sig & f["date"].ge(pd.Timestamp(cfg["data"]["backtest_start"]))
    if "eligible" in f:
        cand &= f["eligible"].astype(bool)
    if "is_warmup" in f:
        cand &= ~f["is_warmup"].astype(bool)
    if entry_mask is not None:
        cand &= entry_mask.reset_index(drop=True).fillna(False).astype(bool)

    tick = f["ticker"].astype(str).to_numpy()
    bounds = np.flatnonzero(np.r_[True, tick[1:] != tick[:-1], True])
    cand_np = cand.to_numpy()
    rows, skip_rows = [], []
    n_tickers = len(bounds) - 1
    for k, (s, e) in enumerate(zip(bounds[:-1], bounds[1:])):
        if k % 50 == 0:
            ctx.progress(k, n_tickers)
            ctx.check_cancel()
        local = np.flatnonzero(cand_np[s:e])
        if len(local) == 0:
            continue
        a = _ticker_arrays(f.iloc[s:e])
        trades, skips = _simulate_ticker(a, local, rule, ex, tick[s] in delisted_tickers)
        rows += [(s + t, s + en, None if d is None else s + d, s + x, px_in, px_out, why, rt, ac)
                 for t, en, d, x, px_in, px_out, why, rt, ac in trades]
        skip_rows += [(s + t, why) for t, why in skips]
    ctx.progress(n_tickers, n_tickers)
    return f, rows, skip_rows


def build_trades(f: pd.DataFrame, rows: list, skip_rows: list, index: pd.DataFrame, cfg: dict,
                 sectors: pd.DataFrame | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """시뮬레이션 결과에 수익률·비용·초과수익·진입 당시 국면·그룹·업종을 결합한다 (FR-E4·E5)."""
    cost = cfg["execution"]["round_trip_cost_pct"] / 100
    trades = _build_trades(f, rows, index, cost, sectors)
    tick = f["ticker"].astype(str).to_numpy()
    skipped = pd.DataFrame(
        {"ticker": [tick[i] for i, _ in skip_rows],
         "signal_date": [f.at[i, "date"] for i, _ in skip_rows],
         "reason": pd.Categorical([w for _, w in skip_rows], categories=SKIP_REASONS)}
    )
    return trades, skipped


def run_backtest(
    frame: pd.DataFrame,
    signal: pd.Series,
    index: pd.DataFrame,
    cfg: dict,
    delisted_tickers: set[str] | None = None,
    sectors: pd.DataFrame | None = None,
    exit_cfg: dict | None = None,
    entry_mask: pd.Series | None = None,
    ctx: RunContext = NULL_CONTEXT,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """거래 내역과 진입 스킵 내역을 반환한다 (simulate_trades + build_trades).

    frame: (ticker, date) 정렬, eligible·국면·그룹 컬럼 포함 권장. signal: frame 과 같은 인덱스의 bool.
    """
    f, rows, skip_rows = simulate_trades(frame, signal, cfg, delisted_tickers, exit_cfg, entry_mask, ctx)
    return build_trades(f, rows, skip_rows, index, cfg, sectors)


def _index_price(index: pd.DataFrame) -> dict[str, pd.DataFrame]:
    return {m: g.set_index("date")[["open", "close"]] for m, g in index.groupby("market")}


def _build_trades(f, rows, index, cost, sectors) -> pd.DataFrame:
    cols = ["ticker", "signal_date", "entry_date", "exit_signal_date", "exit_date", "entry_price", "exit_price",
            "exit_reason", "exit_retries", "exit_at_close"]
    if not rows:
        return pd.DataFrame(columns=cols + ["gross_ret", "cost", "net_ret", "index_ret", "excess_ret", "hold_days"])
    t_i, e_i, d_i, x_i, pin, pout, why, rt, ac = map(list, zip(*rows))
    date = f["date"].to_numpy()
    tr = pd.DataFrame({
        "ticker": f["ticker"].astype(str).to_numpy()[t_i],
        "signal_date": date[t_i],
        "entry_date": date[e_i],
        "exit_signal_date": [date[d] if d is not None else pd.NaT for d in d_i],
        "exit_date": date[x_i],
        "entry_price": pin,
        "exit_price": pout,
        "exit_reason": pd.Categorical(why, categories=EXIT_REASONS),
        "exit_retries": rt,
        "exit_at_close": ac,
        "hold_days": np.asarray(x_i) - np.asarray(e_i) + np.where(ac, 1, 0),
    })
    tr["gross_ret"] = tr["exit_price"] / tr["entry_price"] - 1
    tr["cost"] = cost
    tr["net_ret"] = tr["gross_ret"] - cost
    tr["excluded"] = tr["exit_reason"] == "end_of_data"

    # 진입 당시(신호일 t) 속성 (FR-E5)
    for c in ("market", "stock_regime", "market_regime", "cap_group", "liq_group"):
        if c in f:
            tr[c] = f[c].astype(object).to_numpy()[t_i]
    # 초과수익 (A10): 신호일 소속 시장 지수의 같은 보유기간 수익률 (진입 시가 → 청산 시가/종가)
    ip = _index_price(index)
    idx_ret = []
    for m, ed, xd, atc in zip(tr.get("market", [None] * len(tr)), tr["entry_date"], tr["exit_date"],
                              tr["exit_at_close"]):
        p = ip.get(m)
        if p is None or ed not in p.index or xd not in p.index:
            idx_ret.append(np.nan)
            continue
        idx_ret.append(p.at[xd, "close" if atc else "open"] / p.at[ed, "open"] - 1)
    tr["index_ret"] = idx_ret
    tr["excess_ret"] = tr["net_ret"] - tr["index_ret"]

    # 업종: 신호일 이전(포함) 최신 월말 스냅샷 (FR-E5, 진입 시점 기준)
    if sectors is not None and len(sectors):
        s = sectors.sort_values("date")
        key = tr[["ticker", "signal_date"]].reset_index().sort_values("signal_date")
        m = pd.merge_asof(key, s.rename(columns={"date": "signal_date"}), on="signal_date", by="ticker",
                          direction="backward")
        tr["sector"] = m.set_index("index")["sector"].reindex(tr.index)
    else:
        tr["sector"] = None
    return tr.sort_values(["entry_date", "ticker"], kind="stable").reset_index(drop=True)
