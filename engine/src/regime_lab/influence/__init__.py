"""P3-12 가격 통제 Ridge·시간 순서 가중 특징. 입력/출력 값만 사용하는 도메인."""
from __future__ import annotations

import hashlib

import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge

from regime_lab.forecast.features import FEATURES, make_features
from regime_lab.forecast.targets import make_targets
from regime_lab.fusion import feature_definitions, fingerprint


def _fit(x, y, alpha):
    a = x.to_numpy(float)
    # 전부 결측인 가격 특징은 0; train 외 값으로 imputation/표준화하지 않는다.
    med = x.median().fillna(0).to_numpy(float)
    a = np.where(np.isfinite(a), a, med)
    mean, scale = a.mean(axis=0), a.std(axis=0)
    scale[scale == 0] = 1
    fit = Ridge(alpha=alpha).fit((a - mean) / scale, y.to_numpy(float))
    coef = fit.coef_ / scale
    return {"features": list(x), "median": med.tolist(), "mean": mean.tolist(), "scale": scale.tolist(),
            "coef": coef.tolist(), "intercept": float(fit.intercept_ - mean @ coef), "alpha": float(alpha)}


def predict(model, x):
    a = x[model["features"]].to_numpy(float)
    a = np.where(np.isfinite(a), a, np.asarray(model["median"]))
    return a @ np.asarray(model["coef"]) + model["intercept"]


def _events(evidence, col, definition, rows, cutoff):
    if evidence.empty:
        return 0
    e = evidence[(evidence["group"] == definition["group"]) & (evidence["linked_as_of"] < cutoff)]
    # 해당 학습 표본에 실제로 관측된 종목/시간 범위의 고유 사건만.
    spans = {str(code): frame.sort_values("bar_ordinal") for code, frame in rows.groupby("code", sort=False)}
    keys = set()
    for r in e.to_dict("records"):
        frame = spans.get(r["code"])
        if frame is not None:
            pos = int(frame["bar_ordinal"].searchsorted(r["linked_ordinal"]))
            if pos < len(frame):
                row = frame.iloc[pos]
                if row["bar_ordinal"] < r["linked_ordinal"] + definition["window"] and row["as_of"] < r["until"]:
                    keys.add(r["doc_id"])
    return len(keys)


def _validate(cfg):
    ic = cfg["influence"]
    if (any(not isinstance(ic[k], int) or isinstance(ic[k], bool) or ic[k] <= 0
            for k in ("min_train_days", "fold_days", "min_events", "min_eval_samples", "bootstrap")) or
            not 0 < ic["alpha"] < 1 or not ic["alphas"] or
            any(not np.isfinite(a) or a <= 0 for a in ic["alphas"])):
        raise ValueError("influence 설정의 표본·창·반복 수/alpha 범위 오류")


