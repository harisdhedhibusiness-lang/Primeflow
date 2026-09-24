"""The PrimeFlow Regime Gate, daily edition.

PrimeFlow's rule: classify the regime first; the regime decides which setups are legal.
A setup that fires in the wrong regime is not a trade.

Two readings, applied in order:

1. Daily bias (the higher-timeframe filter)
     bull     close above the 200 SMA and 8 EMA above 50 EMA
     bear     close below the 200 SMA and 8 EMA below 50 EMA
     neutral  anything else (structure and momentum disagree)

2. Volatility state
     calm / elevated / stressed   from VIX against fixed thresholds
     spike                        VIX well above its own recent average (a shock day)
     backwardation                VIX above VIX3M: near-term fear above longer-term fear
                                  (only when VIX3M data is available)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .config import RegimeConfig
from .data import Bar
from .indicators import Series, ema, rsi, sma


# Median VIX9D / VIX and VIX3M / VIX by VIX level, measured on Cboe data 2011-2026
# (VIX9D) and 2009-2026 (VIX3M). Used only to fill the years before those indexes existed.
_RATIO_9D = ((11.5, 0.892), (14.5, 0.931), (18.0, 0.946), (22.5, 0.974), (27.5, 0.997), (35.0, 1.020), (50.0, 1.104))
_RATIO_3M = ((11.5, 1.202), (14.5, 1.148), (18.0, 1.127), (22.5, 1.092), (27.5, 1.053), (35.0, 1.008), (50.0, 0.902))


def _interp(table, x: float) -> float:
    if x <= table[0][0]:
        return table[0][1]
    for (x1, y1), (x2, y2) in zip(table, table[1:]):
        if x <= x2:
            return y1 + (y2 - y1) * (x - x1) / (x2 - x1)
    return table[-1][1]


@dataclass(frozen=True)
class RegimeState:
    bias: str
    vol: str
    vix: float
    vix_spike: bool
    backwardation: Optional[bool]

    @property
    def risk_off(self) -> bool:
        return self.vol == "stressed" or self.vix_spike or bool(self.backwardation)

    def describe(self) -> str:
        flags = []
        if self.vix_spike:
            flags.append("VIX spike")
        if self.backwardation:
            flags.append("VIX term structure inverted")
        extra = f" ({', '.join(flags)})" if flags else ""
        return f"bias {self.bias.upper()}, volatility {self.vol} at VIX {self.vix:.1f}{extra}"


class MarketFrame:
    """Price history plus cached indicators and the regime for every bar."""

    def __init__(self, bars: List[Bar], cfg: RegimeConfig = RegimeConfig()):
        if not bars:
            raise ValueError("no bars")
        self.cfg = cfg
        self.bars = bars
        self.dates = [b.date for b in bars]
        self.close = [b.close for b in bars]
        self.vix = [b.vix for b in bars]
        self.vix3m = [b.vix3m for b in bars]
        self.vix9d = [b.vix9d for b in bars]
        self._cache: Dict[Tuple[str, int], Series] = {}
        self._term: Dict[Tuple[int, int], float] = {}
        self.regime = self._classify()

    def __len__(self) -> int:
        return len(self.bars)

    def sma(self, n: int) -> Series:
        key = ("sma", n)
        if key not in self._cache:
            self._cache[key] = sma(self.close, n)
        return self._cache[key]

    def ema(self, n: int) -> Series:
        key = ("ema", n)
        if key not in self._cache:
            self._cache[key] = ema(self.close, n)
        return self._cache[key]

    def rsi(self, n: int) -> Series:
        key = ("rsi", n)
        if key not in self._cache:
            self._cache[key] = rsi(self.close, n)
        return self._cache[key]

    def term_vol(self, i: int, days: int) -> Optional[float]:
        """Cboe volatility index level for an option `days` out, from VIX9D / VIX / VIX3M.

        Where VIX9D (from 2011) or VIX3M (from late 2009) is missing, it is estimated from VIX
        using the median ratio at that VIX level over the years both exist.
        """
        key = (i, days)
        if key in self._term:
            return self._term[key]
        v30 = self.vix[i]
        if v30 is None:
            return None
        v9 = self.vix9d[i] or v30 * _interp(_RATIO_9D, v30)
        v93 = self.vix3m[i] or v30 * _interp(_RATIO_3M, v30)
        if days <= 9:
            out = v9
        elif days >= 93:
            out = v93
        else:
            (d1, a), (d2, b) = ((9, v9), (30, v30)) if days <= 30 else ((30, v30), (93, v93))
            var = a * a * d1 + (b * b * d2 - a * a * d1) * (days - d1) / (d2 - d1)
            out = math.sqrt(max(var, 1e-9) / days)
        self._term[key] = out
        return out

    def index_of(self, start) -> int:
        for i, d in enumerate(self.dates):
            if d >= start:
                return i
        return len(self.dates)

    def _classify(self) -> List[Optional[RegimeState]]:
        c = self.cfg
        trend, fast, mid = self.sma(c.trend_sma), self.ema(c.fast_ema), self.ema(c.mid_ema)
        vix_avg = sma(self.vix, c.vix_spike_lookback)
        out: List[Optional[RegimeState]] = []
        for i, close in enumerate(self.close):
            vix = self.vix[i]
            if trend[i] is None or fast[i] is None or mid[i] is None or vix is None:
                out.append(None)
                continue
            if close > trend[i] and fast[i] > mid[i]:
                bias = "bull"
            elif close < trend[i] and fast[i] < mid[i]:
                bias = "bear"
            else:
                bias = "neutral"
            if vix >= c.vix_stressed:
                vol = "stressed"
            elif vix >= c.vix_calm:
                vol = "elevated"
            else:
                vol = "calm"
            spike = vix_avg[i] is not None and vix >= c.vix_spike_ratio * vix_avg[i]
            v3 = self.vix3m[i]
            back = (vix > v3) if v3 is not None else None
            out.append(RegimeState(bias, vol, vix, spike, back))
        return out
