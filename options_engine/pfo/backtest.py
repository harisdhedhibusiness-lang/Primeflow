"""Daily event-driven backtest.

Each bar, in order:
  1. Settle anything that expired, then mark every open spread and apply its exits:
     profit target, stop loss, time exit, max hold, strategy invalidation.
  2. Update the risk manager with current equity (circuit breakers, kill switch).
  3. If the strategy signalled, and the risk manager allows it, size and open the trade.

Fills are at the model mid plus slippage on every leg, plus commission per contract per
leg. Everything is evaluated at the daily close, so a stop can fill worse than its level
after a gap, which is also what happens in real life.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from typing import List, Optional, Tuple

from .config import CostConfig, PricingConfig, RiskConfig
from .pricing import OptionPricer, years_to_expiry
from .regime import MarketFrame
from .risk import RiskManager
from .strategies import Leg, Plan, Strategy, spread_intrinsic, spread_value


@dataclass
class Position:
    plan: Plan
    entry_index: int
    entry_date: date
    contracts: int
    entry_price: float  # per share, incl. slippage; negative = credit received
    max_loss: float  # dollars for the whole position, incl. round-trip commission
    tp_pnl: float  # per share
    sl_pnl: float  # per share
    equity_at_entry: float
    entry_commission: float

    @property
    def legs(self) -> Tuple[Leg, ...]:
        return self.plan.legs


@dataclass
class Trade:
    strategy: str
    entry_date: date
    exit_date: date
    expiry: date
    legs: str
    contracts: int
    entry_price: float
    exit_price: float
    pnl: float
    equity_at_entry: float
    max_loss: float
    reason: str
    bars_held: int

    @property
    def return_on_equity(self) -> float:
        return self.pnl / self.equity_at_entry if self.equity_at_entry else 0.0


@dataclass
class BacktestResult:
    label: str
    strategy: Strategy
    risk: RiskConfig
    costs: CostConfig
    entry_lag: int
    trades: List[Trade]
    curve: List[Tuple[date, float]]
    events: List[Tuple[date, str]]
    signals: int
    blocked: Counter = field(default_factory=Counter)
    halted_bars: int = 0
    bars_in_market: int = 0
    killed: bool = False

    @property
    def start_equity(self) -> float:
        return self.risk.starting_equity

    @property
    def end_equity(self) -> float:
        return self.curve[-1][1] if self.curve else self.risk.starting_equity


def run_backtest(
    mf: MarketFrame,
    strategy: Strategy,
    risk: RiskConfig = RiskConfig(),
    costs: CostConfig = CostConfig(),
    pricing: PricingConfig = PricingConfig(),
    start: Optional[date] = None,
    end: Optional[date] = None,
    entry_lag: int = 0,
    label: str = "",
) -> BacktestResult:
    pricer = OptionPricer(pricing)
    rm = RiskManager(risk)
    cash = risk.starting_equity
    open_pos: List[Position] = []
    trades: List[Trade] = []
    curve: List[Tuple[date, float]] = []
    blocked: Counter = Counter()
    signals = bars_in_market = 0
    pending: Optional[int] = None
    fee = costs.commission_per_contract
    slip = costs.slippage_per_leg

    first = next((i for i, r in enumerate(mf.regime) if r is not None), len(mf))
    lo = max(first, mf.index_of(start) if start else 0)
    hi = len(mf) - 1 if end is None else max(i for i, d in enumerate(mf.dates) if d <= end)

    def mark(pos: Position, i: int) -> float:
        days = max((pos.plan.expiry - mf.dates[i]).days, 1)
        T = years_to_expiry(mf.dates[i], pos.plan.expiry)
        return spread_value(pos.legs, mf.close[i], T, mf.term_vol(i, days), pricer)

    def close(pos: Position, i: int, exit_price: float, reason: str, commission: float) -> None:
        nonlocal cash
        n = pos.contracts
        cash += exit_price * 100 * n - commission
        pnl = (exit_price - pos.entry_price) * 100 * n - pos.entry_commission - commission
        trades.append(
            Trade(
                pos.plan.strategy, pos.entry_date, mf.dates[i], pos.plan.expiry,
                " / ".join(leg.describe() for leg in pos.legs), n,
                pos.entry_price, exit_price, pnl, pos.equity_at_entry, pos.max_loss,
                reason, i - pos.entry_index,
            )
        )
        open_pos.remove(pos)
        rm.on_trade_closed(i, mf.dates[i], pnl)

    for i in range(lo, hi + 1):
        today = mf.dates[i]

        # 1. Manage open positions.
        for pos in list(open_pos):
            n_legs = len(pos.legs)
            if today >= pos.plan.expiry:
                settle_px = mf.close[i] if today == pos.plan.expiry else mf.close[i - 1]
                close(pos, i, spread_intrinsic(pos.legs, settle_px), "expired", 0.0)
                continue
            exit_px = mark(pos, i) - slip * n_legs
            pnl_ps = exit_px - pos.entry_price
            reason = None
            if pnl_ps >= pos.tp_pnl:
                reason = "take_profit"
            elif pnl_ps <= pos.sl_pnl:
                reason = "stop_loss"
            elif (pos.plan.expiry - today).days <= pos.plan.exit_dte:
                reason = "time_exit"
            elif pos.plan.max_hold_days and i - pos.entry_index >= pos.plan.max_hold_days:
                reason = "max_hold"
            else:
                reason = strategy.exit_signal(mf, i, pos)
            if reason:
                close(pos, i, exit_px, reason, fee * n_legs * pos.contracts)

        equity = cash + sum(mark(p, i) * 100 * p.contracts for p in open_pos)
        rm.update(i, today, equity)

        # 2. Entries.
        fire_at: Optional[int] = None
        if entry_lag == 0:
            if strategy.entry_signal(mf, i):
                fire_at = i
        else:
            if pending is not None and i - pending >= entry_lag:
                fire_at, pending = i, None
            if pending is None and fire_at is None and strategy.entry_signal(mf, i):
                pending = i

        if fire_at is not None:
            signals += 1
            ok, why = rm.can_open(i, len(open_pos))
            plan = strategy.build(mf, i, pricer) if ok else None
            if not ok:
                blocked[why] += 1
            elif plan is None:
                blocked["no_valid_contract"] += 1
            else:
                pos = open_position(plan, i, today, equity, open_pos, rm, fee, slip)
                if pos is None:
                    blocked["too_small_to_afford"] += 1
                else:
                    cash -= pos.entry_price * 100 * pos.contracts + pos.entry_commission
                    open_pos.append(pos)
                    equity = cash + sum(mark(p, i) * 100 * p.contracts for p in open_pos)

        if open_pos:
            bars_in_market += 1
        curve.append((today, equity))

    # Close anything still open at the last mark so the result is fully realised.
    for pos in list(open_pos):
        exit_px = mark(pos, hi) - slip * len(pos.legs)
        close(pos, hi, exit_px, "end_of_test", fee * len(pos.legs) * pos.contracts)
    if curve:
        curve[-1] = (curve[-1][0], cash)

    return BacktestResult(
        label or strategy.title, strategy, risk, costs, entry_lag, trades, curve,
        rm.events, signals, blocked, rm.halted_bars, bars_in_market, rm.killed,
    )


def open_position(plan: Plan, i: int, today: date, equity: float, open_pos: List[Position],
                  rm: RiskManager, fee: float, slip: float) -> Optional[Position]:
    """Price the fill, set the exit thresholds and size the trade. None if it can't be afforded."""
    n_legs = len(plan.legs)
    entry = plan.mid + slip * n_legs  # pay up on every leg
    round_trip_fee = fee * n_legs * 2
    if plan.kind == "credit":
        credit = -entry
        if credit <= 0:
            return None
        loss_ps = plan.width - credit
        tp = plan.take_profit * credit
        sl = -min(plan.stop_loss * credit, loss_ps)
    else:
        debit = entry
        loss_ps = debit
        tp = plan.take_profit * (plan.width - debit)
        sl = -plan.stop_loss * debit
    per_contract = loss_ps * 100 + round_trip_fee
    open_risk = sum(p.max_loss for p in open_pos)
    n = rm.size(equity, per_contract, open_risk)
    if n <= 0:
        return None
    return Position(plan, i, today, n, entry, per_contract * n, tp, sl, equity, fee * n_legs * n)
