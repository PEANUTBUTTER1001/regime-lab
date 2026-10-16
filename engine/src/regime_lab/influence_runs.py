"""영향력 가중치 결과 조회 (P3-13.3, SRS FR-N6·FR-N7). 학습 실행(P3-12)은 이 모듈에 따로 들어온다. 결정 P3-12a.

결과 파일은 인프라 data/influence_store.py 가 읽고, 여기서는 기준 시각 T 에 맞춰 고르고 거르기만 한다 (R1·R2).
시간 순서 규칙 (docs/P3_영향분석_화면_요구.md §2):
- 모델: 학습 구간 끝 날짜 < T 의 KST 날짜인 것 중 학습 끝이 가장 늦은 것. 없으면 no_model (뒤에 학습된 모델이
  있는지는 알리지 않는다 — T 뒤 모델을 지워도 같은 응답이어야 하므로).
- 사건: T 에 보이는 자료 버전만 (select_as_of 와 같은 행 조건). 연결 봉은 bar.start > available_at 인 첫 거래일
  일봉이며, 거래일 달력과 가격은 종가가 T 이전에 확정된 날만 쓴다. 그 뒤 봉에 연결될 사건은 연결 봉 없음(None).
- 결과 없음 4종: no_model · no_events · coverage_gap(기간 안 수집 공백·부분) · data_unavailable(오류로 냄).
"""

from __future__ import annotations

import bisect
from datetime import date, datetime, time, timedelta

import pandas as pd

from regime_lab import retrieval
from regime_lab.bars import KST, to_kst_naive
from regime_lab.rag.select import AS_OF_MODES, aware, select_as_of

NOTICE = "자료 묶음과 수익률의 과거 관련성이며 인과관계나 미래 수익을 뜻하지 않습니다."
PERIODS = ("train", "valid", "test")
_TICKER_LEN = 6
_EVENT_DOC_FIELDS = ("title", "url", "source_type", "license_scope", "time_precision")


