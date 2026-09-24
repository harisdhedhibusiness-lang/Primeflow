"""Win rate versus growth, with no account-size limit.

Growth is measured the aggressive way: an account that puts all its capital into every trade
(as many contracts as its cash or margin covers) and compounds. That flatters the high-return
rules as much as possible, so if a high win rate still can't buy strong growth here, it can't
anywhere.
"""

from __future__ import annotations

import html
import json
import math
from datetime import datetime
from typing import Dict

from .config import CostConfig, PricingConfig
from .metrics import max_drawdown
from .report import _CSS
from .search import MIN_TRADES, _S, _init, _years, every_entry_day, every_entry_day_many, simulate, summarize
from .search_report import PROOF_TRADES, STRESS

FLOORS = (0.50, 0.60, 0.70, 0.80, 0.85, 0.90, 0.95, 0.97, 0.98, 0.99, 1.00)
FAMILY = {"buy": "buy one option", "sell": "sell one option (uncovered, Reg T margin)", "spread": "sell a spread"}


def _money(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def _pct(x: float) -> str:
    return f"{x:+.1%}"


def analyze_growth(results, window, data_dir: str, start=None, stress_top: int = 4, log=print) -> Dict[str, object]:
    mf = _S["mf"]
    lo, hi = _S["lo"], len(mf) - 1
    years = _years(mf.dates[lo], mf.dates[hi])
    closes = mf.close[lo:hi + 1]
    bench = {"cagr": (closes[-1] / closes[0]) ** (1 / years) - 1, "max_dd": max_drawdown(closes)}

    ranked = [r for r in results if r[1]["n"] >= MIN_TRADES and not r[1]["ruined"]]
    ruined = sum(1 for r in results if r[1]["n"] >= MIN_TRADES and r[1]["ruined"])

    frontier = []
    for floor in FLOORS:
        pool = [r for r in ranked if r[1]["win_rate"] >= floor - 1e-12]
        best = max(pool, key=lambda r: r[1]["cagr"]) if pool else None
        frontier.append({"floor": floor, "rules": len(pool), "best": best})

    # Everything at 99%+ on its backtest path gets the every-entry-day retest.
    high = sorted((r for r in ranked if r[1]["win_rate"] >= 0.99), key=lambda r: -r[1]["cagr"])
    log(f"  re-testing {len(high)} rules at 99%+ on every possible entry day...")
    eds = every_entry_day_many([r[0] for r in high], data_dir, start)
    _init(data_dir, PricingConfig(), CostConfig(), None, start)
    retested = [{"rule": rule, "full": full, "ins": ins, "oos": oos, "ed": ed}
                for (rule, full, ins, oos), ed in zip(high, eds)]
    perfect = [x for x in retested if x["full"]["win_rate"] >= 1 and x["ed"]["win_rate"] >= 1]
    ninety_nine = [x for x in retested if x["ed"]["win_rate"] >= 0.99]

    # Stress the fastest-growing perfect rules (distinct shapes).
    top, seen = [], set()
    for x in perfect:
        r = x["rule"]
        key = (r.action, r.right, r.dte, r.delta, r.width, r.signal)
        if key not in seen:
            seen.add(key)
            top.append(x)
        if len(top) >= stress_top:
            break
    log(f"  stress-testing the top {len(top)} perfect rules under {len(STRESS)} scenarios...")
    for x in top:
        x["stress"] = []
    for name, pricing, costs in STRESS:
        _init(data_dir, pricing, costs, None, start)
        for x in top:
            path = summarize(simulate(x["rule"], lo, hi), years)
            ed = summarize(every_entry_day(x["rule"], lo, hi))
            x["stress"].append((name, path, ed))
    _init(data_dir, PricingConfig(), CostConfig(), None, start)
    for x in top:
        x["perfect_everywhere"] = all(e["win_rate"] >= 1 and p["win_rate"] >= 1 for _, p, e in x["stress"])
        x["min_ed"] = min(e["win_rate"] for _, _, e in x["stress"])
        x["min_cagr"] = min(p["cagr"] for _, p, _ in x["stress"])

    leaders = sorted(ranked, key=lambda r: -r[1]["cagr"])[:8]
    return {"window": window, "years": years, "bench": bench, "tested": len(results), "ranked": len(ranked),
            "ruined": ruined, "frontier": frontier, "retested": retested, "perfect": perfect,
            "ninety_nine": ninety_nine, "top": top, "leaders": leaders}


def console_text(a: Dict[str, object]) -> str:
    start, split, end = a["window"]
    b = a["bench"]
    out = [f"Win rate vs growth, {start} -> {end}. {a['tested']:,} rules; {a['ranked']:,} with {MIN_TRADES}+ trades "
           f"that never wiped out an all-in account ({a['ruined']:,} did).",
           f"SPY buy and hold (price only): {_pct(b['cagr'])}/yr, worst drop {b['max_dd']:.0%}.", "",
           f"  {'Win-rate floor':<16}{'Rules':>8}{'Best growth/yr':>16}{'Its worst drop':>16}  Rule"]
    for f in a["frontier"]:
        r = f["best"]
        if r is None:
            out.append(f"  {f['floor']:>6.0%}{'':<10}{f['rules']:>8}")
            continue
        out.append(f"  {f['floor']:>6.0%}{'':<10}{f['rules']:>8}{_pct(r[1]['cagr']):>16}{r[1]['growth_dd']:>15.0%}  "
                   f"{r[0].describe()}")
    out += ["", f"100% on the backtest path AND on every possible entry day: {len(a['perfect'])} rules."]
    for x in a["top"]:
        tag = "100% in every stress test" if x["perfect_everywhere"] else f"drops to {x['min_ed']:.2%} under stress"
        out.append(f"  {_pct(x['full']['cagr'])}/yr, worst drop {x['full']['growth_dd']:.0%}, {x['full']['n']} trades, "
                   f"{tag} | {x['rule'].describe()}")
    out += ["", f"Proving even 99% takes about {PROOF_TRADES} straight wins; 100% can never be proven."]
    return "\n".join(out)


def _frontier_chart(a: Dict[str, object]) -> str:
    pts = [(f["floor"], f["best"][1]["cagr"], f["best"][0].describe(), f["best"][1]["win_rate"],
            f["best"][1]["growth_dd"], f["rules"]) for f in a["frontier"] if f["best"]]
    bench = a["bench"]["cagr"]
    ys = [p[1] for p in pts] + [bench, 0.0]
    lo_y, hi_y = min(ys), max(ys)
    step = _nice_step((hi_y - lo_y) / 5)
    lo_y = math.floor(lo_y / step) * step
    hi_y = math.ceil(hi_y / step) * step
    W, H, L, R, T, B = 760, 320, 56, 150, 16, 44
    pw, ph = W - L - R, H - T - B
    n = len(pts)
    x = lambda k: L + pw * k / max(n - 1, 1)
    y = lambda v: T + ph * (1 - (v - lo_y) / ((hi_y - lo_y) or 1))
    grid = ""
    t = lo_y
    while t <= hi_y + step / 2:
        grid += (f'<line x1="{L}" x2="{L + pw}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid"/>'
                 f'<text x="{L - 8}" y="{y(t) + 4:.1f}" class="tick" text-anchor="end">{t:+.0%}</text>')
        t += step
    xt = "".join(f'<text x="{x(k):.1f}" y="{T + ph + 18}" class="tick" text-anchor="middle">{p[0]:.0%}</text>'
                 for k, p in enumerate(pts))
    path = "M" + " L".join(f"{x(k):.1f},{y(p[1]):.1f}" for k, p in enumerate(pts))
    dots = "".join(f'<circle cx="{x(k):.1f}" cy="{y(p[1]):.1f}" r="4" class="dot s1"/>' for k, p in enumerate(pts))
    first, last = pts[0], pts[-1]
    labels = (f'<text x="{x(n - 1) + 10:.1f}" y="{y(last[1]) + 4:.1f}" class="endlabel">{last[1]:+.1%}/yr at 100%</text>'
              f'<text x="{x(0):.1f}" y="{y(first[1]) - 10:.1f}" class="endlabel">{first[1]:+.1%}/yr</text>')
    bench_line = (f'<line x1="{L}" x2="{L + pw}" y1="{y(bench):.1f}" y2="{y(bench):.1f}" class="bench"/>'
                  f'<text x="{L + pw + 10}" y="{y(bench) + 4:.1f}" class="tick">SPY buy and hold {bench:+.1%}</text>')
    data = json.dumps({"x": [round(x(k), 1) for k in range(n)], "L": L, "pw": pw,
                       "rows": [{"floor": f"{p[0]:.0%}", "cagr": f"{p[1]:+.1%}", "wr": f"{p[3]:.1%}",
                                 "dd": f"{p[4]:.0%}", "rule": p[2], "n": p[5]} for p in pts]})
    return f"""
<figure class="chart">
  <div class="legend"><span><i class="key s1"></i>Best growth per year among rules at or above the win-rate floor</span>
  <span><i class="key s3"></i>SPY buy and hold (price only)</span></div>
  <div class="plot">
    <svg viewBox="0 0 {W} {H}" role="img" aria-label="Best annual growth at each win-rate floor">
      {grid}{bench_line}{xt}
      <text x="{L + pw / 2:.1f}" y="{H - 6}" class="tick" text-anchor="middle">Minimum win rate required</text>
      <path d="{path}" class="line s1"/>{dots}{labels}
      <line class="cross" y1="{T}" y2="{T + ph}" x1="-10" x2="-10"/>
      <rect class="hit" x="{L - 20}" y="{T}" width="{pw + 40}" height="{ph}"/>
    </svg>
    <div class="tip" hidden></div>
  </div>
  <script type="application/json">{data}</script>
</figure>"""


def _nice_step(raw: float) -> float:
    raw = max(raw, 1e-4)
    mag = 10 ** math.floor(math.log10(raw))
    return next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)


