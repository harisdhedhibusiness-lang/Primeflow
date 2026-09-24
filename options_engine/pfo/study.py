"""The standard study: every strategy across account sizes, stress tests and a go/no-go gate.

The gate is fixed in advance. A strategy only earns "paper-trade candidate" if it clears
every line of it; it never earns "live" from a backtest alone.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date
from typing import Dict, List, Optional, Tuple

from .backtest import BacktestResult, run_backtest
from .config import CostConfig, RiskConfig
from .metrics import compute_stats, max_drawdown, monte_carlo
from .regime import MarketFrame
from .strategies import Strategy, make_strategy

# The go/no-go gate for moving a strategy from backtest to paper trading.
GATE = {
    "min_trades": 30,
    "min_profit_factor": 1.3,
    "max_drawdown": 0.25,
}

# A $100 account with every safeguard switched off, to show what "just send it" does.
UNPROTECTED_100 = RiskConfig(
    starting_equity=100, micro_account_cap=1.0, max_total_risk=1.0,
    hard_floor=0.0, drawdown_halt=1.0, loss_streak=10**6,
)


@dataclass
class Run:
    label: str
    result: BacktestResult
    stats: Dict[str, float]
    mc: Dict[str, float]
    note: str = ""


@dataclass
class StrategyStudy:
    strategy: Strategy
    main: List[Run]
    stress: List[Run]
    gate: List[Tuple[str, bool, str]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.gate) and all(ok for _, ok, _ in self.gate)

    @property
    def headline(self) -> Run:
        return next(r for r in self.main if r.result.risk.starting_equity == 5000)


@dataclass
class Study:
    start: date
    end: date
    source: str
    strategies: List[StrategyStudy]
    benchmark: Dict[str, float]
    benchmark_curve: List[Tuple[date, float]]


def _run(mf: MarketFrame, strategy: Strategy, label: str, note: str = "", **kw) -> Run:
    res = run_backtest(mf, strategy, label=label, **kw)
    return Run(label, res, compute_stats(res), monte_carlo(res), note)


def _micro(strategy: Strategy) -> Strategy:
    """Same strategy forced to $1-wide spreads, the only size a $100 account can hold."""
    return type(strategy)(replace(strategy.cfg, width=1.0))


def run_study(mf: MarketFrame, names: List[str], start: Optional[date] = None,
              end: Optional[date] = None, source: str = "") -> Study:
    first = next(i for i, r in enumerate(mf.regime) if r is not None)
    lo = max(first, mf.index_of(start) if start else 0)
    hi = max(i for i, d in enumerate(mf.dates) if end is None or d <= end)
    start_d, end_d = mf.dates[lo], mf.dates[hi]
    mid_d = mf.dates[(lo + hi) // 2]
    window = dict(start=start_d, end=end_d)

    studies = []
    for name in names:
        s = make_strategy(name)
        main = [
            _run(mf, s, "$100, safeguards on", risk=RiskConfig(starting_equity=100), **window),
            _run(mf, _micro(s), "$100, safeguards off, $1-wide", risk=UNPROTECTED_100,
                 note="All-in sizing, no floor, no halts. Shown to make the risk visible.", **window),
            _run(mf, s, "$2,000", risk=RiskConfig(starting_equity=2000), **window),
            _run(mf, s, "$5,000", risk=RiskConfig(starting_equity=5000), **window),
        ]
        base = RiskConfig(starting_equity=5000)
        stress = [
            _run(mf, s, "Zero commission", risk=base, costs=CostConfig(commission_per_contract=0.0), **window),
            _run(mf, s, "Double slippage", risk=base, costs=CostConfig(slippage_per_leg=0.02), **window),
            _run(mf, s, "Next-day fills", risk=base, entry_lag=1, **window),
            _run(mf, s, f"First half (to {mid_d})", risk=base, start=start_d, end=mid_d),
            _run(mf, s, f"Second half (from {mid_d})", risk=base, start=mid_d, end=end_d),
        ]
        st = StrategyStudy(s, main, stress)
        st.gate = _gate(st)
        studies.append(st)

    closes = mf.close[lo:hi + 1]
    years = (end_d - start_d).days / 365.25
    bench = {
        "total_return": closes[-1] / closes[0] - 1,
        "cagr": (closes[-1] / closes[0]) ** (1 / years) - 1 if years > 0 else 0.0,
        "max_drawdown": max_drawdown(closes),
    }
    curve = list(zip(mf.dates[lo:hi + 1], closes))
    return Study(start_d, end_d, source, studies, bench, curve)


def _gate(st: StrategyStudy) -> List[Tuple[str, bool, str]]:
    h = st.headline.stats
    by = {r.label.split(" (")[0]: r.stats for r in st.stress}
    rows = [
        (f"At least {GATE['min_trades']} trades", h["trades"] >= GATE["min_trades"], f"{h['trades']}"),
        (f"Profit factor of at least {GATE['min_profit_factor']}", h["profit_factor"] >= GATE["min_profit_factor"],
         f"{h['profit_factor']:.2f}"),
        (f"Max drawdown under {GATE['max_drawdown']:.0%}", h["max_drawdown"] < GATE["max_drawdown"],
         f"{h['max_drawdown']:.1%}"),
        ("Profitable in the first half", by["First half"]["total_return"] > 0,
         f"{by['First half']['total_return']:+.1%}"),
        ("Profitable in the second half", by["Second half"]["total_return"] > 0,
         f"{by['Second half']['total_return']:+.1%}"),
        ("Still profitable with double slippage", by["Double slippage"]["total_return"] > 0,
         f"{by['Double slippage']['total_return']:+.1%}"),
        ("Still profitable with next-day fills", by["Next-day fills"]["total_return"] > 0,
         f"{by['Next-day fills']['total_return']:+.1%}"),
    ]
    return rows
