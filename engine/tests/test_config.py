from regime_lab.config import config_hash, load_config, load_paths


def test_default_config_has_decided_values():
    cfg = load_config()
    assert cfg["data"]["as_of_date"] == "2026-09-18"
    assert cfg["data"]["backtest_start"] == "2020-09-01"
    assert cfg["universe"]["min_avg_value_krw"] == 500_000_000
    assert cfg["groups"]["marketcap_split"] == [0.30, 0.40, 0.30]
    assert cfg["regime"]["market_regime_start"] == "2021-07-21"
    assert cfg["exit"] == {
        "stop_loss_pct": -8, "take_profit_pct": 20, "max_hold_days": 20, "trailing_stop_pct": None,
    }
    assert cfg["execution"]["round_trip_cost_pct"] == 0.30
    assert cfg["analysis"]["min_cell_trades"] == 300
    assert cfg["analysis"]["fdr_q"] == 0.10
    assert cfg["analysis"]["random_bench_iterations"] == 1000


def test_override_and_hash():
    base = load_config()
    over = load_config({"exit": {"max_hold_days": 10}})
    assert over["exit"]["max_hold_days"] == 10
    assert over["exit"]["stop_loss_pct"] == -8
    assert config_hash(base) != config_hash(over)
    assert config_hash(base) == config_hash(load_config())


def test_paths_resolve():
    p = load_paths()
    assert p.store.name == "store"
