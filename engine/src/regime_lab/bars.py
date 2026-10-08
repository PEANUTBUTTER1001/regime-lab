"""1분봉 정규화·시간봉 집계 (P2-4, docs/분봉_데이터_설계.md §2). I/O 없음.

처리 순서(종목·일 단위, §2.2): ① (code, dt) 정렬 → ② N4 value_1m 차분(장외 포함 전체 행)
→ ③ N7 이상값 제외 → ④ N1 정규장 필터 → ⑤ N2·N3 가격 기준·조정(P2-4.3) → ⑥ §2.1 집계.

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
    g = minutes.assign(slot=slot).groupby(["code", "date", "slot"], sort=True)
    bars = g.agg(open=("open", "first"), high=("high", "max"), low=("low", "min"), close=("close", "last"),
                 volume=("volume", "sum"), value_1m=("value_1m", "sum"), n_minutes=("dt", "size"),
                 nxt_period=("nxt_period", "first")).reset_index()
    start = edges[bars["slot"] - 1]
    end = edges[bars["slot"]]
    bars["bar_start"] = bars["date"] + pd.to_timedelta(start, unit="min")
    bars["available_at"] = bars["date"] + pd.to_timedelta(end, unit="min")
    bars["bar_minutes"] = end - start
    return bars[BAR_COLS]


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
