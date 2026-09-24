"""Every tunable number in one place.

The strategy defaults are standard textbook values fixed *before* any backtest was run.
They have not been optimised against the data. Changing them after looking at results
is how backtests end up lying, so treat edits here as a new experiment, not a fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class CostConfig:
    # Schwab charges $0.65 per contract per leg, each side. Robinhood charges $0 (plus
    # small regulatory fees) but has no official API for options orders.
    commission_per_contract: float = 0.65
    # Dollars per share, per leg, per side, paid versus the model's mid price.
    slippage_per_leg: float = 0.01


@dataclass(frozen=True)
class PricingConfig:
    risk_free: float = 0.03
    dividend_yield: float = 0.013
    # VIX prices the whole 30-day strip including the wings, so it sits above at-the-money
    # implied vol. 0.90 x VIX is a common approximation for SPX/SPY ATM IV.
    atm_iv_ratio: float = 0.90
    # Put skew: IV rises by this fraction of ATM IV for each standard deviation of
    # moneyness below the forward (and falls above it).
    skew: float = 0.25
    iv_floor_ratio: float = 0.55
    iv_cap_ratio: float = 2.5
    strike_step: float = 1.0


@dataclass(frozen=True)
class RegimeConfig:
    # Daily bias, from the PrimeFlow higher-timeframe filter: 200 SMA structure plus 8/50 EMA.
    trend_sma: int = 200
    fast_ema: int = 8
    mid_ema: int = 50
    # Volatility state from VIX.
    vix_calm: float = 20.0
    vix_stressed: float = 30.0
    # A VIX close this far above its recent average is a shock day.
    vix_spike_ratio: float = 1.20
    vix_spike_lookback: int = 10


@dataclass(frozen=True)
class RiskConfig:
    starting_equity: float = 100.0
    # Target maximum loss per trade as a fraction of equity.
    risk_per_trade: float = 0.05
    # Small-account rule: if the target buys zero contracts, allow ONE contract only when
    # its maximum loss is at or below this fraction of equity. Otherwise skip the trade.
    micro_account_cap: float = 0.60
    max_open_positions: int = 1
    max_total_risk: float = 0.60
    max_contracts: int = 10
    # Circuit breaker: pause new entries after this drop from the equity peak.
    drawdown_halt: float = 0.25
    halt_days: int = 20
    # Kill switch: stop opening trades for good below this fraction of starting equity.
    hard_floor: float = 0.50
    # Cool-off after consecutive losing trades.
    loss_streak: int = 3
    loss_streak_pause: int = 10


@dataclass(frozen=True)
class PutSpreadConfig:
    """Trend Put Credit Spread: sell an out-of-the-money put spread in a bullish, calm tape."""

    short_delta: float = 0.20
    # Spread width as a fraction of SPY's price (1% ~ $5 wide at SPY 500), rounded to the
    # strike grid. Set `width` to force a fixed dollar width instead.
    width_pct: float = 0.01
    width: Optional[float] = None
    dte_target: int = 45
    dte_min: int = 30
    dte_max: int = 60
    take_profit: float = 0.50  # close after capturing 50% of the credit
    stop_loss: float = 2.0  # close when the loss reaches 2x the credit
    exit_dte: int = 21  # never hold into the last three weeks
    min_credit_frac: float = 0.10  # skip if credit is under 10% of the width
    max_vix: float = 30.0
    exit_on_bear_bias: bool = True


@dataclass(frozen=True)
class DipCallConfig:
    """Pullback Call Debit Spread: buy a short-dated call spread on a sharp dip in an uptrend."""

    long_delta: float = 0.60
    short_delta: float = 0.30  # short leg by delta, so the spread keeps real exposure to the bounce
    width: Optional[float] = None  # force a fixed dollar width instead of the short delta
    dte_target: int = 14
    dte_min: int = 7
    dte_max: int = 21
    rsi_length: int = 2
    rsi_entry: float = 10.0
    exit_sma: int = 5  # the bounce is done when SPY closes back above its 5-day average
    take_profit: float = 0.75  # fraction of the spread's maximum profit
    stop_loss: float = 0.50  # fraction of the debit
    max_hold_days: int = 7
    exit_dte: int = 2
    max_debit_frac: float = 0.80  # skip if the debit is over 80% of the width


# Standard SPY weekly expirations are only modelled from this date. Before it the
# backtest uses the monthly (third-Friday) cycle, which is what actually traded.
WEEKLY_EXPIRY_START = "2010-06-01"
