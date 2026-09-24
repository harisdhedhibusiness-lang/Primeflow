"""Side-income planner: how much capital each strategy needs to pay a monthly target safely.

"Safe" here means sized so the worst drop in 2005-2026 stays under a cap (10% or 20%). Each
rule's trades are replayed with a fixed fraction of the account committed per trade; the rest
sits in cash earning nothing (conservative: T-bills would add to every row). The fraction is
the largest one that keeps the historical worst drop under the cap. Growth at that fraction
sets the capital needed for the income target.
"""

from __future__ import annotations

import html
import math
from datetime import datetime
from typing import Dict, List, Sequence, Tuple

from .config import CostConfig, PricingConfig
from .report import _CSS
from .search import MIN_TRADES, TradeRec, _S, _init, _years, simulate
from .search_report import STRESS

DD_CAPS = (0.10, 0.20)
TARGETS = (1000, 2000)


def _curve(trades: Sequence[TradeRec], f: float) -> Tuple[float, float, Dict[int, float]]:
    """Final multiple, max drawdown, and return by calendar year of exit, at fraction f."""
    eq = peak = 1.0
    dd = 0.0
    year_start: Dict[int, float] = {}
    year_end: Dict[int, float] = {}
    for t in trades:
        y = t.exit.year
        year_start.setdefault(y, eq)
        roc = t.pnl / t.capital if t.capital > 0 else 0.0
        eq *= 1 + f * roc
        if eq <= 1e-9:
            return 0.0, 1.0, {}
        year_end[y] = eq
        peak = max(peak, eq)
        dd = max(dd, 1 - eq / peak)
    years = {y: year_end[y] / year_start[y] - 1 for y in year_end}
    return eq, dd, years


def size_for_cap(trades: Sequence[TradeRec], cap: float) -> float:
    """Largest fraction of the account per trade (<= 1) whose worst historical drop stays under cap."""
    if not trades:
        return 0.0
    if _curve(trades, 1.0)[1] <= cap:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(40):
        mid = (lo + hi) / 2
        if _curve(trades, mid)[1] <= cap:
            lo = mid
        else:
            hi = mid
    return lo


def plan(trades: Sequence[TradeRec], years: float, cap: float) -> Dict[str, float]:
    f = size_for_cap(trades, cap)
    final, dd, by_year = _curve(trades, f)
    cagr = final ** (1 / years) - 1 if final > 0 else -1.0
    worst_year = min(by_year.values()) if by_year else 0.0
    losing = sum(1 for r in by_year.values() if r < 0)
    return {"fraction": f, "cagr": cagr, "dd": dd, "worst_year": worst_year, "losing_years": losing,
            "years_counted": len(by_year)}


def capital_needed(monthly: float, cagr: float) -> float:
    return math.inf if cagr <= 0 else monthly * 12 / cagr


def analyze_income(results, window, data_dir: str, start=None, shortlist: int = 250, stress_top: int = 5,
                   log=print) -> Dict[str, object]:
    mf = _S["mf"]
    lo, hi = _S["lo"], len(mf) - 1
    years = _years(mf.dates[lo], mf.dates[hi])

    # Profitable over the whole period and in both halves, never wiped out, enough trades.
    pool = [r for r in results if r[1]["n"] >= MIN_TRADES and not r[1]["ruined"] and r[1]["total"] > 0
            and r[2]["cagr"] > 0 and r[3]["cagr"] > 0]
    pool.sort(key=lambda r: -(r[1]["cagr"] / max(r[1]["growth_dd"], 0.01)))
    log(f"  {len(pool):,} rules profitable in both halves; sizing the best {min(shortlist, len(pool))} for safety...")

    rows = []
    for rule, full, ins, oos in pool[:shortlist]:
        trades = simulate(rule, lo, hi)
        entry = {"rule": rule, "full": full, "ins": ins, "oos": oos, "win_rate": full["win_rate"]}
        for cap in DD_CAPS:
            entry[cap] = plan(trades, years, cap)
        rows.append(entry)
    rows.sort(key=lambda e: -e[DD_CAPS[0]]["cagr"])

    top, seen = [], set()
    for e in rows:
        r = e["rule"]
        key = (r.action, r.right, r.dte, r.delta, r.width, r.signal)
        if key not in seen:
            seen.add(key)
            top.append(e)
        if len(top) >= stress_top:
            break
    log(f"  stress-testing the top {len(top)} at their safe size under {len(STRESS)} scenarios...")
    for e in top:
        e["stress"] = []
    for name, pricing, costs in STRESS:
        _init(data_dir, pricing, costs, None, start)
        for e in top:
            trades = simulate(e["rule"], lo, hi)
            final, dd, _ = _curve(trades, e[DD_CAPS[0]]["fraction"])
            cagr = final ** (1 / years) - 1 if final > 0 else -1.0
            e["stress"].append((name, cagr, dd))
    _init(data_dir, PricingConfig(), CostConfig(), None, start)
    for e in top:
        e["worst_stress_cagr"] = min(c for _, c, _ in e["stress"])
        e["worst_stress_dd"] = max(d for _, _, d in e["stress"])
    return {"window": window, "years": years, "pool": len(pool), "rows": rows, "top": top}


