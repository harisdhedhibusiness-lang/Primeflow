"""Live SPY option quotes from Cboe's free delayed chain (about 15 minutes behind).

Used by `today` so the daily signal names real strikes with real bid/ask prices instead of
model estimates.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List, Optional, Tuple

from .data import _get

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{symbol}.json"
_OCC = re.compile(r"^[A-Z]+(\d{6})([CP])(\d{8})$")


@dataclass(frozen=True)
class Quote:
    expiry: date
    is_call: bool
    strike: float
    bid: float
    ask: float
    delta: float
    iv: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass
class Chain:
    as_of: str
    underlying: float
    quotes: Dict[Tuple[date, bool], Dict[float, Quote]]

    def expiries(self, is_call: bool) -> List[date]:
        return sorted(e for e, c in self.quotes if c == is_call)

    def nearest_expiry(self, today: date, target_dte: int, min_dte: int, max_dte: int, is_call: bool) -> Optional[date]:
        ok = [e for e in self.expiries(is_call) if min_dte <= (e - today).days <= max_dte]
        return min(ok, key=lambda e: abs((e - today).days - target_dte)) if ok else None

    def by_delta(self, expiry: date, is_call: bool, target: float) -> Optional[Quote]:
        side = [q for q in self.quotes.get((expiry, is_call), {}).values() if q.bid > 0 and q.delta]
        return min(side, key=lambda q: abs(abs(q.delta) - target)) if side else None

    def get(self, expiry: date, is_call: bool, strike: float) -> Optional[Quote]:
        return self.quotes.get((expiry, is_call), {}).get(strike)


def parse(payload: dict) -> Chain:
    data = payload["data"]
    quotes: Dict[Tuple[date, bool], Dict[float, Quote]] = {}
    for o in data["options"]:
        m = _OCC.match(o["option"])
        if not m:
            continue
        ymd, cp, k = m.groups()
        expiry = date(2000 + int(ymd[:2]), int(ymd[2:4]), int(ymd[4:]))
        q = Quote(expiry, cp == "C", int(k) / 1000, float(o.get("bid") or 0), float(o.get("ask") or 0),
                  float(o.get("delta") or 0), float(o.get("iv") or 0))
        quotes.setdefault((expiry, q.is_call), {})[q.strike] = q
    as_of = payload.get("timestamp") or data.get("last_trade_time") or datetime.now().isoformat()
    return Chain(str(as_of), float(data["current_price"]), quotes)


def fetch_chain(symbol: str = "SPY") -> Chain:
    return parse(json.loads(_get(CBOE_URL.format(symbol=symbol), timeout=60)))
