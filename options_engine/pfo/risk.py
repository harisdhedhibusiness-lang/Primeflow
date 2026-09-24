"""The risk manager. Every entry goes through it; nothing trades around it.

Rules, in the order they are checked:
  1. Kill switch     equity below hard_floor x starting equity -> no new trades, ever
  2. Drawdown halt   equity down drawdown_halt from its peak -> no new trades for halt_days
  3. Losing streak   loss_streak losers in a row -> no new trades for loss_streak_pause
  4. Position cap    at most max_open_positions open at once
  5. Sizing          contracts = target risk / max loss per contract, never more than the
                     account can cover, one contract only under the small-account rule
"""

from __future__ import annotations

import math
from typing import List, Tuple

from .config import RiskConfig


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg
        self.peak = cfg.starting_equity
        self.killed = False
        self.halted_until = -1
        self.paused_until = -1
        self.streak = 0
        self.events: List[Tuple[object, str]] = []
        self.halted_bars = 0

    def update(self, i: int, day, equity: float) -> None:
        if self.killed:
            self.halted_bars += 1
            return
        self.peak = max(self.peak, equity)
        if equity < self.cfg.starting_equity * self.cfg.hard_floor:
            self.killed = True
            self.events.append((day, f"KILL SWITCH: equity ${equity:,.2f} below the "
                                     f"{self.cfg.hard_floor:.0%} floor. No new trades."))
        elif i >= self.halted_until and equity < self.peak * (1 - self.cfg.drawdown_halt):
            self.halted_until = i + self.cfg.halt_days
            self.events.append((day, f"Drawdown halt: ${equity:,.2f} is {1 - equity / self.peak:.0%} "
                                     f"off the peak. Paused {self.cfg.halt_days} trading days."))
            self.peak = equity
        if i < max(self.halted_until, self.paused_until):
            self.halted_bars += 1

    def on_trade_closed(self, i: int, day, pnl: float) -> None:
        self.streak = self.streak + 1 if pnl < 0 else 0
        if self.streak >= self.cfg.loss_streak:
            self.paused_until = i + self.cfg.loss_streak_pause
            self.events.append((day, f"{self.streak} losses in a row. Paused "
                                     f"{self.cfg.loss_streak_pause} trading days."))
            self.streak = 0

    def can_open(self, i: int, open_count: int) -> Tuple[bool, str]:
        if self.killed:
            return False, "kill_switch"
        if i < self.halted_until:
            return False, "drawdown_halt"
        if i < self.paused_until:
            return False, "loss_streak_pause"
        if open_count >= self.cfg.max_open_positions:
            return False, "position_cap"
        return True, ""

    def size(self, equity: float, max_loss_per_contract: float, open_risk: float) -> int:
        """Contracts to trade, given the dollar max loss of one contract (fees included)."""
        c = self.cfg
        if max_loss_per_contract <= 0 or equity <= 0:
            return 0
        n = math.floor(equity * c.risk_per_trade / max_loss_per_contract)
        if n == 0 and max_loss_per_contract <= equity * c.micro_account_cap:
            n = 1
        room = equity * c.max_total_risk - open_risk
        n = min(n, math.floor(room / max_loss_per_contract), c.max_contracts)
        # Never commit more than the cash in the account.
        n = min(n, math.floor((equity - open_risk) / max_loss_per_contract))
        return max(n, 0)
