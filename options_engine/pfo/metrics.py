"""Performance statistics and a Monte Carlo stress test of the trade sequence."""

from __future__ import annotations

import math
import random
from typing import Dict, List, Sequence

from .backtest import BacktestResult


def max_drawdown(values: Sequence[float]) -> float:
    peak, worst = -float("inf"), 0.0
    for v in values:
        peak = max(peak, v)
        if peak > 0:
            worst = max(worst, 1 - v / peak)
    return worst


def compute_stats(res: BacktestResult) -> Dict[str, float]:
    equity = [v for _, v in res.curve]
    trades = res.trades
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    days = (res.curve[-1][0] - res.curve[0][0]).days if len(res.curve) > 1 else 0
    years = days / 365.25 if days else 0.0
    start, end = res.start_equity, res.end_equity
    cagr = (end / start) ** (1 / years) - 1 if years > 0 and end > 0 else (-1.0 if end <= 0 else 0.0)

    rets = [equity[k] / equity[k - 1] - 1 for k in range(1, len(equity)) if equity[k - 1] > 0]
    sharpe = 0.0
    if len(rets) > 1:
        mean = sum(rets) / len(rets)
        sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1))
        sharpe = mean / sd * math.sqrt(252) if sd > 0 else 0.0

    streak = worst_streak = 0
    for t in trades:
        streak = streak + 1 if t.pnl <= 0 else 0
        worst_streak = max(worst_streak, streak)

    gross_win, gross_loss = sum(wins), -sum(losses)
    return {
        "start_equity": start,
        "end_equity": end,
        "total_return": end / start - 1,
        "cagr": cagr,
        "max_drawdown": max_drawdown(equity),
        "sharpe": sharpe,
        "trades": len(trades),
        "win_rate": len(wins) / len(trades) if trades else 0.0,
        "avg_win": gross_win / len(wins) if wins else 0.0,
        "avg_loss": -gross_loss / len(losses) if losses else 0.0,
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else float("inf") if gross_win else 0.0,
        "expectancy": sum(t.pnl for t in trades) / len(trades) if trades else 0.0,
        "worst_trade": min((t.pnl for t in trades), default=0.0),
        "worst_trade_pct": min((t.return_on_equity for t in trades), default=0.0),
        "max_losing_streak": worst_streak,
        "exposure": res.bars_in_market / len(res.curve) if res.curve else 0.0,
        "signals": res.signals,
        "unaffordable": res.blocked.get("too_small_to_afford", 0),
        "years": years,
    }


def monte_carlo(res: BacktestResult, paths: int = 5000, seed: int = 7) -> Dict[str, float]:
    """Reshuffle the trades (with replacement) to see how much of the result was luck of order.

    Each trade's P&L is taken as a fraction of the equity it was opened with, and paths
    compound from 1.0 with the same number of trades as the backtest.
    """
    rets: List[float] = [t.return_on_equity for t in res.trades]
    if len(rets) < 5:
        return {}
    rng = random.Random(seed)
    finals, drawdowns = [], []
    floor = res.risk.hard_floor
    for _ in range(paths):
        eq = peak = 1.0
        dd = 0.0
        for _ in range(len(rets)):
            eq *= 1 + rng.choice(rets)
            if eq <= 0:
                eq = 0.0
                dd = 1.0
                break
            peak = max(peak, eq)
            dd = max(dd, 1 - eq / peak)
        finals.append(eq)
        drawdowns.append(dd)
    finals.sort()
    pct = lambda p: finals[min(int(p * paths), paths - 1)]
    return {
        "median": pct(0.5),
        "p05": pct(0.05),
        "p95": pct(0.95),
        "prob_loss": sum(f < 1 for f in finals) / paths,
        "prob_dd_50": sum(d >= 0.5 for d in drawdowns) / paths,
        "prob_below_floor": sum(f < floor for f in finals) / paths,
    }
