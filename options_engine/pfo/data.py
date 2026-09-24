"""Market data: download free daily history, cache it as CSV, and load it back.

Sources, tried in order until one works:
  SPY   Yahoo Finance chart API -> Stooq CSV -> Nasdaq API
  VIX   Cboe CSV -> FRED (VIXCLS) -> Yahoo
  VIX3M Cboe CSV -> FRED (VXVCLS) -> Yahoo           (optional, for term structure)
  VIX9D Cboe CSV -> Yahoo                            (optional, short-dated option pricing)

If every source fails you can save the files by hand. Any CSV with a Date column and a
Close column works (a Yahoo "Download" export is fine). Put them at data/SPY.csv,
data/VIX.csv and optionally data/VIX3M.csv.
"""

from __future__ import annotations

import csv
import json
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
SAMPLE_DIR = os.path.join(DATA_DIR, "sample")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

Row = Tuple[date, float, float, float, float]  # date, open, high, low, close


class DataError(RuntimeError):
    pass


@dataclass
class Bar:
    date: date
    open: float
    high: float
    low: float
    close: float
    vix: Optional[float] = None
    vix3m: Optional[float] = None
    vix9d: Optional[float] = None


# ---------------------------------------------------------------- downloading


def _get(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.URLError as exc:
        if isinstance(getattr(exc, "reason", None), ssl.SSLCertVerificationError):
            raise DataError(
                "SSL certificate check failed. On a Mac, open your Python folder in "
                "Applications and double-click 'Install Certificates.command', then retry."
            ) from exc
        raise


def _parse_date(text: str) -> date:
    text = text.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%d-%m-%Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"unrecognised date: {text!r}")


def _num(text: str) -> Optional[float]:
    text = (text or "").strip().replace("$", "").replace(",", "")
    if text in ("", ".", "null", "None", "N/A"):
        return None
    return float(text)


def parse_csv(text: str) -> List[Row]:
    """Any CSV with a date column and a close column. Open/High/Low default to Close."""
    reader = csv.reader(text.strip().splitlines())
    header = [h.strip().lower() for h in next(reader)]

    def col(*names: str) -> Optional[int]:
        for n in names:
            if n in header:
                return header.index(n)
        return None

    i_date = col("date", "observation_date", "timestamp")
    i_close = col("close", "adj close", "vixcls", "vxvcls", "price")
    if i_date is None or i_close is None:
        raise DataError(f"CSV needs Date and Close columns, got {header}")
    i_open, i_high, i_low = col("open"), col("high"), col("low")
    rows: List[Row] = []
    for rec in reader:
        if len(rec) <= max(i_date, i_close):
            continue
        close = _num(rec[i_close])
        if close is None or close <= 0:
            continue
        pick = lambda i: (_num(rec[i]) if i is not None and i < len(rec) else None) or close
        rows.append((_parse_date(rec[i_date]), pick(i_open), pick(i_high), pick(i_low), close))
    if not rows:
        raise DataError("CSV contained no usable rows")
    rows.sort(key=lambda r: r[0])
    return rows


def _yahoo(symbol: str, start: date, adjusted: bool = False) -> List[Row]:
    p1 = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    p2 = int(time.time()) + 86400
    last_err: Exception = DataError("no Yahoo host answered")
    for host in ("query1", "query2"):
        url = (
            f"https://{host}.finance.yahoo.com/v8/finance/chart/{urllib.request.quote(symbol)}"
            f"?period1={p1}&period2={p2}&interval=1d&events={'div' if adjusted else 'history'}"
        )
        try:
            payload = json.loads(_get(url))
            result = payload["chart"]["result"][0]
            quote = result["indicators"]["quote"][0]
            closes = result["indicators"]["adjclose"][0]["adjclose"] if adjusted else quote["close"]
            rows: List[Row] = []
            for ts, o, h, l, c in zip(
                result["timestamp"], quote["open"], quote["high"], quote["low"], closes
            ):
                if c is None:
                    continue
                d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
                rows.append((d, o or c, h or c, l or c, c))
            if rows:
                return rows
        except Exception as exc:  # try the next host
            last_err = exc
    raise last_err


def _stooq(symbol: str, start: date) -> List[Row]:
    text = _get(f"https://stooq.com/q/d/l/?s={symbol}&i=d")
    if not text.lower().startswith("date"):
        raise DataError("Stooq did not return CSV")
    return [r for r in parse_csv(text) if r[0] >= start]


def _nasdaq(symbol: str, start: date) -> List[Row]:
    url = (
        f"https://api.nasdaq.com/api/quote/{symbol}/historical?assetclass=etf"
        f"&fromdate={start.isoformat()}&todate={date.today().isoformat()}&limit=10000"
    )
    rows_json = json.loads(_get(url))["data"]["tradesTable"]["rows"]
    rows: List[Row] = []
    for r in rows_json:
        close = _num(r["close"])
        if close is None:
            continue
        rows.append(
            (
                _parse_date(r["date"]),
                _num(r.get("open", "")) or close,
                _num(r.get("high", "")) or close,
                _num(r.get("low", "")) or close,
                close,
            )
        )
    rows.sort(key=lambda r: r[0])
    return rows


