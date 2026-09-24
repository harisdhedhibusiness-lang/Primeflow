"""Command line.

    python -m pfo fetch                 download SPY / VIX history (free sources)
    python -m pfo backtest              run the full study, write an HTML report
    python -m pfo backtest --sample     same, on the bundled 2014-2018 sample (offline)
    python -m pfo today --equity 2000   today's regime and exact trade, for paper trading
    python -m pfo search                win-rate search over single SPY calls and puts
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser
from datetime import date

from . import __version__
from .data import DATA_DIR, SAMPLE_DIR, DataError, fetch_all, load_market
from .regime import MarketFrame
from .report import DISCLOSURE, console_summary, html_report
from .strategies import make_strategy
from .study import run_study
from .today import todays_signal

REPORT_DIR = os.path.join(os.path.dirname(DATA_DIR), "reports")


def _date(text: str) -> date:
    return date.fromisoformat(text)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="pfo", description="PrimeFlow Options Engine")
    p.add_argument("--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="download market data")
    f.add_argument("--start", type=_date, default=date(2003, 1, 1))

    b = sub.add_parser("backtest", help="run the study and write an HTML report")
    b.add_argument("--start", type=_date, default=date(2005, 1, 1))
    b.add_argument("--end", type=_date)
    b.add_argument("--strategies", default="put_spread,dip_call")
    b.add_argument("--sample", action="store_true", help="use the bundled offline sample data")
    b.add_argument("--no-open", action="store_true", help="don't open the report in a browser")

    sr = sub.add_parser("search", help="win-rate search over single-leg SPY calls and puts")
    sr.add_argument("--sample", action="store_true", help="use the bundled offline sample data")
    sr.add_argument("--start", type=_date, default=date(2005, 1, 1))
    sr.add_argument("--account", type=float, default=500.0, help="account size the trades must fit")
    sr.add_argument("--split", type=_date, help="first out-of-sample date (default: 60%% through)")
    sr.add_argument("--no-open", action="store_true")

    t = sub.add_parser("today", help="today's regime and trade plan")
    t.add_argument("--equity", type=float, default=500.0)
    t.add_argument("--strategies", default="ceiling_call,dip_put")
    t.add_argument("--no-fetch", action="store_true", help="use cached data")

    args = p.parse_args(argv)
    try:
        if args.cmd == "fetch":
            print("Downloading daily history...")
            fetch_all(args.start)
            print(f"Saved to {DATA_DIR}")
            return 0

        if args.cmd == "backtest":
            data_dir = SAMPLE_DIR if args.sample else DATA_DIR
            source = "bundled sample (S&P 500 / 10 as SPY proxy, real VIX)" if args.sample else "SPY + VIX daily closes"
            names = [n.strip() for n in args.strategies.split(",") if n.strip()]
            for n in names:
                make_strategy(n)
            mf = MarketFrame(load_market(data_dir))
            study = run_study(mf, names, None if args.sample else args.start, args.end, source)
            print(console_summary(study))
            os.makedirs(REPORT_DIR, exist_ok=True)
            path = os.path.join(REPORT_DIR, f"backtest_{date.today():%Y%m%d}{'_sample' if args.sample else ''}.html")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html_report(study))
            print(f"Report: {path}")
            print(DISCLOSURE)
            if not args.no_open:
                webbrowser.open("file://" + os.path.abspath(path))
            return 0

        if args.cmd == "search":
            from .search import run_search
            from .search_report import CALIBRATION_NOTE, DISCLOSURE as SEARCH_DISCLOSURE, analyze, console_text, html_page

            data_dir = SAMPLE_DIR if args.sample else DATA_DIR
            start = None if args.sample else args.start
            source = ("bundled sample (S&P 500 / 10 as SPY proxy, real VIX)" if args.sample else
                      "SPY, VIX, VIX9D, VIX3M daily history")
            print("Testing every rule combination (10-20 minutes on a typical laptop)...")
            results, window = run_search(data_dir, split=args.split, start=start)
            findings = analyze(results, window, args.account, data_dir, start)
            print(console_text(findings))
            os.makedirs(REPORT_DIR, exist_ok=True)
            path = os.path.join(REPORT_DIR, f"search_{date.today():%Y%m%d}{'_sample' if args.sample else ''}.html")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(html_page(findings, source, CALIBRATION_NOTE))
            print(f"Report: {path}")
            print(SEARCH_DISCLOSURE)
            if not args.no_open:
                webbrowser.open("file://" + os.path.abspath(path))
            return 0

        if args.cmd == "today":
            if not args.no_fetch:
                fetch_all(date(2003, 1, 1), log=lambda *_: None)
            mf = MarketFrame(load_market(DATA_DIR))
            names = [n.strip() for n in args.strategies.split(",") if n.strip()]
            from .chain import fetch_chain

            print(todays_signal(mf, [make_strategy(n) for n in names], args.equity, chain_loader=fetch_chain))
            return 0
    except (DataError, KeyError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    sys.exit(main())
