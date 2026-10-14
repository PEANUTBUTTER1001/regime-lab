"""예측 평가 틀 (P2-5.3, 채점 지표 확정은 P2-8). 백테스트 승률과 섞지 않는다.

지표 (h 하나, 묶음별):
- n            : 예측이 있고 목표가 실현된 표본 수
- coverage     : 실제 목표가 [가장 낮은 분위, 가장 높은 분위] 안에 든 비율 (0.1~0.9 면 목표 80%)
- pinball      : 분위 손실 평균 (분위별 평균의 평균, 낮을수록 좋음)
- dir_share    : "상승 쪽"·"하락 쪽"으로 방향을 낸 표본 비율 (나머지는 "불확실")
- dir_hit      : 방향을 낸 표본 중 실제 부호가 맞은 비율
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import binomtest


def direction(p_up: pd.Series, cfg: dict) -> pd.Series:
    """상승 확률 → up / down / uncertain (통계적 방향 — 화면은 E17 고정 문구와 함께 표시)."""
    d = cfg["forecast"]["direction"]
    return pd.Series(np.select([p_up >= d["up"], p_up <= d["down"]], ["up", "down"], "uncertain"),
                     index=p_up.index).where(p_up.notna())


def score(targets: pd.DataFrame, pred: pd.DataFrame, cfg: dict, by: list[str]) -> pd.DataFrame:
    """targets(y + 묶음 열) 와 같은 index 의 pred(q… 열, p_up) → 묶음별 지표 표."""
    qs = cfg["forecast"]["quantiles"]
    qcols = [f"q{q:g}" for q in qs]
    ok = targets["y"].notna() & pred["p_up"].notna()
    t, p = targets[ok], pred[ok]
    y = t["y"]
    pin = sum(np.maximum(q * (y - p[c]), (q - 1) * (y - p[c])) for q, c in zip(qs, qcols)) / len(qs)
    d = direction(p["p_up"], cfg)
    called = d.isin(["up", "down"])
    hit = ((d == "up") & (y > 0)) | ((d == "down") & (y < 0))
    frame = t[by].assign(cov=((y >= p[qcols[0]]) & (y <= p[qcols[-1]])).astype(float), pin=pin,
                         called=called.astype(float), hit=hit.where(called).astype(float))
    out = frame.groupby(by, dropna=False, observed=True).agg(
        n=("cov", "size"), coverage=("cov", "mean"), pinball=("pin", "mean"),
        dir_share=("called", "mean"), dir_hit=("hit", "mean")).reset_index()
    return out


def direction_policy(y: pd.Series, model_p_up: pd.Series, baseline_p_up: pd.Series, cfg: dict) -> dict:
    """P2-8: 이 봉·h 에서 화면에 방향("상승 쪽"·"하락 쪽")을 보여도 되는지 판정 표본(y 와 같은 index)으로 정한다.

    조건 (모두): 모델이 방향을 낸 표본이 min_called 이상, 그 적중률이 0.5 보다 크다는 단측 이항검정 p < alpha,
    그리고 같은 표본에서 기준선(과거 실현 분위) 방향 적중률보다 높음. 아니면 화면은 "불확실".
    판정 표본은 호출자가 고른다 — 모델 보정에 쓴 표본·최종 평가 구간이 아니어야 한다(낙관 편향).
    """
    pcfg = cfg["forecast"]["direction_policy"]
    va = y.notna()
    y = y[va]

    def hits(p_up: pd.Series) -> tuple[int, int]:
        d = direction(p_up[va], cfg)
        called = d.isin(["up", "down"])
        ok = ((d == "up") & (y > 0)) | ((d == "down") & (y < 0))
        return int(called.sum()), int((ok & called).sum())

    n, k = hits(model_p_up)
    bn, bk = hits(baseline_p_up)
    p = float(binomtest(k, n, 0.5, alternative="greater").pvalue) if n else 1.0
    hit, base_hit = (k / n if n else None), (bk / bn if bn else None)
    # 기준선이 방향을 한 번도 안 내면(항상 "불확실") 동전과 같다고 보고 0.5 와 비교한다 (seongmin-claude #54)
    base_cmp = base_hit if base_hit is not None else 0.5
    enabled = (n >= pcfg["min_called"] and p < pcfg["alpha"] and hit > base_cmp)
    return {"enabled": bool(enabled), "valid_called": n, "valid_hit": hit, "p_value": p,
            "baseline_called": bn, "baseline_hit": base_hit, "min_called": pcfg["min_called"], "alpha": pcfg["alpha"]}


def _row_pinball(y: pd.Series, pred: pd.DataFrame, qs: list[float]) -> pd.Series:
    return sum(np.maximum(q * (y - pred[f"q{q:g}"]), (q - 1) * (y - pred[f"q{q:g}"])) for q in qs) / len(qs)


def compare_variants(targets: pd.DataFrame, price: pd.DataFrame, fused: pd.DataFrame, cfg: dict) -> dict:
    """P4-3 (FR-B2): 같은 표본에서 가격 단독(price) 과 외부 자료 결합(fused) 예측을 비교한다.

    targets(y·as_of) 와 두 예측은 같은 index. 둘 다 예측이 있고 목표가 실현된 표본의 교집합만 쓴다.
    판정 근거는 표본별 pinball 차이 d = fused − price 의 평균. 같은 거래일 표본은 서로 얽혀 있으므로
    거래일 단위로 다시 뽑는 부트스트랩으로 '평균 d ≥ 0' 의 한쪽 p 값을 낸다(설정 시드로 재현).
    구간 적중률은 목표(가장 높은 분위 − 가장 낮은 분위)에서 가격 단독보다 coverage_tol 넘게 멀어지면 안 되고,
    pinball 상대 개선(rel_gain)이 min_rel_gain 이상이어야 한다 — 표본이 수십만이면 아주 작은 차이도 유의해지므로.
    평균 d 의 95% 구간(부트스트랩 백분위)과 rel_gain 을 함께 돌려줘 화면이 크기를 보이게 한다.
    verdict 는 여기서 insufficient 만 정하고, improved/not_improved 는 여러 h 를 함께 보정한 뒤 정한다(holm).
    """
    c = cfg["forecast"]["fusion_compare"]
    qs = cfg["forecast"]["quantiles"]
    ok = targets["y"].notna() & price["p_up"].notna() & fused["p_up"].notna()
    t, a, b = targets[ok], price[ok], fused[ok]
    days = t["as_of"].dt.normalize()
    out = {"n": int(ok.sum()), "n_days": int(days.nunique())}
    if out["n"] < c["min_samples"] or out["n_days"] < c["min_days"]:
        return {**out, "verdict": "insufficient", "p_value": None}
    y = t["y"]
    pa, pb = _row_pinball(y, a, qs), _row_pinball(y, b, qs)
    lo, hi = f"q{qs[0]:g}", f"q{qs[-1]:g}"
    cov_a = float(((y >= a[lo]) & (y <= a[hi])).mean())
    cov_b = float(((y >= b[lo]) & (y <= b[hi])).mean())
    target = qs[-1] - qs[0]
    per_day = (pb - pa).groupby(days).agg(["sum", "size"])
    rng = np.random.default_rng(c["seed"])
    pick = rng.integers(0, len(per_day), size=(c["bootstrap"], len(per_day)))
    boot = per_day["sum"].to_numpy()[pick].sum(axis=1) / per_day["size"].to_numpy()[pick].sum(axis=1)
    p = float((np.sum(boot >= 0) + 1) / (c["bootstrap"] + 1))
    rel = float((pa.mean() - pb.mean()) / pa.mean()) if pa.mean() > 0 else 0.0
    return {**out, "verdict": None, "p_value": p, "pinball_price": float(pa.mean()), "pinball_fused": float(pb.mean()),
            "pinball_diff": float((pb - pa).mean()), "pinball_diff_ci95": [float(x) for x in np.percentile(boot, [2.5, 97.5])],
            "rel_gain": rel, "gain_ok": bool(rel >= c["min_rel_gain"]), "coverage_price": cov_a, "coverage_fused": cov_b,
            "coverage_ok": bool(abs(cov_b - target) <= abs(cov_a - target) + c["coverage_tol"])}


def holm_verdicts(results: dict[str, dict], alpha: float) -> dict[str, dict]:
    """여러 h 의 compare_variants 결과에 Holm 보정을 걸어 improved / not_improved 를 정한다 (insufficient 는 그대로).
    improved = Holm 통과 AND 상대 개선 ≥ min_rel_gain(gain_ok) AND 구간 적중률 유지(coverage_ok)."""
    tested = sorted(((r["p_value"], h) for h, r in results.items() if r["verdict"] is None))
    m, still = len(tested), True
    out = {h: dict(r) for h, r in results.items()}
    for i, (p, h) in enumerate(tested):
        still = still and p <= alpha / (m - i)
        out[h]["p_holm_pass"] = still
        out[h]["verdict"] = "improved" if still and out[h]["gain_ok"] and out[h]["coverage_ok"] else "not_improved"
    return out
