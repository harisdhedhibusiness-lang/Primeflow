"""Win-rate search over SPY calls and puts: buying one, selling one, or selling a two-option
spread (a short option with a further out-of-the-money long option bought to cap the loss).

Each rule is one entry signal plus one way of trading and getting out. Trades never overlap
(one open at a time), so every trade is a separate observation.

How the numbers are kept honest:
  * Every historical trade is replayed at today's SPY price level (see `scale_to`), so the
    dollar premiums, fees and what a small account can afford are today's, not 2005's.
  * Every trade pays commission and slippage on the way in and out.
  * Options are priced off the Cboe volatility index for their own tenor (VIX9D / VIX /
    VIX3M), with at-the-money level and skew fitted to a real Cboe SPY chain.
  * Take-profit limit orders may fill intraday. They are checked at the day's high or low,
    priced with whichever of yesterday's and today's volatility is *worse* for the trade.
  * Stops are checked at the close only, so gaps fill at the gap price.
  * A rule needs at least MIN_TRADES trades to be ranked at all.
  * Rules can be picked on one stretch of data and judged on a later one they never saw.
  * `every_entry_day` re-runs a rule from every day its signal fired, so it cannot hide a
    bad day behind the timing luck of one path.
"""

from __future__ import annotations

import itertools
import math
import os
from dataclasses import dataclass
from datetime import date
from functools import lru_cache
from multiprocessing import Pool
from typing import Dict, List, Optional, Sequence, Tuple

from .config import CostConfig, PricingConfig
from .data import load_market
from .pricing import OptionPricer, pick_expiry
from .regime import MarketFrame

MIN_TRADES = 30
MIN_PREMIUM = 0.05  # options or spreads quoted under 5 cents are skipped as untradeable

BULLISH = ("every_day", "bull_trend", "bull_calm", "dip_in_bull", "deep_oversold")
BEARISH = ("every_day", "bear_trend", "bear_stressed", "rip_in_bear", "deep_overbought")


@dataclass(frozen=True)
class Rule:
    action: str  # "buy", "sell" (one option) or "spread" (sell a credit spread)
    right: str  # "call" or "put"
    signal: str
    dte: int
    delta: float  # the bought option for "buy", the sold option otherwise
    take_profit: float  # buy: gain as a fraction of premium; sell/spread: fraction of credit kept (1 = hold)
    stop_loss: Optional[float]  # buy: loss as a fraction of premium; sell/spread: loss as a multiple of credit
    max_hold: int  # trading days
    width: float = 0.0  # spread width in dollars at today's SPY price

    def describe(self) -> str:
        if self.action == "buy":
            what = f"BUY a {self.delta:.2f}-delta {self.right}"
            tp = f"take +{self.take_profit:.0%}"
            sl = f"stop -{self.stop_loss:.0%}" if self.stop_loss else "no stop"
        else:
            what = (f"SELL a {self.delta:.2f}-delta {self.right}" if self.action == "sell" else
                    f"SELL a {self.delta:.2f}-delta {self.right}, BUY the {self.right} ${self.width:g} further out")
            tp = "hold to expiry" if self.take_profit >= 1 else f"take {self.take_profit:.0%} of credit"
            sl = f"stop at {self.stop_loss:g}x credit" if self.stop_loss else "no stop"
        hold = "" if self.max_hold >= 999 else f", max {self.max_hold} days"
        return f"{what}, ~{self.dte} DTE, on {self.signal.replace('_', ' ')}; {tp}, {sl}{hold}"