class InfluenceError(Exception):
    def __init__(self, code: str, message: str, detail: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail or {}


def _hm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _parse_as_of(as_of, mode, errors: dict) -> datetime | None:
    t = None
    try:
        t = aware(as_of)
    except (TypeError, ValueError):
        errors["as_of"] = "시간대가 포함된 ISO 8601 시각이 필요합니다 (예: 2026-10-07T09:00:00+09:00)"
    if mode is not None and mode not in AS_OF_MODES:
        errors["mode"] = f"{AS_OF_MODES} 중 하나"
    return t


def _raise_if(errors: dict):
    if errors:
        raise InfluenceError("validation_failed", "Check the input values.", {"fields": errors})


def _models(store) -> list[dict]:
    try:
        return store.list_models()
    except ValueError as e:
        raise InfluenceError("data_unavailable", "Influence results could not be read.", {"error": str(e)}) from None


def _train_end(m: dict) -> date:
    return date.fromisoformat(m["periods"]["train"]["end"])


def pick_model(store, t: datetime, h: int = 1) -> dict | None:
    """학습 구간 끝 날짜 < T 의 KST 날짜 (그 날 자료가 다 모인 뒤) 인 모델 중 학습 끝이 가장 늦은 것."""
    day = t.astimezone(KST).date()
    try:
        usable = [m for m in _models(store) if _train_end(m) < day
                  and ((m.get("format_version", 1) == 1 and h == 1) or
                       (m.get("format_version") == 2 and aware(m["knowledge_cutoff"]) <= t and str(h) in m.get("metrics", {})))]
    except (KeyError, ValueError, TypeError):
        raise InfluenceError("data_unavailable", "Influence model cutoff is invalid.") from None
    return max(usable, key=lambda m: (_train_end(m), m["model_version"])) if usable else None


def _model_info(m: dict, t: datetime) -> dict:
    test_end = (m.get("periods") or {}).get("test", {}).get("end")
    return {**{k: m.get(k) for k in ("model_version", "created_at", "target", "as_of_mode", "periods", "baseline",
                                    "min_eval_samples", "format_version", "knowledge_cutoff", "metrics", "audit") if k in m},
            # 최종 평가 구간이 T 뒤까지 이어지면 화면이 "평가는 기준 시각 뒤 기간 포함"을 알린다
            "eval_beyond_as_of": bool(test_end and date.fromisoformat(test_end) >= t.astimezone(KST).date())}


def _no_model(t: datetime) -> dict:
    return {"status": "no_model", "model": None, "as_of": t.isoformat(), "notice": NOTICE}


def model_view(store, as_of, h: int = 1) -> dict:
    errors: dict = {}
    t = _parse_as_of(as_of, None, errors)
    _raise_if(errors)
    m = pick_model(store, t, h)
    if m is None:
        return _no_model(t)
    return {"status": "ok", "model": _model_info(m, t), "as_of": t.isoformat(), "notice": NOTICE}


def _num(v):
    """JSON 으로 낼 값: numpy 숫자는 파이썬 값으로, NaN 은 None 으로."""
    if hasattr(v, "item"):
        v = v.item()
    return None if v is None or (isinstance(v, float) and v != v) else v


def training_inputs(price_store, docs_store, cfg, codes=None):
    """공개 입력 조립 유스케이스. 인프라 객체는 호출자가 주입; DB/수집기 import 없음."""
    from regime_lab.forecast_runs import build_bars
    from regime_lab.fusion import fingerprint

    manifests = docs_store.manifests()
    sources = cfg["fusion"]["sources"]
    if not sources or any(s not in manifests or manifests[s].get("license_scope") != "metadata_only" or
                          manifests[s].get("active", True) is False for s in sources):
        raise ValueError("활성 설정 출처의 metadata_only manifest가 필요합니다")
    files = docs_store.files()
    before_docs = docs_store.fingerprint(files)
    before_price = price_store.fingerprint(minutes=False)
    coverage = docs_store.coverage()
    docs = docs_store.read(files)
    codes = sorted(codes) if codes is not None else price_store.common_codes()
    bars, prep = build_bars(price_store, cfg, codes, "1d", cfg["forecast"]["runs"]["batch_codes"])
    if bars.empty:
        raise ValueError("일봉이 없습니다")
    if (before_docs != docs_store.fingerprint(docs_store.files()) or manifests != docs_store.manifests() or
            fingerprint(coverage) != fingerprint(docs_store.coverage()) or
            before_price != price_store.fingerprint(minutes=False)):
        raise ValueError("학습 입력이 읽는 도중 변경됨: 수집 완료 후 재시도")
    return bars, docs, coverage, {"docs": fingerprint({"files": before_docs, "coverage": coverage, "manifests": manifests}),
                                 "docs_files": before_docs, "coverage": fingerprint(coverage),
                                 "manifests": fingerprint(manifests), "price": before_price,
                                 "policy": fingerprint({"fusion": cfg["fusion"], "influence": cfg["influence"],
                                                        "forecast": cfg["forecast"], "minutes": cfg["minutes"],
                                                        "feature_contract": 1, "codes": codes}), "prep": prep}


def run_training(price_store, docs_store, result_store, cfg, now, codes=None, mode="observed"):
    """P3-12b 서버 offline CLI. 완성된 세 파일만 저장소가 원자적으로 공개한다."""
    from regime_lab.fusion import build_doc_features, fingerprint
    from regime_lab.influence import train_influence
    import sklearn
    import numpy as np

    bars, docs, coverage, inputs = training_inputs(price_store, docs_store, cfg, codes)
    features, evidence, audit = build_doc_features(docs, bars, cfg, mode, coverage)
    fitted, weights = train_influence(bars, features, evidence, cfg)
    if evidence.empty or audit["by_split"]["train"]["usable_rows"] == 0:
        raise ValueError("공시/가격 학습 교집합이 없습니다")
    created = pd.Timestamp(now)
    created = created.tz_localize(KST) if created.tzinfo is None else created.tz_convert(KST)
    model_version = f"influence-{created:%Y%m%dT%H%M%S}-{fingerprint([inputs, mode])[:12]}"
    sc = cfg["forecast"]["split"]
    first, last = bars["date"].min().date().isoformat(), bars["date"].max().date().isoformat()
    periods = {"train": {"start": first, "end": sc["train_end"]},
               "valid": {"start": (pd.Timestamp(sc["train_end"]) + pd.Timedelta(days=1)).date().isoformat(), "end": sc["valid_end"]},
               "test": {"start": (pd.Timestamp(sc["valid_end"]) + pd.Timedelta(days=1)).date().isoformat(), "end": last}}
    metric = fitted["metrics"].get("1", {})
    meta = {"format_version": 2, "model_version": model_version, "created_at": created.isoformat(),
            "target": "next_hd_log_return", "as_of_mode": mode, "periods": periods,
            "knowledge_cutoff": pd.Timestamp(fitted["knowledge_cutoff"]).tz_localize(KST).isoformat(),
            "min_eval_samples": cfg["influence"]["min_eval_samples"], "inputs": inputs, "audit": audit,
            "baseline": {"name": "ridge_price", "metric": "rmse", "value": metric.get("baseline_rmse"),
                         "model_value": metric.get("rmse"), "higher_is_better": False},
            "snapshots": fitted["snapshots"], "metrics": fitted["metrics"],
            "versions": {"scikit-learn": sklearn.__version__, "numpy": np.__version__, "pandas": pd.__version__},
            "settings": {"fusion": cfg["fusion"], "influence": cfg["influence"]},
            "evaluation_mode": "retrospective", "notice": NOTICE}
    events = evidence.rename(columns={"code": "ticker"}).copy()
    events["period"] = events["linked_as_of"].map(lambda t: "train" if t.date().isoformat() <= sc["train_end"] else
                                                "valid" if t.date().isoformat() <= sc["valid_end"] else "test")
    events["contribution"] = None  # 시점/창에 따른 계수 합을 최종 모델 원인으로 표현하지 않는다.
    result_store.write_model(model_version, meta, weights, events)
    return model_version, meta


def _shown(r: dict) -> str:
    """표시 방향: 표본 부족이면 숨김, 불확실성 구간이 0 을 포함하거나 없으면 불확실 (FR-N7)."""
    if not r["sample_ok"]:
        return "hidden"
    lo, hi = r["ci_low"], r["ci_high"]
    if r["direction"] not in ("+", "-") or lo is None or hi is None or lo <= 0 <= hi:
        return "uncertain"
    return r["direction"]


def _baseline_line(b: dict | None) -> dict | None:
    if not b:
        return None
    v, mv = _num(b.get("value")), _num(b.get("model_value"))
    better = None
    if v is not None and mv is not None:
        better = mv > v if b.get("higher_is_better", True) else mv < v
    return {"name": b.get("name"), "metric": b.get("metric"), "baseline": v, "model": mv, "improved": better}


def weights_view(store, as_of, h: int = 1) -> dict:
    errors: dict = {}
    t = _parse_as_of(as_of, None, errors)
    _raise_if(errors)
    m = pick_model(store, t, h)
    if m is None:
        return _no_model(t)
    try:
        w = store.read_weights(m["model_version"])
        if "h" in w:
            w = w[w["h"] == h]
    except (OSError, ValueError) as e:
        raise InfluenceError("data_unavailable", "Influence results could not be read.", {"error": str(e)}) from None
    rows = []
    for r in w.to_dict("records"):
        r = {k: _num(v) for k, v in r.items()}
        r["sample_ok"] = bool(r.get("sample_ok"))
        r["direction_shown"] = _shown(r)
        rows.append(r)
    rows.sort(key=lambda r: (not r["sample_ok"], -abs(r.get("weight") or 0.0), str(r.get("group_type")), str(r.get("group"))))
    metric = m.get("metrics", {}).get(str(h))
    baseline = ({"name": "ridge_price", "metric": "rmse", "value": metric.get("baseline_rmse"),
                 "model_value": metric.get("rmse"), "higher_is_better": False} if metric else m.get("baseline"))
    return {"status": "ok", "model": _model_info(m, t), "baseline": _baseline_line(baseline), "rows": rows,
            "as_of": t.isoformat(), "notice": NOTICE}


def _confirmed_days(frame: pd.DataFrame, t: datetime, close_at: time) -> list[pd.Timestamp]:
    """종가가 T 이전에 확정된 거래일 (워밍업 제외). 연결 봉 달력·차트 가격 모두 이 날까지만."""
    tk = t.astimezone(KST).replace(tzinfo=None)
    days = pd.DatetimeIndex(frame.loc[~frame["is_warmup"].astype(bool), "date"].unique()).sort_values()
    return [d for d in days if d + pd.Timedelta(hours=close_at.hour, minutes=close_at.minute) <= tk]


def link_day(available_at, days: list[pd.Timestamp], open_at: time):
    """bar.start > available_at 인 첫 거래일 (수집 계약 §4-5). 봉 시각은 시간대 없는 KST. 달력 안에 없으면 None."""
    a = to_kst_naive(available_at) - pd.Timedelta(hours=open_at.hour, minutes=open_at.minute)
    i = bisect.bisect_right(days, a)
    return days[i] if i < len(days) else None


def _visible_rows(docs: list[dict], t: datetime, mode: str) -> dict[tuple[str, int], dict]:
    """T 에 쓰는 버전: doc_id 마다 허용 버전 중 최신 1개 (근거 검색·보관함과 같은 select_as_of). 옛 버전 사건은 보이지 않는다."""
    return {(docs[i]["doc_id"], int(docs[i]["version"])): docs[i] for i in select_as_of(docs, t, mode)}


def _parse_days(start, end, errors: dict) -> tuple[date | None, date | None]:
    out = []
    for k, v in (("from", start), ("to", end)):
        d = None
        if v:
            try:
                d = date.fromisoformat(v) if len(v) == 10 else None
            except ValueError:
                d = None
            if d is None:
                errors[k] = "YYYY-MM-DD 날짜"
        out.append(d)
    if out[0] and out[1] and out[0] > out[1]:
        errors["to"] = "시작일 이후 날짜"
    return out[0], out[1]


def events_view(store, docs: list[dict], docs_store, frame: pd.DataFrame, cfg: dict, ticker: str, start, end,
                as_of, mode: str | None = None) -> dict:
    """한 종목·기간의 사건과 일봉 종가 (가격 차트 위 표시용).

    docs: T 시점 보이는 버전을 판정할 문서 행 (근거 검색 색인의 docs). docs_store: 수집 상태(coverage) 판정용.
    frame: 준비 프레임 (종목 상세와 같은 일봉). mode 를 주지 않으면 모델의 as_of_mode 를 쓴다.
    """
    errors: dict = {}
    t = _parse_as_of(as_of, mode, errors)
    ticker = (ticker or "").strip()
    if len(ticker) != _TICKER_LEN or not ticker.isalnum():
        errors["ticker"] = "종목코드는 6자리 영숫자입니다"
    d0, d1 = _parse_days(start, end, errors)
    _raise_if(errors)
    m = pick_model(store, t)
    if m is None:
        return {**_no_model(t), "ticker": ticker}
    mode = mode or m.get("as_of_mode") or "observed"
    if mode not in AS_OF_MODES:
        raise InfluenceError("data_unavailable", "Influence results could not be read.",
                             {"error": f"meta as_of_mode {mode!r}"})
    mcfg = cfg["minutes"]
    open_at, close_at = _hm(mcfg["session_open"]), _hm(mcfg["session_close"])
    days = _confirmed_days(frame, t, close_at)
    in_range = (lambda d: (d0 is None or d >= d0) and (d1 is None or d <= d1))

    f = frame[(frame["ticker"].astype(str) == ticker) & ~frame["is_warmup"].astype(bool)]
    px = f.set_index("date")["close"]
    if not any(d in px.index for d in days):  # T 까지 확정된 가격이 없는 종목 (T 뒤 상장 포함)
        raise InfluenceError("ticker_not_found", f"Security not in the loaded data at as_of: {ticker}", {"ticker": ticker})
    bars = [{"date": d.date().isoformat(), "close": float(px[d])} for d in days if d in px.index and in_range(d.date())]

    try:
        ev = store.read_events(m["model_version"], ticker)
    except (OSError, ValueError) as e:
        raise InfluenceError("data_unavailable", "Influence results could not be read.", {"error": str(e)}) from None
    visible = _visible_rows(docs, t, mode)
    events = []
    try:
        for r in ev.to_dict("records"):
            doc = visible.get((r["doc_id"], int(r["version"])))
            if doc is None:  # T 에 쓰는 버전이 아님 (아직 안 보이거나 더 새 버전이 있음, 또는 docs 에 없는 자료) — 개수도 알리지 않는다
                continue
            day = link_day(r["available_at"], days, open_at)
            # 연결 봉이 아직 없는 사건(마지막 확정 봉 뒤)은 자료를 쓸 수 있게 된 KST 날짜로 기간을 본다
            if not in_range(day.date() if day is not None else to_kst_naive(r["available_at"]).date()):
                continue
            start_at = day + pd.Timedelta(hours=open_at.hour, minutes=open_at.minute) if day is not None else None
            events.append({"doc_id": r["doc_id"], "version": int(r["version"]), "ticker": ticker,
                           "available_at": aware(r["available_at"]).astimezone(KST).isoformat(),
                           "linked_bar_start": start_at.tz_localize(KST).isoformat() if start_at is not None else None,
                           "group": r.get("group"), "contribution": _num(r.get("contribution")),
                           "period": r.get("period"),
                           "close": float(px[day]) if day is not None and day in px.index else None,
                           **{k: doc.get(k) for k in _EVENT_DOC_FIELDS}})
    except (KeyError, TypeError, ValueError) as e:  # 시간대 없는 available_at·빠진 열 — 계약과 다른 결과 파일
        raise InfluenceError("data_unavailable", "Influence results could not be read.",
                             {"error": f"events.parquet: {type(e).__name__}: {e}"}) from None
    events.sort(key=lambda e: (e["available_at"], e["doc_id"], e["version"]))

    try:
        cov = retrieval.coverage(docs_store, cfg, t, mode, d0.isoformat() if d0 else None,
                                 d1.isoformat() if d1 else None)
    except retrieval.RetrievalError as e:
        raise InfluenceError(e.code if e.code == "data_unavailable" else "validation_failed", e.message, e.detail) from None
    gaps = {src: {"gap_days": s["gap_days"], "partial_days": s["partial_days"],
                  "gap": s["counts"].get("gap", 0), "partial": s["counts"].get("partial", 0)}
            for src, s in cov["sources"].items() if s["counts"].get("gap", 0) or s["counts"].get("partial", 0)}
    status = "ok" if events else ("coverage_gap" if gaps else "no_events")
    return {"status": status, "model": _model_info(m, t), "ticker": ticker,
            "from": d0.isoformat() if d0 else None, "to": d1.isoformat() if d1 else None,
            "last_bar": days[-1].date().isoformat() if days else None,
            "as_of": t.isoformat(), "mode": mode, "events": events, "bars": bars, "coverage_gaps": gaps,
            "notice": NOTICE}
