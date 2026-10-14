"""과거 as_of 예측 일괄 생성·저장 (P2-7, 유스케이스).

흐름: 종목 묶음마다 1분봉 → 정규화(N1·N4·N5·N7) → 가격 기준(N2) → 가격 구간(N3) → 시간봉 집계,
     전 종목 봉으로 특징(시장 평균 포함) → h 마다 목표·시간 분할 → 모델 학습(학습 구간)·보정(검증 구간)
     → 모든 표본 예측 + 기준선 → 채점 → 저장.
외부 자료 결합(P4-3, 결정 P4-3a): doc_features 를 넘기면 같은 봉·분할·모델 설정으로 가격 단독과 결합 모델을 함께
학습하고, 결합 모델 예측을 저장하면서 평가 구간 비교(vs_price)를 scores 에 남긴다. 개선이 없으면 '검증 부족'(FR-B2).
파일·데이터 접근은 인자로 받은 store(인프라 data/forecast_store.py 의 ForecastStore)가 맡고, 시각도 호출자가
넘긴다 — 이 파일은 파일·시계에 직접 닿지 않는다(R1·R2). 저장 형식은 ForecastStore 문서 참고.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime

import numpy as np
import pandas as pd
import sklearn

from regime_lab.bars import (KST, MINUTE_PREP_VERSION, add_segments, aggregate_bars, apply_price_basis,
                             judge_price_basis, normalize_minutes, slot_edges, to_kst_naive)
from regime_lab.forecast import model as M
from regime_lab.forecast.baseline import historical
from regime_lab.forecast.evaluate import compare_variants, direction, direction_policy, holm_verdicts, score
from regime_lab.forecast.features import FEATURES, attach_doc_features, make_features
from regime_lab.forecast.split import SPLITS, assign_split
from regime_lab.forecast.targets import make_targets

CODE_RE = re.compile(r"^[0-9A-Za-z]{6}$")
# 결과 파일 형식 버전 (열을 바꾸면 올린다). 2: direction_shown·방향 표시 판정·slot/날 넘김 채점
# 3: meta.variant(price·fused)·docs_fingerprint·as_of_mode·docs_missing_rate·run_fingerprint, fused 는 scores[h].vs_price
#    (P4-3a ③). 형식 2 run 도 그대로 읽힌다 — 읽는 쪽은 없는 키를 price 로 본다.
FORECAST_RUN_VERSION = 3
AS_OF_MODES = ("observed", "historical_assumed")


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


def _split_valid(t: pd.DataFrame, va: pd.Series, holdout_share: float) -> tuple[pd.Series, pd.Series]:
    """검증 구간을 거래일 순서로 나눈다: 앞부분 = 모델 보정, 뒷부분(holdout_share) = 방향 표시 판정.
    같은 표본으로 보정과 판정을 하면 판정이 낙관적으로 된다."""
    days = np.sort(t.loc[va, "as_of"].dt.normalize().unique())
    n_pol = int(round(len(days) * holdout_share))
    if len(days) < 2 or n_pol < 1:
        return va, va & False
    first_pol = days[len(days) - n_pol]
    pol = va & (t["as_of"].dt.normalize() >= first_pol)
    # 보정 표본 중 목표가 판정 구간에 실현되는 것은 판정 구간 가격을 보므로 뺀다 (seongmin-claude #54 제안)
    cal = va & ~pol & (t["realized_at"] < first_pol)
    return cal, pol


def _date_split(as_of: pd.Series, cfg: dict) -> np.ndarray:
    s = cfg["forecast"]["split"]
    a = as_of.dt.normalize()
    return np.select([a <= pd.Timestamp(s["train_end"]), a <= pd.Timestamp(s["valid_end"])], ["train", "valid"], "test")


def run_forecast(store, cfg: dict, timeframe: str, now: datetime, codes: list[str] | None = None,
                 doc_features: pd.DataFrame | None = None, docs_info: dict | None = None) -> tuple[str, dict]:
    """docs_info (doc_features 와 함께): fingerprint(자료 지문) · as_of_mode(observed | historical_assumed — 소급 공시는
    historical_assumed 로만, 결과를 '소급 가정'으로 따로 보고, P4-3a ④)."""
    fcfg = cfg["forecast"]
    fused = doc_features is not None
    if fused and (not docs_info or docs_info.get("as_of_mode") not in AS_OF_MODES or not docs_info.get("fingerprint")):
        raise ValueError(f"doc_features 에는 docs_info(fingerprint, as_of_mode: {'|'.join(AS_OF_MODES)})가 필요합니다")
    if timeframe not in fcfg["timeframes"]:
        raise ValueError(f"지원하지 않는 봉: {timeframe} (가능: {fcfg['timeframes']})")
    rcfg = fcfg["runs"]
    if not set(rcfg["store_splits"]) <= {"test"}:
        # 학습·검증 구간 예측은 모델 학습·보정에 그 시점 뒤 값까지 쓰여 '그 시점 예측'이 아니다 (FR-F4)
        raise ValueError(f"forecast.runs.store_splits 는 test 만 가능합니다: {rcfg['store_splits']}")
    codes = sorted(codes) if codes else store.common_codes()
    bars, prep = build_bars(store, cfg, codes, timeframe, rcfg["batch_codes"])
    if bars.empty:
        raise ValueError("집계 봉이 없습니다 — 1분봉 사본·종목을 확인하세요")
    feats = make_features(bars)
    X = feats[FEATURES]
    date_split = _date_split(bars["available_at"], cfg)
    doc_cols, XF, missing_rate, compare = [], X, {}, {}
    if fused:
        D, doc_cols = attach_doc_features(feats, doc_features)
        XF = pd.concat([X, D], axis=1)
        missing_rate = {s: float(D.loc[date_split == s, "docs_missing"].mean()) for s in ("train", "valid", "test")
                        if (date_split == s).any()}
    keep = np.isin(date_split, rcfg["store_splits"])
    H = fcfg["horizon"][timeframe][0]
    n_slots = len(slot_edges(timeframe, cfg)) - 1
    preds, scores = [], {}
    for h in range(1, H + 1):
        t = make_targets(bars, h)
        t["split"] = assign_split(t, cfg)
        tr, va = t["split"].eq("train"), t["split"].eq("valid")
        cal, pol = _split_valid(t, va, fcfg["direction_policy"]["holdout_share"])
        fm = M.fit(XF[tr], t.loc[tr, "y"], XF[cal], t.loc[cal, "y"], cfg)  # 보정은 검증 앞부분만
        pm, pb = M.predict(fm, XF), historical(t, cfg)
        if fused:  # 같은 표본·분할·설정의 가격 단독 모델 — 평가 구간에서만 비교 (FR-B2)
            pp = M.predict(M.fit(X[tr], t.loc[tr, "y"], X[cal], t.loc[cal, "y"], cfg), X)
            te = t["split"].eq("test")
            compare[str(h)] = compare_variants(t[te], pp[te], pm[te], cfg)
        policy = direction_policy(t.loc[pol, "y"], pm.loc[pol, "p_up"], pb.loc[pol, "p_up"], cfg)  # 판정은 뒷부분
        ev = t[t["split"].isin(["valid", "test"])].assign(nxt=lambda d: d["nxt_period"])
        scores[str(h)] = {
            "conformal_widen": float(fm.widen),
            "direction_policy": policy,
            "model": _records(score(ev, pm.loc[ev.index], cfg, ["split", "nxt"])),
            "baseline": _records(score(ev, pb.loc[ev.index], cfg, ["split", "nxt"])),
            "model_by_slot": _records(score(ev, pm.loc[ev.index], cfg, ["split", "slot"])),
            "model_by_cross_day": _records(score(ev, pm.loc[ev.index], cfg, ["split", "cross_day"])),
        }
        qcols = [c for c in pm.columns if c.startswith("q")]
        out = pd.DataFrame({"code": t["code"], "as_of": t["as_of"], "h": h, "split": date_split,
                            "scored": t["split"].isin(SPLITS), "base_close": bars["close"]})
        out[qcols] = pm[qcols]
        out["p_up"] = pm["p_up"]
        out["direction"] = direction(pm["p_up"], cfg)
        # 화면 표시용: 방향 표시 조건(P2-8)을 통과한 봉·h 만 방향, 아니면 "불확실"
        out["direction_shown"] = out["direction"] if policy["enabled"] else out["direction"].where(
            out["direction"].isna(), "uncertain")
        out["reason"] = pm["reason"]
        # 날 넘김: 실현된 표본은 실제 목표 봉 날짜로, 아직 실현 전이면 slot 으로 (하루 마지막 slot 을 넘으면 다음 거래일)
        out["y"] = t["y"]
        out["cross_day"] = t["cross_day"].where(t["realized_at"].notna(), (t["slot"] + h) > n_slots)
        preds.append(out[keep])
    predictions = pd.concat(preds, ignore_index=True)
    if fused:
        for h, r in holm_verdicts(compare, fcfg["fusion_compare"]["alpha"]).items():
            scores[h]["vs_price"] = r
    chash = _hash({k: cfg[k] for k in ("minutes", "forecast")})
    run_id = f"{timeframe}-{now:%Y%m%dT%H%M%S}-{chash[:8]}"
    created = pd.Timestamp(now)
    created = created.tz_localize("+09:00") if created.tzinfo is None else created.tz_convert("+09:00")  # 시각은 KST
    meta = {
        "run_id": run_id, "created_at": created.isoformat(), "format_version": FORECAST_RUN_VERSION,
        "versions": {"scikit-learn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__},  # NFR-10
        "timeframe": timeframe, "horizon": H, "quantiles": fcfg["quantiles"], "direction": fcfg["direction"],
        "split": fcfg["split"], "stored_splits": rcfg["store_splits"], "model": {"kind": "sklearn.HistGradientBoosting",
        **fcfg["model"]}, "features": FEATURES, "minute_prep_version": MINUTE_PREP_VERSION, "config_hash": chash,
        "data": store.fingerprint(), "codes": len(codes), "bars": int(len(bars)), "prep": prep,
        "as_of_range": [str(bars["available_at"].min()), str(bars["available_at"].max())],
        "variant": "fused" if fused else "price", "doc_features": doc_cols,
        "docs_fingerprint": docs_info["fingerprint"] if fused else None,
        "as_of_mode": docs_info["as_of_mode"] if fused else None, "docs_missing_rate": missing_rate or None,
        # 같은 입력(설정·데이터·종목·자료) → 같은 지문. run_id 의 시각과 무관하게 재현 여부를 대조한다
        "run_fingerprint": _hash({"config": chash, "data": store.fingerprint(), "codes": codes, "timeframe": timeframe,
                                  "docs": docs_info["fingerprint"] if fused else None,
                                  "as_of_mode": docs_info["as_of_mode"] if fused else None, "doc_features": doc_cols}),
        "notice": "과거 데이터로 계산한 통계적 예상 범위이며 실제 가격 전망이나 매매 신호가 아닙니다.",
    }
    store.write_run(run_id, meta, predictions, scores)
    return run_id, meta


# ── 읽기 (P2-9 API·화면용). 계산(구간 → 가격, 시점 고르기)은 엔진에 두고 API 는 전달만 한다 ──

class ForecastError(Exception):
    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail or {}


def _iso_kst(t) -> str:
    return pd.Timestamp(t).tz_localize(KST).isoformat()


def list_runs(store) -> list[dict]:
    """예측 실행 목록 (최근 것 먼저)."""
    out = []
    for run_id in store.list_runs():
        m = store.read_meta(run_id)
        rng = store.as_of_range(run_id)  # 저장된 예측 = 최종 평가 구간
        out.append({**{k: m.get(k) for k in ("run_id", "timeframe", "created_at", "horizon", "codes", "format_version")},
                    "test_as_of_range": [_iso_kst(t) for t in rng] if rng else None})
    return sorted(out, key=lambda r: r["created_at"] or "", reverse=True)


def _require_run(store, run_id: str) -> dict:
    if not store.has_run(run_id):
        raise ForecastError("forecast_run_not_found", f"예측 실행이 없습니다: {run_id}", {"run_id": run_id})
    return store.read_meta(run_id)


def stock_forecast(store, cfg: dict, run_id: str, code: str, as_of: str | None = None) -> dict:
    """한 종목의 한 시점 예측. as_of 가 없으면 가장 최근 시점, 있으면 그 시각 이전(포함)의 마지막 확정 시점.

    steps: h=1..H 마다 회색 구간(가격 = 기준 종가 × exp(분위)), 통계적 방향(표시 판정 통과 h 만), 상승 확률, 실제 종가(있으면).
    가운데 분위(q0.5)는 채점용이라 응답에 넣지 않는다 (E17: 가운데 예측선 없음).
    history: 그 시점까지의 직전 기준 종가들 (차트용, 확정 봉만).
    상태 (P2-9a): ok / no_stock(이 실행에 종목 없음) / no_as_of(그 시각이 평가 구간 밖) / insufficient_history(예측 값 결측)
    """
    if not CODE_RE.match(code or ""):
        raise ForecastError("invalid_code", "종목코드는 6자리 영숫자입니다", {"code": code})
    meta = _require_run(store, run_id)
    try:
        t = to_kst_naive(as_of) if as_of else None
    except (ValueError, TypeError):
        raise ForecastError("invalid_as_of", "as_of 는 ISO 시각이어야 합니다", {"as_of": as_of}) from None
    p = store.read_predictions(run_id, code)
    base = {"run_id": run_id, "timeframe": meta["timeframe"], "code": code,
            "horizon": meta["horizon"], "notice": meta.get("notice")}
    if p.empty:
        return {**base, "status": "no_stock"}
    # 평가 구간 밖: 그 종목 첫 예측보다 이르거나, 마지막 예측 날짜보다 늦은 날 (같은 날 장 마감 뒤는 마지막 확정 시점)
    if t is not None and (t < p["as_of"].min() or t.normalize() > p["as_of"].max().normalize()):
        return {**base, "status": "no_as_of", "test_as_of_range": [_iso_kst(p["as_of"].min()), _iso_kst(p["as_of"].max())]}
    if t is not None:
        p = p[p["as_of"] <= t]
    at = p["as_of"].max()
    rows = p[p["as_of"] == at].sort_values("h")
    qs = meta["quantiles"]
    lo, hi = f"q{qs[0]:g}", f"q{qs[-1]:g}"
    if rows[[lo, hi, "p_up"]].isna().any(axis=None):
        return {**base, "status": "insufficient_history", "as_of": _iso_kst(at)}
    shown = rows["direction_shown"] if "direction_shown" in rows else pd.Series("uncertain", index=rows.index)
    bc = float(rows["base_close"].iloc[0])
    steps = [{"h": int(r["h"]), "low": bc * float(np.exp(r[lo])), "high": bc * float(np.exp(r[hi])), "p_up": float(r["p_up"]), "direction": d,
              "actual": None if pd.isna(r["y"]) else bc * float(np.exp(r["y"])), "cross_day": bool(r["cross_day"])}
             for r, d in zip(rows.to_dict("records"), shown)]
    hist = p[(p["h"] == 1) & (p["as_of"] <= at)].sort_values("as_of").tail(cfg["forecast"]["api"]["history_bars"])
    return {**base, "status": "ok", "as_of": _iso_kst(at), "base_close": bc, "steps": steps,
            "history": [{"as_of": _iso_kst(a), "close": float(c)} for a, c in zip(hist["as_of"], hist["base_close"])]}


def run_scores(store, run_id: str) -> dict:
    """봉·h 별 모델 vs 기준선 채점과 방향 표시 판정."""
    meta = _require_run(store, run_id)
    return {"run_id": run_id, "timeframe": meta["timeframe"], "horizon": meta["horizon"], "split": meta["split"],
            "scores": store.read_scores(run_id), "notice": meta.get("notice")}