def grid() -> List[Rule]:
    rules: List[Rule] = []
    for right, signals in (("call", BULLISH), ("put", BEARISH)):
        for sig, dte, delta, tp, sl, hold in itertools.product(
            signals, (7, 14, 30, 60), (0.10, 0.20, 0.30, 0.50, 0.70),
            (0.05, 0.10, 0.25, 0.50, 1.00), (None, 0.25, 0.50), (3, 10, 20),
        ):
            rules.append(Rule("buy", right, sig, dte, delta, tp, sl, hold))
    # Selling: bullish signals sell puts, bearish signals sell calls.
    for right, signals in (("put", BULLISH), ("call", BEARISH)):
        for sig, dte, delta, tp, sl, hold in itertools.product(
            signals, (7, 14, 30, 45), (0.03, 0.05, 0.10, 0.16),
            (0.50, 0.80, 1.00), (None, 1.0, 2.0, 3.0), (5, 20, 999),
        ):
            rules.append(Rule("sell", right, sig, dte, delta, tp, sl, hold))
        for sig, dte, delta, width, tp, sl in itertools.product(
            signals, (7, 14, 30, 45), (0.03, 0.05, 0.10, 0.16, 0.25), (1.0, 2.0, 3.0, 5.0),
            (0.50, 0.80, 1.00), (None, 1.0, 2.0, 3.0),
        ):
            rules.append(Rule("spread", right, sig, dte, delta, tp, sl, 999, width))
    return rules


# ---------------------------------------------------------------- simulation state

_S: Dict[str, object] = {}


def _init(data_dir: str, pricing: PricingConfig, costs: CostConfig, scale_to: Optional[float] = None,
          start: Optional[date] = None) -> None:
    mf = MarketFrame(load_market(data_dir))
    rsi2 = mf.rsi(2)
    reg = mf.regime
    sig: Dict[str, List[bool]] = {
        "every_day": [r is not None for r in reg],
        "bull_trend": [r is not None and r.bias == "bull" for r in reg],
        "bull_calm": [r is not None and r.bias == "bull" and not r.risk_off for r in reg],
        "dip_in_bull": [r is not None and r.bias == "bull" and (rsi2[i] or 50) < 10 for i, r in enumerate(reg)],
        "deep_oversold": [r is not None and (rsi2[i] or 50) < 5 for i, r in enumerate(reg)],
        "bear_trend": [r is not None and r.bias == "bear" for r in reg],
        "bear_stressed": [r is not None and r.bias == "bear" and r.vol != "calm" for r in reg],
        "rip_in_bear": [r is not None and r.bias == "bear" and (rsi2[i] or 50) > 90 for i, r in enumerate(reg)],
        "deep_overbought": [r is not None and (rsi2[i] or 50) > 95 for i, r in enumerate(reg)],
    }
    first = next(i for i, r in enumerate(reg) if r is not None)
    lo = max(first, mf.index_of(start)) if start else first
    # A trade needs today's VIX and yesterday's (for the intraday take-profit check).
    valid = [mf.vix[i] is not None and i > 0 and mf.vix[i - 1] is not None for i in range(len(mf))]
    for k in sig:
        sig[k] = [s and valid[i] for i, s in enumerate(sig[k])]
    _S.update(mf=mf, sig=sig, pricer=OptionPricer(pricing), costs=costs, lo=lo,
              scale_to=scale_to or mf.close[-1],
              high=[b.high for b in mf.bars], low=[b.low for b in mf.bars])
    _price.cache_clear()
    _entry.cache_clear()


@lru_cache(maxsize=4_000_000)
def _price(S: float, K: float, days: int, is_call: bool, vol: float) -> float:
    return _S["pricer"].price(S, K, days / 365.0, is_call, vol)


@lru_cache(maxsize=400_000)
def _entry(i: int, dte: int, delta: float, is_call: bool) -> Optional[Tuple[date, float, float]]:
    """(expiry, strike on today's-scale $1 grid, scale factor) for an entry on bar i."""
    mf: MarketFrame = _S["mf"]
    today = mf.dates[i]
    expiry = pick_expiry(today, dte, max(1, int(dte * 0.7)), int(dte * 1.3) + 3)
    if expiry is None:
        return None
    f = _S["scale_to"] / mf.close[i]
    days = (expiry - today).days
    vol = mf.term_vol(i, days)
    return expiry, _S["pricer"].strike_for_delta(mf.close[i] * f, days / 365.0, vol, delta, is_call), f


@dataclass
class TradeRec:
    entry: date
    exit: date
    cost: float  # dollars: premium paid (buy), or credit received (sell/spread)
    pnl: float  # dollars per contract after fees
    reason: str
    capital: float  # dollars an account must set aside for one contract (inf = uncovered)


