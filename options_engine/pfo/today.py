"""Today's signal: the regime, the risk check and the exact trade each strategy would place.

Built for paper trading by hand (for example in thinkorswim's paperMoney) until a broker
connection exists. Strategies that support it are priced from live Cboe quotes (delayed about
15 minutes); the rest show model estimates.
"""

from __future__ import annotations

from datetime import timedelta
from typing import List, Optional

from .backtest import open_position
from .config import CostConfig, PricingConfig, RiskConfig
from .pricing import OptionPricer
from .regime import MarketFrame
from .risk import RiskManager
from .strategies import Strategy


def todays_signal(mf: MarketFrame, strategies: List[Strategy], equity: float, chain_loader=None) -> str:
    i = len(mf) - 1
    r = mf.regime[i]
    pricer = OptionPricer(PricingConfig())
    costs = CostConfig()
    lines = [f"Last close: {mf.dates[i]}   SPY {mf.close[i]:.2f}   VIX {mf.vix[i] or float('nan'):.2f}"]
    if r is None:
        return "\n".join(lines + ["Not enough history to classify the regime."])
    lines += [f"Regime gate: {r.describe()}", ""]
    chain, chain_error = None, None

    for s in strategies:
        lines.append(f"{s.title}")
        if not s.entry_signal(mf, i):
            lines += ["  No signal today. Regime gate or setup not met.", ""]
            continue

        live: Optional[dict] = None
        if chain_loader is not None and type(s).live_plan is not Strategy.live_plan:
            if chain is None and chain_error is None:
                try:
                    chain = chain_loader()
                except Exception as exc:  # fall back to the model
                    chain_error = str(exc)
            if chain is not None:
                live = s.live_plan(chain, mf.dates[i])
        if live is not None:
            lines += _live_lines(live, chain, mf.dates[i], equity, costs)
            continue

        plan = s.build(mf, i, pricer)
        if plan is None:
            lines += ["  Signal fired, but no contract met the rules (expiry or premium).", ""]
            continue
        rm = RiskManager(RiskConfig(starting_equity=equity))
        pos = open_position(plan, i, mf.dates[i], equity, [], rm, costs.commission_per_contract, costs.slippage_per_leg)
        dte = (plan.expiry - mf.dates[i]).days
        lines.append(f"  SIGNAL (model prices{'; live quotes unavailable: ' + chain_error if chain_error else ''}). "
                     f"Expiry {plan.expiry} ({dte} days)")
        lines += [f"    {leg.describe()}" for leg in plan.legs]
        if plan.kind == "credit" and plan.take_profit >= 1:
            credit = -plan.mid
            lines += [
                f"  Model credit ~${credit:.2f}  (width ${plan.width:g}, max loss ~${(plan.width - credit) * 100:.0f} per spread)",
                "  Enter as ONE spread limit order near the live mid. Then hold to expiry.",
            ]
        elif plan.kind == "credit":
            credit = -plan.mid
            lines += [
                f"  Model credit ~${credit:.2f}  (width ${plan.width:g}, max loss ~${(plan.width - credit) * 100:.0f} per spread)",
                f"  Enter as a LIMIT order near the live mid. Skip if the credit is under ${s.cfg.min_credit_frac * plan.width:.2f}.",
                f"  Take profit: buy back at {credit * (1 - plan.take_profit):.2f}",
                f"  Stop loss:   buy back at {credit * (1 + plan.stop_loss):.2f}",
                f"  Time exit:   close on or after {plan.expiry - timedelta(days=plan.exit_dte)}",
            ]
        else:
            debit = plan.mid
            lines += [
                f"  Model debit ~${debit:.2f}  (width ${plan.width:g}, max loss ~${debit * 100:.0f} per spread)",
                "  Enter as a LIMIT order near the live mid.",
                f"  Take profit: sell at {debit + plan.take_profit * (plan.width - debit):.2f}",
                f"  Stop loss:   sell at {debit * (1 - plan.stop_loss):.2f}",
                f"  Time exit:   after {plan.max_hold_days} trading days, or {plan.exit_dte} days before expiry",
            ]
        extra = s.exit_levels(mf, i)
        if extra:
            lines.append(f"  Also: {extra}")
        if pos is None:
            lines.append(f"  RISK CHECK: BLOCKED. One spread risks more than a ${equity:,.0f} account allows.")
        else:
            lines.append(f"  RISK CHECK: OK for {pos.contracts} spread(s), total max loss ${pos.max_loss:,.0f} "
                         f"({pos.max_loss / equity:.0%} of the account).")
        lines.append("")
    lines.append("Paper trade first. Skip a signal if you already hold a position from the same strategy.")
    return "\n".join(lines)


def _live_lines(live: dict, chain, today, equity: float, costs: CostConfig) -> List[str]:
    short, long_, expiry = live["short"], live["long"], live["expiry"]
    kind = "call" if short.is_call else "put"
    fees = costs.commission_per_contract * 2  # opening two legs; expiring worthless costs nothing
    capital = (live["width"] - max(live["natural"], 0.0)) * 100 + fees
    breach = "closes ABOVE" if short.is_call else "closes BELOW"
    out = [
        f"  SIGNAL from live Cboe quotes (delayed ~15 min, as of {chain.as_of}, SPY {chain.underlying:.2f})",
        f"  Expiry {expiry} ({(expiry - today).days} days)",
        f"    SELL {short.strike:g} {kind}   bid {short.bid:.2f} / ask {short.ask:.2f}   delta {short.delta:+.3f}",
        f"    BUY  {long_.strike:g} {kind}   bid {long_.bid:.2f} / ask {long_.ask:.2f}",
        f"  Credit: mid ${live['mid']:.2f}, natural ${live['natural']:.2f} (${live['mid'] * 100:.0f} per spread before fees)",
    ]
    if live["mid"] < live["min_credit"]:
        out += [f"  SKIP: credit below the ${live['min_credit']:.2f} minimum the backtest required.", ""]
        return out
    out += [
        f"  Place ONE spread limit order for a ${live['mid']:.2f} credit. Do not accept less than ${live['min_credit']:.2f}.",
        f"  Then do nothing until expiry. It expires worthless (full win) unless SPY {breach} {short.strike:g} on {expiry}.",
        f"  Max loss per spread: ${capital:,.0f}.",
    ]
    if capital > equity:
        out.append(f"  ACCOUNT CHECK: BLOCKED. One spread needs ${capital:,.0f}; the account has ${equity:,.0f}.")
    else:
        out.append(f"  ACCOUNT CHECK: one spread ties up ${capital:,.0f} = {capital / equity:.0%} of a "
                   f"${equity:,.0f} account. A loss costs about that much.")
    out.append("")
    return out