def html_page(a: Dict[str, object], source: str, calibration: str) -> str:
    start, split, end = a["window"]
    b = a["bench"]
    frows = "".join(
        f"<tr><th scope='row'>{f['floor']:.0%}</th><td>{f['rules']:,}</td>"
        + (f"<td>{_pct(f['best'][1]['cagr'])}</td><td>{f['best'][1]['growth_dd']:.0%}</td>"
           f"<td>{f['best'][1]['win_rate']:.1%} of {f['best'][1]['n']}</td>"
           f"<td class='rule'>{html.escape(f['best'][0].describe())}</td>" if f["best"] else "<td colspan='4'>none</td>")
        + "</tr>" for f in a["frontier"])

    prow = "".join(
        f"<tr><td class='rule'>{html.escape(x['rule'].describe())}</td><td>{FAMILY[x['rule'].action]}</td>"
        f"<td>{x['full']['n']}</td><td>{x['ed']['n']:,}</td><td>{_pct(x['full']['cagr'])}</td>"
        f"<td>{x['full']['growth_dd']:.0%}</td><td>{_pct(x['ins']['cagr'])} / {_pct(x['oos']['cagr'])}</td>"
        f"<td>{_money(x['full']['avg_win'])}</td><td>{_money(x['full']['max_capital'])}</td></tr>"
        for x in a["perfect"])

    stress_blocks = []
    for x in a["top"]:
        rows = "".join(
            f"<tr><th scope='row'>{html.escape(name)}</th><td class='{'ok' if p['win_rate'] >= 1 else 'bad'}'>"
            f"{p['win_rate']:.2%} of {p['n']}</td><td class='{'ok' if e['win_rate'] >= 1 else 'bad'}'>{e['win_rate']:.2%}</td>"
            f"<td>{_pct(p['cagr'])}</td><td>{p['growth_dd']:.0%}</td><td>{_money(p['worst'])}</td></tr>"
            for name, p, e in x["stress"])
        verdict = ("<p class='verdict ok'>✓ Stays at 100% in every stress scenario</p>" if x["perfect_everywhere"] else
                   f"<p class='verdict bad'>✕ Falls to {x['min_ed']:.2%} in at least one stress scenario</p>")
        stress_blocks.append(
            f"<h3>{html.escape(x['rule'].describe())}</h3>{verdict}<div class='scroll'><table><thead><tr><th>Scenario</th>"
            f"<th>Backtest path</th><th>Every entry day</th><th>Growth per year</th><th>Worst drop</th>"
            f"<th>Worst trade</th></tr></thead><tbody>{rows}</tbody></table></div>")

    lrows = "".join(
        f"<tr><td class='rule'>{html.escape(r.describe())}</td><td>{s['win_rate']:.1%} of {s['n']}</td>"
        f"<td>{_pct(s['cagr'])}</td><td>{s['growth_dd']:.0%}</td><td>{s['worst_roc']:.0%}</td>"
        f"<td>{_pct(i['cagr'])} / {_pct(o['cagr'])}</td></tr>"
        for r, s, i, o in a["leaders"])

    best100 = a["frontier"][-1]["best"]
    best_any = a["leaders"][0] if a["leaders"] else None
    answer = [f"<p><b>Tested:</b> {a['tested']:,} SPY rules from {start} to {end}, every trade priced at today's SPY level "
              f"with fees and slippage. {a['ruined']:,} rules with {MIN_TRADES}+ trades would have wiped out an all-in "
              f"account at least once.</p>"]
    if best100:
        answer.append(f"<p><b>Best growth at a 100% win rate (backtest path):</b> {_pct(best100[1]['cagr'])} per year, "
                      f"from {html.escape(best100[0].describe())}. SPY buy and hold did {_pct(b['cagr'])} per year "
                      f"(price only).</p>")
    answer.append(f"<p><b>100% on every possible entry day, too:</b> {len(a['perfect'])} rules.")
    if a["top"]:
        still = sum(x["perfect_everywhere"] for x in a["top"])
        answer.append(f" Of the {len(a['top'])} fastest-growing, {still} stayed at 100% through all seven stress tests.")
    answer.append("</p>")
    if best_any:
        r, s = best_any[0], best_any[1]
        answer.append(f"<p><b>Fastest-growing rule overall:</b> {_pct(s['cagr'])} per year, but it won only "
                      f"{s['win_rate']:.1%} of trades and fell {s['growth_dd']:.0%} from its peak along the way "
                      f"({html.escape(r.describe())}).</p>")
    answer.append(f"<p><b>Proof standard:</b> even 99% takes about {PROOF_TRADES} straight wins to show with 95% "
                  f"confidence. A true 100% can never be proven; every one of these trades can lose.</p>")

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SPY Win Rate vs Growth</title>
<style>{_CSS}
td.rule{{text-align:left;white-space:normal;min-width:240px}}
.answer{{border-left:4px solid var(--s1);padding:4px 14px;margin:16px 0}}
.key.s3{{background:var(--ink-3)}} .bench{{stroke:var(--ink-3);stroke-width:1}}
h3{{font-size:15px;margin-top:28px}}
</style></head>
<body><main>
<header>
  <h1>SPY Win Rate vs Growth</h1>
  <p class="meta">{start} to {end} · {html.escape(source)} · no account-size limit · generated {datetime.now():%Y-%m-%d %H:%M}</p>