def _legs(rule: Rule, K: float) -> Tuple[Tuple[float, int], ...]:
    if rule.action == "buy":
        return ((K, +1),)
    if rule.action == "sell":
        return ((K, -1),)
    far = K - rule.width if rule.right == "put" else K + rule.width
    return ((K, -1), (far, +1))


def simulate(rule: Rule, lo: int, hi: int, max_trades: Optional[int] = None) -> List[TradeRec]:
    mf: MarketFrame = _S["mf"]
    sig = _S["sig"][rule.signal]
    costs: CostConfig = _S["costs"]
    slip, fee = costs.slippage_per_leg, costs.commission_per_contract
    highs, lows, close, tv = _S["high"], _S["low"], mf.close, mf.term_vol
    is_call, buy = rule.right == "call", rule.action == "buy"
    hold_to_expiry = not buy and rule.take_profit >= 1
    # Which intraday extreme helps the position: rising prices help long calls and short puts.
    up_is_good = is_call == buy
    out: List[TradeRec] = []
    i = lo
    while i < hi:
        if not sig[i]:
            i += 1
            continue
        got = _entry(i, rule.dte, rule.delta, is_call)
        if got is None:
            i += 1
            continue
        expiry, K, f = got
        legs = _legs(rule, K)
        n_legs = len(legs)

        def value(S: float, days: int, v: float) -> float:
            return sum(q * _price(S, k, days, is_call, v) for k, q in legs)

        d0 = (expiry - mf.dates[i]).days
        v0 = value(close[i] * f, d0, tv(i, d0))
        if abs(v0) < MIN_PREMIUM:
            i += 1
            continue
        cost = v0 + slip * n_legs  # per share: positive = paid, negative = received
        if (buy and cost <= 0) or (not buy and cost >= 0):
            i += 1
            continue
        basis = abs(cost)
        target = rule.take_profit * basis
        stop = rule.stop_loss * basis if rule.stop_loss else None
        exit_px, reason, j = None, "", i
        for j in range(i + 1, hi + 1):
            d = mf.dates[j]
            if d >= expiry:
                S = (close[j] if d == expiry else close[j - 1]) * f
                exit_px = sum(q * (max(S - k, 0.0) if is_call else max(k - S, 0.0)) for k, q in legs)
                reason = "expired"
                break
            days = (expiry - d).days
            if not hold_to_expiry:
                best_S = (highs[j] if up_is_good else lows[j]) * f
                # Long options gain from higher vol, short ones lose: take the worse of two closes.
                v_worse = min(tv(j, days), tv(j - 1, days)) if buy else max(tv(j, days), tv(j - 1, days))
                if value(best_S, days, v_worse) - slip * n_legs - cost >= target:
                    exit_px, reason = cost + target, "take_profit"
                    break
            px = value(close[j] * f, days, tv(j, days)) - slip * n_legs
            if stop is not None and px - cost <= -stop:
                exit_px, reason = px, "stop_loss"
                break
            if j - i >= rule.max_hold or (days <= 1 and not hold_to_expiry):
                exit_px, reason = px, "time_exit"
                break
        if exit_px is None:
            break  # ran out of data with the trade open; drop it
        worthless = reason == "expired" and abs(exit_px) < 1e-9
        fees = fee * n_legs * (1 if worthless else 2)
        if buy:
            capital = cost * 100 + fee * n_legs * 2
        elif rule.action == "spread":
            capital = (rule.width - basis) * 100 + fee * n_legs * 2
        else:
            capital = math.inf
        out.append(TradeRec(mf.dates[i], mf.dates[j], basis * 100, (exit_px - cost) * 100 - fees, reason, capital))
        if max_trades and len(out) >= max_trades:
            break
        i = j
    return out


def _tradeable_entry(i: int, rule: Rule) -> bool:
    if not _S["sig"][rule.signal][i]:
        return False
    got = _entry(i, rule.dte, rule.delta, rule.right == "call")
    if got is None:
        return False
    mf: MarketFrame = _S["mf"]
    expiry, K, f = got
    days = (expiry - mf.dates[i]).days
    vol = mf.term_vol(i, days)
    v0 = sum(q * _price(mf.close[i] * f, k, days, rule.right == "call", vol) for k, q in _legs(rule, K))
    return abs(v0) >= MIN_PREMIUM


