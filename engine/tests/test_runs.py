"""S9·2A 실행 저장·재현성·전략 입력 (FR-E8, FR-U1, FR-X5, NFR-5, NFR-10)."""

import dataclasses
import json

import pandas as pd
import pytest

from regime_lab.config import ENGINE_DIR
from regime_lab.runs import Strategy, StrategyError, entry_mask, run_and_save


def test_strategy_structure_validation():
    with pytest.raises(StrategyError) as e:
        Strategy.from_dict({"name": "x", "patterns": ["unknown"]})
    assert "patterns" in e.value.errors
    with pytest.raises(StrategyError) as e:  # 패턴 파라미터는 전략 파일에서 변경 불가 (C-3)
        Strategy.from_dict({"name": "x", "patterns": ["breakout_vol"], "params": {"volume_mult": 1.0}})
    assert "_" in e.value.errors
    with pytest.raises(StrategyError) as e:
        Strategy.from_dict({"name": "bad name!", "patterns": ["breakout_vol"], "combine": "xor"})
    assert {"name", "combine"} <= set(e.value.errors)
    files = sorted((ENGINE_DIR / "strategies").glob("*.yaml"))
    assert len([f for f in files if f.name.startswith("core_")]) == 5
    for f in files:
        Strategy.load(f)


@pytest.mark.parametrize("field,value,key", [
    ("markets", ["NYSE"], "markets"),
    ("markets", [], "markets"),
    ("period", {"start": "2020/09/01", "end": "2021-01-01"}, "period.start"),
    ("period", {"start": "2020-08-31", "end": "2021-01-01"}, "period.start"),
    ("period", {"start": "2021-01-01", "end": "2026-12-31"}, "period.end"),
    ("period", {"start": "2022-01-01", "end": "2021-01-01"}, "period"),
    ("period", {"start": "2021-02-30", "end": "2021-03-01"}, "period.start"),
    ("min_avg_value_krw", 100_000_000, "min_avg_value_krw"),
    ("min_avg_value_krw", 5.5e8, "min_avg_value_krw"),
    ("cap_groups", ["huge"], "cap_groups"),
    ("exit", {"stop_loss_pct": "-8"}, "exit.stop_loss_pct"),
    ("exit", {"max_hold_days": 20.5}, "exit.max_hold_days"),
    ("exit", {"max_hold_days": 300}, "exit.max_hold_days"),
    ("exit", {"trailing_stop_pct": 5}, "exit.trailing_stop_pct"),
])
def test_strategy_value_validation(cfg, field, value, key):
    s = Strategy.from_dict({"name": "x", "patterns": ["breakout_20d"], field: value})
    with pytest.raises(StrategyError) as e:
        s.validate(cfg)
    assert key in e.value.errors, e.value.errors


def test_strategy_defaults_and_valid_input(cfg):
    s = Strategy.from_dict({"name": "x", "patterns": ["breakout_20d"], "markets": ["KOSDAQ"],
                            "period": {"start": "2021-01-04", "end": "2023-12-29"},
                            "min_avg_value_krw": 1_000_000_000, "cap_groups": ["mid", "small"],
                            "exit": {"stop_loss_pct": None, "max_hold_days": 10}})
    s.validate(cfg)
    eff = s.effective(cfg)
    assert eff["exit"]["take_profit_pct"] == 20 and eff["exit"]["stop_loss_pct"] is None
    d = Strategy.from_dict({"name": "y", "patterns": ["breakout_20d"]}).effective(cfg)
    assert d["markets"] == ["KOSPI", "KOSDAQ"] and d["min_avg_value_krw"] == 500_000_000
    assert d["period"] == {"start": "2020-09-01", "end": "2026-09-18"}


def test_entry_mask_filters_on_signal_date_values():
    f = pd.DataFrame({
        "date": pd.to_datetime(["2021-01-04", "2021-01-05", "2021-01-06", "2021-01-07"]),
        "market": ["KOSPI", "KOSDAQ", "KOSDAQ", "KOSDAQ"],
        "avg_value20": [2e9, 2e9, 6e8, 2e9],
        "cap_group": ["large", "mid", "mid", "large"],
    })
    s = Strategy.from_dict({"name": "x", "patterns": ["breakout_20d"], "markets": ["KOSDAQ"],
                            "period": {"start": "2021-01-04", "end": "2021-01-06"},
                            "min_avg_value_krw": 1_000_000_000, "cap_groups": ["mid"]})
    assert entry_mask(f, s).tolist() == [False, True, False, False]


@pytest.mark.data
def test_two_runs_identical(sample_prepared, paths, cfg, tmp_path):
    p = dataclasses.replace(paths, runs=tmp_path)
    s = Strategy.load(ENGINE_DIR / "strategies" / "example_breakout_and_rsi.yaml")
    a = run_and_save(s, sample_prepared, cfg, p, ["sample30"], run_id="a")
    b = run_and_save(s, sample_prepared, cfg, p, ["sample30"], run_id="b")
    sd = "strategies/example_breakout_and_rsi"
    for f in ("trades", "skipped", "equity", "cells_market", "cells_stock"):
        pd.testing.assert_frame_equal(pd.read_parquet(a / sd / f"{f}.parquet"),
                                      pd.read_parquet(b / sd / f"{f}.parquet"))
    pd.testing.assert_frame_equal(pd.read_parquet(a / "validation.parquet"), pd.read_parquet(b / "validation.parquet"))
    ra = json.loads((a / "result.json").read_text(encoding="utf-8"))
    rb = json.loads((b / "result.json").read_text(encoding="utf-8"))
    ra.pop("run_id"), rb.pop("run_id")
    assert ra == rb and ra["strategies"][0]["summary"]["trades"] > 0
    meta = json.loads((a / "meta.json").read_text(encoding="utf-8"))
    for k in ("config", "config_hash", "data_as_of", "input_files", "seed", "strategies", "fdr_family_size"):
        assert k in meta
    assert meta["data_as_of"] == "2026-09-18" and meta["fdr_family_size"] == 1
    assert meta["strategies"][0]["exit"]["max_hold_days"] == 10
    assert "not investment advice" in ra["disclaimer"]
    t = pd.read_parquet(a / sd / "trades.parquet")
    for c in ("net_ret", "excess_ret", "market_regime", "stock_regime", "cap_group", "liq_group", "sector"):
        assert c in t.columns
    assert not list(tmp_path.glob(".tmp_*"))


def test_yaml_unquoted_dates_accepted(cfg, tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text(
        "name: y\n"
        "patterns: [breakout_20d]\n"
        "period: {start: 2021-01-04, end: 2025-12-30}\n"
        "markets: [KOSDAQ]\n"
        "min_avg_value_krw: 1000000000\n"
        "cap_groups: [mid, small]\n",
        encoding="utf-8",
    )
    s = Strategy.load(f)
    s.validate(cfg)
    assert s.period == {"start": "2021-01-04", "end": "2025-12-30"}
