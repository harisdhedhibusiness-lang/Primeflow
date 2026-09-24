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

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from .config import RegimeConfig
from .data import Bar
from .indicators import Series, ema, rsi, sma


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
        self._cache: Dict[Tuple[str, int], Series] = {}
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
