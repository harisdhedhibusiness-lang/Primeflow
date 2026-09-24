"""Turn raw search results into findings: the win-rate ceiling per family, the 99% club
re-tested on every entry day, and the in-sample -> out-of-sample survival rate."""

from __future__ import annotations

import html
import math
from datetime import datetime
from typing import Dict

from .report import _CSS
from .search import MIN_TRADES, _S, every_entry_day, summarize

TARGET = 0.99
# Straight wins, with zero losses, needed to show a true win rate of at least 99% with 95%
# confidence (one-sided): 0.99 ** n <= 0.05.
PROOF_TRADES = math.ceil(math.log(0.05) / math.log(TARGET))

DISCLOSURE = (
    "Hypothetical, model-priced results on historical data. Option prices are simulated with "
    "Black-Scholes and a VIX-derived volatility surface, not historical quotes. Past performance "
    "does not indicate future results. Selling options without a hedge can lose many times the "
    "premium collected. Educational software, not investment advice."
)

FAMILIES = (("buy", "call"), ("buy", "put"), ("sell", "call"), ("sell", "put"))


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


def analyze(results, window) -> Dict[str, object]:
    mf = _S["mf"]
    lo = next(i for i, r in enumerate(mf.regime) if r is not None)
    hi = len(mf) - 1
    ranked = [r for r in results if r[1]["n"] >= MIN_TRADES]

    families = {}
    for action, right in FAMILIES:
        sub = [r for r in ranked if r[0].action == action and r[0].right == right]
        families[(action, right)] = {
            "rules": len(sub),
            "at_target": sum(r[1]["win_rate"] >= TARGET for r in sub),
            "profitable": sum(r[1]["total"] > 0 for r in sub),
            "best": max(sub, key=lambda r: (r[1]["win_rate"], r[1]["n"])) if sub else None,
        }

    club = [r for r in ranked if r[1]["win_rate"] >= TARGET]
    retest, seen = [], set()
    for rule, full, _, _ in sorted(club, key=lambda r: -r[1]["total"]):
        trades = every_entry_day(rule, lo, hi)
        s = summarize(trades)
        key = (s["n"], round(s["win_rate"], 5), round(s["total"]))
        if key in seen:
            continue
        seen.add(key)
        # Overlapping entries on neighbouring days are nearly the same trade, so the evidence is
        # only as strong as the number of non-overlapping trades, at the every-day win rate.
        independent = full["n"]
        wins = min(independent, math.floor(s["win_rate"] * independent + 1e-9))
        worst = sorted(trades, key=lambda t: t.pnl)[:3]
        retest.append((rule, full, s, win_rate_lower_bound(wins, independent), worst))
    retest.sort(key=lambda x: (-x[2]["win_rate"], -x[2]["n"]))

    picked = [r for r in results if r[2]["n"] >= 20 and r[2]["win_rate"] >= TARGET and r[3]["n"] >= 10]
    oos = {
        "picked": len(picked),
        "held": sum(r[3]["win_rate"] >= TARGET for r in picked),
        "profitable": sum(r[3]["total"] > 0 for r in picked),
        "worst": min((r[3]["worst"] for r in picked), default=0.0),
    }
    return {"window": window, "tested": len(results), "ranked": len(ranked), "families": families,
            "club": len(club), "retest": retest, "oos": oos}


def _money(x: float) -> str:
    return f"-${-x:,.0f}" if x < 0 else f"${x:,.0f}"


def console_text(a: Dict[str, object]) -> str:
    start, split, end = a["window"]
    out = [f"Win-rate search {start} -> {end}. {a['tested']:,} rules tested, {a['ranked']:,} with "
           f"{MIN_TRADES}+ trades.", ""]
    out.append(f"  {'Family':<11}{'Rules':>7}{'>=99%':>7}{'Best win rate':>15}{'Avg win':>9}{'Avg loss':>10}")
    for (action, right), f in a["families"].items():
        b = f["best"]
        if b is None:
            continue
        s = b[1]
        out.append(f"  {action + ' ' + right + 's':<11}{f['rules']:>7}{f['at_target']:>7}"
                   f"{s['win_rate']:>10.1%} ({s['n']}){_money(s['avg_win']):>9}{_money(s['avg_loss']):>10}")
    o = a["oos"]
    out += ["", f"Rules at 99%+ before {split} that stayed 99%+ from {split} on: "
                f"{o['held']} of {o['picked']} (worst later loss {_money(o['worst'])}).", "",
            "99% rules re-tested on every possible entry day:"]
    for rule, full, s, lb, _ in a["retest"][:8]:
        out.append(f"  {s['win_rate']:6.1%} of {s['n']:>4} days (95% lower bound {lb:.0%}), avg win "
                   f"{_money(s['avg_win'])}, worst {_money(s['worst'])} | {rule.describe()}")
    out += ["", f"Proving 99% with 95% confidence takes about {PROOF_TRADES} straight wins and zero losses."]
    return "\n".join(out)


