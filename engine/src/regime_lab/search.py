"""역방향 탐색 (P1, D-1): 목표 성과를 만족한 전략 조합을 후보 곱집합에서 찾는다.

절차 (docs/역방향_백테스트_설계.md §3.4)
1. 후보 생성: 패턴 조합 × 결합 × 손절 × 익절 × 보유일. 순번은 요청이 같으면 항상 같다.
2. 탐색 구간(backtest_start ~ split_date) 전 후보: 요약 지표 + 단측 p-value. 무작위 벤치마크는 하지 않는다.
3. 선별: 목표 조건 충족 + 거래 수 ≥ min_cell_trades + BH-FDR 통과(m = 전체 후보 수). sort_by 내림차순 순위.
4. 평가 구간(split_date 다음 날 ~ 기준일): 상위 finalists 개를 execute() 로 3중 검증(FDR m = 최종 후보 수).
   held = 평가 구간에서도 목표 조건 충족 + 거래 수 ≥ min_cell_trades + FDR 통과.

각 후보는 기존 Strategy 그대로라 POST /runs 에 같은 전략·기간을 넣으면 같은 지표가 나온다 (정방향 일치).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from regime_lab.analysis.validation import bh_reject, one_sided_pvalue
from regime_lab.backtest import build_trades, simulate_trades, summarize
from regime_lab.context import NULL_CONTEXT, RunContext
from regime_lab.patterns import CORE_PATTERNS
from regime_lab.patterns.base import combine_signals
from regime_lab.patterns.core import get_pattern
from regime_lab.pipeline import Prepared
from regime_lab.runs import Strategy, StrategyError, entry_mask, execute

SEARCH_KEYS = {"name", "axes", "filters", "target", "sort_by"}
AXIS_KEYS = ("patterns", "combine", "stop_loss_pct", "take_profit_pct", "max_hold_days")
FILTER_KEYS = {"markets", "min_avg_value_krw", "cap_groups"}
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
METRIC_KEYS = ("trades", "win_rate", "mean_ret", "median_ret", "mean_excess", "payoff_ratio", "sharpe", "mdd")


class SearchError(StrategyError):
    """탐색 요청 검증 실패. errors = {필드 경로: 사유}. code 는 API 오류 코드."""

    def __init__(self, errors: dict[str, str], code: str = "validation_failed", detail: dict | None = None):
        super().__init__(errors)
        self.code, self.detail = code, detail or {}


@dataclass
class SearchRequest:
    name: str
    axes: dict
    filters: dict
    target: dict[str, float]
    sort_by: str

    @classmethod
    def from_dict(cls, d: dict, cfg: dict) -> "SearchRequest":
        scfg = cfg["search"]
        if not isinstance(d, dict):
            raise SearchError({"": "A search request must be an object"})
        errors: dict[str, str] = {}
        if set(d) - SEARCH_KEYS:
            errors["_"] = f"Keys not allowed: {sorted(set(d) - SEARCH_KEYS)}"
        name = d.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            errors["name"] = "Use 1-64 letters, digits, _ or -"

        raw_axes = d.get("axes") or {}
        axes: dict = {}
        if not isinstance(raw_axes, dict):
            errors["axes"] = "Must be an object"
            raw_axes = {}
        for k in set(raw_axes) - set(AXIS_KEYS):
            errors[f"axes.{k}"] = "Key not allowed"
        allowed = {"patterns": list(CORE_PATTERNS), **scfg["axes"]}
        defaults = {"patterns": list(CORE_PATTERNS), **scfg["defaults"]}
        for k in AXIS_KEYS:
            v = raw_axes.get(k, defaults[k])
            if not isinstance(v, list) or not v:
                errors[f"axes.{k}"] = "A non-empty list"
            elif any(isinstance(x, bool) for x in v) or not all(x in allowed[k] for x in v):
                errors[f"axes.{k}"] = f"Use values from {allowed[k]}"
            elif len(set(v)) != len(v):
                errors[f"axes.{k}"] = "Duplicate values"
            else:
                axes[k] = [x for x in allowed[k] if x in v]  # 허용 목록 순서로 정렬 → 후보 순번 고정

        filters = d.get("filters") or {}
        if not isinstance(filters, dict):
            errors["filters"] = "Must be an object"
            filters = {}
        for k in set(filters) - FILTER_KEYS:
            errors[f"filters.{k}"] = "Key not allowed"

        target = d.get("target")
        metrics = scfg["target_metrics"]
        if not isinstance(target, dict) or not target:
            errors["target"] = f"At least one of {metrics}"
            target = {}
        for k, v in target.items():
            if k not in metrics:
                errors[f"target.{k}"] = f"Use one of {metrics}"
            elif isinstance(v, bool) or not isinstance(v, (int, float)) or not np.isfinite(v):
                errors[f"target.{k}"] = "A number (lower bound)"
        sort_by = d.get("sort_by", next(iter(target), None))
        if target and sort_by not in target:
            errors["sort_by"] = "Must be one of the target metrics"

        if errors:
            raise SearchError(errors)
        req = cls(name, axes, dict(filters), {k: float(v) for k, v in target.items()}, sort_by)
        try:  # 필터 값 검증은 기존 전략 규칙 그대로
            Strategy.from_dict({"name": name, "patterns": axes["patterns"][:1], **req.filters}).validate(cfg)
        except StrategyError as e:
            raise SearchError({f"filters.{k}": v for k, v in e.errors.items()}) from None
        n, cap = count_candidates(req), int(scfg["max_candidates"])
        if n > cap:
            raise SearchError({"axes": f"{n} candidates exceed the limit of {cap}"}, "too_many_candidates",
                              {"candidates": n, "max_candidates": cap})
        return req

    def to_dict(self) -> dict:
        return {"name": self.name, "axes": self.axes, "filters": self.filters, "target": self.target,
                "sort_by": self.sort_by}


# ---------------------------------------------------------------- 후보 생성 (P1-5.1)
def pattern_sets(patterns: list[str], combines: list[str]) -> list[tuple[tuple[str, ...], str]]:
    """공집합 아닌 부분집합 × 결합. 패턴 1개짜리는 결합과 무관하므로 or 하나만 둔다.

    순서: 패턴 수 → 패턴 이름 사전순 → 결합(입력 순서).
    """
    names = sorted(patterns)
    out = []
    for r in range(1, len(names) + 1):
        for combo in combinations(names, r):
            out += [(combo, "or")] if r == 1 else [(combo, c) for c in combines]
    return out


def count_candidates(req: SearchRequest) -> int:
    a = req.axes
    return (len(pattern_sets(a["patterns"], a["combine"]))
            * len(a["stop_loss_pct"]) * len(a["take_profit_pct"]) * len(a["max_hold_days"]))


def generate_candidates(req: SearchRequest, period: dict | None = None) -> list[Strategy]:
    a = req.axes
    out = []
    for combo, how in pattern_sets(a["patterns"], a["combine"]):
        for sl in a["stop_loss_pct"]:
            for tp in a["take_profit_pct"]:
                for hold in a["max_hold_days"]:
                    out.append(Strategy.from_dict({
                        "name": f"c{len(out) + 1:03d}", "patterns": list(combo), "combine": how,
                        "exit": {"stop_loss_pct": sl, "take_profit_pct": tp, "max_hold_days": hold},
                        **({"period": period} if period else {}), **req.filters,
                    }))
    return out


# ---------------------------------------------------------------- 구간 분할 (P1-5.2)
def split_periods(cfg: dict) -> tuple[dict, dict]:
    """탐색 구간 [backtest_start, split_date], 평가 구간 [split_date 다음 날, as_of_date] (신호일 기준)."""
    d = cfg["data"]
    split = pd.Timestamp(cfg["analysis"]["split_date"])
    explore = {"start": d["backtest_start"], "end": split.date().isoformat()}
    evaluate = {"start": (split + pd.Timedelta(days=1)).date().isoformat(), "end": d["as_of_date"]}
    return explore, evaluate


def with_period(s: Strategy, period: dict) -> Strategy:
    return Strategy.from_dict({**s.to_dict(), "period": period})


# ---------------------------------------------------------------- 후보 평가 (P1-5.3)
def target_met(metrics: dict, target: dict[str, float]) -> bool:
    """모든 목표 지표가 하한 이상. 값이 없으면(NaN) 미충족."""
    for k, lo in target.items():
        v = metrics.get(k)
        if v is None or not np.isfinite(v) or v < lo:
            return False
    return True


def evaluate_candidates(cands: list[Strategy], prep: Prepared, cfg: dict,
                        ctx: RunContext = NULL_CONTEXT) -> pd.DataFrame:
    """후보별 요약 지표와 단측 p-value (무작위 벤치마크 없음). 패턴 신호는 패턴마다 한 번만 계산한다."""
    signals: dict[str, pd.Series] = {}
    rows = []
    for i, s in enumerate(cands):
        ctx.progress(i, len(cands))
        ctx.check_cancel()
        s.validate(cfg)
        for p in s.patterns:
            if p not in signals:
                signals[p] = get_pattern(p, cfg).signal(prep.frame)
        sig = combine_signals([signals[p] for p in s.patterns], s.combine)
        f, tr_rows, skip_rows = simulate_trades(prep.frame, sig, cfg, prep.delisted, s.exit_cfg(cfg),
                                                entry_mask(prep.frame, s))
        trades, skipped = build_trades(f, tr_rows, skip_rows, prep.index, cfg, prep.sectors)
        summ = summarize(trades, skipped, prep.frame)
        inc = trades[~trades["excluded"]] if len(trades) else trades
        excess = inc["excess_ret"].dropna().to_numpy(float) if len(inc) else np.array([])
        rows.append({"id": s.name, **{k: summ[k] for k in METRIC_KEYS}, "p_value": one_sided_pvalue(excess)})
    ctx.progress(len(cands), len(cands))
    return pd.DataFrame(rows, columns=["id", *METRIC_KEYS, "p_value"])



def select(scores: pd.DataFrame, req: SearchRequest, cfg: dict) -> pd.DataFrame:
    """목표 충족·표본·FDR(m = 전체 후보 수) 판정과 통과 후보 순위 (1부터, 동률은 순번 순)."""
    out = scores.copy()
    min_n = int(cfg["analysis"]["min_cell_trades"])
    out["target_met"] = [target_met(r, req.target) for r in out.to_dict(orient="records")]
    out["enough_trades"] = out["trades"] >= min_n
    out["fdr_pass"] = bh_reject(out["p_value"].to_numpy(float), float(cfg["analysis"]["fdr_q"]))
    passed = out["target_met"] & out["enough_trades"] & out["fdr_pass"]
    order = out[passed].sort_values([req.sort_by, "id"], ascending=[False, True], kind="stable")
    out["rank"] = pd.Series(np.arange(1, len(order) + 1), index=order.index).reindex(out.index).astype("Int64")
    return out


def run_search(req: SearchRequest, prep: Prepared, cfg: dict, ctx: RunContext = NULL_CONTEXT) -> dict:
    """탐색 → 선별 → 평가. 저장하지 않는다."""
    explore_p, evaluate_p = split_periods(cfg)
    cands = generate_candidates(req, explore_p)
    by_id = {s.name: s for s in cands}

    explore = select(evaluate_candidates(cands, prep, cfg, ctx), req, cfg)
    ctx.check_cancel()

    top = explore.dropna(subset=["rank"]).sort_values("rank").head(int(cfg["search"]["finalists"]))
    finalists = pd.DataFrame(columns=["id", *METRIC_KEYS, "p_value", "fdr_pass", "random_percentile",
                                      "random_pass", "target_met", "held"])
    if len(top):
        res = execute([with_period(by_id[i], evaluate_p) for i in top["id"]], prep, cfg, ctx)
        val = res["validation"].set_index("strategy")
        min_n = int(cfg["analysis"]["min_cell_trades"])
        rows = []
        for i in top["id"]:
            m = res["results"][i]["summary"]
            v = val.loc[i]
            met = target_met(m, req.target)
            rows.append({"id": i, **{k: m[k] for k in METRIC_KEYS}, "p_value": v["p_value"],
                         "fdr_pass": bool(v["fdr_pass"]), "random_percentile": v["random_percentile"],
                         "random_pass": bool(v["random_pass"]), "target_met": met,
                         "held": bool(met and m["trades"] >= min_n and v["fdr_pass"])})
        finalists = pd.DataFrame(rows)
    return {"request": req, "split": {"explore": explore_p, "evaluate": evaluate_p},
            "candidates": cands, "explore": explore, "finalists": finalists}
