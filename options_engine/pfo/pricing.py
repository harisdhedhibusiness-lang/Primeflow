"""Option pricing for the backtest.

There is no free source of historical SPY option quotes, so the backtest prices every
option with Black-Scholes using an implied-vol surface built from that day's VIX:

    ATM IV  = atm_iv_ratio x VIX
    IV(K)   = ATM IV x (1 - skew x z),  z = ln(K / F) / (ATM IV x sqrt(T))

That captures the three things that drive these trades (price moves, volatility moves and
put skew) but it is a model, not a record of real fills. See README "What the backtest
can and cannot tell you".
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from typing import List, Optional

from .config import WEEKLY_EXPIRY_START, PricingConfig

_WEEKLY_START = date.fromisoformat(WEEKLY_EXPIRY_START)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_price(S: float, K: float, T: float, r: float, q: float, sigma: float, is_call: bool) -> float:
    if T <= 0 or sigma <= 0:
        return max(S - K, 0.0) if is_call else max(K - S, 0.0)
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t
    if is_call:
        return S * math.exp(-q * T) * norm_cdf(d1) - K * math.exp(-r * T) * norm_cdf(d2)
    return K * math.exp(-r * T) * norm_cdf(-d2) - S * math.exp(-q * T) * norm_cdf(-d1)


def bs_delta(S: float, K: float, T: float, r: float, q: float, sigma: float, is_call: bool) -> float:
    if T <= 0 or sigma <= 0:
        if is_call:
            return 1.0 if S > K else 0.0
        return -1.0 if S < K else 0.0
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T))
    if is_call:
        return math.exp(-q * T) * norm_cdf(d1)
    return -math.exp(-q * T) * norm_cdf(-d1)


class OptionPricer:
    def __init__(self, cfg: PricingConfig = PricingConfig()):
        self.cfg = cfg

    def iv(self, S: float, K: float, T: float, vix: float) -> float:
        c = self.cfg
        atm = max(vix, 1.0) / 100.0 * c.atm_iv_ratio
        if T <= 0:
            return atm
        forward = S * math.exp((c.risk_free - c.dividend_yield) * T)
        z = math.log(K / forward) / (atm * math.sqrt(T))
        sigma = atm * (1.0 - c.skew * z)
        return min(max(sigma, atm * c.iv_floor_ratio), atm * c.iv_cap_ratio)

    def price(self, S: float, K: float, T: float, is_call: bool, vix: float) -> float:
        c = self.cfg
        return bs_price(S, K, T, c.risk_free, c.dividend_yield, self.iv(S, K, T, vix), is_call)

    def delta(self, S: float, K: float, T: float, is_call: bool, vix: float) -> float:
        c = self.cfg
        return bs_delta(S, K, T, c.risk_free, c.dividend_yield, self.iv(S, K, T, vix), is_call)

    def strike_for_delta(self, S: float, T: float, vix: float, target: float, is_call: bool) -> float:
        """Strike on the listed grid whose |delta| is closest to `target`."""
        step = self.cfg.strike_step
        center = round(S / step) * step
        span = int(0.35 * S / step)
        best_k, best_err = center, float("inf")
        for j in range(-span, span + 1):
            k = center + j * step
            if k <= 0:
                continue
            err = abs(abs(self.delta(S, k, T, is_call, vix)) - target)
            if err < best_err:
                best_k, best_err = k, err
        return best_k


def years_to_expiry(today: date, expiry: date) -> float:
    return max((expiry - today).days, 0) / 365.0


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    offset = (4 - first.weekday()) % 7
    return first + timedelta(days=offset + 14)


def is_listed_expiry(d: date) -> bool:
    if d.weekday() != 4:
        return False
    return d >= _WEEKLY_START or d == third_friday(d.year, d.month)


def pick_expiry(today: date, target_dte: int, min_dte: int, max_dte: int) -> Optional[date]:
    candidates: List[date] = [
        today + timedelta(days=n)
        for n in range(min_dte, max_dte + 1)
        if is_listed_expiry(today + timedelta(days=n))
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda e: abs((e - today).days - target_dte))
