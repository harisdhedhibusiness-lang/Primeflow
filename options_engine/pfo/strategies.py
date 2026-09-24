"""Strategies. Each one is defined-risk: its maximum loss is known before the order is sent.

A strategy answers three questions:
  entry_signal  Is the regime gate open and has the setup fired on this bar?
  build         Which exact contracts would we trade on this bar?
  exit_signal   Has something other than the profit/stop/time rules invalidated the trade?

Profit target, stop, time exit and position size are enforced by the engine and the risk
manager, so no strategy can skip them.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Tuple

from .config import CeilingCallConfig, DipCallConfig, DipFloorPutConfig, IncomeCallConfig, PutSpreadConfig
from .pricing import OptionPricer, pick_expiry, years_to_expiry
from .regime import MarketFrame


@dataclass(frozen=True)
class Leg:
    strike: float
    is_call: bool
    qty: int  # +1 long, -1 short, per one spread

    def describe(self) -> str:
        side = "BUY " if self.qty > 0 else "SELL"
        kind = "call" if self.is_call else "put"
        return f"{side} {self.strike:g} {kind}"


@dataclass(frozen=True)
class Plan:
    strategy: str
    legs: Tuple[Leg, ...]
    expiry: date
    kind: str  # "credit" or "debit"
    width: float
    mid: float  # model value per share: positive = pay (debit), negative = receive (credit)
    take_profit: float
    stop_loss: float
    exit_dte: int
    max_hold_days: Optional[int] = None


def spread_value(legs, S: float, T: float, vol: float, pricer: OptionPricer) -> float:
    return sum(leg.qty * pricer.price(S, leg.strike, T, leg.is_call, vol) for leg in legs)


def spread_intrinsic(legs, S: float) -> float:
    return sum(
        leg.qty * (max(S - leg.strike, 0.0) if leg.is_call else max(leg.strike - S, 0.0))
        for leg in legs
    )


class Strategy:
    name = ""
    title = ""
    summary = ""

    def entry_signal(self, mf: MarketFrame, i: int) -> bool:
        raise NotImplementedError

    def build(self, mf: MarketFrame, i: int, pricer: OptionPricer) -> Optional[Plan]:
        raise NotImplementedError

    def exit_signal(self, mf: MarketFrame, i: int, position) -> Optional[str]:
        return None

    def exit_levels(self, mf: MarketFrame, i: int) -> str:
        """Human-readable non-price exit rule, for the daily signal."""
        return ""

    def live_plan(self, chain, today: date) -> Optional[dict]:
        """The same trade picked from live quotes instead of the model. None if unsupported."""
        return None


class TrendPutSpread(Strategy):
    name = "put_spread"
    title = "Trend Put Credit Spread"
    summary = (
        "Sell a ~20-delta SPY put spread (width ~1% of SPY) about 45 days out when the daily bias is bullish "
        "and volatility is not in shock. Take 50% of the credit, stop at 2x the credit, "
        "and close at 21 days to expiry or if the daily bias turns bearish."
    )

    def __init__(self, cfg: PutSpreadConfig = PutSpreadConfig()):
        self.cfg = cfg

    def entry_signal(self, mf: MarketFrame, i: int) -> bool:
        r = mf.regime[i]
        return r is not None and r.bias == "bull" and not r.risk_off and r.vix <= self.cfg.max_vix

    def build(self, mf: MarketFrame, i: int, pricer: OptionPricer) -> Optional[Plan]:
        c = self.cfg
        today, S = mf.dates[i], mf.close[i]
        expiry = pick_expiry(today, c.dte_target, c.dte_min, c.dte_max)
        vix = mf.term_vol(i, (expiry - today).days) if expiry else None
        if expiry is None or vix is None:
            return None
        T = years_to_expiry(today, expiry)
        step = pricer.cfg.strike_step
        width = c.width or max(step, round(S * c.width_pct / step) * step)
        short_k = pricer.strike_for_delta(S, T, vix, c.short_delta, is_call=False)
        legs = (Leg(short_k, False, -1), Leg(short_k - width, False, +1))
        mid = spread_value(legs, S, T, vix, pricer)
        if -mid < c.min_credit_frac * width:
            return None
        return Plan(self.name, legs, expiry, "credit", width, mid, c.take_profit, c.stop_loss, c.exit_dte)

    def exit_signal(self, mf: MarketFrame, i: int, position) -> Optional[str]:
        r = mf.regime[i]
        if self.cfg.exit_on_bear_bias and r is not None and r.bias == "bear":
            return "bias_turned_bear"
        return None

    def exit_levels(self, mf: MarketFrame, i: int) -> str:
        return "close early if the daily bias flips to BEAR"


class PullbackCallSpread(Strategy):
    name = "dip_call"
    title = "Pullback Call Debit Spread"
    summary = (
        "Buy a 60/30-delta SPY call spread about 14 days out when SPY is in a bullish daily "
        "bias and RSI(2) drops below 10. Exit when SPY closes back above its 5-day average, "
        "at 75% of max profit, at a 50% loss, or after 7 trading days."
    )

    def __init__(self, cfg: DipCallConfig = DipCallConfig()):
        self.cfg = cfg

    def entry_signal(self, mf: MarketFrame, i: int) -> bool:
        r = mf.regime[i]
        osc = mf.rsi(self.cfg.rsi_length)[i]
        return (
            r is not None
            and r.bias == "bull"
            and r.vol != "stressed"
            and osc is not None
            and osc < self.cfg.rsi_entry
        )

    def build(self, mf: MarketFrame, i: int, pricer: OptionPricer) -> Optional[Plan]:
        c = self.cfg
        today, S = mf.dates[i], mf.close[i]
        expiry = pick_expiry(today, c.dte_target, c.dte_min, c.dte_max)
        vix = mf.term_vol(i, (expiry - today).days) if expiry else None
        if expiry is None or vix is None:
            return None
        T = years_to_expiry(today, expiry)
        long_k = pricer.strike_for_delta(S, T, vix, c.long_delta, is_call=True)
        if c.width:
            short_k = long_k + c.width
        else:
            short_k = max(pricer.strike_for_delta(S, T, vix, c.short_delta, is_call=True),
                          long_k + pricer.cfg.strike_step)
        width = short_k - long_k
        legs = (Leg(long_k, True, +1), Leg(short_k, True, -1))
        mid = spread_value(legs, S, T, vix, pricer)
        if mid <= 0 or mid > c.max_debit_frac * width:
            return None
        return Plan(
            self.name, legs, expiry, "debit", width, mid,
            c.take_profit, c.stop_loss, c.exit_dte, c.max_hold_days,
        )

    def exit_signal(self, mf: MarketFrame, i: int, position) -> Optional[str]:
        avg = mf.sma(self.cfg.exit_sma)[i]
        if avg is not None and mf.close[i] > avg:
            return "bounce_complete"
        return None

    def exit_levels(self, mf: MarketFrame, i: int) -> str:
        avg = mf.sma(self.cfg.exit_sma)[i]
        level = f" (currently {avg:.2f})" if avg is not None else ""
        return f"close when SPY closes above its {self.cfg.exit_sma}-day average{level}"


class _FarCreditSpread(Strategy):
    """Sell a far out-of-the-money option, buy one further out, hold to expiry."""

    is_call = False

    def build(self, mf: MarketFrame, i: int, pricer: OptionPricer) -> Optional[Plan]:
        c = self.cfg
        today, S = mf.dates[i], mf.close[i]
        expiry = pick_expiry(today, c.dte_target, c.dte_min, c.dte_max)
        vol = mf.term_vol(i, (expiry - today).days) if expiry else None
        if expiry is None or vol is None:
            return None
        T = years_to_expiry(today, expiry)
        short_k = pricer.strike_for_delta(S, T, vol, c.short_delta, is_call=self.is_call)
        far_k = short_k + c.width if self.is_call else short_k - c.width
        legs = (Leg(short_k, self.is_call, -1), Leg(far_k, self.is_call, +1))
        mid = spread_value(legs, S, T, vol, pricer)
        if -mid < c.min_credit:
            return None
        # Hold to expiry: no profit target; without a stop, the spread's own maximum loss is the stop.
        return Plan(self.name, legs, expiry, "credit", c.width, mid, 1.0, c.stop_multiple or 1e9, 0)

    def live_plan(self, chain, today: date) -> Optional[dict]:
        c = self.cfg
        expiry = chain.nearest_expiry(today, c.dte_target, c.dte_min, c.dte_max, self.is_call)
        if expiry is None:
            return None
        short = chain.by_delta(expiry, self.is_call, c.short_delta)
        if short is None:
            return None
        far = chain.get(expiry, self.is_call, short.strike + (c.width if self.is_call else -c.width))
        if far is None:
            return None
        return {"expiry": expiry, "short": short, "long": far, "width": c.width,
                "mid": short.mid - far.mid, "natural": short.bid - far.ask, "min_credit": c.min_credit,
                "stop_multiple": c.stop_multiple, "account_fraction": getattr(c, "account_fraction", None)}


class CeilingCallSpread(_FarCreditSpread):
    name = "ceiling_call"
    title = "Ceiling Call Spread"
    summary = (
        "Every day it isn't already in a trade: sell the ~3-delta SPY call about 45 days out "
        "(roughly 8-10% above the market) and buy the call $5 higher. Hold to expiry. It loses "
        "only if SPY rallies through the sold strike by expiry."
    )
    is_call = True

    def __init__(self, cfg: CeilingCallConfig = CeilingCallConfig()):
        self.cfg = cfg

    def entry_signal(self, mf: MarketFrame, i: int) -> bool:
        return mf.regime[i] is not None


class DipFloorPutSpread(_FarCreditSpread):
    name = "dip_put"
    title = "Dip Floor Put Spread"
    summary = (
        "When the daily bias is bullish and RSI(2) closes under 10 (a sharp dip in an uptrend): "
        "sell the ~5-delta SPY put about 14 days out and buy the put $5 lower. Hold to expiry. "
        "It loses only if SPY keeps falling through the sold strike by expiry."
    )
    is_call = False

    def __init__(self, cfg: DipFloorPutConfig = DipFloorPutConfig()):
        self.cfg = cfg

    def entry_signal(self, mf: MarketFrame, i: int) -> bool:
        r = mf.regime[i]
        osc = mf.rsi(self.cfg.rsi_length)[i]
        return r is not None and r.bias == "bull" and osc is not None and osc < self.cfg.rsi_entry


class IncomeCallSpread(_FarCreditSpread):
    name = "income_call"
    title = "Income Call Spread"
    summary = (
        "Every day it isn't already in a trade: sell the ~3-delta SPY call about 14 days out and "
        "buy the call $20 higher. Close it if the loss reaches 2x the credit; otherwise hold to "
        "expiry. Sized so its worst historical drop stayed under 10%."
    )
    is_call = True

    def __init__(self, cfg: IncomeCallConfig = IncomeCallConfig()):
        self.cfg = cfg

    def entry_signal(self, mf: MarketFrame, i: int) -> bool:
        return mf.regime[i] is not None


STRATEGIES = {s.name: s for s in (TrendPutSpread, PullbackCallSpread, CeilingCallSpread, DipFloorPutSpread,
                                  IncomeCallSpread)}


def make_strategy(name: str) -> Strategy:
    if name not in STRATEGIES:
        raise KeyError(f"unknown strategy {name!r}; choose from {', '.join(STRATEGIES)}")
    return STRATEGIES[name]()