def every_entry_day(rule: Rule, lo: int, hi: int) -> List[TradeRec]:
    """One independent trade from every day the signal fired, not just one non-overlapping path.

    A non-overlapping backtest walks a single path of entry dates and can dodge the bad days
    by luck of timing. This asks the real question: of all the days you could have entered,
    how many would have won?
    """
    out: List[TradeRec] = []
    for i in range(lo, hi):
        if _tradeable_entry(i, rule):
            out.extend(simulate(rule, i, hi, max_trades=1))
    return out


def account_paths(rule: Rule, lo: int, hi: int, equity: float, years: int, step: int = 5) -> Dict[str, float]:
    """Start a small account on every `step`-th day and trade the rule for `years`, one contract.

    An account that can't cover the next trade's capital is stuck: in real life it can no
    longer place the trade.
    """
    span = 252 * years
    ends, stuck = [], 0
    for s in range(lo, hi - span, step):
        eq = equity
        for t in simulate(rule, s, s + span):
            if eq < t.capital:
                stuck += 1
                break
            eq += t.pnl
        ends.append(eq)
    if not ends:
        return {}
    ends.sort()
    return {
        "starts": len(ends),
        "median": ends[len(ends) // 2],
        "worst": ends[0],
        "best": ends[-1],
        "below_start": sum(e < equity for e in ends) / len(ends),
        "stuck": stuck / len(ends),
    }


def summarize(trades: Sequence[TradeRec]) -> Dict[str, float]:
    n = len(trades)
    if not n:
        return {"n": 0, "win_rate": 0.0, "total": 0.0, "avg": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "worst": 0.0, "pf": 0.0, "max_dd": 0.0, "avg_cost": 0.0, "max_capital": 0.0}
    wins = [t.pnl for t in trades if t.pnl > 0]
    losses = [t.pnl for t in trades if t.pnl <= 0]
    eq = peak = dd = 0.0
    for t in trades:
        eq += t.pnl
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    gl = -sum(losses)
    return {
        "n": n,
        "win_rate": len(wins) / n,
        "total": sum(t.pnl for t in trades),
        "avg": sum(t.pnl for t in trades) / n,
        "avg_win": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss": -gl / len(losses) if losses else 0.0,
        "worst": min(t.pnl for t in trades),
        "pf": sum(wins) / gl if gl > 0 else math.inf,
        "max_dd": dd,
        "avg_cost": sum(t.cost for t in trades) / n,
        "max_capital": max(t.capital for t in trades),
    }


def _eval(args) -> Tuple[Rule, Dict[str, float], Dict[str, float], Dict[str, float]]:
    rule, (lo, mid, hi) = args
    full = simulate(rule, lo, hi)
    cut = _S["mf"].dates[mid]
    return (rule, summarize(full), summarize([t for t in full if t.entry < cut]),
            summarize([t for t in full if t.entry >= cut]))


def run_search(data_dir: str, split: Optional[date] = None, processes: Optional[int] = None,
               rules: Optional[List[Rule]] = None, pricing: PricingConfig = PricingConfig(),
               costs: CostConfig = CostConfig(), start: Optional[date] = None,
               scale_to: Optional[float] = None):
    """Evaluate every rule. Returns (results, window) where window = (start, split, end)."""
    init_args = (data_dir, pricing, costs, scale_to, start)
    _init(*init_args)
    mf: MarketFrame = _S["mf"]
    lo, hi = _S["lo"], len(mf) - 1
    mid = mf.index_of(split) if split else lo + int((hi - lo) * 0.6)
    rules = rules or grid()
    jobs = [(r, (lo, mid, hi)) for r in rules]
    procs = processes or os.cpu_count() or 1
    if procs > 1:
        with Pool(procs, initializer=_init, initargs=init_args) as pool:
            results = pool.map(_eval, jobs, chunksize=16)
    else:
        results = [_eval(j) for j in jobs]
    return results, (mf.dates[lo], mf.dates[mid], mf.dates[hi])
