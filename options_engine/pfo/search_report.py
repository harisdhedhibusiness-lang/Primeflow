"""Turn raw search results into findings.

Pipeline, for a given account size:
  1. Win-rate ceiling for each family (buy / sell one option / sell a spread, calls and puts)
  2. Candidates: 99%+ win rate, profitable, and affordable for the account
  3. Every-entry-day retest: still 99%+ and profitable from every day the signal fired?
  4. Stress tests on the top survivors: fees, fills, volatility level, skew
  5. The actual account: start on every week in history and trade it for 1, 3 and 5 years
"""

from __future__ import annotations

import html
import math
from datetime import datetime
from typing import Dict, List, Optional

from .config import CostConfig, PricingConfig
from .report import _CSS
from .search import MIN_TRADES, _S, _init, account_paths, every_entry_day, simulate, summarize

TARGET = 0.99
# Straight wins, with zero losses, needed to show a true win rate of at least 99% with 95%
# confidence (one-sided): 0.99 ** n <= 0.05.
PROOF_TRADES = math.ceil(math.log(0.05) / math.log(TARGET))

DISCLOSURE = (
    "Hypothetical, model-priced results on historical data. Option prices are simulated with "
    "Black-Scholes on a volatility surface calibrated to a real Cboe SPY chain, not historical "
    "quotes. Past performance does not indicate future results. A 99% win rate is not a guarantee: "
    "a single loss on these trades can cost most of a small account. Educational software, not "
    "investment advice."
)

CALIBRATION_NOTE = (
    "Check against the real Cboe SPY chain at the close of 2026-09-22 (SPY $773.38): for 2- to 16-delta puts "
    "from 6 to 59 days out, the model picked strikes within a median of 0.5 points of the real ones (worst 6), "
    "and priced $5-wide put spreads at 1.04x the real mid for 10 days or less and 1.08x across all tenors. The "
    "far out-of-the-money call spread was priced at 0.86x-0.95x the real mid, so its backtest is slightly "
    "conservative. The first, uncalibrated model overpriced these spreads 3-10x; every result here uses the "
    "calibrated one."
)

FAMILIES = (("buy", "call"), ("buy", "put"), ("sell", "call"), ("sell", "put"), ("spread", "call"), ("spread", "put"))
FAMILY_NAMES = {"buy": "Buy one {}", "sell": "Sell one {} (uncovered)", "spread": "Sell a {} spread"}

STRESS = (
    ("Base: Schwab fees, $0.01 slippage per leg", PricingConfig(), CostConfig()),
    ("Zero commission (Robinhood-style)", PricingConfig(), CostConfig(commission_per_contract=0.0)),
    ("Slippage doubled to $0.02 per leg", PricingConfig(), CostConfig(slippage_per_leg=0.02)),
    ("Volatility 6% lower", PricingConfig(atm_iv_ratio=0.75), CostConfig()),
    ("Volatility 6% higher", PricingConfig(atm_iv_ratio=0.85), CostConfig()),
    ("Skew 20% flatter", PricingConfig(smile_scale=0.8), CostConfig()),
    ("Skew 20% steeper", PricingConfig(smile_scale=1.2), CostConfig()),
)


def win_rate_lower_bound(wins: int, n: int, conf: float = 0.95) -> float:
    """One-sided lower confidence bound on the true win rate (exact binomial)."""
    if n == 0:
        return 0.0
    if wins == n:
        return (1 - conf) ** (1 / n)
    lo, hi = 0.0, wins / n
    for _ in range(60):
        p = (lo + hi) / 2
        tail = sum(math.comb(n, k) * p ** k * (1 - p) ** (n - k) for k in range(wins, n + 1))
        if tail < 1 - conf:
            lo = p
        else:
            hi = p
    return lo


def _family_label(action: str, right: str) -> str:
    return FAMILY_NAMES[action].format(right)


