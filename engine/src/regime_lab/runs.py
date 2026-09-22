"""전략 실행과 결과 저장 (FR-E8, FR-U1, FR-X5, FR-A5, NFR-5, NFR-10).

한 실행 요청(run_id)은 전략 1개 이상을 담는다. 요청 안의 전략 수가 FDR 가족 크기 m 이다 (E3).

runs/<run_id>/
  meta.json          요청 전략(기본값 채움)·전체 설정·설정 해시·데이터 기준일·입력 파일 지문·시드·버전·단계 로그
  result.json        화면용 결과 (요약·지표 정의·검증·셀·손익 분포·종목 표·기간 분할, FR-U3·X6) + 고지
  validation.parquet 전략별 3중 검증 결과
  universe.json      유니버스 제외 사유별 행 수 (FR-D4)
  strategies/<name>/ trades·skipped·equity·cells_market·cells_stock (parquet), summary.json

저장은 runs/.tmp_<run_id>/ 에 쓴 뒤 완료 시 한 번에 옮긴다. 취소·실패 시 결과 폴더를 남기지 않는다 (E4).
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import time
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import yaml

from regime_lab.analysis.aggregate import aggregate_cells
from regime_lab.analysis.report import strategy_result, to_jsonable
from regime_lab.analysis.validation import validate
from regime_lab.backtest import METRIC_DEFS, ExitRule, build_trades, equity_curve, simulate_trades, summarize
from regime_lab.config import Paths, config_hash
from regime_lab.context import NULL_CONTEXT, RunContext
from regime_lab.data.loader import input_file_hashes
from regime_lab.patterns import CORE_PATTERNS, compute_signals
from regime_lab.pipeline import Prepared, prep_hash
from regime_lab.universe import exclusion_summary

DISCLAIMER = "Historical analysis — not investment advice. 과거 데이터 분석 결과이며 투자 권유가 아닙니다."
FDR_NOTE = ("FDR 가족 크기 m 은 이 실행 요청에 포함된 전략 수입니다. 여러 전략을 따로 실행해 비교했다면 "
            "다중검정 보정이 약해집니다.")
ENGINE_VERSION = "0.2.0"

STRATEGY_KEYS = {"name", "patterns", "combine", "exit", "markets", "period", "min_avg_value_krw", "cap_groups"}
EXIT_KEYS = {"stop_loss_pct", "take_profit_pct", "max_hold_days", "trailing_stop_pct"}
CAP_GROUPS = ["large", "mid", "small"]
NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class StrategyError(ValueError):
    """전략 입력 검증 실패. errors = {필드 경로: 사유} (API 422 응답에 그대로 사용)."""

    def __init__(self, errors: dict[str, str]):
        self.errors = errors
        super().__init__("; ".join(f"{k}: {v}" for k, v in errors.items()))


@dataclass
class Strategy:
    name: str
    patterns: list[str]
    combine: str = "or"
    exit: dict | None = None
    markets: list[str] | None = None
    period: dict | None = None
    min_avg_value_krw: int | None = None
    cap_groups: list[str] | None = None

    @classmethod
    def load(cls, path: Path) -> "Strategy":
        with open(path, encoding="utf-8") as f:
            return cls.from_dict(yaml.safe_load(f))

    @classmethod
    def from_dict(cls, d: dict) -> "Strategy":
        """구조 검증 (설정 없이 가능한 것). 값 범위 검증은 validate(cfg)."""
        if not isinstance(d, dict):
            raise StrategyError({"": "A strategy must be an object (JSON/YAML mapping)"})
        errors: dict[str, str] = {}
        unknown = set(d) - STRATEGY_KEYS
        if unknown:
            # 패턴 파라미터 등은 전략 파일에서 바꿀 수 없다 (C-3, FR-P4)
            errors["_"] = f"Keys not allowed: {sorted(unknown)}"
        name = d.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            errors["name"] = "Use 1-64 letters, digits, _ or -"
        pats = d.get("patterns")
        if not isinstance(pats, list) or not pats:
            errors["patterns"] = "Select at least one pattern"
        else:
            bad = [p for p in pats if p not in CORE_PATTERNS]
            if bad:
                errors["patterns"] = f"Unsupported patterns: {bad}"
            elif len(set(pats)) != len(pats):
                errors["patterns"] = "Duplicate patterns"
        combine = str(d.get("combine", "or")).lower()
        if combine not in ("and", "or"):
            errors["combine"] = "Use and or or"
        if errors:
            raise StrategyError(errors)
        period = d.get("period")
        if isinstance(period, dict):  # YAML 은 따옴표 없는 날짜를 date 객체로 읽는다 → YYYY-MM-DD 문자열
            period = {k: v.isoformat() if isinstance(v, date) else v for k, v in period.items()}
        return cls(name, list(pats), combine, d.get("exit"), d.get("markets"), period,
                   d.get("min_avg_value_krw"), d.get("cap_groups"))

    # ------------------------------------------------------------------ 값 검증 (FR-X5)
    def validate(self, cfg: dict) -> None:
        errors: dict[str, str] = {}
        dcfg = cfg["data"]

        if self.exit is not None:
            if not isinstance(self.exit, dict):
                errors["exit"] = "Must be an object"
            else:
                for k in set(self.exit) - EXIT_KEYS:
                    errors[f"exit.{k}"] = "Key not allowed"
                for k in ("stop_loss_pct", "take_profit_pct", "trailing_stop_pct"):
                    v = self.exit.get(k)
                    if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float))):
                        errors[f"exit.{k}"] = "A percent number (e.g. -8) or null"
                mh = self.exit.get("max_hold_days", cfg["exit"]["max_hold_days"])
                if isinstance(mh, bool) or not isinstance(mh, int):
                    errors["exit.max_hold_days"] = "Whole number of trading days"
                if not errors:
                    try:
                        ExitRule.from_cfg(self.exit_cfg(cfg), cfg["exit_limits"])
                    except ValueError as e:
                        key = next((k for k in EXIT_KEYS if k in str(e)), "exit")
                        errors[f"exit.{key}" if key != "exit" else "exit"] = str(e)

        if self.markets is not None:
            if (not isinstance(self.markets, list) or not self.markets
                    or not set(self.markets) <= set(dcfg["markets"]) or len(set(self.markets)) != len(self.markets)):
                errors["markets"] = f"One or more of {dcfg['markets']}, no duplicates"

        if self.period is not None:
            p = self.period
            if not isinstance(p, dict) or set(p) != {"start", "end"}:
                errors["period"] = "An object with start and end"
            else:
                ok = True
                for k in ("start", "end"):
                    v = p[k]
                    if not isinstance(v, str) or not DATE_RE.match(v):
                        errors[f"period.{k}"] = "YYYY-MM-DD"
                        ok = False
                    else:
                        try:
                            pd.Timestamp(v)
                        except ValueError:
                            errors[f"period.{k}"] = "Not a valid calendar date"
                            ok = False
                if ok:
                    s, e = pd.Timestamp(p["start"]), pd.Timestamp(p["end"])
                    if s < pd.Timestamp(dcfg["backtest_start"]):
                        errors["period.start"] = f"On or after {dcfg['backtest_start']}"
                    if e > pd.Timestamp(dcfg["as_of_date"]):
                        errors["period.end"] = f"On or before the data date {dcfg['as_of_date']}"
                    if s > e:
                        errors["period"] = "Start date is after end date"

        if self.min_avg_value_krw is not None:
            v = self.min_avg_value_krw
            floor = int(cfg["universe"]["min_avg_value_krw"])
            if isinstance(v, bool) or not isinstance(v, int):
                errors["min_avg_value_krw"] = "Whole KRW amount"
            elif v < floor:
                errors["min_avg_value_krw"] = f"At least {floor:,} KRW (universe liquidity floor, A4)"

        if self.cap_groups is not None:
            if (not isinstance(self.cap_groups, list) or not self.cap_groups
                    or not set(self.cap_groups) <= set(CAP_GROUPS) or len(set(self.cap_groups)) != len(self.cap_groups)):
                errors["cap_groups"] = f"One or more of {CAP_GROUPS}, no duplicates"

        if errors:
            raise StrategyError(errors)

    def exit_cfg(self, cfg: dict) -> dict:
        return {**cfg["exit"], **(self.exit or {})}

    def effective(self, cfg: dict) -> dict:
        """기본값을 채운 전략 (메타데이터·화면 표시용)."""
        return {
            "name": self.name, "patterns": self.patterns, "combine": self.combine,
            "exit": self.exit_cfg(cfg),
            "markets": self.markets or list(cfg["data"]["markets"]),
            "period": self.period or {"start": cfg["data"]["backtest_start"], "end": cfg["data"]["as_of_date"]},
            "min_avg_value_krw": self.min_avg_value_krw or int(cfg["universe"]["min_avg_value_krw"]),
            "cap_groups": self.cap_groups or list(CAP_GROUPS),
        }

    def to_dict(self) -> dict:
        d = {"name": self.name, "patterns": self.patterns, "combine": self.combine, "exit": self.exit}
        for k in ("markets", "period", "min_avg_value_krw", "cap_groups"):
            if getattr(self, k) is not None:
                d[k] = getattr(self, k)
        return d


def entry_mask(frame: pd.DataFrame, strategy: Strategy) -> pd.Series:
    """전략 입력 필터 (E1). 신호일 값으로만 판정한다. 지표·국면·그룹은 전종목 기준 그대로."""
    m = pd.Series(True, index=frame.index)
    if strategy.markets is not None:
        m &= frame["market"].astype(object).isin(strategy.markets)
    if strategy.period is not None:
        m &= frame["date"].between(pd.Timestamp(strategy.period["start"]), pd.Timestamp(strategy.period["end"]))
    if strategy.min_avg_value_krw is not None:
        m &= frame["avg_value20"] >= strategy.min_avg_value_krw
    if strategy.cap_groups is not None:
        m &= frame["cap_group"].astype(object).isin(strategy.cap_groups)
    return m


class _Offset(RunContext):
    """여러 전략의 진행을 하나의 처리 수/전체 수로 합친다."""

    def __init__(self, ctx: RunContext, index: int, count: int):
        self.ctx, self.index, self.count = ctx, index, count

    def progress(self, done: int, total: int) -> None:
        self.ctx.progress(self.index * total + done, self.count * total)

    def check_cancel(self) -> None:
        self.ctx.check_cancel()


def _as_list(strategies) -> list[Strategy]:
    lst = [strategies] if isinstance(strategies, Strategy) else list(strategies)
    if not lst:
        raise StrategyError({"strategies": "At least one strategy is required"})
    names = [s.name for s in lst]
    if len(set(names)) != len(names):
        raise StrategyError({"strategies": f"Duplicate strategy names: {names}"})
    return lst


def execute(strategies, prep: Prepared, cfg: dict, ctx: RunContext = NULL_CONTEXT) -> dict:
    """전략(들)을 실행하고 3중 검증까지 수행한다. 저장하지 않는다."""
    lst = _as_list(strategies)
    for s in lst:
        s.validate(cfg)

    ctx.stage("filter")
    masks = {s.name: entry_mask(prep.frame, s) for s in lst}
    ctx.stage("load", "in-memory prepared frame")
    ctx.stage("indicators", "cached")

    ctx.stage("signals_execution")
    sims = {}
    for i, s in enumerate(lst):
        sig = compute_signals(prep.frame, s.patterns, s.combine, cfg)
        sims[s.name] = simulate_trades(prep.frame, sig, cfg, prep.delisted, s.exit_cfg(cfg), masks[s.name],
                                       _Offset(ctx, i, len(lst)))

    ctx.stage("regime_join")
    per: dict[str, dict] = {}
    for s in lst:
        f, rows, skip_rows = sims.pop(s.name)
        trades, skipped = build_trades(f, rows, skip_rows, prep.index, cfg, prep.sectors)
        per[s.name] = {"trades": trades, "skipped": skipped}
        ctx.check_cancel()

    ctx.stage("aggregate")
    for s in lst:
        r = per[s.name]
        inc = r["trades"][~r["trades"]["excluded"]] if len(r["trades"]) else r["trades"]
        r["summary"] = summarize(r["trades"], r["skipped"], prep.frame)
        r["equity"] = equity_curve(inc, prep.frame)
        r["cells_market"] = aggregate_cells(r["trades"], cfg, "market_regime")
        r["cells_stock"] = aggregate_cells(r["trades"], cfg, "stock_regime")
        r["exit_cfg"] = s.exit_cfg(cfg)
    periods = {s.name: s.period for s in lst}
    val = validate({n: r["trades"] for n, r in per.items()}, prep.frame, prep.index, cfg, periods, ctx)
    return {"strategies": lst, "results": per, "validation": val}


def make_run_id(strategies: list[Strategy], cfg: dict, data_ver: str) -> str:
    key = config_hash({"strategies": [s.to_dict() for s in strategies], "cfg": cfg, "data": data_ver})[:8]
    label = strategies[0].name if len(strategies) == 1 else f"compare{len(strategies)}"
    return f"{datetime.now().strftime('%Y%m%dT%H%M%S')}_{label}_{key}"


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(to_jsonable(obj), ensure_ascii=False, indent=2), encoding="utf-8")


def run_and_save(strategies, prep: Prepared, cfg: dict, paths: Paths, tickers: list[str] | None = None,
                 run_id: str | None = None, ctx: RunContext = NULL_CONTEXT) -> Path:
    t0 = time.time()
    started = datetime.now().isoformat(timespec="seconds")
    lst = _as_list(strategies)
    inputs = input_file_hashes(paths.store)
    data_ver = config_hash(inputs)[:8]
    run_id = run_id or make_run_id(lst, cfg, data_ver)
    out = paths.runs / run_id
    if out.exists():
        raise FileExistsError(out)
    tmp = paths.runs / f".tmp_{run_id}"
    try:
        res = execute(lst, prep, cfg, ctx)
        ctx.stage("save")
        tmp.mkdir(parents=True, exist_ok=False)
        val = res["validation"]
        val.to_parquet(tmp / "validation.parquet", index=False)
        val_rows = {r["strategy"]: r for r in val.to_dict(orient="records")}
        strategy_results = []
        for s in lst:
            r = res["results"][s.name]
            sd = tmp / "strategies" / s.name
            sd.mkdir(parents=True)
            for k in ("trades", "skipped", "equity", "cells_market", "cells_stock"):
                r[k].to_parquet(sd / f"{k}.parquet", index=False)
            _write_json(sd / "summary.json", {"strategy": s.effective(cfg), "metrics": r["summary"]})
            sr = strategy_result(s.name, r, val_rows[s.name], prep.names, cfg)
            sr["strategy_input"] = s.effective(cfg)
            strategy_results.append(sr)
        m = len(lst)
        _write_json(tmp / "result.json", {
            "run_id": run_id,
            "data_as_of": cfg["data"]["as_of_date"],
            "fdr_family_size": m,
            "fdr_note": FDR_NOTE,
            "min_cell_trades": cfg["analysis"]["min_cell_trades"],
            "metric_definitions": {k: {"name": n, "unit": u, "formula": fml} for k, (n, u, fml) in METRIC_DEFS.items()},
            "strategies": strategy_results,
            "disclaimer": DISCLAIMER,
        })
        uni = exclusion_summary(prep.frame)
        _write_json(tmp / "universe.json", {str(r.reason): int(r.rows) for r in uni.itertuples()})
        stage_log = ctx.stage_log() if hasattr(ctx, "stage_log") else None
        _write_json(tmp / "meta.json", {
            "run_id": run_id,
            "strategies": [s.effective(cfg) for s in lst],
            "fdr_family_size": m,
            "data_as_of": cfg["data"]["as_of_date"],
            "backtest_start": cfg["data"]["backtest_start"],
            "tickers": "all" if tickers is None else tickers,
            "config": cfg,
            "config_hash": config_hash(cfg),
            "prep_hash": prep_hash(cfg),
            "input_files": inputs,
            "data_version": data_ver,
            "seed": cfg["analysis"]["random_seed"],
            "external_inputs": None,  # 야간·뉴스 입력은 이번 범위 밖 (§8 T-2)
            "delisted_coverage": {"delisted_tickers_in_store": len(prep.delisted),
                                  "note": "store/delisted 333종목 모두 시세 보유 (데이터_검토_결과 §2.1)"},
            "engine_version": ENGINE_VERSION,
            "python": platform.python_version(),
            "pandas": pd.__version__,
            "started_at": started,
            "elapsed_sec": round(time.time() - t0, 2),
            "stage_log": stage_log,
            "disclaimer": DISCLAIMER,
        })
        ctx.check_cancel()
        os.replace(tmp, out)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return out
