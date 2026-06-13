"""Research dashboard: static, reproducible, self-contained HTML report with
equity curve, regime overlays, rolling Sharpe/Sortino, drawdown, exposure, and
per-regime trade attribution. The tradeable equity curve uses the causal
(filtered) regime signal; smoothed regimes appear only as a labelled overlay."""

from quantis.dashboard.report import StrategyResult, generate_report, run_strategy

__all__ = ["StrategyResult", "generate_report", "run_strategy"]