def _snapshot(x, target, mask, evidence, defs, cfg, cutoff, h):
    ic = cfg["influence"]
    dates = target.loc[mask, "as_of"].dt.normalize().sort_values().unique()
    if len(dates) < ic["min_train_days"]:
        return None
    columns = [c for c in defs if defs[c]["group_type"] == "report_type"]
    support = {c: _events(evidence, c, defs[c], target.loc[mask], cutoff) for c in columns}
    columns = [c for c in columns if support[c] >= ic["min_events"]]
    if not columns:
        return None
    features = [*FEATURES, *columns]
    # alpha 탐색은 outer train 안의 시간 holdout만; 경계를 넘는 라벨은 버린다.
    alpha = ic["alphas"][len(ic["alphas"]) // 2]
    if len(dates) >= ic["min_train_days"] + ic["fold_days"]:
        split = pd.Timestamp(dates[-ic["fold_days"]])
        a = mask & (target["as_of"] < split) & (target["realized_at"] < split)
        b = mask & (target["as_of"] >= split)
        if a.any() and b.any():
            losses = []
            for candidate in ic["alphas"]:
                fm = _fit(x.loc[a, features], target.loc[a, "y"], candidate)
                losses.append((float(np.mean((predict(fm, x.loc[b]) - target.loc[b, "y"]) ** 2)), candidate))
            alpha = min(losses)[1]
    model = _fit(x.loc[mask, features], target.loc[mask, "y"], alpha)
    baseline = _fit(x.loc[mask, FEATURES], target.loc[mask, "y"], alpha)
    identity = hashlib.sha256(pd.util.hash_pandas_object(pd.concat([x.loc[mask, features], target.loc[mask, ["y", "realized_at"]]], axis=1), index=False).values.tobytes()).hexdigest()
    version = fingerprint({"fit": identity, "cfg": ic, "cutoff": str(cutoff), "h": h})[:20]
    return {"snapshot_version": version, "h": h, "knowledge_cutoff": cutoff.isoformat(),
            "first_train": str(target.loc[mask, "as_of"].min()), "train_rows": int(mask.sum()),
            "model": model, "baseline": baseline, "support": support}


def _confidence(snapshot, x, target, mask, cfg):
    """전처리/alpha 고정, 학습 거래일을 묶어 재표집. 평가 자료는 보지 않는다."""
    ic, m = cfg["influence"], snapshot["model"]
    xx = x.loc[mask, m["features"]].to_numpy(float)
    xx = np.where(np.isfinite(xx), xx, m["median"])
    xx = (xx - m["mean"]) / m["scale"]
    yy = target.loc[mask, "y"].to_numpy(float)
    days, indices = np.unique(target.loc[mask, "as_of"].dt.normalize(), return_inverse=True)
    rng = np.random.default_rng(ic["seed"])
    # 거래일별 충분통계로 같은 가중 Ridge를 계산. 1000회마다 전체 주가 행을 복제/재학습하지 않는다.
    stats = []
    for day in range(len(days)):
        a, y = xx[indices == day], yy[indices == day]
        stats.append((len(a), a.sum(axis=0), float(y.sum()), a.T @ a, a.T @ y))
    ns = np.array([s[0] for s in stats])
    sx = np.stack([s[1] for s in stats])
    sy = np.array([s[2] for s in stats])
    sxx = np.stack([s[3] for s in stats])
    sxy = np.stack([s[4] for s in stats])
    coefs = []
    for _ in range(ic["bootstrap"]):
        counts = np.bincount(rng.integers(0, len(days), len(days)), minlength=len(days))
        n, ax, ay = counts @ ns, counts @ sx, counts @ sy
        lhs = np.tensordot(counts, sxx, axes=1) - np.outer(ax, ax) / n + m["alpha"] * np.eye(xx.shape[1])
        rhs = counts @ sxy - ax * ay / n
        coefs.append(np.linalg.solve(lhs, rhs) / np.asarray(m["scale"]))
    if not coefs:
        raise ValueError("influence.bootstrap 는 양수")
    q = [ic["alpha"] / 2 * 100, (1 - ic["alpha"] / 2) * 100]
    return np.percentile(coefs, q, axis=0)


def train_influence(bars, docs_features, evidence, cfg):
    """outer train 내 expanding snapshot들과 고정 최종 snapshot. 반환에 sklearn 객체 없음."""
    _validate(cfg)
    f = make_features(bars)
    d = docs_features.set_index(["code", "as_of"]).reindex(pd.MultiIndex.from_frame(f[["code", "as_of"]])).reset_index()
    defs = feature_definitions(cfg)
    x = pd.concat([f[FEATURES], d[list(defs)]], axis=1)
    cutoff = pd.Timestamp(cfg["forecast"]["split"]["train_end"]) + pd.Timedelta(days=1)
    valid_end = pd.Timestamp(cfg["forecast"]["split"]["valid_end"]) + pd.Timedelta(days=1)
    ic = cfg["influence"]
    dates = np.sort(f.loc[f["as_of"] < cutoff, "as_of"].dt.normalize().unique())
    snapshots, weights, metrics = [], [], {}
    horizon = cfg["forecast"]["horizon"]["1d"][0]
    for h in range(1, horizon + 1):
        t = make_targets(bars, h)
        t["bar_ordinal"] = bars.groupby("code", sort=False).cumcount()
        eligible = t["y"].notna() & ~d["docs_missing"].fillna(True).astype(bool)
        for i in range(ic["min_train_days"], len(dates), ic["fold_days"]):
            start = pd.Timestamp(dates[i])
            stop = min(pd.Timestamp(dates[min(i + ic["fold_days"], len(dates) - 1)]) if i + ic["fold_days"] < len(dates) else cutoff, cutoff)
            mask = eligible & (t["as_of"] < start) & (t["realized_at"] < start)
            snap = _snapshot(x, t, mask, evidence, defs, cfg, start, h)
            if snap:
                snapshots.append({**snap, "apply_from": start.isoformat(), "apply_until": stop.isoformat(), "final": False})
        tr = eligible & (t["as_of"] < cutoff) & (t["realized_at"] < cutoff)
        snap = _snapshot(x, t, tr, evidence, defs, cfg, cutoff, h)
        if snap is None:
            metrics[str(h)] = {"status": "insufficient"}
            continue
        snapshots.append({**snap, "apply_from": cutoff.isoformat(), "apply_until": None, "final": True})
        te = eligible & (t["as_of"] >= valid_end)
        test = t.loc[te]
        pred = predict(snap["model"], x.loc[te]) if te.any() else np.array([])
        base = predict(snap["baseline"], x.loc[te]) if te.any() else np.array([])
        metrics[str(h)] = {"status": "ok" if te.any() else "insufficient", "n": int(te.sum()),
                           "rmse": float(np.sqrt(np.mean((pred - test["y"]) ** 2))) if te.any() else None,
                           "mae": float(np.mean(np.abs(pred - test["y"]))) if te.any() else None,
                           "baseline_rmse": float(np.sqrt(np.mean((base - test["y"]) ** 2))) if te.any() else None,
                           "baseline_mae": float(np.mean(np.abs(base - test["y"]))) if te.any() else None}
        lo, hi = _confidence(snap, x, t, tr, cfg)
        for j, col in enumerate(snap["model"]["features"]):
            if col not in defs:
                continue
            info = defs[col]
            # n_eval/CI는 표시/평가만; weighted_features가 이 필드로 입력 계수를 고르지 않는다.
            ne = _events(evidence[evidence["linked_as_of"] >= valid_end], col, info, test, pd.Timestamp.max)
            beta = snap["model"]["coef"][j]
            weights.append({**info, "feature": col, "h": h, "weight": beta,
                            "direction": "+" if lo[j] > 0 else "-" if hi[j] < 0 else "0",
                            "n_events": snap["support"][col], "n_eval": ne,
                            "eval_metric": metrics[str(h)]["rmse"], "ci_low": float(lo[j]), "ci_high": float(hi[j]),
                            "sample_ok": bool(ne >= ic["min_eval_samples"]), "ci_basis": "train_day_bootstrap"})
    if not any(s["final"] for s in snapshots):
        raise ValueError("영향력 학습 표본 부족: 수집 범위·학습 거래일·고유 사건 수 확인 필요")
    return {"snapshots": snapshots, "metrics": metrics, "knowledge_cutoff": cutoff.isoformat()}, pd.DataFrame(weights)


def weighted_features(docs_features, fitted, cfg):
    out = docs_features.copy()
    used = {}
    horizon = cfg["forecast"]["horizon"]["1d"][0]
    for h in range(1, horizon + 1):
        for col, definition in feature_definitions(cfg).items():
            if definition["group_type"] == "report_type":
                out[f"f_influence_h{h}_{col[2:]}"] = np.nan
        missing = pd.Series(True, index=out.index)
        for snap in fitted["snapshots"]:
            if snap["h"] != h:
                continue
            start = pd.Timestamp(snap["apply_from"])
            cutoff = pd.Timestamp(snap["knowledge_cutoff"])
            if cutoff > start:
                raise ValueError("가중치 knowledge_cutoff 가 적용 시각 이후")
            mask = (out["as_of"] >= start) & ~out["docs_missing"]
            if snap["apply_until"]:
                mask &= out["as_of"] < pd.Timestamp(snap["apply_until"])
            if not mask.any():
                continue
            for col, beta in zip(snap["model"]["features"], snap["model"]["coef"]):
                if col.startswith("f_"):
                    name = f"f_influence_h{h}_{col[2:]}"
                    if name not in out:
                        raise ValueError("가중치 특징과 설정이 다름")
                    out.loc[mask, name] = out.loc[mask, col] * beta
            missing.loc[mask] = False
            used[snap["snapshot_version"]] = {"h": h, "from": snap["apply_from"], "until": snap["apply_until"],
                                               "knowledge_cutoff": snap["knowledge_cutoff"]}
        out[f"f_influence_missing_h{h}"] = missing.astype(float)
    return out, used