</header>
<aside class="disclosure">Hypothetical, model-priced results on historical data. Growth assumes every trade uses all
available capital and compounds, which maximises both gains and drawdowns. Past performance does not indicate future
results. Selling options can lose many times the premium collected. Educational software, not investment advice.</aside>
<div class="answer">{''.join(answer)}</div>

<section>
  <h2>How much growth each win rate can buy</h2>
  <p class="lede">For each minimum win rate, the fastest-growing rule that meets it (at least {MIN_TRADES} trades, never
  wiped out). Hover for the rule.</p>
  {_frontier_chart(a)}
  <div class="scroll"><table><thead><tr><th>Win-rate floor</th><th>Rules</th><th>Best growth per year</th>
  <th>Its worst drop</th><th>Its win rate</th><th>Rule</th></tr></thead><tbody>{frows}</tbody></table></div>
</section>

<section>
  <h2>Rules at 100% on every possible entry day</h2>
  <p class="lede">These won every trade on their backtest path and from every day their signal fired, 2005-2026.
  "Before / after" is growth per year before and after {split}.</p>
  <div class="scroll"><table><thead><tr><th>Rule</th><th>Type</th><th>Trades</th><th>Entry days</th>
  <th>Growth per year</th><th>Worst drop</th><th>Before / after</th><th>Avg win</th><th>Capital per contract</th></tr></thead>
  <tbody>{prow}</tbody></table></div>
  {''.join(stress_blocks)}