def analyze(results, window, account: float, data_dir: str, start=None, finalists: int = 4,
            log=print) -> Dict[str, object]:
    mf = _S["mf"]
    lo, hi = _S["lo"], len(mf) - 1
    ranked = [r for r in results if r[1]["n"] >= MIN_TRADES]

    families = {}
    for action, right in FAMILIES:
        sub = [r for r in ranked if r[0].action == action and r[0].right == right]
        aff = [r for r in sub if r[1]["max_capital"] <= account]
        families[(action, right)] = {
            "rules": len(sub),
            "at_target": sum(r[1]["win_rate"] >= TARGET for r in sub),
            "affordable": len(aff),
            "qualify": sum(r[1]["win_rate"] >= TARGET and r[1]["total"] > 0 for r in aff),
            "best": max(sub, key=lambda r: (r[1]["win_rate"], r[1]["n"])) if sub else None,
        }

    candidates = [r for r in ranked if r[1]["win_rate"] >= TARGET and r[1]["total"] > 0
                  and r[1]["max_capital"] <= account]
    log(f"  {len(candidates)} candidates; re-testing each on every entry day...")
    survivors = []
    for rule, full, ins, oos in candidates:
        ed = summarize(every_entry_day(rule, lo, hi))
        if ed["win_rate"] >= TARGET and ed["total"] > 0:
            wins = min(full["n"], math.floor(ed["win_rate"] * full["n"] + 1e-9))
            survivors.append({"rule": rule, "full": full, "ins": ins, "oos": oos, "ed": ed,
                              "lb": win_rate_lower_bound(wins, full["n"])})
    survivors.sort(key=lambda x: -x["full"]["total"])

    top: List[dict] = []
    seen = set()
    for s in survivors:
        r = s["rule"]
        key = (r.action, r.right, r.dte, r.delta, r.width, r.signal)
        if key not in seen:
            seen.add(key)
            top.append(s)
        if len(top) >= finalists:
            break

    log(f"  stress-testing the top {len(top)} under {len(STRESS)} scenarios...")
    for s in top:
        s["stress"] = []
    for name, pricing, costs in STRESS:
        _init(data_dir, pricing, costs, None, start)
        for s in top:
            path = summarize(simulate(s["rule"], lo, hi))
            ed = summarize(every_entry_day(s["rule"], lo, hi))
            acct = ({y: account_paths(s["rule"], lo, hi, account, y) for y in (1, 3, 5)}
                    if name.startswith(("Base", "Zero")) else {})
            s["stress"].append((name, path, ed, acct))
    _init(data_dir, PricingConfig(), CostConfig(), None, start)
    for s in top:
        s["robust"] = all(p["total"] > 0 and e["total"] > 0 for _, p, e, _ in s["stress"])
        s["min_ed"] = min(e["win_rate"] for _, _, e, _ in s["stress"])
    top.sort(key=lambda s: (not s["robust"], -s["min_ed"]))

    picked = [r for r in results if r[2]["n"] >= 20 and r[2]["win_rate"] >= TARGET and r[3]["n"] >= 10
              and r[1]["max_capital"] <= account]
    oos = {"picked": len(picked), "held": sum(r[3]["win_rate"] >= TARGET for r in picked)}
    return {"window": window, "account": account, "tested": len(results), "ranked": len(ranked),
            "families": families, "candidates": len(candidates), "survivors": survivors, "top": top,
            "oos": oos}


