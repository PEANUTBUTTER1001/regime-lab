"""백테스트 엔진 (FR-E)."""

from regime_lab.backtest.engine import ExitRule, build_trades, run_backtest, simulate_trades
from regime_lab.backtest.metrics import METRIC_DEFS, equity_curve, max_drawdown, summarize, trade_stats

__all__ = ["ExitRule", "run_backtest", "simulate_trades", "build_trades", "summarize", "trade_stats",
           "equity_curve", "max_drawdown", "METRIC_DEFS"]
