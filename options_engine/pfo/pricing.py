"""Option pricing for the backtest.

There is no free source of historical SPY option quotes, so the backtest prices every
option with Black-Scholes using an implied-vol surface built from that day's Cboe
volatility indexes:

    V(T)    = VIX9D / VIX / VIX3M interpolated in variance to the option's tenor
    ATM IV  = atm_iv_ratio x V(T)
    IV(K)   = ATM IV x SMILE(z),  z = ln(K / F) / (ATM IV x sqrt(T))

Both the 0.80 ratio and the SMILE table were measured on a real Cboe SPY chain.

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

# IV / (0.80 x tenor vol index) by standardised moneyness z, measured per tenor bucket: medians
# over 1,671 out-of-the-money SPY options, Cboe delayed chain at the close of 2026-09-22.
# Keys are the bucket's median days to expiry (buckets 4-7, 8-14, 15-35 and 36-70 days).
_Z = (-8.5, -7.5, -6.5, -5.5, -4.75, -4.25, -3.75, -3.25, -2.75, -2.25, -1.75, -1.25, -0.75, -0.25,
      0.25, 0.75, 1.25, 1.75, 2.25, 2.75, 3.5)
_SMILE_ROWS = {
    7: (None, None, 2.605, 2.317, 2.109, 1.951, 1.793, 1.656, 1.517, 1.385, 1.252, 1.132, 1.036, 0.957,
        0.908, 0.915, 0.923, 0.960, 1.040, 1.172, 1.333),
    10: (None, 3.007, 2.718, 2.396, 2.216, 2.071, 1.931, 1.775, 1.630, 1.493, 1.355, 1.229, 1.122, 1.048,
         0.991, 0.970, 0.980, 1.008, 1.090, 1.208, 1.389),
    24: (3.435, 3.181, 2.887, 2.614, 2.341, 2.194, 2.055, 1.885, 1.719, 1.567, 1.433, 1.294, 1.165, 1.052,
         0.965, 0.920, 0.915, 0.949, 1.056, 1.143, 1.262),
    59: (3.395, 3.170, 2.893, 2.609, 2.386, 2.254, 2.099, 1.944, 1.788, 1.628, 1.470, 1.316, 1.169, 1.034,
         0.931, 0.860, 0.893, 0.910, 0.962, 1.032, None),
}
SMILE = {days: tuple((z, v) for z, v in zip(_Z, row) if v is not None) for days, row in _SMILE_ROWS.items()}
_TENORS = tuple(sorted(SMILE))


def _curve(pts, z: float) -> float:
    """Piecewise-linear in z, extended past both ends at the end slopes."""
    if z <= pts[0][0]:
        (x1, y1), (x2, y2) = pts[0], pts[1]
    elif z >= pts[-1][0]:
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
    else:
        k = next(n for n in range(1, len(pts)) if z <= pts[n][0])
        (x1, y1), (x2, y2) = pts[k - 1], pts[k]
    return y1 + (y2 - y1) * (z - x1) / (x2 - x1)


def smile(z: float, days: float) -> float:
    """Measured IV / ATM ratio at moneyness z, interpolated linearly between tenor buckets."""
    if days <= _TENORS[0]:
        return _curve(SMILE[_TENORS[0]], z)
    if days >= _TENORS[-1]:
        return _curve(SMILE[_TENORS[-1]], z)
    k = next(n for n in range(1, len(_TENORS)) if days <= _TENORS[n])
    d1, d2 = _TENORS[k - 1], _TENORS[k]
    w = (days - d1) / (d2 - d1)
    return (1 - w) * _curve(SMILE[d1], z) + w * _curve(SMILE[d2], z)


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

    def iv(self, S: float, K: float, T: float, vol: float) -> float:
        """`vol` is the Cboe volatility index level for this option's tenor (MarketFrame.term_vol)."""
        c = self.cfg
        atm = max(vol, 1.0) / 100.0 * c.atm_iv_ratio
        if T <= 0:
            return atm
        forward = S * math.exp((c.risk_free - c.dividend_yield) * T)
        z = math.log(K / forward) / (atm * math.sqrt(T))
        ratio = 1.0 + c.smile_scale * (smile(z, T * 365.0) - 1.0)
        return atm * min(max(ratio, 0.5), 6.0)

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