def _money(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def console_text(a: Dict[str, object]) -> str:
    start, split, end = a["window"]
    acct = a["account"]
    out = [f"Win-rate search {start} -> {end}, every trade priced at today's SPY level. "
           f"{a['tested']:,} rules, {a['ranked']:,} with {MIN_TRADES}+ trades.", ""]
    fit = f"Fit {_money(acct)}"
    out.append(f"  {'Family':<28}{'Rules':>7}{'99%+':>6}{fit:>10}{'Qualify':>9}{'Best win rate':>16}")
    for (action, right), f in a["families"].items():
        b = f["best"]
        if b is None:
            continue
        out.append(f"  {_family_label(action, right):<28}{f['rules']:>7}{f['at_target']:>6}{f['affordable']:>10}"
                   f"{f['qualify']:>9}{b[1]['win_rate']:>10.2%} ({b[1]['n']})")
    out += ["", f"{a['candidates']} rules are 99%+, profitable and fit a {_money(acct)} account. "
                f"{len(a['survivors'])} stay 99%+ and profitable when entered on every possible day.", ""]
    for s in a["top"]:
        base = s["stress"][0]
        out.append(("ROBUST  " if s["robust"] else "FRAGILE ") + s["rule"].describe())
        out.append(f"    {s['full']['win_rate']:.2%} of {s['full']['n']} trades, every-day {s['ed']['win_rate']:.2%} "
                   f"(worst scenario {s['min_ed']:.2%}), 95% lower bound {s['lb']:.1%}, "
                   f"avg win {_money(s['full']['avg_win'])}, total {_money(s['full']['total'])} per contract")
        for y, v in base[3].items():
            if v:
                out.append(f"    {_money(acct)} for {y}y: median {_money(v['median'])}, below start {v['below_start']:.0%}, "
                           f"stuck after a loss {v['stuck']:.0%}")
    out += ["", f"Proving 99% with 95% confidence takes about {PROOF_TRADES} straight wins and zero losses."]
    return "\n".join(out)


def html_page(a: Dict[str, object], source: str, calibration: Optional[str] = None) -> str:
    start, split, end = a["window"]
    acct = a["account"]
    fam_rows = []
    for (action, right), f in a["families"].items():
        b = f["best"]
        if b is None:
            continue
        rule, s = b[0], b[1]
        cap = "uncovered margin" if math.isinf(s["max_capital"]) else _money(s["max_capital"])
        fam_rows.append(
            f"<tr><th scope='row'>{_family_label(action, right)}</th><td>{f['rules']:,}</td>"
            f"<td>{f['at_target']}</td><td>{f['affordable']:,}</td>"
            f"<td class='{'ok' if f['qualify'] else 'bad'}'>{f['qualify']}</td>"
            f"<td>{s['win_rate']:.2%}</td><td>{s['n']}</td><td>{_money(s['avg_win'])}</td>"
            f"<td>{_money(s['avg_loss'])}</td><td>{cap}</td><td class='rule'>{html.escape(rule.describe())}</td></tr>")

    surv_rows = "".join(
        f"<tr><td class='rule'>{html.escape(s['rule'].describe())}</td><td>{s['full']['win_rate']:.2%} of {s['full']['n']}</td>"
        f"<td>{s['ed']['win_rate']:.2%} of {s['ed']['n']:,}</td><td>{s['lb']:.1%}</td>"
        f"<td>{s['ins']['win_rate']:.1%} / {s['oos']['win_rate']:.1%}</td>"
        f"<td>{_money(s['full']['avg_win'])}</td><td>{_money(s['full']['avg_loss'])}</td>"
        f"<td>{_money(s['full']['max_capital'])}</td><td>{_money(s['full']['total'])}</td></tr>"
        for s in a["survivors"])

    finalist_blocks = []
    for s in a["top"]:
        rows = []
        for name, p, e, _ in s["stress"]:
            rows.append(f"<tr><th scope='row'>{html.escape(name)}</th><td>{p['win_rate']:.2%} of {p['n']}</td>"
                        f"<td class='{'ok' if e['win_rate'] >= TARGET else 'bad'}'>{e['win_rate']:.2%}</td>"
                        f"<td class='{'ok' if p['total'] > 0 else 'bad'}'>{_money(p['total'])}</td>"
                        f"<td>{_money(p['avg_win'])}</td><td>{_money(p['avg_loss'])}</td></tr>")
        acct_rows = []
        for name, _, _, accts in s["stress"]:
            for y, v in accts.items():
                if v:
                    acct_rows.append(
                        f"<tr><th scope='row'>{html.escape(name.split(':')[0])}, {y} year{'s' if y > 1 else ''}</th>"
                        f"<td>{v['starts']:,}</td><td>{_money(v['median'])}</td><td>{v['below_start']:.1%}</td>"
                        f"<td>{v['stuck']:.1%}</td><td>{_money(v['worst'])}</td><td>{_money(v['best'])}</td></tr>")
        verdict = ("<p class='verdict ok'>✓ Profitable in every stress scenario</p>" if s["robust"] else
                   "<p class='verdict bad'>✕ Loses money in at least one stress scenario</p>")
        finalist_blocks.append(f"""
<section>
  <h3>{html.escape(s['rule'].describe())}</h3>
  {verdict}
  <div class="scroll"><table><thead><tr><th>Scenario</th><th>Win rate (backtest path)</th>
  <th>Win rate (every entry day)</th><th>Total, 1 contract</th><th>Avg win</th><th>Avg loss</th></tr></thead>
  <tbody>{''.join(rows)}</tbody></table></div>
  <h4>A {_money(acct)} account, started on every week in history</h4>
  <div class="scroll"><table><thead><tr><th>Run</th><th>Start dates</th><th>Median ending balance</th>
  <th>Ended below {_money(acct)}</th><th>Hit a loss and got stuck</th><th>Worst</th><th>Best</th></tr></thead>
  <tbody>{''.join(acct_rows)}</tbody></table></div>
</section>""")

    buys = sum(f["at_target"] for (act, _), f in a["families"].items() if act == "buy")
    robust = [s for s in a["top"] if s["robust"]]
    lead = robust[0] if robust else (a["top"][0] if a["top"] else None)
    lead_txt = ""
    if lead:
        base_acct = lead["stress"][0][3].get(5) or {}
        lead_txt = (f"<p><b>Most robust rule:</b> {html.escape(lead['rule'].describe())}. It won "
                    f"{lead['full']['win_rate']:.2%} of {lead['full']['n']} trades and "
                    f"{lead['ed']['win_rate']:.2%} of {lead['ed']['n']:,} possible entry days. The worst stress "
                    f"scenario still won {lead['min_ed']:.2%}. It averages {_money(lead['full']['avg_win'])} per win, "
                    f"and a loss can cost up to {_money(lead['full']['max_capital'])}."
                    + (f" A {_money(acct)} account traded for 5 years ended at a median of "
                       f"{_money(base_acct['median'])}; {base_acct['stuck']:.1%} of start dates hit a loss and could not "
                       f"afford the next trade." if base_acct else "") + "</p>")
    o = a["oos"]
    cal = f"<p class='lede'>{html.escape(calibration)}</p>" if calibration else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SPY Win-Rate Search</title>
<style>{_CSS}
td.rule{{text-align:left;white-space:normal;min-width:260px}}
h3{{font-size:15px}} h4{{font-size:14px;margin:16px 0 6px}}
.answer{{border-left:4px solid var(--s1);padding:4px 14px;margin:16px 0}}
</style></head>
<body><main>
<header>
  <h1>SPY Win-Rate Search</h1>
  <p class="meta">{start} to {end} · {html.escape(source)} · {a['tested']:,} rules · {_money(acct)} account ·
  generated {datetime.now():%Y-%m-%d %H:%M}</p>
</header>
<aside class="disclosure">{html.escape(DISCLOSURE)}</aside>

<div class="answer">
  <p><b>Buying calls or puts:</b> {buys} rules reached a 99% win rate.</p>
  <p><b>Fit a {_money(acct)} account at 99%+ and profitable:</b> {a['candidates']} rules, all of them spreads
  (sell one far out-of-the-money option, buy one further out). {len(a['survivors'])} stayed at 99%+ and profitable
  when entered on every possible day.</p>
  {lead_txt}
  <p><b>Held up on later data:</b> of {o['picked']} affordable rules at 99%+ before {split}, {o['held']} stayed at
  99%+ after it.</p>
  <p><b>Proof standard:</b> showing a true 99% win rate with 95% confidence takes about {PROOF_TRADES} straight wins
  with zero losses.</p>
</div>

<section>
  <h2>Best win rate in each family</h2>
  <p class="lede">Rules with at least {MIN_TRADES} trades, every trade replayed at today's SPY price. "Qualify" means
  99%+, profitable and affordable for {_money(acct)}.</p>
  <div class="scroll"><table><thead><tr><th>Family</th><th>Rules</th><th>At 99%+</th><th>Fit {_money(acct)}</th>
  <th>Qualify</th><th>Best win rate</th><th>Trades</th><th>Avg win</th><th>Avg loss</th><th>Capital per contract</th>
  <th>Best rule</th></tr></thead><tbody>{''.join(fam_rows)}</tbody></table></div>
</section>

<section>
  <h2>Rules that survive the every-day retest</h2>
  <p class="lede">A single backtest path can dodge the bad days by timing luck, so each candidate is re-run from every
  day its signal fired. "Lower bound" is the lowest true win rate the evidence supports with 95% confidence,
  counting only non-overlapping trades.</p>
  <div class="scroll"><table><thead><tr><th>Rule</th><th>Backtest path</th><th>Every entry day</th><th>Lower bound</th>
  <th>Before / after {split}</th><th>Avg win</th><th>Avg loss</th><th>Capital</th><th>Total, 1 contract</th></tr></thead>
  <tbody>{surv_rows}</tbody></table></div>
</section>

<section>
  <h2>Stress tests and the real account</h2>
  {''.join(finalist_blocks)}
</section>

<section>
  <h2>How the prices were checked</h2>
  {cal}
  <ul>
    <li>Option prices come from a model, not historical quotes. The model's volatility level and skew were fitted to a
    real Cboe SPY chain, and today's signal is priced from live Cboe quotes.</li>
    <li>The years in the data include 2008, 2011, 2015, 2018, 2020, 2022 and 2025. A rule that never lost here can
    still meet a crash it hasn't seen.</li>
    <li>Spreads need a margin account with spread approval at both Schwab and Robinhood.</li>
  </ul>
</section>
</main></body></html>"""
