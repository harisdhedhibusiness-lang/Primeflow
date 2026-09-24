"""Console summary and a self-contained HTML report (no internet needed to open it)."""

from __future__ import annotations

import html
import json
import math
from datetime import datetime
from typing import List, Sequence, Tuple

from .study import GATE, Run, Study, StrategyStudy

DISCLOSURE = (
    "Hypothetical, model-priced results. Option prices are simulated with Black-Scholes and a "
    "VIX-derived volatility surface, not historical quotes, so real fills will differ. Past "
    "performance does not indicate future results. Trading options involves substantial risk of "
    "loss and is not suitable for every investor. Educational software, not investment advice."
)


# ---------------------------------------------------------------- console


def _money(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def _pf(x: float) -> str:
    return "inf" if math.isinf(x) else f"{x:.2f}"


def console_summary(study: Study) -> str:
    out = [
        f"Backtest {study.start} -> {study.end}   data: {study.source}",
        f"SPY price over the same window: {study.benchmark['total_return']:+.1%} "
        f"({study.benchmark['cagr']:+.1%}/yr), worst drop {study.benchmark['max_drawdown']:.0%}",
        "",
    ]
    hdr = f"  {'Run':<34}{'End':>10}{'Return':>9}{'Max DD':>8}{'Trades':>8}{'Win%':>6}{'PF':>6}{'Skipped':>9}"
    for st in study.strategies:
        out += [st.strategy.title, hdr]
        for r in st.main:
            s = r.stats
            out.append(
                f"  {r.label:<34}{_money(s['end_equity']):>10}{s['total_return']:>+9.1%}"
                f"{s['max_drawdown']:>8.0%}{s['trades']:>8}{s['win_rate']:>6.0%}"
                f"{_pf(s['profit_factor']):>6}{s['unaffordable']:>9}"
            )
        verdict = "PASS: paper-trade candidate" if st.passed else "FAIL: do not trade"
        failed = [name for name, ok, _ in st.gate if not ok]
        out.append(f"  Gate: {verdict}" + (f"  (failed: {'; '.join(failed)})" if failed else ""))
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- html


def _downsample(points: Sequence[Tuple[object, float]], n: int = 420) -> List[Tuple[object, float]]:
    if len(points) <= n:
        return list(points)
    step = (len(points) - 1) / (n - 1)
    return [points[round(k * step)] for k in range(n)]


def _nice_ticks(lo: float, hi: float, count: int = 5) -> List[float]:
    span = hi - lo or 1.0
    raw = span / count
    mag = 10 ** math.floor(math.log10(raw))
    step = next(m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw)
    start = math.floor(lo / step) * step
    ticks, t = [], start
    while t <= hi + step * 0.001:
        ticks.append(round(t, 10))
        t += step
    return ticks


def _chart(cid: str, st: StrategyStudy, study: Study) -> str:
    run = st.headline
    strat = _downsample(run.result.curve)
    base_s = run.result.start_equity
    bench_map = dict(study.benchmark_curve)
    base_b = study.benchmark_curve[0][1]
    dates = [d for d, _ in strat]
    s_vals = [v / base_s - 1 for _, v in strat]
    b_vals = [bench_map.get(d, float("nan")) / base_b - 1 for d in dates]
    finite = [v for v in s_vals + b_vals if not math.isnan(v)]
    ticks = _nice_ticks(min(finite + [0.0]), max(finite + [0.0]))
    lo, hi = ticks[0], ticks[-1]

    W, H, L, R, T, B = 760, 300, 52, 118, 14, 30
    pw, ph = W - L - R, H - T - B
    x = lambda k: L + pw * k / max(len(dates) - 1, 1)
    y = lambda v: T + ph * (1 - (v - lo) / (hi - lo or 1))

    def path(vals):
        pts = [f"{x(k):.1f},{y(v):.1f}" for k, v in enumerate(vals) if not math.isnan(v)]
        return "M" + " L".join(pts)

    grid = "".join(
        f'<line x1="{L}" x2="{L + pw}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid"/>'
        f'<text x="{L - 8}" y="{y(t) + 4:.1f}" class="tick" text-anchor="end">{t:+.0%}</text>'
        for t in ticks
    )
    years = sorted({d.year for d in dates})
    every = max(1, len(years) // 8)
    xt = ""
    for yr in years[::every]:
        k = next(i for i, d in enumerate(dates) if d.year == yr)
        xt += f'<text x="{x(k):.1f}" y="{H - 8}" class="tick" text-anchor="middle">{yr}</text>'

    def end_label(vals, name):
        v = vals[-1]
        return (f'<circle cx="{x(len(vals) - 1):.1f}" cy="{y(v):.1f}" r="4" class="dot {name}"/>'
                f'<text x="{x(len(vals) - 1) + 10:.1f}" y="{y(v) + 4:.1f}" class="endlabel">{v:+.0%}</text>')

    data = json.dumps({
        "d": [d.isoformat() for d in dates],
        "s": [round(v, 4) for v in s_vals],
        "b": [None if math.isnan(v) else round(v, 4) for v in b_vals],
        "L": L, "pw": pw, "T": T, "ph": ph, "lo": lo, "hi": hi,
    })
    return f"""
<figure class="chart" id="{cid}">
  <div class="legend">
    <span><i class="key s1"></i>{html.escape(st.strategy.title)}, $5,000 account</span>
    <span><i class="key s2"></i>SPY price (buy and hold, no dividends)</span>
  </div>
  <div class="plot">
    <svg viewBox="0 0 {W} {H}" role="img" aria-label="Equity change of {html.escape(st.strategy.title)} versus SPY">
      {grid}
      <line x1="{L}" x2="{L + pw}" y1="{y(0):.1f}" y2="{y(0):.1f}" class="zero"/>
      {xt}
      <path d="{path(b_vals)}" class="line s2"/>
      <path d="{path(s_vals)}" class="line s1"/>
      {end_label([v for v in b_vals if not math.isnan(v)], "s2")}
      {end_label(s_vals, "s1")}
      <line class="cross" y1="{T}" y2="{T + ph}" x1="-10" x2="-10"/>
      <circle class="hov s2" r="4" cx="-10" cy="-10"/><circle class="hov s1" r="4" cx="-10" cy="-10"/>
      <rect class="hit" x="{L}" y="{T}" width="{pw}" height="{ph}"/>
    </svg>
    <div class="tip" hidden></div>
  </div>
  <script type="application/json">{data}</script>
</figure>"""


def _pct(x: float) -> str:
    return f"{x:+.1%}"


def _run_rows(runs: List[Run]) -> str:
    rows = []
    for r in runs:
        s = r.stats
        note = f'<div class="note">{html.escape(r.note)}</div>' if r.note else ""
        rows.append(
            f"<tr><th scope='row'>{html.escape(r.label)}{note}</th>"
            f"<td>{_money(s['start_equity'])}</td><td>{_money(s['end_equity'])}</td>"
            f"<td>{_pct(s['total_return'])}</td><td>{_pct(s['cagr'])}</td>"
            f"<td>{s['max_drawdown']:.1%}</td><td>{s['trades']}</td><td>{s['win_rate']:.0%}</td>"
            f"<td>{_money(s['avg_win'])}</td><td>{_money(s['avg_loss'])}</td>"
            f"<td>{_pf(s['profit_factor'])}</td><td>{s['worst_trade_pct']:+.0%}</td>"
            f"<td>{s['max_losing_streak']}</td><td>{s['unaffordable']}</td></tr>"
        )
    head = ("<tr><th scope='col'>Run</th><th>Start</th><th>End</th><th>Return</th><th>Per year</th>"
            "<th>Max drawdown</th><th>Trades</th><th>Win rate</th><th>Avg win</th><th>Avg loss</th>"
            "<th>Profit factor</th><th>Worst trade (% of account)</th><th>Longest losing streak</th>"
            "<th>Signals skipped as unaffordable</th></tr>")
    return f"<div class='scroll'><table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table></div>"


def _mc_block(runs: List[Run]) -> str:
    rows = []
    for r in runs:
        m = r.mc
        if not m:
            rows.append(f"<tr><th scope='row'>{html.escape(r.label)}</th><td colspan='5'>Too few trades to resample</td></tr>")
            continue
        rows.append(
            f"<tr><th scope='row'>{html.escape(r.label)}</th><td>{m['median']:.2f}x</td>"
            f"<td>{m['p05']:.2f}x to {m['p95']:.2f}x</td><td>{m['prob_loss']:.0%}</td>"
            f"<td>{m['prob_dd_50']:.0%}</td></tr>"
        )
    head = ("<tr><th scope='col'>Run</th><th>Median ending multiple</th><th>90% range</th>"
            "<th>Chance of ending below start</th><th>Chance of a 50%+ drawdown</th></tr>")
    return f"<div class='scroll'><table><thead>{head}</thead><tbody>{''.join(rows)}</tbody></table></div>"


def _gate_block(st: StrategyStudy) -> str:
    rows = "".join(
        f"<tr><th scope='row'>{html.escape(name)}</th><td>{html.escape(val)}</td>"
        f"<td class='{'ok' if ok else 'bad'}'>{'✓ Pass' if ok else '✕ Fail'}</td></tr>"
        for name, ok, val in st.gate
    )
    verdict = ("<p class='verdict ok'>✓ Passes the gate: candidate for paper trading. Not for live money yet.</p>"
               if st.passed else
               "<p class='verdict bad'>✕ Fails the gate: do not trade this with real money.</p>")
    return (f"{verdict}<div class='scroll'><table class='gate'><thead><tr><th scope='col'>Requirement "
            f"($5,000 account)</th><th>Result</th><th>Status</th></tr></thead><tbody>{rows}</tbody></table></div>")


def _trades_block(run: Run) -> str:
    rows = "".join(
        f"<tr><td>{t.entry_date}</td><td>{t.exit_date}</td><td>{html.escape(t.legs)}</td>"
        f"<td>{t.expiry}</td><td>{t.contracts}</td><td>{t.entry_price:+.2f}</td><td>{t.exit_price:+.2f}</td>"
        f"<td class='{'ok' if t.pnl > 0 else 'bad'}'>{_money(t.pnl)}</td><td>{t.reason.replace('_', ' ')}</td></tr>"
        for t in run.result.trades
    )
    return (f"<details><summary>All {len(run.result.trades)} trades, {html.escape(run.label)} account</summary>"
            "<div class='scroll'><table><thead><tr><th>Entry</th><th>Exit</th><th>Spread</th><th>Expiry</th>"
            "<th>Qty</th><th>Entry price</th><th>Exit price</th><th>P&amp;L</th><th>Exit reason</th></tr></thead>"
            f"<tbody>{rows}</tbody></table></div></details>")


def _events_block(run: Run) -> str:
    if not run.result.events:
        return ""
    items = "".join(f"<li><b>{d}</b> {html.escape(msg)}</li>" for d, msg in run.result.events)
    return f"<details><summary>Risk manager interventions ({len(run.result.events)})</summary><ul>{items}</ul></details>"


def html_report(study: Study) -> str:
    sections = []
    for n, st in enumerate(study.strategies):
        hundred = st.main[0].stats
        blocked = hundred["unaffordable"]
        sections.append(f"""
<section>
  <h2>{html.escape(st.strategy.title)}</h2>
  <p class="lede">{html.escape(st.strategy.summary)}</p>
  {_gate_block(st)}
  {_chart(f"c{n}", st, study)}
  <h3>Account sizes</h3>
  <p>With $100 and the safeguards on, the engine took {hundred['trades']} trades and skipped {blocked}
  signals because one contract would have risked more than the small-account cap allows.</p>
  {_run_rows(st.main)}
  <h3>Stress tests ($5,000 account)</h3>
  {_run_rows(st.stress)}
  <h3>Luck check: 5,000 reshuffles of the trade sequence</h3>
  {_mc_block([r for r in st.main if r.stats['trades']])}
  {_trades_block(st.headline)}
  {_events_block(st.headline)}
</section>""")

    b = study.benchmark
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>PrimeFlow Options Backtest</title>
<style>{_CSS}</style></head>
<body><main>
<header>
  <h1>PrimeFlow Options Backtest</h1>
  <p class="meta">{study.start} to {study.end} · data: {html.escape(study.source)} ·
  generated {datetime.now():%Y-%m-%d %H:%M}</p>
  <p class="meta">SPY price over the same window: {_pct(b['total_return'])} ({_pct(b['cagr'])} per year),
  worst peak-to-trough drop {b['max_drawdown']:.0%}.</p>
</header>
<aside class="disclosure">{html.escape(DISCLOSURE)}</aside>
{''.join(sections)}
<section>
  <h2>How to read this</h2>
  <ul>
    <li><b>Profit factor</b> is gross profit divided by gross loss. Under 1.0 lost money. The gate asks for {GATE['min_profit_factor']}.</li>
    <li><b>Win rate means little on its own.</b> A strategy can win most trades and still lose money if the losers are bigger.</li>
    <li><b>First half / second half</b> checks that results don't come from one lucky stretch.</li>
    <li><b>Next-day fills</b> checks the strategy still works if you enter a day late.</li>
    <li><b>Signals skipped as unaffordable</b> are trades the risk manager refused because the account was too small to take them safely.</li>
  </ul>
</section>
</main>
<script>{_JS}</script>
</body></html>"""


_CSS = """
:root{--surface:#fcfcfb;--surface-2:#f3f2ef;--ink:#0b0b0b;--ink-2:#52514e;--ink-3:#7a7974;--rule:#e4e2dc;
--s1:#2a78d6;--s2:#9a9891;--good:#0a7d0a;--bad:#c02f2f;color-scheme:light}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--surface:#1a1a19;--surface-2:#232321;--ink:#fff;
--ink-2:#c3c2b7;--ink-3:#8f8e86;--rule:#33332f;--s1:#3987e5;--s2:#7d7b74;--good:#3fbf3f;--bad:#e66767;color-scheme:dark}}
:root[data-theme="dark"]{--surface:#1a1a19;--surface-2:#232321;--ink:#fff;--ink-2:#c3c2b7;--ink-3:#8f8e86;--rule:#33332f;
--s1:#3987e5;--s2:#7d7b74;--good:#3fbf3f;--bad:#e66767;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--surface);color:var(--ink);
font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:1080px;margin:0 auto;padding:24px 16px 64px}h1{font-size:28px;margin:0 0 4px}
h2{font-size:22px;margin:40px 0 4px;padding-top:16px;border-top:1px solid var(--rule)}h3{font-size:16px;margin:24px 0 8px}
.meta,.lede,.note{color:var(--ink-2)}.meta{margin:2px 0}.note{font-weight:400;font-size:12px}
.disclosure{background:var(--surface-2);border-radius:8px;padding:12px 14px;color:var(--ink-2);font-size:13px;margin:16px 0}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13px;font-variant-numeric:tabular-nums}
th,td{padding:6px 8px;border-bottom:1px solid var(--rule);text-align:right;white-space:nowrap}
th:first-child,td:first-child{text-align:left}thead th{color:var(--ink-2);font-weight:600;white-space:normal;vertical-align:bottom}
tbody th{font-weight:600}.gate td,.gate th{white-space:normal}
.ok{color:var(--good)}.bad{color:var(--bad)}.verdict{font-weight:600;font-size:16px}
details{margin:12px 0}summary{cursor:pointer;color:var(--ink-2)}
.chart{margin:16px 0}.legend{display:flex;flex-wrap:wrap;gap:16px;font-size:13px;color:var(--ink-2);margin-bottom:6px}
.key{display:inline-block;width:14px;height:2px;margin:0 6px 4px 0;vertical-align:middle}
.key.s1{background:var(--s1)}.key.s2{background:var(--s2)}
.plot{position:relative}svg{width:100%;height:auto;display:block}
.grid{stroke:var(--rule);stroke-width:1}.zero{stroke:var(--ink-3);stroke-width:1}
.tick{fill:var(--ink-3);font-size:11px}.endlabel{fill:var(--ink);font-size:12px;font-weight:600}
.line{fill:none;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.line.s1{stroke:var(--s1)}.line.s2{stroke:var(--s2)}
.dot,.hov{stroke:var(--surface);stroke-width:2}.dot.s1,.hov.s1{fill:var(--s1)}.dot.s2,.hov.s2{fill:var(--s2)}
.cross{stroke:var(--ink-3);stroke-width:1}.hit{fill:transparent;cursor:crosshair}
.tip{position:absolute;pointer-events:none;background:var(--surface);border:1px solid var(--rule);border-radius:6px;
padding:6px 8px;font-size:12px;box-shadow:0 2px 8px rgba(0,0,0,.12);white-space:nowrap}
.tip i{display:inline-block;width:10px;height:2px;margin-right:6px;vertical-align:middle}
"""

_JS = """
document.querySelectorAll('figure.chart').forEach(function(fig){
  var D=JSON.parse(fig.querySelector('script').textContent),svg=fig.querySelector('svg'),
      tip=fig.querySelector('.tip'),cross=svg.querySelector('.cross'),
      h1=svg.querySelector('.hov.s1'),h2=svg.querySelector('.hov.s2'),n=D.d.length;
  function Y(v){return D.T+D.ph*(1-(v-D.lo)/((D.hi-D.lo)||1));}
  function pct(v){return (v>=0?'+':'')+(v*100).toFixed(1)+'%';}
  function hide(){tip.hidden=true;cross.setAttribute('x1',-10);cross.setAttribute('x2',-10);
    [h1,h2].forEach(function(c){c.setAttribute('cx',-10);c.setAttribute('cy',-10);});}
  svg.querySelector('.hit').addEventListener('mousemove',function(e){
    var r=svg.getBoundingClientRect(),vx=(e.clientX-r.left)*svg.viewBox.baseVal.width/r.width,
        k=Math.max(0,Math.min(n-1,Math.round((vx-D.L)/D.pw*(n-1)))),X=D.L+D.pw*k/Math.max(n-1,1);
    cross.setAttribute('x1',X);cross.setAttribute('x2',X);
    h1.setAttribute('cx',X);h1.setAttribute('cy',Y(D.s[k]));
    if(D.b[k]!==null){h2.setAttribute('cx',X);h2.setAttribute('cy',Y(D.b[k]));}
    tip.innerHTML='<b>'+D.d[k]+'</b><br><i style="background:var(--s1)"></i>Strategy '+pct(D.s[k])+
      (D.b[k]!==null?'<br><i style="background:var(--s2)"></i>SPY '+pct(D.b[k]):'');
    tip.hidden=false;var px=X*r.width/svg.viewBox.baseVal.width;
    tip.style.left=Math.min(px+12,r.width-150)+'px';tip.style.top='8px';
  });
  svg.querySelector('.hit').addEventListener('mouseleave',hide);
});
"""
