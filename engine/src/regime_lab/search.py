"""역방향 탐색 (P1, D-1): 목표 승률 N% 이상을 과거에 충족한 조건 조합을 후보 곱집합에서 찾는다.

절차 (docs/역방향_백테스트_설계.md §3.4, docs/plan/01-presets-and-reverse-backtest.md 작업 ②)
1. 후보 생성: 패턴 조합 × 결합 × 손절 × 익절 × 보유일. 순번은 요청이 같으면 항상 같다.
2. 탐색 구간(기간 시작 ~ split_date) 전 후보: 기존 엔진 규칙 그대로 요약 지표 + 단측 p-value.
   데이터를 split_date 까지 잘라 계산한다(as_of = split_date 백테스트). 그래서 평가 구간 데이터가 바뀌어도
   탐색 결과·후보 선택이 바뀌지 않는다. 분할일에 보유 중인 거래는 기존 규칙대로 end_of_data(집계 제외)다.
3. 탐색 구간에서 목표 승률과 최소 거래 수를 충족한 후보만 평가 구간(split_date 다음 날 ~ 기간 끝)에서 다시 평가한다.
   평가 구간 결과는 후보 선택에 쓰지 않는다.
4. FDR 가족 크기는 두 구간 모두 **시도한 전체 후보 수**다. 평가하지 않은 후보는 p = 1 로 두어 m 에만 포함한다.
5. 상태: both(탐색·평가 모두 충족) / explore_only(탐색만 충족) / insufficient_trades(탐색 거래 수 부족) / not_met(미충족).
   충족 후보가 없으면 가장 가까운 후보와 부족분을 돌려준다.

각 후보는 기존 Strategy 그대로다. 탐색 구간 값은 같은 전략·기간을 split_date 까지의 데이터로 정방향 실행한 값,
평가 구간 값은 같은 전략을 평가 기간으로 정방향 실행한 값과 같다 (정방향 일치, P1-11.1).
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from itertools import combinations
from pathlib import Path

import numpy as np
import pandas as pd

from regime_lab.analysis.report import to_jsonable
from regime_lab.analysis.validation import bh_reject, one_sided_pvalue
from regime_lab.backtest import METRIC_DEFS, build_trades, simulate_trades, summarize
from regime_lab.config import Paths, config_hash
from regime_lab.context import NULL_CONTEXT, RunCancelled, RunContext
from regime_lab.patterns import CORE_PATTERNS
from regime_lab.patterns.base import combine_signals
from regime_lab.patterns.core import get_pattern
from regime_lab.data.loader import input_file_hashes
from regime_lab.pipeline import Prepared, prep_hash
from regime_lab.runs import DISCLAIMER, ENGINE_VERSION, Strategy, StrategyError, entry_mask, execute

SEARCH_KEYS = {"name", "target_win_rate", "min_trades", "axes", "filters"}
AXIS_KEYS = ("patterns", "combine", "stop_loss_pct", "take_profit_pct", "max_hold_days")
FILTER_KEYS = {"markets", "period", "min_avg_value_krw", "cap_groups"}
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
METRIC_KEYS = ("trades", "win_rate", "mean_ret", "median_ret", "mean_excess", "payoff_ratio", "sharpe", "mdd")
# 표시 순서. not_evaluated 는 탐색 충족 후보가 평가 전에 취소된 경우에만 나온다.
STATUSES = ("both", "explore_only", "not_evaluated", "not_met", "insufficient_trades")
NOTE = "목표 조건을 과거 구간에서 충족한 조합입니다. 미래 성과를 보장하지 않습니다."
SORT_RULE = ("status (both → explore_only → not_evaluated → not_met → insufficient_trades), "
             "then explore win_rate desc, then id")


class SearchError(StrategyError):
    """탐색 요청 검증 실패. errors = {필드 경로: 사유}. code 는 API 오류 코드."""

    def __init__(self, errors: dict[str, str], code: str = "validation_failed", detail: dict | None = None):
        super().__init__(errors)
        self.code, self.detail = code, detail or {}


@dataclass
class SearchRequest:
    name: str
    target_win_rate: float
    min_trades: int
    axes: dict
    filters: dict

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

        wr = d.get("target_win_rate")
        if isinstance(wr, bool) or not isinstance(wr, (int, float)) or not 0 <= wr <= 1:
            errors["target_win_rate"] = "A ratio from 0 to 1 (e.g. 0.55 = 55%)"
        mt = d.get("min_trades", scfg["min_trades_default"])
        lo, hi = scfg["min_trades_limits"]
        if isinstance(mt, bool) or not isinstance(mt, int) or not lo <= mt <= hi:
            errors["min_trades"] = f"Whole number from {lo} to {hi}"

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

        if errors:
            raise SearchError(errors)
        req = cls(name, float(wr), mt, axes, dict(filters))
        try:  # 필터 값 검증은 기존 전략 규칙 그대로
            Strategy.from_dict({"name": name, "patterns": axes["patterns"][:1], **req.filters}).validate(cfg)
        except StrategyError as e:
            raise SearchError({f"filters.{k}": v for k, v in e.errors.items()}) from None
        split = cfg["analysis"]["split_date"]
        p = req.filters.get("period")
        if p and not pd.Timestamp(p["start"]) <= pd.Timestamp(split) < pd.Timestamp(p["end"]):
            raise SearchError({"filters.period": f"The period must include the split date {split} "
                                                 "(search before it, evaluate after it)"})
        n, cap = count_candidates(req), int(scfg["max_candidates"])
        if n > cap:
            raise SearchError({"axes": f"{n} candidates exceed the limit of {cap}"}, "too_many_candidates",
                              {"candidates": n, "max_candidates": cap})
        return req

    def to_dict(self) -> dict:
        return {"name": self.name, "target_win_rate": self.target_win_rate, "min_trades": self.min_trades,
                "axes": self.axes, "filters": self.filters}


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


def estimate_seconds(n_candidates: int, cfg: dict) -> float:
    """실행 전 예상 시간 (탐색 구간 전 후보 기준, NFR-14). 평가 구간 재평가 수는 실행 전에 알 수 없다."""
    return round(n_candidates * float(cfg["search"]["sec_per_candidate"]), 1)


def generate_candidates(req: SearchRequest, period: dict | None = None) -> list[Strategy]:
    a = req.axes
    filters = {k: v for k, v in req.filters.items() if k != "period"}
    out = []
    for combo, how in pattern_sets(a["patterns"], a["combine"]):
        for sl in a["stop_loss_pct"]:
            for tp in a["take_profit_pct"]:
                for hold in a["max_hold_days"]:
                    out.append(Strategy.from_dict({
                        "name": f"c{len(out) + 1:03d}", "patterns": list(combo), "combine": how,
                        "exit": {"stop_loss_pct": sl, "take_profit_pct": tp, "max_hold_days": hold},
                        **({"period": period} if period else {}), **filters,
                    }))
    return out


# ---------------------------------------------------------------- 구간 분할 (P1-5.2)
def split_periods(cfg: dict, period: dict | None = None) -> tuple[dict, dict]:
    """탐색 구간 [start, split_date], 평가 구간 [split_date 다음 날, end] (신호일 기준).

    period 가 없으면 [backtest_start, as_of_date]. 분할일은 기존 기간 분할(FR-A3)의 split_date 와 같다.
    """
    d = cfg["data"]
    start, end = (period["start"], period["end"]) if period else (d["backtest_start"], d["as_of_date"])
    split = pd.Timestamp(cfg["analysis"]["split_date"])
    explore = {"start": start, "end": split.date().isoformat()}
    evaluate = {"start": (split + pd.Timedelta(days=1)).date().isoformat(), "end": end}
    return explore, evaluate


def truncate(prep: Prepared, end: str) -> Prepared:
    """end 일까지의 데이터만 남긴 준비 프레임 (as_of = end).

    지표·유니버스·그룹·국면은 t일까지의 값만 쓰므로(test_lookahead 절단 불변) 앞부분 값은 그대로다.
    상장폐지 종목은 마지막 거래일이 end 이하인 종목만 남긴다. 그 밖의 종목은 end 에서 보유 중(end_of_data)이다.
    """
    e = pd.Timestamp(end)
    f = prep.frame
    last = f.groupby(f["ticker"].astype(str), observed=True)["date"].max()
    delisted = {t for t in prep.delisted if t in last.index and last[t] <= e}
    return Prepared(f[f["date"] <= e].reset_index(drop=True), prep.index[prep.index["date"] <= e],
                    delisted, prep.sectors, prep.names)


def with_period(s: Strategy, period: dict) -> Strategy:
    return Strategy.from_dict({**s.to_dict(), "period": period})


# ---------------------------------------------------------------- 후보 평가 (P1-5.3)
def score_candidate(s: Strategy, prep: Prepared, cfg: dict, signals: dict[str, pd.Series]) -> dict:
    """후보 1개의 요약 지표와 단측 p-value (무작위 벤치마크 없음). signals 는 패턴별 신호 캐시(호출자가 공유)."""
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
    return {"id": s.name, **{k: summ[k] for k in METRIC_KEYS}, "p_value": one_sided_pvalue(excess)}


def evaluate_candidates(cands: list[Strategy], prep: Prepared, cfg: dict) -> pd.DataFrame:
    """후보 전체를 한 번에 평가한다. 패턴 신호는 패턴마다 한 번만 계산한다."""
    signals: dict[str, pd.Series] = {}
    return pd.DataFrame([score_candidate(s, prep, cfg, signals) for s in cands],
                        columns=["id", *METRIC_KEYS, "p_value"])


def evaluate_in_period(s: Strategy, period: dict, prep: Prepared, cfg: dict, req: "SearchRequest") -> dict:
    """평가 구간 재평가: 정방향 실행 그대로(무작위 벤치마크 포함). FDR 은 호출자가 m = 전체 후보로 다시 계산한다."""
    res = execute(with_period(s, period), prep, cfg)
    m, v = res["results"][s.name]["summary"], res["validation"].iloc[0]
    return {"id": s.name, **{k: m[k] for k in METRIC_KEYS}, "p_value": v["p_value"],
            "random_percentile": v["random_percentile"], "random_pass": bool(v["random_pass"]),
            "met": meets(m["win_rate"], m["trades"], req)}


def meets(win_rate: float, trades: int, req: SearchRequest) -> bool:
    """목표 판정: 기존 win_rate 정의 그대로 N% 이상 + 최소 거래 수 이상. 승률이 없으면(NaN) 미충족."""
    return bool(np.isfinite(win_rate) and win_rate >= req.target_win_rate and trades >= req.min_trades)


def fdr_family(p_values: pd.Series, ids: pd.Series, q: float) -> pd.Series:
    """BH-FDR, 가족 = ids 전체(시도한 후보 수). p_values 에 없는 후보는 p = 1 로 m 에만 들어간다."""
    p = p_values.reindex(ids).fillna(1.0).to_numpy(float)
    return pd.Series(bh_reject(p, q), index=ids.to_numpy())


def closest_candidate(explore: pd.DataFrame, req: SearchRequest) -> dict | None:
    """충족 후보가 없을 때 가장 가까운 후보: 거래 수 충족 우선 → 승률 부족분 작은 순 → 거래 수 많은 순 → 순번."""
    if explore.empty:
        return None
    wr = explore["win_rate"].astype(float)
    gap = pd.DataFrame({
        "id": explore["id"],
        "win_rate_short": np.maximum(req.target_win_rate - wr.fillna(-np.inf), 0.0).clip(upper=1.0),
        "trades_short": np.maximum(req.min_trades - explore["trades"].astype(int), 0),
        "trades": explore["trades"].astype(int),
    })
    best = gap.sort_values(["trades_short", "win_rate_short", "trades", "id"],
                           ascending=[True, True, False, True], kind="stable").iloc[0]
    return {"id": best["id"], "win_rate_short": float(best["win_rate_short"]),
            "trades_short": int(best["trades_short"])}


def run_search(req: SearchRequest, prep: Prepared, cfg: dict, ctx: RunContext = NULL_CONTEXT) -> dict:
    """탐색 → 평가 → 상태 분류. 저장하지 않는다.

    ctx 단계는 SEARCH_STAGES(generate·explore·evaluate). 진행은 처리 후보 수 / 전체, report(met_so_far).
    취소되면 그때까지 처리한 후보만 담아 status = "cancelled" 로 돌려준다(예외를 올리지 않는다).
    FDR 가족 크기는 취소와 관계없이 시도하려던 전체 후보 수다.
    table: 처리한 후보마다 한 행. explore_* / evaluate_* 지표, fdr_pass 두 개, 무작위 벤치마크, status, order.
    """
    q = float(cfg["analysis"]["fdr_q"])
    explore_p, evaluate_p = split_periods(cfg, req.filters.get("period"))
    status = "completed"
    ex_rows: list[dict] = []
    ev_rows: list[dict] = []
    cands = generate_candidates(req, explore_p)  # 취소와 관계없이 FDR 가족(시도하려던 전체 후보)을 정한다
    by_id = {s.name: s for s in cands}
    try:
        ctx.stage("generate", f"{len(cands)} candidates")

        ctx.stage("explore", f"{explore_p['start']}~{explore_p['end']}")
        cut = truncate(prep, explore_p["end"])
        signals: dict[str, pd.Series] = {}
        met = 0
        for i, s in enumerate(cands):
            ctx.progress(i, len(cands))
            ctx.check_cancel()
            row = score_candidate(s, cut, cfg, signals)
            ex_rows.append(row)
            met += meets(row["win_rate"], row["trades"], req)
            ctx.report(met_so_far=met)
        ctx.progress(len(cands), len(cands))

        qualified = [r["id"] for r in ex_rows if meets(r["win_rate"], r["trades"], req)]
        ctx.stage("evaluate", f"{evaluate_p['start']}~{evaluate_p['end']}")
        for i, cid in enumerate(qualified):
            ctx.progress(i, len(qualified))
            ctx.check_cancel()
            ev_rows.append(evaluate_in_period(by_id[cid], evaluate_p, prep, cfg, req))
        ctx.progress(len(qualified), len(qualified))
    except RunCancelled:
        status = "cancelled"

    ids = pd.Series([s.name for s in cands])
    ex = pd.DataFrame(ex_rows, columns=["id", *METRIC_KEYS, "p_value"])
    ex["enough_trades"] = ex["trades"] >= req.min_trades
    ex["met"] = [meets(w, t, req) for w, t in zip(ex["win_rate"], ex["trades"])]
    ex["fdr_pass"] = fdr_family(ex.set_index("id")["p_value"], ids, q).reindex(ex["id"]).to_numpy(bool)
    ev = pd.DataFrame(ev_rows, columns=["id", *METRIC_KEYS, "p_value", "random_percentile", "random_pass", "met"])
    ev["fdr_pass"] = fdr_family(ev.set_index("id")["p_value"], ids, q).reindex(ev["id"]).to_numpy(bool)

    table = ex.add_prefix("explore_").rename(columns={"explore_id": "id"}).merge(
        ev.add_prefix("evaluate_").rename(columns={"evaluate_id": "id"}), on="id", how="left")
    evaluated = table["evaluate_trades"].notna().to_numpy(bool)
    ev_met = table["evaluate_met"].astype("boolean").fillna(False).to_numpy(bool)
    ex_met = table["explore_met"].astype(bool).to_numpy()  # 처리한 후보가 0개면 object 형이라 bool 로 맞춘다
    table["status"] = np.select(
        [ex_met & ev_met, ex_met & evaluated, ex_met, table["explore_enough_trades"].astype(bool).to_numpy()],
        ["both", "explore_only", "not_evaluated", "not_met"], "insufficient_trades")
    table["_s"] = table["status"].map({s: i for i, s in enumerate(STATUSES)})
    table = table.sort_values(["_s", "explore_win_rate", "id"], ascending=[True, False, True],
                              na_position="last", kind="stable").drop(columns="_s").reset_index(drop=True)
    table["order"] = np.arange(1, len(table) + 1)

    any_met = bool(table["explore_met"].any()) if len(table) else False
    return {"request": req, "status": status, "split": {"explore": explore_p, "evaluate": evaluate_p},
            "candidates": cands, "table": table, "sort_rule": SORT_RULE,
            "counts": {"candidates": len(cands), "processed": len(ex),
                       **{s: int((table["status"] == s).sum()) for s in STATUSES},
                       "explore_fdr_pass": int(table["explore_fdr_pass"].sum()),
                       "evaluate_fdr_pass": int(table["evaluate_fdr_pass"].astype("boolean").fillna(False).sum())},
            "closest": None if any_met else closest_candidate(ex, req)}


# ---------------------------------------------------------------- 탐색 기록 저장 (P1-6.3)
def searches_dir(paths: Paths) -> Path:
    return paths.runs / "searches"


def make_search_id(req: SearchRequest, cfg: dict, data_ver: str) -> str:
    key = config_hash({"request": req.to_dict(), "cfg": cfg, "data": data_ver})[:8]
    return f"{datetime.now().strftime('%Y%m%dT%H%M%S')}_search_{req.name}_{key}"


def _side(row: dict, prefix: str, keys) -> dict | None:
    if prefix == "evaluate_" and pd.isna(row.get("evaluate_trades")):
        return None
    return {k: row.get(prefix + k) for k in keys}


def search_payload(out: dict, search_id: str, cfg: dict) -> dict:
    """화면용 search.json (설계 §4.3)."""
    by_id = {s.name: s for s in out["candidates"]}
    ex_keys = (*METRIC_KEYS, "p_value", "fdr_pass")
    ev_keys = (*METRIC_KEYS, "p_value", "fdr_pass", "random_percentile", "random_pass")
    rows = [{"order": r["order"], "id": r["id"], "status": r["status"], "strategy": by_id[r["id"]].to_dict(),
             "explore": _side(r, "explore_", ex_keys), "evaluate": _side(r, "evaluate_", ev_keys)}
            for r in out["table"].to_dict(orient="records")]
    return {
        "search_id": search_id, "status": out["status"], "data_as_of": cfg["data"]["as_of_date"],
        "request": out["request"].to_dict(), "method": "exhaustive", "split": out["split"],
        "fdr_q": cfg["analysis"]["fdr_q"], "fdr_family_size": out["counts"]["candidates"],
        "counts": out["counts"], "sort_rule": out["sort_rule"], "rows": rows, "closest": out["closest"],
        "metric_definitions": {k: {"name": n, "unit": u, "formula": f} for k, (n, u, f) in METRIC_DEFS.items()},
        "note": NOTE, "disclaimer": DISCLAIMER,
    }


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(to_jsonable(obj), ensure_ascii=False, indent=2), encoding="utf-8")


def run_and_save_search(req: SearchRequest, prep: Prepared, cfg: dict, paths: Paths, search_id: str | None = None,
                        ctx: RunContext = NULL_CONTEXT) -> Path:
    """탐색을 실행하고 runs/searches/<search_id>/ 에 기록한다. 완료·취소 모두 기록을 남긴다(취소는 status=cancelled).

    임시 폴더에 쓴 뒤 한 번에 옮긴다(E4). 실패(예외)하면 기록을 남기지 않는다.
    """
    t0 = time.time()
    started = datetime.now().isoformat(timespec="seconds")
    inputs = input_file_hashes(paths.store)
    data_ver = config_hash(inputs)[:8]
    search_id = search_id or make_search_id(req, cfg, data_ver)
    root = searches_dir(paths)
    out_dir, tmp = root / search_id, root / f".tmp_{search_id}"
    if out_dir.exists():
        raise FileExistsError(out_dir)
    try:
        out = run_search(req, prep, cfg, ctx)
        if out["status"] == "completed":  # 취소된 탐색은 단계 전환(취소 확인) 없이 바로 기록한다
            ctx.stage("save")
        tmp.mkdir(parents=True, exist_ok=False)
        table = out["table"].copy()
        by_id = {s.name: s for s in out["candidates"]}
        table["strategy"] = [json.dumps(by_id[i].to_dict(), ensure_ascii=False, sort_keys=True) for i in table["id"]]
        table.to_parquet(tmp / "candidates.parquet", index=False)
        _write_json(tmp / "search.json", search_payload(out, search_id, cfg))
        _write_json(tmp / "meta.json", {
            "search_id": search_id, "status": out["status"], "request": req.to_dict(),
            "split": out["split"], "counts": out["counts"], "method": "exhaustive",
            "data_as_of": cfg["data"]["as_of_date"], "config": cfg, "config_hash": config_hash(cfg),
            "prep_hash": prep_hash(cfg), "input_files": inputs, "data_version": data_ver,
            "seed": cfg["analysis"]["random_seed"], "engine_version": ENGINE_VERSION,
            "python": platform.python_version(), "pandas": pd.__version__,
            "started_at": started, "elapsed_sec": round(time.time() - t0, 2),
            "stage_log": ctx.stage_log() if hasattr(ctx, "stage_log") else None, "disclaimer": DISCLAIMER,
        })
        os.replace(tmp, out_dir)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return out_dir
