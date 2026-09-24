"""Win-rate search: single-leg SPY calls and puts, thousands of rule combinations.

Each rule is one entry signal plus one way of buying or selling one option and getting out.
Trades never overlap (one open at a time), so every trade is a separate observation.

How honest numbers are kept honest:
  * Every trade pays commission and slippage on the way in and out.
  * Take-profit limit orders may fill intraday. They are checked at the day's high or low,
    priced with whichever of yesterday's and today's VIX is *worse* for the trade.
  * Stops are checked at the close only, so gaps fill at the gap price.
  * A rule needs at least MIN_TRADES trades to be ranked at all.
  * Rules are picked on the first part of the data (in-sample) and then judged on a later
    stretch they never saw (out-of-sample). A win rate that only exists in-sample is luck.
"""

from __future__ import annotations

import itertools
import math
import os
from dataclasses import asdict, dataclass
from datetime import date
from functools import lru_cache
from multiprocessing import Pool
from typing import Dict, List, Optional, Sequence, Tuple

from .config import CostConfig, PricingConfig
from .data import load_market
from .pricing import OptionPricer, pick_expiry
from .regime import MarketFrame

MIN_TRADES = 30
MIN_PREMIUM = 0.05  # options quoted under 5 cents are skipped as untradeable

BULLISH = ("every_day", "bull_trend", "bull_calm", "dip_in_bull", "deep_oversold")
BEARISH = ("every_day", "bear_trend", "bear_stressed", "rip_in_bear", "deep_overbought")


@dataclass(frozen=True)
class Rule:
    action: str  # "buy" or "sell"
    right: str  # "call" or "put"
    signal: str
    dte: int
    delta: float
    take_profit: float  # buy: gain as a fraction of premium; sell: fraction of premium kept
    stop_loss: Optional[float]  # buy: loss as a fraction of premium; sell: loss as a multiple of premium
    max_hold: int  # trading days

    def describe(self) -> str:
        if self.action == "buy":
            tp = f"take +{self.take_profit:.0%}"
            sl = f"stop -{self.stop_loss:.0%}" if self.stop_loss else "no stop"
        else:
            tp = "hold to expiry" if self.take_profit >= 1 else f"take {self.take_profit:.0%} of premium"
            sl = f"stop at {self.stop_loss:g}x premium" if self.stop_loss else "no stop"
        hold = "" if self.max_hold >= 999 else f", max {self.max_hold} days"
        return (f"{self.action.upper()} {self.delta:.2f}-delta {self.right}, ~{self.dte} DTE, "
                f"on {self.signal.replace('_', ' ')}; {tp}, {sl}{hold}")


def grid() -> List[Rule]:
    rules: List[Rule] = []
    for right, signals in (("call", BULLISH), ("put", BEARISH)):
        for sig, dte, delta, tp, sl, hold in itertools.product(
            signals, (7, 14, 30, 60), (0.30, 0.50, 0.70, 0.90),
            (0.05, 0.10, 0.25, 0.50, 1.00), (None, 0.25, 0.50), (3, 10, 20),
        ):
            rules.append(Rule("buy", right, sig, dte, delta, tp, sl, hold))
    # Selling: bullish signals sell puts, bearish signals sell calls.
    for right, signals in (("put", BULLISH), ("call", BEARISH)):
        for sig, dte, delta, tp, sl, hold in itertools.product(
            signals, (7, 14, 30, 45), (0.05, 0.10, 0.16, 0.30),
            (0.50, 0.80, 1.00), (None, 1.0, 2.0, 3.0), (5, 20, 999),
        ):
            rules.append(Rule("sell", right, sig, dte, delta, tp, sl, hold))
    return rules


# ---------------------------------------------------------------- simulation state

_S: Dict[str, object] = {}