def _cboe(name: str, start: date) -> List[Row]:
    text = _get(f"https://cdn.cboe.com/api/global/us_indices/daily_prices/{name}_History.csv")
    return [r for r in parse_csv(text) if r[0] >= start]


def _fred(series: str, start: date) -> List[Row]:
    text = _get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}")
    return [r for r in parse_csv(text) if r[0] >= start]


SOURCES: Dict[str, List[Tuple[str, Callable[[date], List[Row]]]]] = {
    "SPY": [
        ("Yahoo", lambda s: _yahoo("SPY", s)),
        ("Stooq", lambda s: _stooq("spy.us", s)),
        ("Nasdaq", lambda s: _nasdaq("SPY", s)),
    ],
    "VIX": [
        ("Cboe", lambda s: _cboe("VIX", s)),
        ("FRED", lambda s: _fred("VIXCLS", s)),
        ("Yahoo", lambda s: _yahoo("^VIX", s)),
    ],
    "VIX3M": [
        ("Cboe", lambda s: _cboe("VIX3M", s)),
        ("FRED", lambda s: _fred("VXVCLS", s)),
        ("Yahoo", lambda s: _yahoo("^VIX3M", s)),
    ],
    "VIX9D": [
        ("Cboe", lambda s: _cboe("VIX9D", s)),
        ("Yahoo", lambda s: _yahoo("^VIX9D", s)),
    ],
    # Benchmarks for the income planner.
    "SPY_TR": [("Yahoo", lambda s: _yahoo("SPY", s, adjusted=True))],
    "IRX": [("Yahoo", lambda s: _yahoo("^IRX", s))],
}


def fetch(symbol: str, start: date, data_dir: str = DATA_DIR, log=print) -> str:
    errors = []
    for source_name, fn in SOURCES[symbol]:
        try:
            rows = fn(start)
            if len(rows) < 250:
                raise DataError(f"only {len(rows)} rows")
            path = write_csv(os.path.join(data_dir, f"{symbol}.csv"), rows)
            log(f"  {symbol:<6} {len(rows):>5} days  {rows[0][0]} -> {rows[-1][0]}  ({source_name})")
            return path
        except Exception as exc:
            errors.append(f"{source_name}: {exc}")
    raise DataError(f"could not download {symbol}. Tried " + "; ".join(errors))


def fetch_all(start: date, data_dir: str = DATA_DIR, log=print) -> None:
    os.makedirs(data_dir, exist_ok=True)
    fetch("SPY", start, data_dir, log)
    fetch("VIX", start, data_dir, log)
    for optional in ("VIX3M", "VIX9D", "SPY_TR", "IRX"):
        try:
            fetch(optional, start, data_dir, log)
        except DataError as exc:
            log(f"  {optional:<6} skipped (optional): {exc}")


def write_csv(path: str, rows: List[Row]) -> str:
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Date", "Open", "High", "Low", "Close"])
        for d, o, h, l, c in rows:
            w.writerow([d.isoformat(), f"{o:.4f}", f"{h:.4f}", f"{l:.4f}", f"{c:.4f}"])
    return path


# ---------------------------------------------------------------- loading


def read_csv(path: str) -> List[Row]:
    with open(path, newline="") as fh:
        return parse_csv(fh.read())


def load_market(data_dir: str = DATA_DIR, max_fill_days: int = 5) -> List[Bar]:
    """SPY bars joined with VIX (required) and VIX3M / VIX9D (optional) closes.

    Gaps in a volatility series of up to `max_fill_days` bars are forward-filled; longer
    gaps leave the value empty. The engine will not trade a bar without VIX.
    """
    spy_path = os.path.join(data_dir, "SPY.csv")
    vix_path = os.path.join(data_dir, "VIX.csv")
    for p in (spy_path, vix_path):
        if not os.path.exists(p):
            raise DataError(f"missing {p}. Run:  python -m pfo fetch")
    spy = read_csv(spy_path)
    series = {"vix": {r[0]: r[4] for r in read_csv(vix_path)}}
    for name in ("VIX3M", "VIX9D"):
        path = os.path.join(data_dir, f"{name}.csv")
        series[name.lower()] = {r[0]: r[4] for r in read_csv(path)} if os.path.exists(path) else {}

    last = {k: None for k in series}
    age = {k: max_fill_days + 1 for k in series}
    bars: List[Bar] = []
    for d, o, h, l, c in spy:
        vals = {}
        for k, table in series.items():
            if d in table:
                last[k], age[k] = table[d], 0
            else:
                age[k] += 1
            vals[k] = last[k] if age[k] <= max_fill_days else None
        bars.append(Bar(d, o, h, l, c, vals["vix"], vals["vix3m"], vals["vix9d"]))
    return bars
