"""과거 as_of 예측 일괄 생성·저장 (P2-7, 유스케이스).

흐름: 종목 묶음마다 1분봉 → 정규화(N1·N4·N5·N7) → 가격 기준(N2) → 가격 구간(N3) → 시간봉 집계,
     전 종목 봉으로 특징(시장 평균 포함) → h 마다 목표·시간 분할 → 모델 학습(학습 구간)·보정(검증 구간)
     → 모든 표본 예측 + 기준선 → 채점 → 저장.
파일·데이터 접근은 인자로 받은 store(인프라 data/forecast_store.py 의 ForecastStore)가 맡고, 시각도 호출자가
넘긴다 — 이 파일은 파일·시계에 직접 닿지 않는다(R1·R2). 저장 형식은 ForecastStore 문서 참고.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime

import numpy as np
import pandas as pd

from regime_lab.bars import (MINUTE_PREP_VERSION, add_segments, aggregate_bars, apply_price_basis, judge_price_basis,
                             normalize_minutes, slot_edges)
from regime_lab.forecast import model as M
from regime_lab.forecast.baseline import historical
from regime_lab.forecast.evaluate import direction, score
from regime_lab.forecast.features import FEATURES, make_features
from regime_lab.forecast.split import SPLITS, assign_split
from regime_lab.forecast.targets import make_targets

FORECAST_RUN_VERSION = 1  # 결과 파일 형식 버전 (열을 바꾸면 올린다)


def build_bars(store, cfg: dict, codes: list[str], timeframe: str, batch: int) -> tuple[pd.DataFrame, dict]:
    """종목 묶음별로 1분봉을 정규화·집계해 전 종목 봉을 만든다 (메모리 상한). 제외 행 수를 합산해 돌려준다."""
    parts, stats = [], {"rows_in": 0, "outliers": 0, "off_session": 0, "no_verdict": 0, "not_raw": 0}
    for i in range(0, len(codes), batch):
        chunk = codes[i:i + batch]
        m, s1 = normalize_minutes(store.load_minutes(chunk), cfg)
        m, s2 = apply_price_basis(m, judge_price_basis(m, store.load_daily_raw(chunk), cfg))
        for k, v in {**s1, **s2}.items():
            if k in stats:
                stats[k] += v
        parts.append(aggregate_bars(add_segments(m, cfg), timeframe, cfg))
    bars = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
    return bars.sort_values(["code", "available_at"], kind="stable").reset_index(drop=True), stats


def _hash(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def _records(df: pd.DataFrame) -> list[dict]:
    return json.loads(df.to_json(orient="records", force_ascii=False))  # numpy 값 → JSON 기본형


def _date_split(as_of: pd.Series, cfg: dict) -> np.ndarray:
    s = cfg["forecast"]["split"]
    a = as_of.dt.normalize()
    return np.select([a <= pd.Timestamp(s["train_end"]), a <= pd.Timestamp(s["valid_end"])], ["train", "valid"], "test")


def run_forecast(store, cfg: dict, timeframe: str, now: datetime, codes: list[str] | None = None) -> tuple[str, dict]:
    fcfg = cfg["forecast"]
    if timeframe not in fcfg["timeframes"]:
        raise ValueError(f"지원하지 않는 봉: {timeframe} (가능: {fcfg['timeframes']})")
    rcfg = fcfg["runs"]
    codes = sorted(codes) if codes else store.common_codes()
    bars, prep = build_bars(store, cfg, codes, timeframe, rcfg["batch_codes"])
    if bars.empty:
        raise ValueError("집계 봉이 없습니다 — 1분봉 사본·종목을 확인하세요")
    feats = make_features(bars)
    X = feats[FEATURES]
    date_split = _date_split(bars["available_at"], cfg)
    keep = np.isin(date_split, rcfg["store_splits"])
    H = fcfg["horizon"][timeframe][0]
    n_slots = len(slot_edges(timeframe, cfg)) - 1
    preds, scores = [], {}
    for h in range(1, H + 1):
        t = make_targets(bars, h)
        t["split"] = assign_split(t, cfg)
        tr, va = t["split"].eq("train"), t["split"].eq("valid")
        fm = M.fit(X[tr], t.loc[tr, "y"], X[va], t.loc[va, "y"], cfg)
        pm, pb = M.predict(fm, X), historical(t, cfg)
        ev = t[t["split"].isin(["valid", "test"])].assign(nxt=lambda d: d["nxt_period"])
        scores[str(h)] = {
            "conformal_widen": float(fm.widen),
            "model": _records(score(ev, pm.loc[ev.index], cfg, ["split", "nxt"])),
            "baseline": _records(score(ev, pb.loc[ev.index], cfg, ["split", "nxt"])),
        }
        qcols = [c for c in pm.columns if c.startswith("q")]
        out = pd.DataFrame({"code": t["code"], "as_of": t["as_of"], "h": h, "split": date_split,
                            "scored": t["split"].isin(SPLITS), "base_close": bars["close"]})
        out[qcols] = pm[qcols]
        out["p_up"] = pm["p_up"]
        out["direction"] = direction(pm["p_up"], cfg)
        out["reason"] = pm["reason"]
        # 날 넘김: 실현된 표본은 실제 목표 봉 날짜로, 아직 실현 전이면 slot 으로 (하루 마지막 slot 을 넘으면 다음 거래일)
        out["y"] = t["y"]
        out["cross_day"] = t["cross_day"].where(t["realized_at"].notna(), (t["slot"] + h) > n_slots)
        preds.append(out[keep])
    predictions = pd.concat(preds, ignore_index=True)
    chash = _hash({k: cfg[k] for k in ("minutes", "forecast")})
    run_id = f"{timeframe}-{now:%Y%m%dT%H%M%S}-{chash[:8]}"
    meta = {
        "run_id": run_id, "created_at": now.isoformat(), "format_version": FORECAST_RUN_VERSION,
        "timeframe": timeframe, "horizon": H, "quantiles": fcfg["quantiles"], "direction": fcfg["direction"],
        "split": fcfg["split"], "stored_splits": rcfg["store_splits"], "model": {"kind": "sklearn.HistGradientBoosting",
        **fcfg["model"]}, "features": FEATURES, "minute_prep_version": MINUTE_PREP_VERSION, "config_hash": chash,
        "data": store.fingerprint(), "codes": len(codes), "bars": int(len(bars)), "prep": prep,
        "as_of_range": [str(bars["available_at"].min()), str(bars["available_at"].max())],
        "notice": "과거 데이터로 계산한 통계적 예상 범위이며 실제 가격 전망이나 매매 신호가 아닙니다.",
    }
    store.write_run(run_id, meta, predictions, scores)
    return run_id, meta