def _init(data_dir: str, pricing: PricingConfig, costs: CostConfig) -> None:
    mf = MarketFrame(load_market(data_dir))
    rsi2 = mf.rsi(2)
    sig: Dict[str, List[bool]] = {}
    reg = mf.regime
    sig["every_day"] = [r is not None for r in reg]
    sig["bull_trend"] = [r is not None and r.bias == "bull" for r in reg]
    sig["bull_calm"] = [r is not None and r.bias == "bull" and not r.risk_off for r in reg]
    sig["dip_in_bull"] = [r is not None and r.bias == "bull" and (rsi2[i] or 50) < 10 for i, r in enumerate(reg)]
    sig["deep_oversold"] = [r is not None and (rsi2[i] or 50) < 5 for i, r in enumerate(reg)]
    sig["bear_trend"] = [r is not None and r.bias == "bear" for r in reg]
    sig["bear_stressed"] = [r is not None and r.bias == "bear" and r.vol != "calm" for r in reg]
    sig["rip_in_bear"] = [r is not None and r.bias == "bear" and (rsi2[i] or 50) > 90 for i, r in enumerate(reg)]
    sig["deep_overbought"] = [r is not None and (rsi2[i] or 50) > 95 for i, r in enumerate(reg)]
    _S.update(mf=mf, sig=sig, pricer=OptionPricer(pricing), costs=costs,
              high=[b.high for b in mf.bars], low=[b.low for b in mf.bars])
    _price.cache_clear()
    _entry.cache_clear()


@lru_cache(maxsize=2_000_000)
def _price(S: float, K: float, days: int, is_call: bool, vix: float) -> float:
    return _S["pricer"].price(S, K, days / 365.0, is_call, vix)


@lru_cache(maxsize=200_000)
def _entry(i: int, dte: int, delta: float, is_call: bool) -> Optional[Tuple[date, float]]:
    mf: MarketFrame = _S["mf"]
    today = mf.dates[i]
    expiry = pick_expiry(today, dte, max(1, int(dte * 0.7)), int(dte * 1.3) + 3)
    if expiry is None:
        return None
    T = (expiry - today).days / 365.0
    return expiry, _S["pricer"].strike_for_delta(mf.close[i], T, mf.vix[i], delta, is_call)


@dataclass
class TradeRec:
    entry: date
    exit: date
    premium: float
    pnl: float
    reason: str


def simulate(rule: Rule, lo: int, hi: int, max_trades: Optional[int] = None) -> List[TradeRec]:
    mf: MarketFrame = _S["mf"]
    sig = _S["sig"][rule.signal]
    costs: CostConfig = _S["costs"]
    slip, fee = costs.slippage_per_leg, costs.commission_per_contract
    highs, lows = _S["high"], _S["low"]
    is_call, buy = rule.right == "call", rule.action == "buy"
    hold_to_expiry = not buy and rule.take_profit >= 1
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
        expiry, K = got
        mid = _price(mf.close[i], K, (expiry - mf.dates[i]).days, is_call, mf.vix[i])
        if mid < MIN_PREMIUM:
            i += 1
            continue
        entry = mid + slip if buy else mid - slip
        if entry <= 0:
            i += 1
            continue
        exit_px, reason, j = None, "", i
        for j in range(i + 1, hi + 1):
            d = mf.dates[j]
            if d >= expiry:
                S = mf.close[j] if d == expiry else mf.close[j - 1]
                exit_px, reason = (max(S - K, 0.0) if is_call else max(K - S, 0.0)), "expired"
                break
            days = (expiry - d).days
            # Resting take-profit limit: filled if the day's best price reached it.
            if buy:
                best_S = highs[j] if is_call else lows[j]
                best_vix = min(mf.vix[j], mf.vix[j - 1] or mf.vix[j])
                target = entry * (1 + rule.take_profit)
                if _price(best_S, K, days, is_call, best_vix) - slip >= target:
                    exit_px, reason = target, "take_profit"
                    break
            elif rule.take_profit < 1:
                best_S = lows[j] if is_call else highs[j]
                best_vix = max(mf.vix[j], mf.vix[j - 1] or mf.vix[j])
                target = entry * (1 - rule.take_profit)
                if _price(best_S, K, days, is_call, best_vix) + slip <= target:
                    exit_px, reason = target, "take_profit"
                    break
            v = _price(mf.close[j], K, days, is_call, mf.vix[j])
            if buy:
                px = v - slip
                if rule.stop_loss and px <= entry * (1 - rule.stop_loss):
                    exit_px, reason = px, "stop_loss"
                    break
            else:
                px = v + slip
                if rule.stop_loss and px - entry >= rule.stop_loss * entry:
                    exit_px, reason = px, "stop_loss"
                    break
            if j - i >= rule.max_hold or (days <= 1 and not hold_to_expiry):
                exit_px, reason = px, "time_exit"
                break
        if exit_px is None:
            break  # ran out of data with the trade open; drop it
        closing_fee = 0.0 if reason == "expired" and exit_px == 0 else fee
        gross = (exit_px - entry) if buy else (entry - exit_px)
        out.append(TradeRec(mf.dates[i], mf.dates[j], entry, gross * 100 - fee - closing_fee, reason))
        if max_trades and len(out) >= max_trades:
            break
        i = j
    return out