def benchmarks_from_data(data_dir: str, start, end) -> List[dict]:
    """Benchmarks from downloaded SPY total-return and 13-week T-bill (^IRX) history."""
    import os
    from statistics import mean

    from .data import read_csv
    from .metrics import max_drawdown

    tr_path = os.path.join(data_dir, "SPY_TR.csv")
    spy_path = tr_path if os.path.exists(tr_path) else os.path.join(data_dir, "SPY.csv")
    closes = [(r[0], r[4]) for r in read_csv(spy_path) if start <= r[0] <= end]
    yrs = _years(closes[0][0], closes[-1][0])
    cagr = (closes[-1][1] / closes[0][1]) ** (1 / yrs) - 1
    dd = max_drawdown([c for _, c in closes])
    irx_path = os.path.join(data_dir, "IRX.csv")
    if os.path.exists(irx_path):
        irx = [(r[0], r[4]) for r in read_csv(irx_path) if start <= r[0] <= end]
        now, avg = irx[-1][1] / 100, mean(v for _, v in irx) / 100
    else:
        now = avg = 0.0
    return benchmarks(cagr, dd, now, avg)


def benchmarks(spy_total_cagr: float, spy_total_dd: float, tbill_now: float, tbill_avg: float) -> List[dict]:
    out = [{"name": f"T-bills at today's rate ({tbill_now:.2%})", "cagr": tbill_now, "dd": 0.0},
           {"name": f"T-bills at the 2005-2026 average ({tbill_avg:.2%})", "cagr": tbill_avg, "dd": 0.0},
           {"name": "SPY buy and hold, dividends reinvested", "cagr": spy_total_cagr, "dd": spy_total_dd}]
    for cap in DD_CAPS:
        f = min(1.0, cap / spy_total_dd)
        out.append({"name": f"SPY, only {f:.0%} of the account invested (worst drop {cap:.0%})",
                    "cagr": (1 + spy_total_cagr) ** f - 1 if f < 1 else spy_total_cagr, "dd": cap})
    return out


def _money(x: float) -> str:
    return "not reachable" if math.isinf(x) else f"${x:,.0f}"


def console_text(a: Dict[str, object], bench: List[dict]) -> str:
    out = [f"Income planner, {a['window'][0]} -> {a['window'][2]}. {a['pool']:,} rules were profitable in both halves.", "",
           f"  {'Strategy':<66}{'Per year':>9}{'Worst drop':>11}{'For $1k/mo':>13}{'For $2k/mo':>13}"]
    for b in bench:
        out.append(f"  {b['name']:<66}{b['cagr']:>+8.1%}{b['dd']:>11.0%}{_money(capital_needed(1000, b['cagr'])):>13}"
                   f"{_money(capital_needed(2000, b['cagr'])):>13}")
    for cap in DD_CAPS:
        out += ["", f"Best options rules sized so the worst drop since 2005 stays under {cap:.0%}:"]
        for e in sorted(a["rows"], key=lambda e: -e[cap]["cagr"])[:5]:
            p = e[cap]
            out.append(f"  {p['cagr']:+.1%}/yr (drop {p['dd']:.0%}, worst year {p['worst_year']:+.1%}, win rate "
                       f"{e['win_rate']:.0%}) needs {_money(capital_needed(1000, p['cagr']))} for $1k/mo | "
                       f"{e['rule'].describe()}")
    return "\n".join(out)


