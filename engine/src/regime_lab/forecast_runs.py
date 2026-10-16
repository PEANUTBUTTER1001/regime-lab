"""과거 as_of 예측 일괄 생성·저장 (P2-7, 유스케이스).

흐름: 봉 만들기 — 일봉(1d, 제품 기본, 결정 D-2b)은 KRX 정규장 원주가 일봉에서 바로(daily_bars),
     시간봉(1h·3h)은 종목 묶음마다 1분봉 → 정규화(N1·N4·N5·N7) → 가격 기준(N2) → 가격 구간(N3) → 시간봉 집계,
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

from regime_lab.bars import (KST, MINUTE_PREP_VERSION, add_segments, aggregate_bars, apply_price_basis, daily_bars,
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
# 4: meta.bar_source(krx_daily·minutes) — 일봉(1d) 예측(P2-11, 결정 D-2b). 일봉 run 은 meta.minute_prep_version 이 null 이고
#    데이터 지문에 1분봉이 없다. 예측·채점 파일의 열은 그대로라 형식 2·3 run 도 그대로 읽힌다.
FORECAST_RUN_VERSION = 5  # P4-3b: fusion_kind/비교 실행/가중치 버전/근거 참조 (2~4 읽기 유지)
DAILY = "1d"  # KRX 원주가 일봉에서 만드는 봉 (1분봉을 쓰지 않음)
AS_OF_MODES = ("observed", "historical_assumed")


def build_bars(store, cfg: dict, codes: list[str], timeframe: str, batch: int) -> tuple[pd.DataFrame, dict]:
    """종목 묶음별로 봉을 만들어 전 종목 봉을 돌려준다 (메모리 상한). 제외 행 수를 합산해 돌려준다.
    일봉은 KRX 원주가 일봉에서(daily_bars), 시간봉은 1분봉을 정규화·집계해서."""
    if timeframe == DAILY:
        parts, stats = [], {"rows_in": 0, "no_trade": 0, "outliers": 0}
        for i in range(0, len(codes), batch):
            b, s = daily_bars(store.load_krx_raw(codes[i:i + batch]), cfg)
            for k in stats:
                stats[k] += s[k]
            parts.append(b)
        bars = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame()
        if bars.empty:
            return bars, stats
        return bars.sort_values(["code", "available_at"], kind="stable").reset_index(drop=True), stats
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
                 doc_features: pd.DataFrame | None = None, docs_info: dict | None = None,
                 *, _prepared=None, _capture=None, _price=None, _docs=None, _evidence=None, _data_fp=None) -> tuple[str, dict]:
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
    bars, prep = _prepared if _prepared is not None else build_bars(store, cfg, codes, timeframe, rcfg["batch_codes"])
    if bars.empty:
        raise ValueError("집계 봉이 없습니다 — 1분봉 사본·종목을 확인하세요")
    feats = make_features(bars)
    X = feats[FEATURES]
    date_split = _date_split(bars["available_at"], cfg)
    doc_cols, XF, missing_rate, compare = [], X, {}, {}
    kind = docs_info.get("fusion_kind", "docs") if fused else "none"
    comparisons = {}
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
            pp = _price[h] if _price is not None else M.predict(M.fit(X[tr], t.loc[tr, "y"], X[cal], t.loc[cal, "y"], cfg), X)
            te = t["split"].eq("test")
            compare[str(h)] = compare_variants(t[te], pp[te], pm[te], cfg)
            if _docs is not None:
                # A/B/C 모두 같은 실현 표본. 전체 3쌍×h의 p값을 한 번에 Holm 보정.
                bp = _docs[h]
                common = te & pp["p_up"].notna() & bp["p_up"].notna() & pm["p_up"].notna()
                for name, a, b in (("docs_price", pp, bp), ("weighted_price", pp, pm), ("weighted_docs", bp, pm)):
                    comparisons[f"{h}:{name}"] = compare_variants(t[common], a[common], b[common], cfg)
        if _capture is not None:
            _capture[h] = pm
        policy = direction_policy(t.loc[pol, "y"], pm.loc[pol, "p_up"], pb.loc[pol, "p_up"], cfg)  # 판정은 뒷부분
        ev = t[t["split"].isin(["valid", "test"])].assign(nxt=lambda d: d["nxt_period"])
        scores[str(h)] = {
            "ignored_features": [c for c in XF if c not in fm.features],
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
        if fused:
            out["bar_ordinal"] = bars.groupby("code", sort=False).cumcount()
            out["docs_missing"] = D["docs_missing"].astype(bool)
            total_col = f"f_all_all_{max(cfg['fusion']['windows'])}"
            out["doc_count"] = doc_features.set_index(["code", "as_of"])[total_col].reindex(
                pd.MultiIndex.from_frame(feats[["code", "as_of"]])).to_numpy() if total_col in doc_features else np.nan
            missing_col = f"f_influence_missing_h{h}"
            out["influence_missing"] = D[missing_col].fillna(1).astype(bool) if missing_col in D else False
        preds.append(out[keep])
    predictions = pd.concat(preds, ignore_index=True)
    if fused:
        for h, r in holm_verdicts(compare, fcfg["fusion_compare"]["alpha"]).items():
            scores[h]["vs_price"] = r
    if comparisons:
        for key, result in holm_verdicts(comparisons, fcfg["fusion_compare"]["alpha"]).items():
            h, name = key.split(":")
            scores[h].setdefault("comparisons", {})[name] = result
        for value in scores.values():
            c = value["comparisons"]
            states = [c[n]["verdict"] for n in ("weighted_price", "weighted_docs")]
            value["weighted_verdict"] = ("improved" if all(s == "improved" for s in states) else
                                         "insufficient" if "insufficient" in states else "not_improved")
    daily = timeframe == DAILY
    data_fp = store.fingerprint(minutes=not daily) if _data_fp is None else _data_fp
    chash = _hash({k: cfg[k] for k in (("minutes", "forecast", "fusion", "influence") if fused else ("minutes", "forecast"))})
    run_id = f"{timeframe}-{now:%Y%m%dT%H%M%S}-{chash[:8]}"
    if fused:
        run_id += f"-{kind}-{_hash(docs_info)[:8]}"
    created = pd.Timestamp(now)
    created = created.tz_localize("+09:00") if created.tzinfo is None else created.tz_convert("+09:00")  # 시각은 KST
    meta = {
        "run_id": run_id, "created_at": created.isoformat(), "format_version": FORECAST_RUN_VERSION,
        "versions": {"scikit-learn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__},  # NFR-10
        "timeframe": timeframe, "horizon": H, "quantiles": fcfg["quantiles"], "direction": fcfg["direction"],
        "split": fcfg["split"], "stored_splits": rcfg["store_splits"], "model": {"kind": "sklearn.HistGradientBoosting",
        **fcfg["model"]}, "features": FEATURES, "bar_source": "krx_daily" if daily else "minutes",
        "minute_prep_version": None if daily else MINUTE_PREP_VERSION, "config_hash": chash, "data": data_fp, "codes": len(codes), "bars": int(len(bars)), "prep": prep,
        "as_of_range": [str(bars["available_at"].min()), str(bars["available_at"].max())],
        "variant": "fused" if fused else "price", "doc_features": doc_cols,
        "fusion_kind": kind, "evaluation_mode": "retrospective",
        "docs_fingerprint": docs_info["fingerprint"] if fused else None,
        "as_of_mode": docs_info["as_of_mode"] if fused else None, "docs_missing_rate": missing_rate or None,
        # 같은 입력(설정·데이터·종목·자료) → 같은 지문. run_id 의 시각과 무관하게 재현 여부를 대조한다
        "run_fingerprint": _hash({"config": chash, "data": data_fp, "codes": codes, "timeframe": timeframe,
                                  "docs": docs_info["fingerprint"] if fused else None,
                                  "influence": docs_info.get("influence_versions") if fused else None,
                                  "as_of_mode": docs_info["as_of_mode"] if fused else None, "doc_features": doc_cols}),
        "notice": "과거 데이터로 계산한 통계적 예상 범위이며 실제 가격 전망이나 매매 신호가 아닙니다.",
    }
    if fused:
        meta.update({k: docs_info[k] for k in ("audit", "influence_versions", "comparison_runs", "feature_policy") if k in docs_info})
    if _evidence is not None:
        store.write_run(run_id, meta, predictions, scores, evidence=_evidence)
    else:
        store.write_run(run_id, meta, predictions, scores)
    return run_id, meta


def run_fusion(store, docs_store, influence_store, cfg, now, codes=None, mode="observed", with_influence=False):
    """공통 입력을 고정해 A/B(/C)를 생성. 순수 특징·학습 결과를 재사용하며 가격 모델 설정은 보존."""
    from regime_lab.fusion import build_doc_features
    from regime_lab.influence import weighted_features
    from regime_lab.influence_runs import training_inputs

    bars, docs, coverage, inputs = training_inputs(store, docs_store, cfg, codes)
    features, evidence, audit = build_doc_features(docs, bars, cfg, mode, coverage)
    if not audit["by_split"]["train"]["usable_rows"] or evidence.empty:
        raise ValueError("외부 자료 학습 교집합 없음: 종목 연결·수집 범위·가격 기간 확인 필요")
    weighted, used, model = None, {}, None
    if with_influence:
        models = [m for m in influence_store.list_models() if m.get("format_version") == 2 and
                  m.get("inputs") == inputs and m.get("as_of_mode") == mode]
        if not models:
            raise ValueError("동일 입력/설정/종목의 영향력 모델 없음: influence-run 먼저 실행")
        model = max(models, key=lambda m: m["created_at"])
        weighted, used = weighted_features(features, model, cfg)
    prepared = (bars, inputs["prep"])
    pa, pb = {}, {}
    a, _ = run_forecast(store, cfg, "1d", now, codes, _prepared=prepared, _capture=pa, _data_fp=inputs["price"])
    info = {"fingerprint": inputs["docs"], "as_of_mode": mode, "audit": audit, "fusion_kind": "docs",
            "feature_policy": cfg["fusion"], "comparison_runs": {"price": a}}
    b, bm = run_forecast(store, cfg, "1d", now, codes, features, info, _prepared=prepared, _capture=pb, _price=pa,
                         _evidence=evidence, _data_fp=inputs["price"])
    if not with_influence:
        return b, bm
    info = {**info, "fusion_kind": "weighted", "influence_versions": {"model": model["model_version"], "snapshots": used},
            "comparison_runs": {"price": a, "docs": b}}
    # 집계/가중치/모델 설정 모두 run 지문에 포함. 모델 결과가 달라지면 기존 run을 덮어쓰지 않음.
    return run_forecast(store, cfg, "1d", now, codes, weighted, info, _prepared=prepared, _price=pa, _docs=pb,
                        _evidence=evidence, _data_fp=inputs["price"])


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
                    **({k: m.get(k) for k in ("fusion_kind", "as_of_mode", "comparison_runs")} if m.get("format_version", 2) >= 5 else {}),
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
    if meta.get("format_version", 2) >= 5:
        base.update({k: meta.get(k) for k in ("fusion_kind", "as_of_mode", "influence_versions", "comparison_runs", "docs_missing_rate", "evaluation_mode")})
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
    if "docs_missing" in rows:
        base["documents"] = {"missing": bool(rows["docs_missing"].iloc[0]),
                             "count": None if pd.isna(rows["doc_count"].iloc[0]) else int(rows["doc_count"].iloc[0]),
                             "influence_missing": {str(int(r.h)): bool(r.influence_missing) for r in rows.itertuples()}}
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


def evidence_view(store, cfg, run_id, code, as_of, docs, now, page=1, page_size=None, manifests=None):
    """확정 예측의 실제 입력 문서 참조. 검색어/top-k와 독립, 현재 삭제/권한 제한 우선."""
    from regime_lab.rag.select import aware, select_as_of

    fc = cfg["fusion"]
    size = fc["evidence_page_size"] if page_size is None else page_size
    if page < 1 or size < 1 or size > fc["evidence_max_page_size"]:
        raise ForecastError("invalid_page", "근거 페이지 범위 오류")
    meta = _require_run(store, run_id)
    stock = stock_forecast(store, cfg, run_id, code, as_of)
    base = {"run_id": run_id, "code": code, "as_of": stock.get("as_of"), "mode": meta.get("as_of_mode"),
            "page": page, "page_size": size, "notice": meta.get("notice"), "influence_versions": meta.get("influence_versions")}
    if meta.get("fusion_kind", "none") == "none" or stock["status"] != "ok":
        return {**base, "status": "no_evidence", "items": [], "total": 0}
    if stock.get("documents", {}).get("missing"):
        return {**base, "status": "docs_missing", "items": [], "total": 0}
    try:
        e = store.read_evidence(run_id, code)
    except (OSError, ValueError):
        return {**base, "status": "evidence_missing", "items": [], "total": 0}
    t = to_kst_naive(stock["as_of"])
    predictions = store.read_predictions(run_id, code)
    ordinal = int(predictions.loc[predictions["as_of"] == t, "bar_ordinal"].iloc[0])
    policy = meta.get("feature_policy", fc)
    delta = ordinal - e["linked_ordinal"]
    e = e[(e["linked_as_of"] <= t) & (e["until"] > t) & (delta >= 0) & (delta < max(policy["windows"]))]
    mode = meta.get("as_of_mode") or "observed"
    selected = { (docs[i]["doc_id"], int(docs[i]["version"])): docs[i]
                 for i in select_as_of(docs, aware(stock["as_of"]), mode)}
    blocked = {d["doc_id"] for d in docs if d.get("status") in ("deleted", "expired") or
               (d.get("expires_at") and aware(d["expires_at"]) <= aware(now))}
    rows = []
    for r in e.sort_values(["linked_as_of", "doc_id"], ascending=[False, True]).to_dict("records"):
        d = selected.get((r["doc_id"], int(r["version"])))
        manifest = (manifests or {}).get(r["source"], {})
        source_allowed = manifest.get("active", True) is not False and manifest.get("license_scope", "metadata_only") == "metadata_only"
        available = d is not None and r["doc_id"] not in blocked and d.get("license_scope") == "metadata_only" and source_allowed
        rows.append({"doc_id": r["doc_id"], "version": int(r["version"]), "source": r["source"], "group": r["group"],
                     "linked_as_of": _iso_kst(r["linked_as_of"]), "available_at": r["available_at"],
                     "windows": [w for w in policy["windows"] if ordinal - r["linked_ordinal"] < w],
                     "status": "available" if available else "restricted" if r["doc_id"] in blocked or not source_allowed else "missing",
                     "title": d.get("title") if available else None, "url": d.get("url") if available else None})
    latest = max((r["available_at"] for r in rows if r["status"] == "available"), key=aware, default=None)
    return {**base, "status": "ok" if rows else "no_evidence", "items": rows[(page - 1) * size:page * size],
            "total": len(rows), "latest_available_at": latest}