def every_entry_day(rule: Rule, lo: int, hi: int) -> List[TradeRec]:
    """One independent trade from every day the signal fired, not just one non-overlapping sequence.

    A non-overlapping backtest walks a single path of entry dates and can dodge the bad days
    by luck of timing. This asks the real question: of all the days you could have entered,
    how many would have won?
    """
    sig = _S["sig"][rule.signal]
    out: List[TradeRec] = []
    for i in range(lo, hi):
        if sig[i]:
            out.extend(simulate(rule, i, hi, max_trades=1)[:1] if _first_is(i, rule) else [])
    return out


def _first_is(i: int, rule: Rule) -> bool:
    got = _entry(i, rule.dte, rule.delta, rule.right == "call")
    if got is None:
        return False
    mf: MarketFrame = _S["mf"]
    expiry, K = got
    return _price(mf.close[i], K, (expiry - mf.dates[i]).days, rule.right == "call", mf.vix[i]) >= MIN_PREMIUM


def summarize(trades: Sequence[TradeRec]) -> Dict[str, float]:
    n = len(trades)
    if not n:
        return {"n": 0, "win_rate": 0.0, "total": 0.0, "avg": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "worst": 0.0, "pf": 0.0, "max_dd": 0.0, "avg_premium": 0.0}
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
        "avg_premium": sum(t.premium for t in trades) / n * 100,
    }


def _eval(args) -> Tuple[Rule, Dict[str, float], Dict[str, float], Dict[str, float]]:
    rule, (lo, mid, hi) = args
    full = simulate(rule, lo, hi)
    ins = [t for t in full if t.entry < _S["mf"].dates[mid]]
    oos = [t for t in full if t.entry >= _S["mf"].dates[mid]]
    return rule, summarize(full), summarize(ins), summarize(oos)


def run_search(data_dir: str, split: Optional[date] = None, processes: Optional[int] = None,
               rules: Optional[List[Rule]] = None, pricing: PricingConfig = PricingConfig(),
               costs: CostConfig = CostConfig()):
    """Evaluate every rule. Returns (results, window) where window = (start, split, end)."""
    _init(data_dir, pricing, costs)
    mf: MarketFrame = _S["mf"]
    lo = next(i for i, r in enumerate(mf.regime) if r is not None)
    hi = len(mf) - 1
    mid = mf.index_of(split) if split else lo + int((hi - lo) * 0.6)
    rules = rules or grid()
    jobs = [(r, (lo, mid, hi)) for r in rules]
    procs = processes or os.cpu_count() or 1
    if procs > 1:
        with Pool(procs, initializer=_init, initargs=(data_dir, pricing, costs)) as pool:
            results = pool.map(_eval, jobs, chunksize=32)
    else:
        results = [_eval(j) for j in jobs]
    return results, (mf.dates[lo], mf.dates[mid], mf.dates[hi])


def rule_to_dict(rule: Rule) -> Dict[str, object]:
    return asdict(rule)
