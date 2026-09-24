"""Today's signal: the regime, the risk check and the exact trade each strategy would place.

Built for paper trading by hand (for example in thinkorswim's paperMoney) until a broker
connection exists. Prices shown are model estimates; always use the live quote.
"""

from __future__ import annotations

from datetime import timedelta
from typing import List

from .backtest import open_position
from .config import CostConfig, PricingConfig, RiskConfig
from .pricing import OptionPricer
from .regime import MarketFrame
from .risk import RiskManager
from .strategies import Strategy


def todays_signal(mf: MarketFrame, strategies: List[Strategy], equity: float) -> str:
    i = len(mf) - 1
    r = mf.regime[i]
    pricer = OptionPricer(PricingConfig())
    costs = CostConfig()
    lines = [f"Last close: {mf.dates[i]}   SPY {mf.close[i]:.2f}   VIX {mf.vix[i] or float('nan'):.2f}"]
    if r is None:
        return "\n".join(lines + ["Not enough history to classify the regime."])
    lines += [f"Regime gate: {r.describe()}", ""]

    for s in strategies:
        lines.append(f"{s.title}")
        if not s.entry_signal(mf, i):
            lines += ["  No signal today. Regime gate or setup not met.", ""]
            continue
        plan = s.build(mf, i, pricer)
        if plan is None:
            lines += ["  Signal fired, but no contract met the rules (expiry or premium).", ""]
            continue
        rm = RiskManager(RiskConfig(starting_equity=equity))
        pos = open_position(plan, i, mf.dates[i], equity, [], rm, costs.commission_per_contract, costs.slippage_per_leg)
        dte = (plan.expiry - mf.dates[i]).days
        lines.append(f"  SIGNAL. Expiry {plan.expiry} ({dte} days)")
        lines += [f"    {leg.describe()}" for leg in plan.legs]
        if plan.kind == "credit":
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
    lines.append("Model prices are estimates. Use live quotes. Paper trade first.")
    return "\n".join(lines)