</section>

<section>
  <h2>The fastest-growing rules, and what they cost</h2>
  <div class="scroll"><table><thead><tr><th>Rule</th><th>Win rate</th><th>Growth per year</th><th>Worst drop</th>
  <th>Worst single trade (% of capital)</th><th>Before / after {split}</th></tr></thead><tbody>{lrows}</tbody></table></div>
</section>

<section>
  <h2>How the prices were checked</h2>
  <p class="lede">{html.escape(calibration)}</p>
  <ul>
    <li>Uncovered options use the standard Reg T margin (premium plus 20% of SPY less the out-of-the-money amount, with a
    10% floor). Brokers can require more, and margin rises as a trade moves against you.</li>
    <li>Portfolio-margin accounts (usually $100,000+) need less margin, which multiplies both the growth and the damage.</li>
  </ul>
</section>
</main>
<script>
document.querySelectorAll('figure.chart').forEach(function(fig){{
  var D=JSON.parse(fig.querySelector('script').textContent),svg=fig.querySelector('svg'),tip=fig.querySelector('.tip'),
      cross=svg.querySelector('.cross');
  svg.querySelector('.hit').addEventListener('mousemove',function(e){{
    var r=svg.getBoundingClientRect(),vx=(e.clientX-r.left)*svg.viewBox.baseVal.width/r.width,k=0,best=1e9;
    D.x.forEach(function(px,i){{if(Math.abs(px-vx)<best){{best=Math.abs(px-vx);k=i;}}}});
    cross.setAttribute('x1',D.x[k]);cross.setAttribute('x2',D.x[k]);var row=D.rows[k];
    tip.innerHTML='<b>Win rate at least '+row.floor+'</b><br>Best growth '+row.cagr+'/yr, worst drop '+row.dd+
      '<br>Its win rate '+row.wr+' ('+row.n+' rules qualify)<br>'+row.rule.replace(/&/g,'&amp;').replace(/</g,'&lt;');
    tip.hidden=false;tip.style.whiteSpace='normal';tip.style.maxWidth='320px';
    var px=D.x[k]*r.width/svg.viewBox.baseVal.width;tip.style.left=Math.min(px+12,r.width-330)+'px';tip.style.top='8px';
  }});
  svg.querySelector('.hit').addEventListener('mouseleave',function(){{tip.hidden=true;cross.setAttribute('x1',-10);cross.setAttribute('x2',-10);}});
}});
</script>
</body></html>"""
