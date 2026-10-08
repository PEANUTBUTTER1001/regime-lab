"""1분봉 정규화·시간봉 집계 (P2-4, docs/분봉_데이터_설계.md §2). I/O 없음.

처리 순서(종목·일 단위, §2.2): ① (code, dt) 정렬 → ② N4 value_1m 차분(장외 포함 전체 행)
→ ③ N7 이상값 제외 → ④ N1 정규장 필터 (normalize_minutes)
→ ⑤ N2 가격 기준(judge_price_basis·apply_price_basis)·N3 가격 구간(add_segments) → ⑥ §2.1 집계(aggregate_bars).

시각 규칙: 1분 행은 다음 분 시작에, 집계 봉은 확정 시각(마지막 분 + 1분)에 이용 가능하다.
as_of 예측은 확정 시각 ≤ as_of 인 봉만 입력으로 쓴다(FR-F3). 결측 분은 채우지 않는다(N6).
"""

from __future__ import annotations

from datetime import timedelta, timezone

import numpy as np
import pandas as pd

KST = timezone(timedelta(hours=9))

# 정규화·집계 코드가 바뀌면 올린다 (정규화 캐시 무효화, §3). 일봉 PREP_VERSION 과 독립
MINUTE_PREP_VERSION = 1

BAR_COLS = ["code", "date", "slot", "bar_start", "available_at", "bar_minutes", "n_minutes",
            "open", "high", "low", "close", "volume", "value_1m", "nxt_period"]