def html_page(a: Dict[str, object], bench: List[dict], source: str) -> str:
    start, split, end = a["window"]

    def bench_rows():
        return "".join(
            f"<tr><th scope='row'>{html.escape(b['name'])}</th><td>{b['cagr']:+.1%}</td><td>{b['dd']:.0%}</td>"
            f"<td>{_money(capital_needed(1000, b['cagr']))}</td><td>{_money(capital_needed(2000, b['cagr']))}</td></tr>"
            for b in bench)

    cap_tables = []
    for cap in DD_CAPS:
        rows = "".join(
            f"<tr><td class='rule'>{html.escape(e['rule'].describe())}</td><td>{e['win_rate']:.1%}</td>"
            f"<td>{e[cap]['fraction']:.0%}</td><td>{e[cap]['cagr']:+.1%}</td><td>{e[cap]['dd']:.0%}</td>"
            f"<td>{e[cap]['worst_year']:+.1%}</td><td>{e[cap]['losing_years']} of {e[cap]['years_counted']}</td>"
            f"<td>{_money(capital_needed(1000, e[cap]['cagr']))}</td><td>{_money(capital_needed(2000, e[cap]['cagr']))}</td></tr>"
            for e in sorted(a["rows"], key=lambda e: -e[cap]["cagr"])[:10])
        cap_tables.append(f"""
<h3>Worst drop kept under {cap:.0%}</h3>
<div class="scroll"><table><thead><tr><th>Rule</th><th>Win rate</th><th>Account per trade</th><th>Per year</th>
<th>Worst drop</th><th>Worst year</th><th>Losing years</th><th>Capital for $1,000/mo</th><th>Capital for $2,000/mo</th>
</tr></thead><tbody>{rows}</tbody></table></div>""")

    stress = []
    for e in a["top"]:
        rows = "".join(f"<tr><th scope='row'>{html.escape(n)}</th><td class='{'ok' if c > 0 else 'bad'}'>{c:+.1%}</td>"
                       f"<td>{d:.0%}</td></tr>" for n, c, d in e["stress"])
        stress.append(f"<h3>{html.escape(e['rule'].describe())}</h3><div class='scroll'><table><thead><tr><th>Scenario</th>"
                      f"<th>Per year at the 10%-drop size</th><th>Worst drop</th></tr></thead><tbody>{rows}</tbody></table></div>")

    best = max(a["rows"], key=lambda e: e[DD_CAPS[0]]["cagr"]) if a["rows"] else None
    lead = ""
    if best:
        p = best[DD_CAPS[0]]
        lead = (f"<p><b>Best options rule with the worst drop under 10%:</b> {p['cagr']:+.1%} per year "
                f"({html.escape(best['rule'].describe())}). $1,000 a month from it takes about "
                f"{_money(capital_needed(1000, p['cagr']))}; $2,000 takes about {_money(capital_needed(2000, p['cagr']))}. "
                f"Its worst calendar year was {p['worst_year']:+.1%}.</p>")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SPY Side Income Planner</title>
<style>{_CSS}
td.rule{{text-align:left;white-space:normal;min-width:240px}}
.answer{{border-left:4px solid var(--s1);padding:4px 14px;margin:16px 0}} h3{{font-size:15px;margin-top:24px}}
</style></head>
<body><main>
<header><h1>SPY Side Income Planner</h1>
<p class="meta">{start} to {end} · {html.escape(source)} · generated {datetime.now():%Y-%m-%d %H:%M}</p></header>
<aside class="disclosure">Hypothetical, model-priced results on historical data. Income from trading is not steady:
it arrives unevenly and some years lose money. Past performance does not indicate future results. Educational
software, not investment advice.</aside>
<div class="answer">
<p><b>The rule that decides everything:</b> monthly income = capital x annual return / 12. No strategy found in
21 years of SPY data turns a small account into $1,000-$2,000 a month safely.</p>
{lead}
<p><b>How "safe" is measured:</b> each strategy is sized so its worst drop from 2005 to 2026 (2008, 2020 and 2022
included) stays under the cap. Unused cash is assumed to earn nothing; T-bills would add a few percent to every row.</p>
</div>
<section><h2>Benchmarks</h2>
<div class="scroll"><table><thead><tr><th>Strategy</th><th>Per year</th><th>Worst drop</th><th>Capital for $1,000/mo</th>
<th>Capital for $2,000/mo</th></tr></thead><tbody>{bench_rows()}</tbody></table></div></section>
<section><h2>Best SPY options rules, sized for safety</h2>
<p class="lede">From {a['pool']:,} rules that made money over the whole period and in both halves (split {split}).</p>
{''.join(cap_tables)}</section>
<section><h2>Stress tests</h2>{''.join(stress)}</section>
</main></body></html>"""