def html_page(a: Dict[str, object], source: str) -> str:
    start, split, end = a["window"]
    fam_rows = []
    for (action, right), f in a["families"].items():
        b = f["best"]
        if b is None:
            continue
        rule, s = b[0], b[1]
        fam_rows.append(
            f"<tr><th scope='row'>{action.title()} {right}s</th><td>{f['rules']:,}</td>"
            f"<td class='{'ok' if f['at_target'] else 'bad'}'>{f['at_target']}</td>"
            f"<td>{s['win_rate']:.1%}</td><td>{s['n']}</td><td>{_money(s['avg_win'])}</td>"
            f"<td>{_money(s['avg_loss'])}</td><td>{_money(s['worst'])}</td>"
            f"<td>{_money(s['avg_premium'])}</td><td class='rule'>{html.escape(rule.describe())}</td></tr>")

    club_rows = []
    for rule, full, s, lb, worst in a["retest"]:
        ratio = (f"{-s['avg_loss'] / s['avg_win']:.0f}x" if s["avg_loss"] < 0 and s["avg_win"] > 0 else "no losses")
        worst_txt = ", ".join(f"{t.entry} {_money(t.pnl)}" for t in worst if t.pnl <= 0) or "none"
        club_rows.append(
            f"<tr><td class='rule'>{html.escape(rule.describe())}</td>"
            f"<td>{full['win_rate']:.1%} of {full['n']}</td>"
            f"<td class='{'ok' if s['win_rate'] >= TARGET else 'bad'}'>{s['win_rate']:.1%} of {s['n']}</td>"
            f"<td>{lb:.1%}</td><td>{_money(s['avg_win'])}</td><td>{ratio}</td>"
            f"<td class='wrap'>{html.escape(worst_txt)}</td></tr>")

    o = a["oos"]
    buys_at_target = sum(f["at_target"] for (act, _), f in a["families"].items() if act == "buy")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SPY Win-Rate Search</title>
<style>{_CSS}
td.rule{{text-align:left;white-space:normal;min-width:260px}}
td.wrap{{white-space:normal;min-width:180px}}
.answer{{border-left:4px solid var(--s1);padding:4px 14px;margin:16px 0}}
</style></head>
<body><main>
<header>
  <h1>SPY Win-Rate Search</h1>
  <p class="meta">{start} to {end} · {html.escape(source)} · {a['tested']:,} rules tested ·
  generated {datetime.now():%Y-%m-%d %H:%M}</p>
</header>
<aside class="disclosure">{html.escape(DISCLOSURE)}</aside>

<div class="answer">
  <p><b>Buying calls or puts:</b> {buys_at_target} rules reached a 99% win rate.</p>
  <p><b>Selling calls or puts:</b> {a['club']} rules reached 99% on their backtest path. Re-tested on
  every possible entry day, {sum(1 for r in a['retest'] if r[2]['win_rate'] >= TARGET)} distinct rules stayed at 99% or better.
  All of them sell far out-of-the-money options, collect a small premium, and need a margin account
  able to cover a large loss.</p>
  <p><b>Held up on later data:</b> of {o['picked']} rules at 99%+ from {start} to {split},
  {o['held']} stayed at 99%+ after {split}. The worst single later loss among them was {_money(o['worst'])}.</p>
  <p><b>Proof standard:</b> showing a true 99% win rate with 95% confidence takes about {PROOF_TRADES}
  straight wins with zero losses.</p>
</div>

<section>
  <h2>Best win rate in each family</h2>
  <p class="lede">Rules with at least {MIN_TRADES} trades. Dollar figures are per contract at the SPY prices of the test period.</p>
  <div class="scroll"><table><thead><tr><th>Family</th><th>Rules</th><th>At 99%+</th><th>Best win rate</th>
  <th>Trades</th><th>Avg win</th><th>Avg loss</th><th>Worst trade</th><th>Avg premium</th><th>Rule</th></tr></thead>
  <tbody>{''.join(fam_rows)}</tbody></table></div>
</section>

<section>
  <h2>The 99% rules, re-tested on every entry day</h2>
  <p class="lede">A backtest that never overlaps trades walks one path of entry dates and can miss the bad
  days by timing luck. This table enters the same rule on every day its signal fired.
  "Lower bound" is the lowest true win rate the evidence supports with 95% confidence. Entries on
  neighbouring days are nearly the same trade, so it counts only the non-overlapping trades.</p>
  <div class="scroll"><table><thead><tr><th>Rule</th><th>Backtest path</th><th>Every entry day</th>
  <th>Lower bound</th><th>Avg win</th><th>One loss = how many wins</th><th>Worst entries</th></tr></thead>
  <tbody>{''.join(club_rows)}</tbody></table></div>
</section>

<section>
  <h2>What this test can't show</h2>
  <ul>
    <li>Only the years in the data. A rule that never lost here may simply not have met its crash yet.
    Run the full history before trusting any 99% rule.</li>
    <li>Selling an uncovered SPY option needs a margin account, top-level options approval and
    several thousand dollars of buying power per contract. Selling a cash-secured put ties up the
    strike price x 100.</li>
    <li>Option prices are modelled, not real quotes. Take-profit orders are assumed to fill intraday
    at the limit. Stops are checked at the close.</li>
  </ul>
</section>
</main></body></html>"""