def _minute_of_day(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def slot_edges(timeframe: str, cfg: dict) -> np.ndarray:
    """집계 봉 경계(하루 안 분). 길이 = 봉 수 + 1, 마지막 값은 정규장 마지막 분 + 1 (확정 시각).

    예: 1h → [540, 600, …, 900, 931] — slot k 는 [edges[k-1], edges[k]) 분을 덮는다.
    """
    mcfg = cfg["minutes"]
    starts = [_minute_of_day(s) for s in mcfg["bar_starts"][timeframe]]
    close = _minute_of_day(mcfg["session_close"])
    if starts != sorted(set(starts)) or starts[0] != _minute_of_day(mcfg["session_open"]) or starts[-1] > close:
        raise ValueError(f"minutes.bar_starts.{timeframe} 은 session_open 부터 오름차순이어야 합니다: {starts}")
    return np.array(starts + [close + 1])


def normalize_minutes(raw: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """원본 1분 행(code, dt, open_p, high_p, low_p, close_p, volume, value) → 정규장 1분 행 + 제외 집계.

    결과 열: code, dt, date, open, high, low, close, volume, value_1m, value_cum, nxt_period.
    value_1m 은 정규장 필터 전에 같은 종목·일의 전체 행으로 차분한다(N4) — 장 전 누적분이 09:00 에 섞이지 않게.
    """
    mcfg = cfg["minutes"]
    df = raw.sort_values(["code", "dt"], kind="stable").reset_index(drop=True)
    date = df["dt"].dt.normalize()
    # N4: 당일 누적 거래대금 → 1분 거래대금. 첫 행은 누적값 그대로
    prev = df.groupby([df["code"], date], sort=False)["value"].shift(1)
    value_1m = (df["value"] - prev).fillna(df["value"])

    # N7: 시가·종가가 고가·저가 범위 밖이거나 고가 < 저가인 행
    bad = ((df["open_p"] > df["high_p"]) | (df["open_p"] < df["low_p"]) | (df["high_p"] < df["low_p"])
           | (df["close_p"] > df["high_p"]) | (df["close_p"] < df["low_p"]))
    # N1: 정규장만 (늦은 개장일은 있는 행 그대로)
    tod = df["dt"].dt.hour * 60 + df["dt"].dt.minute
    in_session = (tod >= _minute_of_day(mcfg["session_open"])) & (tod <= _minute_of_day(mcfg["session_close"]))
    keep = ~bad & in_session

    out = pd.DataFrame({
        "code": df["code"].astype(str), "dt": df["dt"], "date": date,
        "open": df["open_p"], "high": df["high_p"], "low": df["low_p"], "close": df["close_p"],
        "volume": df["volume"], "value_1m": value_1m.astype("int64"), "value_cum": df["value"],
        "nxt_period": date >= pd.Timestamp(mcfg["nxt_start"]),  # N5
    })[keep].reset_index(drop=True)
    stats = {"rows_in": int(len(df)), "outliers": int(bad.sum()),
             "off_session": int((~bad & ~in_session).sum()), "rows_out": int(len(out))}
    return out, stats


def aggregate_bars(minutes: pd.DataFrame, timeframe: str, cfg: dict) -> pd.DataFrame:
    """정규장 1분 행 → 집계 봉 (§2.1). 날을 넘겨 묶지 않고, 길이가 다른 봉도 그대로 둔다.

    slot 은 시각 기준 하루 안 순번(늦은 개장일은 첫 slot 이 빠진다). bar_minutes = 명목 길이,
    n_minutes = 실제 포함된 분 수, available_at = 확정 시각(봉 마지막 분 + 1분).
    """
    edges = slot_edges(timeframe, cfg)
    if minutes.empty:  # 봉이 있을 때와 같은 열 형식
        ts = minutes.dtypes.get("dt", "datetime64[ns]")  # 시각 열은 입력 dt 와 같은 해상도
        fixed = {"date": ts, "slot": "int64", "bar_start": ts, "available_at": ts,
                 "bar_minutes": "int64", "n_minutes": "int64", "nxt_period": "bool"}
        return pd.DataFrame({c: pd.Series(dtype=fixed.get(c, minutes.dtypes.get(c, "float64"))) for c in BAR_COLS})
    tod = (minutes["dt"].dt.hour * 60 + minutes["dt"].dt.minute).to_numpy()
    slot = np.searchsorted(edges, tod, side="right")  # 1..n
    if (slot < 1).any() or (slot >= len(edges)).any():
        raise ValueError("정규장 밖 행이 있습니다 — normalize_minutes 를 먼저 적용하세요")
    g = minutes.sort_values(["code", "dt"], kind="stable").assign(
        slot=lambda d: np.searchsorted(edges, (d["dt"].dt.hour * 60 + d["dt"].dt.minute).to_numpy(), side="right")
    ).groupby(["code", "date", "slot"], sort=True)
    extra = {"segment": ("segment", "first")} if "segment" in minutes else {}
    bars = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                 volume=("volume", "sum"), value_1m=("value_1m", "sum"), n_minutes=("dt", "size"),
                 nxt_period=("nxt_period", "first"), **extra).reset_index()
    start = edges[bars["slot"] - 1]
    end = edges[bars["slot"]]
    bars["bar_start"] = bars["date"] + pd.to_timedelta(start, unit="min")
    bars["available_at"] = bars["date"] + pd.to_timedelta(end, unit="min")
    bars["bar_minutes"] = end - start
    return bars[BAR_COLS + list(extra)]


def _next_trading_day(dates: pd.Series, calendar: pd.Series) -> pd.Series:
    cal = np.sort(pd.Series(calendar).drop_duplicates().to_numpy(dtype="datetime64[ns]"))
    pos = np.searchsorted(cal, dates.to_numpy(dtype="datetime64[ns]"), side="right")
    nxt = np.where(pos < len(cal), cal[np.minimum(pos, len(cal) - 1)], np.datetime64("NaT", "ns"))
    return pd.Series(pd.to_datetime(nxt), index=dates.index)


def judge_price_basis(minutes: pd.DataFrame, daily_raw: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """N2: 종목·일마다 정규장 1분봉 종가를 KRX 원주가 종가(close_raw)와 대조해 가격 기준을 판정한다.

    daily_raw: (code, date, close_raw). store 수정주가는 2026-04-24(NXT) 이후 시간외 가격이 섞여 쓰지 않는다
    (docs/분봉_데이터_설계.md §1.3). 결과 basis: raw(허용오차 안) · mismatch · no_daily.
    판정에 그날 종가가 필요하므로 available_at = 다음 거래일 장 시작 (거래일 달력은 daily_raw 의 날짜).
    """
    mcfg = cfg["minutes"]
    last = (minutes.sort_values(["code", "dt"], kind="stable")
            .groupby(["code", "date"], sort=True)["close"].last().rename("close_min").reset_index())
    out = last.merge(daily_raw[["code", "date", "close_raw"]], on=["code", "date"], how="left")
    off = (out["close_min"] / out["close_raw"] - 1).abs() * 100
    out["basis"] = np.where(out["close_raw"].isna(), "no_daily",
                            np.where(off <= mcfg["price_basis_tol_pct"], "raw", "mismatch"))
    open_ = pd.to_timedelta(_minute_of_day(mcfg["session_open"]), unit="min")
    out["available_at"] = _next_trading_day(out["date"], daily_raw["date"]) + open_
    return out[["code", "date", "basis", "available_at"]]


def apply_price_basis(minutes: pd.DataFrame, verdicts: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """당일 장중에는 전 거래일까지 확정된 판정을 이어 쓴다(N2). 직전 판정이 raw 인 종목·일만 남긴다.

    직전 판정이 없거나(첫 거래일) raw 가 아니면 그날 행을 뺀다 — 원주가 기준이 확인된 가격만 쓰기 위함.
    """
    if minutes.empty:
        return minutes.assign(basis=pd.Series(dtype=object)), {"no_verdict": 0, "not_raw": 0}
    days = minutes[["code", "date"]].drop_duplicates().sort_values("date")
    prev = pd.merge_asof(days, verdicts[["code", "date", "basis"]].sort_values("date"), on="date",
                         by="code", allow_exact_matches=False)
    m = minutes.merge(prev, on=["code", "date"], how="left")
    stats = {"no_verdict": int(m["basis"].isna().sum()), "not_raw": int((m["basis"].notna() & (m["basis"] != "raw")).sum())}
    return m[m["basis"] == "raw"].reset_index(drop=True), stats


def add_segments(minutes: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """N3: 같은 가격 구간 번호 segment (종목별 0부터). 수익률·특징은 같은 segment 안에서만 이어 붙인다.

    전 거래일 정규장 마지막 가격 대비 당일 첫 가격이 가격제한폭(event_gap_pct)을 넘으면 기준가가 바뀐 것
    (분할·병합 등)으로 보고 그날부터 새 구간으로 끊는다. 그날 첫 분이 끝나면 알 수 있는 값이라 미래 정보가 아니다.
    제한폭 안의 작은 조정(유·무상 권리락 등)은 잡지 못한다 — 채점에서 이벤트 당일을 따로 보고한다.
    """
    df = minutes.sort_values(["code", "dt"], kind="stable").reset_index(drop=True)
    day = df.groupby(["code", "date"], sort=True).agg(first=("open", "first"), last=("close", "last")).reset_index()
    day["segment"] = price_segments(day["code"], day.groupby("code")["last"].shift(1), day["first"],
                                    cfg["minutes"]["event_gap_pct"])
    return df.merge(day[["code", "date", "segment"]], on=["code", "date"], how="left")


def price_segments(code: pd.Series, prev_close: pd.Series, first_price: pd.Series, limit_pct: float) -> pd.Series:
    """가격 구간 번호 (종목별 0부터). 행은 종목·거래일 순으로 정렬돼 있어야 한다.

    |당일 첫 가격 ÷ 직전 거래일 정규장 종가 − 1| > limit_pct% 이면 그날부터 새 구간 (가격제한폭 밖 = 기준가 변경).
    첫 가격이 0 이하(무거래)이거나 비교 값이 없으면 끊지 않는다. 1분봉(add_segments)과 일봉(KRX close_raw·open_raw)이
    같은 규칙을 쓰도록 순수 함수로 둔다.
    """
    valid = (first_price > 0) & (prev_close > 0)
    # 제한폭 정확히 30% 인 상·하한가는 같은 구간 (부동소수 오차로 30.000…04% 가 되는 것을 막는 여유)
    jump = valid & ((first_price / prev_close - 1).abs() * 100 > limit_pct + 1e-9)
    return jump.astype(int).groupby(code.to_numpy()).cumsum()


def to_kst_naive(t) -> pd.Timestamp:
    """봉 시각은 시간대 없는 KST 다. 시간대가 있는 시각(예: 문서의 +09:00·UTC ISO)은 KST 로 바꿔 시간대를 뗀다.

    KST 는 1988년 이후 서머타임이 없어 고정 UTC+9 로 바꾼다 (시간대 DB 없이 Windows 에서도 동작).
    """
    ts = pd.Timestamp(t)
    return ts.tz_convert(KST).tz_localize(None) if ts.tzinfo is not None else ts


def confirmed_bars(bars: pd.DataFrame, as_of) -> pd.DataFrame:
    """as_of 에 이미 확정된 봉만 (available_at ≤ as_of). 진행 중인 봉은 쓰지 않는다(D-2).
    as_of 는 시간대가 없으면 KST 로 보고, 있으면 KST 로 바꿔 비교한다."""
    return bars[bars["available_at"] <= to_kst_naive(as_of)].reset_index(drop=True)
